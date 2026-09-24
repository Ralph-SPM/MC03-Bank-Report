"""RCBC Remark Lab trimmer engine for preamble stripping, deduplication, and 200-char formatting."""

from __future__ import annotations

from mc03.services.remarks_lab.trimmer import (
    PREAMBLE_PATTERNS,
    TrimmedRemark,
    deduplicate_phrases,
    fallback_clause_truncate,
    llm_trim_remark,
    strip_preamble,
    trim_and_format,
)

# Backward-compatible alias
extract_core_sentence = fallback_clause_truncate

__all__ = [
    "trim_and_format",
    "strip_preamble",
    "deduplicate_phrases",
    "fallback_clause_truncate",
    "extract_core_sentence",
    "llm_trim_remark",
    "TrimmedRemark",
    "PREAMBLE_PATTERNS",
]
