"""Sanitize RCBC remarks by removing prohibited internal markers."""

from __future__ import annotations

import re

# Marker patterns are deliberately bounded so ordinary words containing a marker
# substring (for example, ``phone`` or ``source``) remain untouched.
_BCAL_CH_PATTERN = re.compile(
    r"(?<!\w)BCAL[\s._:/-]*CH(?:[\s._:/-]*[A-Z0-9][A-Z0-9._/-]*)?",
    re.IGNORECASE,
)
_L3_PATTERN = re.compile(r"(?<!\w)L3(?!\w)(?:\s*[:=/-])?", re.IGNORECASE)
_INB_PATTERN = re.compile(r"(?<!\w)INB(?!\w)(?:\s*[:=/-])?", re.IGNORECASE)
_OBD_PATTERN = re.compile(r"(?<!\w)OBD(?!\w)(?:\s*[:=/-])?", re.IGNORECASE)
_PH_PATTERN = re.compile(
    r"(?<!\w)PH(?:\s*[:=#-]?\s*(?:\+?\d[\d\s()./-]*|[A-Z0-9][A-Z0-9._/-]*))?(?!\w)",
    re.IGNORECASE,
)
_DOT_COM_LINK_PATTERN = re.compile(
    r"(?<![\w.-])"
    r"(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+com"
    r"(?:[/?#][^\s]*)?"
    r"[.,!?;:)]*"
    r"(?![\w.-])",
    re.IGNORECASE,
)
_SRC_TAG_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"\[\s*/?\s*SRC(?:\s*[:=-]\s*[^\]]*)?\s*\]"
    r"|<\s*/?\s*SRC(?:\s*[:=-]\s*[^>]*)?\s*>"
    r"|SRC(?:\s*[:=-]\s*[^\s|,;]+)?"
    r")(?!\w)",
    re.IGNORECASE,
)

_PROHIBITED_PATTERNS: tuple[re.Pattern[str], ...] = (
    _BCAL_CH_PATTERN,
    _L3_PATTERN,
    _INB_PATTERN,
    _OBD_PATTERN,
    _PH_PATTERN,
    _DOT_COM_LINK_PATTERN,
    _SRC_TAG_PATTERN,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
# If marker-only text includes wrappers or separators, those wrappers are not
# meaningful remark content and should not survive as an apparent remark.
_ORPHAN_SEPARATOR_PATTERN = re.compile(r"(?<!\S)[\[\]()<>{}:;|,=/\\-]+(?=\s|$)")


def sanitize_remark(text: str) -> str:
    """Remove prohibited RCBC markers from a remark.

    BCAL-prefixed CH codes, L3 labels, INB/OBD direction markers, PH codes,
    ``.com`` links, and SRC tags are replaced with whitespace. Whitespace is
    collapsed only when a marker was removed, so an otherwise clean remark is
    preserved character-for-character. Applying this function repeatedly is
    therefore idempotent.

    Args:
        text: The source remark text.

    Returns:
        The sanitized remark, or an empty string when no content remains.
    """
    if not text or not text.strip():
        return ""

    if not any(pattern.search(text) for pattern in _PROHIBITED_PATTERNS):
        return text

    sanitized = text
    for pattern in _PROHIBITED_PATTERNS:
        sanitized = pattern.sub(" ", sanitized)

    sanitized = _ORPHAN_SEPARATOR_PATTERN.sub(" ", sanitized)
    return _WHITESPACE_PATTERN.sub(" ", sanitized).strip()
