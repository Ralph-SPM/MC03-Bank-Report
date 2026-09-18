"""Groq LLM remarks summarizer using qwen/qwen3.8-27b for RCBC bank reports."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from typing import Optional, List, Tuple
import httpx
from dotenv import load_dotenv

from mc03.services.remarks_lab.cleaner import clean_remark

# Ensure environment variables are loaded
load_dotenv()


def _is_truthy(val: Optional[str]) -> bool:
    if not val:
        return False
    return val.strip().lower() in ("true", "1", "yes", "on", "enabled")


class GroqRemarksSummarizer:
    """
    Summarizes raw, verbose field collector messages using Groq's qwen/qwen3.8-27b.
    Features:
      - Toggable batch processing (GROQ_BATCH_PROCESSING in .env, defaults to False).
      - Sequential rate pacing to protect free-tier rate limits.
      - Exponential backoff retry on HTTP 429.
      - In-memory caching for identical remarks.
      - Strict character limit enforcement (target <= max_chars).
      - Seamless fallback returning None if API fails or quota exhausted.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        batch_processing: Optional[bool] = None,
        timeout: Optional[float] = None,
        base_url: str = "https://api.groq.com/openai/v1",
    ):
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = os.getenv("GROQ_API_KEY", "").strip()

        self.model = model or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()

        if batch_processing is not None:
            self.batch_processing = batch_processing
        else:
            self.batch_processing = _is_truthy(os.getenv("GROQ_BATCH_PROCESSING", "false"))

        timeout_env = os.getenv("GROQ_REQUEST_TIMEOUT", "20.0")
        try:
            self.timeout = timeout if timeout is not None else float(timeout_env)
        except ValueError:
            self.timeout = 20.0

        self.base_url = base_url.rstrip("/")
        self._cache: dict[str, str] = {}
        self._last_call_time: float = 0.0
        self._cooldown_until: float = 0.0
        self._rate_pace_delay: float = 0.2  # Pacing between sequential calls on free tier

    @property
    def is_available(self) -> bool:
        """True if API key is configured and valid."""
        return bool(self.api_key and not self.api_key.startswith("gsk_placeholder"))

    def _cache_key(self, text: str, max_chars: int, is_retry: bool) -> str:
        h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
        return f"{h}:{max_chars}:{is_retry}"

    def _build_prompt(self, text: str, max_chars: int, is_retry: bool) -> List[dict]:
        clean_input = clean_remark(text).cleaned
        if is_retry:
            system_prompt = (
                f"You are an expert Data Analyst summarizing field collection notes for RCBC bank reports.\n"
                f"Strict Rules:\n"
                f"1. The previous summary was too long. Summarize more aggressively in concise Taglish or English.\n"
                f"2. Keep ONLY: WHO was contacted and WHAT was stated/confirmed/promised.\n"
                f"3. Maximum total output length: strictly {max_chars} characters.\n"
                f"4. Output ONLY the raw summary sentence. Do NOT include markdown, quotes, labels, or prefixes.\n"
                f"5. Never include bank-prohibited tags: BCAL, BKAL, L3, INB, OBD, SRC, or phone numbers."
            )
        else:
            system_prompt = (
                f"You are an expert Data Analyst summarizing field collection notes for RCBC bank reports.\n"
                f"Strict Rules:\n"
                f"1. Summarize the field note in concise Taglish or English preserving: WHO was contacted and WHAT was confirmed/promised.\n"
                f"2. Maximum total output length: strictly {max_chars} characters.\n"
                f"3. Output ONLY the raw summary sentence. Do NOT include markdown, quotes, labels, or prefixes.\n"
                f"4. Never include bank-prohibited tags: BCAL, BKAL, L3, INB, OBD, SRC, or phone numbers."
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Field note to summarize:\n{clean_input}"},
        ]

    def _clean_model_output(self, raw_output: str, max_chars: int) -> str:
        """Strip forbidden artifacts, quotes, prefixes, and enforce character limit."""
        out = raw_output.strip()
        # Remove markdown bold/italics
        out = re.sub(r"[*_`]", "", out).strip()
        # Remove any lingering "Summary:" or "Field Report:" prefixes
        out = re.sub(r"^(?:summary|note|field report|field note)\s*:\s*", "", out, flags=re.IGNORECASE).strip()
        # Remove surrounding quotes and internal quotes
        out = out.strip("\"'“‘’”")
        out = out.replace('"', '').replace("'", "")
        # Strip bank prohibited markers
        out = clean_remark(out).cleaned
        # Normalize whitespace
        out = re.sub(r"\s+", " ", out).strip()

        # If slightly over max_chars, cut at last word boundary
        if len(out) > max_chars:
            cutoff = out[:max_chars]
            last_space = cutoff.rfind(" ")
            if last_space > 20:
                out = cutoff[:last_space].rstrip(" ;,.-")
            else:
                out = cutoff.rstrip(" ;,.-")

        return out

    def summarize(
        self,
        text: str,
        max_chars: int = 140,
        is_retry_shorter: bool = False,
    ) -> Optional[str]:
        """
        Synchronous summarization call with caching, rate pacing, and backoff.
        Returns cleaned summary string or None on failure.
        """
        if not self.is_available or not text or not text.strip():
            return None

        # Circuit breaker check: if under cooldown from rate limit, return None immediately
        if time.time() < self._cooldown_until:
            return None

        raw = text.strip()
        if len(raw) <= max_chars and not is_retry_shorter:
            return raw

        c_key = self._cache_key(raw, max_chars, is_retry_shorter)
        if c_key in self._cache:
            return self._cache[c_key]

        # Rate pacing for free tier sequential execution
        if not self.batch_processing and self._rate_pace_delay > 0:
            elapsed = time.time() - self._last_call_time
            if elapsed < self._rate_pace_delay:
                time.sleep(self._rate_pace_delay - elapsed)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = self._build_prompt(raw, max_chars, is_retry_shorter)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 80,
        }

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                self._last_call_time = time.time()
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        cleaned_summary = self._clean_model_output(content, max_chars)
                        if cleaned_summary:
                            self._cache[c_key] = cleaned_summary
                            return cleaned_summary

                elif resp.status_code == 429:
                    # Rate limit exceeded: activate cooldown so subsequent calls don't stall
                    self._cooldown_until = time.time() + 60.0
                    return None
                else:
                    break

            except Exception:
                if attempt < max_retries:
                    time.sleep(0.5)
                    continue
                break

        return None

    async def summarize_async(
        self,
        text: str,
        max_chars: int = 140,
        is_retry_shorter: bool = False,
    ) -> Optional[str]:
        """Asynchronous summarization call."""
        if not self.is_available or not text or not text.strip():
            return None

        raw = text.strip()
        if len(raw) <= max_chars and not is_retry_shorter:
            return raw

        c_key = self._cache_key(raw, max_chars, is_retry_shorter)
        if c_key in self._cache:
            return self._cache[c_key]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = self._build_prompt(raw, max_chars, is_retry_shorter)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 80,
        }

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        cleaned_summary = self._clean_model_output(content, max_chars)
                        if cleaned_summary:
                            self._cache[c_key] = cleaned_summary
                            return cleaned_summary

                elif resp.status_code == 429:
                    retry_after = 2.0 * (attempt + 1)
                    if attempt < max_retries:
                        await asyncio.sleep(retry_after)
                        continue
                else:
                    break

            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                break

        return None

    def summarize_batch(
        self,
        items: List[Tuple[str, int]],
    ) -> List[Optional[str]]:
        """
        Process multiple remarks.
        If batch_processing is True: uses asyncio with concurrency semaphore.
        If batch_processing is False (default): processes sequentially with pacing.
        """
        if not items:
            return []

        if not self.batch_processing:
            # Sequential mode (safe for free tier)
            results = []
            for text, max_chars in items:
                results.append(self.summarize(text, max_chars=max_chars))
            return results

        # Concurrent batch mode (when enabled)
        async def _run_batch() -> List[Optional[str]]:
            sem = asyncio.Semaphore(3)  # limit concurrency to 3

            async def _task(t: str, mc: int) -> Optional[str]:
                async with sem:
                    return await self.summarize_async(t, max_chars=mc)

            tasks = [_task(t, mc) for t, mc in items]
            return await asyncio.gather(*tasks)

        try:
            return asyncio.run(_run_batch())
        except Exception:
            # Fallback to sequential on event loop conflicts
            return [self.summarize(t, max_chars=mc) for t, mc in items]
