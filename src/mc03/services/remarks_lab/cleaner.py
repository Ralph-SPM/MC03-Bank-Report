"""Tag sanitizer and cleaner for remarks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple
from mc03.domain.sanitizer import sanitize_remark


@dataclass
class CleanedRemark:
    original: str
    cleaned: str
    detected_prohibited_tags: List[str]
    is_modified: bool


# Prohibited pattern definitions to track what got stripped
PROHIBITED_PATTERNS = [
    (r"\bBCAL\b", "BCAL"),
    (r"\bBKAL\b", "BKAL"),
    (r"\bL3\b", "L3"),
    (r"\bINB\b", "INB"),
    (r"\bOBD\b", "OBD"),
    (r"\bSRC\b", "SRC"),
    (r"(?:\+?63|0)9\d{9}\b", "PHONE"),
    (r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b", "PHONE"),
    (r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "EMAIL"),
    (r"\b\S+\.com\S*\b", "URL"),
    (r"\bviber\b", "VIBER"),
    (r"\bwhatsapp\b", "WHATSAPP"),
    (r"\bfacebook\b|\bfb\b", "FACEBOOK"),
]


def clean_remark(text: str) -> CleanedRemark:
    """
    Sanitize remark text using domain sanitizer and track which prohibited tags were removed.
    Also handles subtle phone/Viber/Email/URL/BKAL stripping.
    """
    text_str = str(text).strip() if text is not None else ""
    if not text_str:
        return CleanedRemark(original="", cleaned="", detected_prohibited_tags=[], is_modified=False)

    original = text_str
    detected_tags = []

    # Check for presence of prohibited elements
    for pattern, tag_name in PROHIBITED_PATTERNS:
        if re.search(pattern, original, re.IGNORECASE):
            if tag_name not in detected_tags:
                detected_tags.append(tag_name)

    # Use standard domain sanitizer first
    cleaned = sanitize_remark(original)

    # Additional cleanup for standalone BCAL/BKAL tags, SRC, URLs, phone numbers, and chat handles
    cleaned = re.sub(r"(?<!\w)(?:BCAL|BKAL|SRC)(?!\w)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\S+\.com\S*\b", "", cleaned, flags=re.IGNORECASE)
    phone_pat = r"(?:\+?63|0)9\d{9}\b|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"
    cleaned = re.sub(phone_pat, "", cleaned)
    cleaned = re.sub(r"\b(viber|whatsapp|telegram)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    is_modified = (original != cleaned)

    return CleanedRemark(
        original=original,
        cleaned=cleaned,
        detected_prohibited_tags=detected_tags,
        is_modified=is_modified
    )
