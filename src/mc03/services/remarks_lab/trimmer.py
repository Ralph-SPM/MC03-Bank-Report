"""200-character summarizer, deduplicator, and statement formatter for bank upload compliance."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional, Callable
from mc03.domain.remarks import trim_to_200


@dataclass
class TrimmedRemark:
    trimmed_text: str
    original_length: int
    trimmed_length: int
    fits_within_200: bool
    was_truncated: bool
    preamble_stripped: bool


# Pattern to identify and preserve the ECA header e.g. ECA AUTO_S.P. MADRID_HOME_09/02/2026
ECA_HEADER_PATTERN = re.compile(
    r"^(ECA[\s_]+AUTO_[A-Za-z0-9._\s]+?_\d{1,2}/\d{1,2}/\d{2,4}\s*[-:]?\s*)",
    re.IGNORECASE
)

# Redundant collector prefixes and internal notes to clean from body
PREAMBLE_PATTERNS = [
    r"^(?:FIELD\s+VISIT\s+RESULT\s*[-:]?\s*)",
    r"^(?:VISIT\s+RESULT\s*[-:]?\s*)",
    r"^(?:NOTE\s*[-:]?\s*)",
    r"^(?:REMARKS?\s*[-:]?\s*)",
    r"^(?:UPDATE\s*[-:]?\s*)",
    r"^(?:PER\s+FIELD\s+OFFICER\s*[-:]?\s*)",
]


def strip_preamble(text: str) -> tuple[str, bool]:
    """Repeatedly strip redundant internal collector prefixes without stripping ECA headers."""
    working_text = str(text).strip()
    any_stripped = False
    changed = True
    while changed:
        changed = False
        for pat in PREAMBLE_PATTERNS:
            match = re.match(pat, working_text, re.IGNORECASE)
            if match:
                working_text = working_text[match.end():].strip(" -:\t\r\n")
                any_stripped = True
                changed = True
                break
    return working_text, any_stripped


def deduplicate_phrases(text: str) -> str:
    """
    Remove repeating duplicate sentences, clauses, or phrases.
    E.g. 'Neg; per agent, account already resolved. Neg; per agent, account already resolved.'
    -> 'Neg; per agent, account already resolved.'
    """
    cleaned = text.strip()
    if not cleaned:
        return ""

    # Direct repeating chunk check (handles repeating sentences even with trailing fragments)
    for chunk_len in range(min(150, len(cleaned) // 2), 8, -1):
        chunk = cleaned[:chunk_len].strip(" .,;:-")
        if not chunk:
            continue
        parts = [p.strip(" .,;:-") for p in cleaned.split(chunk)]
        if len(parts) >= 3 and all(not p or chunk.startswith(p) for p in parts):
            return chunk

    # Sentence & clause level deduplication
    tokens = re.split(r"([.;\n]+)", cleaned)
    seen_normalized = set()
    result_pieces: List[str] = []

    i = 0
    while i < len(tokens):
        phrase = tokens[i].strip()
        sep = tokens[i + 1] if i + 1 < len(tokens) else ""
        norm = re.sub(r"[^a-z0-9]+", " ", phrase.lower()).strip()

        if norm and norm not in seen_normalized:
            seen_normalized.add(norm)
            result_pieces.append(phrase + sep)
        elif not norm and sep:
            pass
        i += 2

    deduped = " ".join(result_pieces).strip()
    deduped = re.sub(r"\s{2,}", " ", deduped)
    return deduped if deduped else cleaned


def fallback_clause_truncate(
    text: str,
    max_chars: int = 200,
    preferred_chars: int = 181,
) -> str:
    """
    Demo-safety fallback: truncate at the last complete clause under max_chars
    (≤preferred_chars preferred), rather than a hard character-index cut. Never cuts mid-word/phrase.
    """
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned

    # Try preferred limit first
    target_limit = preferred_chars if len(cleaned) > preferred_chars else max_chars
    candidate = cleaned[:target_limit]

    # Look for last complete clause delimiter (;, ., ,, -)
    clause_delims = [";", ".", ",", " - "]
    best_cut = -1
    for d in clause_delims:
        idx = candidate.rfind(d)
        if idx > 30 and idx > best_cut:
            best_cut = idx

    if best_cut != -1:
        return candidate[:best_cut].rstrip(" ;,.-")

    # Try clause boundary up to max_chars
    cand_max = cleaned[:max_chars]
    for d in clause_delims:
        idx = cand_max.rfind(d)
        if idx > 30 and idx > best_cut:
            best_cut = idx
    if best_cut != -1:
        return cand_max[:best_cut].rstrip(" ;,.-")

    # Cut at last word boundary under target_limit
    last_space = candidate.rfind(" ")
    if last_space > 20:
        return candidate[:last_space].rstrip(" ;,.-")

    # Fallback to word boundary under max_chars
    last_space_max = cand_max.rfind(" ")
    if last_space_max > 20:
        return cand_max[:last_space_max].rstrip(" ;,.-")

    return cand_max


# Backward-compatible alias
extract_core_sentence = fallback_clause_truncate


def llm_trim_remark(
    cleaned_remark: str,
    llm_provider: Optional[Callable[[str, bool], Optional[str]]] = None,
    max_chars: int = 200,
    preferred_chars: int = 181,
) -> str:
    """
    LLM-based trimming step:
      - Input: cleaned remark (tags/CH-codes/INB/OBD/SRC already stripped).
      - Instruction: shorten to keep ONLY who was contacted and what was discussed; target ≤preferred_chars (max ≤max_chars).
      - If output exceeds max_chars: retry once with an explicit 'shorter' instruction.
      - Fallback: truncate at the last complete clause under max_chars.
    """
    if len(cleaned_remark) <= preferred_chars:
        return cleaned_remark

    if llm_provider is not None:
        try:
            import inspect
            sig = inspect.signature(llm_provider)
            params = sig.parameters
            # 1. First LLM attempt
            if "max_chars" in params or len(params) >= 3:
                trimmed = llm_provider(cleaned_remark, max_chars=preferred_chars, is_retry_shorter=False)
            else:
                trimmed = llm_provider(cleaned_remark, False)

            if trimmed and isinstance(trimmed, str):
                trimmed = trimmed.strip()
                if len(trimmed) <= max_chars:
                    return trimmed

                # 2. Retry once with explicit "shorter" instruction if > max_chars
                if "max_chars" in params or len(params) >= 3:
                    shorter = llm_provider(trimmed, max_chars=preferred_chars, is_retry_shorter=True)
                else:
                    shorter = llm_provider(trimmed, True)

                if shorter and isinstance(shorter, str):
                    shorter = shorter.strip()
                    if len(shorter) <= max_chars:
                        return shorter
                    return fallback_clause_truncate(shorter, max_chars=max_chars, preferred_chars=preferred_chars)
        except Exception:
            pass  # Fallback gracefully to demo-safety clause truncation

    # Fallback path: truncate at last complete clause under max_chars
    return fallback_clause_truncate(cleaned_remark, max_chars=max_chars, preferred_chars=preferred_chars)


def trim_and_format(
    text: str,
    contact_person: str = "",
    llm_provider: Optional[Callable[[str, bool], Optional[str]]] = None,
) -> TrimmedRemark:
    """
    Formats remark for bank upload compliance (≤200 chars, ≤181 preferred):
    - Retains the 'ECA AUTO_S.P. MADRID_HOME_mm/dd/yyyy' header intact at the beginning.
    - Cleans redundant internal prefixes and duplicate repeating phrases from the body.
    - Shortens the body using LLM-based trimming (with clause-boundary safety fallback)
      so the total length (ECA header + body) strictly adheres to the 200-char ceiling.
    - Applies to both field-result remarks and DRR/call remarks.
    """
    text_str = str(text).strip() if text is not None else ""
    c_person = str(contact_person).strip() if contact_person is not None else ""
    if not text_str:
        return TrimmedRemark(
            trimmed_text="",
            original_length=0,
            trimmed_length=0,
            fits_within_200=True,
            was_truncated=False,
            preamble_stripped=False,
        )

    original_length = len(text_str)

    # 1. Detect and preserve ECA header prefix (e.g. ECA AUTO_S.P. MADRID_HOME_09/02/2026)
    eca_match = ECA_HEADER_PATTERN.match(text_str)
    if eca_match:
        eca_header = eca_match.group(1)
        body = text_str[len(eca_header):].strip()
    else:
        eca_header = ""
        body = text_str

    # 2. Strip redundant internal collector prefixes from body
    working_body, preamble_stripped = strip_preamble(body)

    # 3. Deduplicate repeating phrases in body
    deduped = deduplicate_phrases(working_body)
    if deduped:
        working_body = deduped

    # 4. If contact person is provided, try domain join
    if c_person:
        joined, fits = trim_to_200(c_person, working_body)
        if fits and (len(eca_header) + len(joined)) <= 200:
            final_res = f"{eca_header}{joined}".strip() if eca_header else joined
            return TrimmedRemark(
                trimmed_text=final_res,
                original_length=original_length,
                trimmed_length=len(final_res),
                fits_within_200=True,
                was_truncated=(original_length > 200 or len(working_body) > 200),
                preamble_stripped=preamble_stripped,
            )

    # 5. Check if ECA header + body already fits within 200 chars
    combined_candidate = f"{eca_header}{working_body}".strip() if eca_header else working_body
    if len(combined_candidate) <= 200:
        return TrimmedRemark(
            trimmed_text=combined_candidate,
            original_length=original_length,
            trimmed_length=len(combined_candidate),
            fits_within_200=True,
            was_truncated=(original_length > 200 or preamble_stripped or len(combined_candidate) < original_length),
            preamble_stripped=preamble_stripped,
        )

    # 6. LLM-based trimming step with retry and clause-level safety fallback on body
    avail_max = max(30, 200 - len(eca_header))
    avail_pref = max(30, 181 - len(eca_header))
    trimmed_body = llm_trim_remark(
        working_body,
        llm_provider=llm_provider,
        max_chars=avail_max,
        preferred_chars=avail_pref,
    )

    final_trimmed = f"{eca_header}{trimmed_body}".strip() if eca_header else trimmed_body
    if len(final_trimmed) > 200:
        final_trimmed = fallback_clause_truncate(final_trimmed, max_chars=200, preferred_chars=181)

    return TrimmedRemark(
        trimmed_text=final_trimmed,
        original_length=original_length,
        trimmed_length=len(final_trimmed),
        fits_within_200=True,
        was_truncated=True,
        preamble_stripped=preamble_stripped,
    )
