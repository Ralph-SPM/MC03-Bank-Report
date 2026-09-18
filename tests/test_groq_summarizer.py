"""Unit tests for GroqRemarksSummarizer and intelligent remark trimming."""

import pytest
from unittest.mock import patch, MagicMock
from mc03.services.remarks_lab.groq_summarizer import GroqRemarksSummarizer
from mc03.services.remarks_lab.trimmer import trim_and_format, llm_trim_remark


def test_groq_summarizer_initialization_defaults():
    summarizer = GroqRemarksSummarizer(api_key="test_key")
    assert summarizer.is_available is True
    assert summarizer.model == "qwen/qwen3.8-27b"
    assert summarizer.batch_processing is False


def test_groq_summarizer_prompt_builder():
    summarizer = GroqRemarksSummarizer(api_key="test_key")
    messages = summarizer._build_prompt("BCAL Upon visit house is closed", max_chars=140, is_retry=False)
    assert len(messages) == 2
    assert "140" in messages[0]["content"]
    assert "BCAL" not in messages[1]["content"]  # Prohibited tag stripped in prompt input


def test_groq_summarizer_clean_output():
    summarizer = GroqRemarksSummarizer(api_key="test_key")
    raw_output = '**Summary:** "Spoke with sister, client is in Davao and will remit payment BCAL" '
    cleaned = summarizer._clean_model_output(raw_output, max_chars=140)
    assert "BCAL" not in cleaned
    assert "Summary:" not in cleaned
    assert '"' not in cleaned
    assert "*" not in cleaned
    assert len(cleaned) <= 140


def test_groq_summarizer_caching():
    summarizer = GroqRemarksSummarizer(api_key="test_key")
    # Mock post
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Client promised to settle balance next week."}}]
    }

    long_narrative = "A very long field narrative that exceeds the character limit test test test " * 2
    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res1 = summarizer.summarize(long_narrative, max_chars=60)
        assert res1 == "Client promised to settle balance next week."
        assert mock_post.call_count == 1

        # Second call with same text should hit cache
        res2 = summarizer.summarize(long_narrative, max_chars=60)
        assert res2 == res1
        assert mock_post.call_count == 1  # No additional network call


def test_groq_summarizer_retry_on_429():
    summarizer = GroqRemarksSummarizer(api_key="test_key")
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"retry-after": "0.01"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"content": "Resolved after backoff."}}]
    }

    with patch("httpx.Client.post", side_effect=[resp_429, resp_200]), \
         patch("time.sleep") as mock_sleep:
        res = summarizer.summarize("A long field remark that needs shortening by the model", max_chars=50)
        assert res == "Resolved after backoff."
        assert mock_sleep.called


def test_groq_summarizer_fallback_when_unavailable():
    # If API key is empty
    summarizer = GroqRemarksSummarizer(api_key="")
    assert summarizer.is_available is False
    res = summarizer.summarize("Long text", max_chars=20)
    assert res is None


def test_trim_and_format_with_groq_provider_success():
    long_remark = "ECA AUTO_S.P. MADRID_HOME_09/02/2026 Upon visit in the given address, House is closed, Talk to neighbor ms delia if she know the client and ms delia said that the resident in the address is confirm angeles but she cant confirm the name mauro"

    def mock_provider(text: str, max_chars: int = 140, is_retry_shorter: bool = False):
        return "Neighbor Ms. Delia confirmed resident is Angeles."

    result = trim_and_format(long_remark, llm_provider=mock_provider)
    assert result.fits_within_200 is True
    assert "ECA AUTO_S.P. MADRID_HOME_09/02/2026" in result.trimmed_text
    assert "Neighbor Ms. Delia confirmed resident is Angeles." in result.trimmed_text
    assert len(result.trimmed_text) <= 200


def test_trim_and_format_fallback_when_groq_fails():
    long_remark = "ECA AUTO_S.P. MADRID_HOME_09/02/2026 " + "A" * 250

    def failing_provider(text: str, max_chars: int = 140, is_retry_shorter: bool = False):
        return None  # simulates API failure

    # Must fall back gracefully to clause/word truncation without crashing
    result = trim_and_format(long_remark, llm_provider=failing_provider)
    assert result.fits_within_200 is True
    assert len(result.trimmed_text) <= 200
