"""Language router engine using Lingua Rust parallel detection for English and Tagalog routing."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

# Restrict scope strictly to English and Tagalog for speed, <20MB RAM, and high precision
LANGUAGES = [Language.ENGLISH, Language.TAGALOG]

# Global singleton warm in RAM with low-accuracy mode (2x-3x speedup, 99%+ accuracy)
DETECTOR: LanguageDetector = (
    LanguageDetectorBuilder.from_languages(*LANGUAGES).with_low_accuracy_mode().build()
)

# Filipino grammatical particles & common loanwords to catch subtle Taglish
TAGALOG_MARKERS_REGEX = re.compile(
    r"\b("
    r"daw|raw|po|opo|nga|naman|kasi|pala|hindi|di|babalik|nakatira|umalis|lipat|"
    r"asawa|anak|kapatid|nanay|tatay|kuya|ate|bahay|meron|wala|ayaw|dun|dito|diyan|"
    r"kay|kayo|nila|namin|natin|sabi|punta|tira|alis|bayad|pera|pumunta|nagpunta|"
    r"kausap|nakausap|tumawag|tinawagan|nagbayad|paki|pakisabi|magbayad|magbabayad|"
    r"pa|lang|ba|na|sa|mga"
    r")\b",
    re.IGNORECASE,
)

# High-confidence particles that unambiguously indicate Filipino/Taglish narrative
HIGH_CONFIDENCE_TAGALOG_REGEX = re.compile(
    r"\b(daw|raw|po|opo|naman|kasi|pala|hindi|babalik|nakatira|umalis|lipat|asawa|anak|kapatid|nanay|tatay)\b",
    re.IGNORECASE,
)


def route_single_confidence(text: str) -> str:
    """Evaluates confidence values for short Taglish text."""
    if not text or not text.strip():
        return "ENGLISH"

    # Fast regex tripwire for high-frequency Taglish particles
    if HIGH_CONFIDENCE_TAGALOG_REGEX.search(text):
        return "TAGALOG"

    try:
        confidences = DETECTOR.compute_language_confidence_values(text)
        for res in confidences:
            if res.language == Language.TAGALOG and res.value >= 0.15:
                return "TAGALOG"
    except Exception:
        pass

    # Secondary check with broader regex if Lingua is uncertain
    if TAGALOG_MARKERS_REGEX.search(text):
        return "TAGALOG"

    return "ENGLISH"


def batch_detect_languages(texts: list[str]) -> list[str]:
    """
    Parallel batch language classification using Lingua's Rust parallel engine
    with a multi-threaded confidence check for subtle Taglish.
    Returns a list of strings: "TAGALOG" or "ENGLISH" matching the input length.
    """
    if not texts:
        return []

    # Clean & normalize texts for detector (Lingua handles non-empty strings)
    sanitized_texts = [t.strip() if t and t.strip() else " " for t in texts]

    # Fast-path: Native Rust parallel detection across all CPU cores (releases GIL)
    try:
        raw_results = DETECTOR.detect_languages_in_parallel_of(sanitized_texts)
    except Exception:
        raw_results = [DETECTOR.detect_language_of(t) for t in sanitized_texts]

    routed_results: list[str] = []
    ambiguous_indices: list[int] = []

    for idx, (lang, text) in enumerate(zip(raw_results, texts, strict=False)):
        orig_text = text or ""
        # 1. Definite Tagalog markers
        if HIGH_CONFIDENCE_TAGALOG_REGEX.search(orig_text):
            routed_results.append("TAGALOG")
        elif lang == Language.TAGALOG:
            routed_results.append("TAGALOG")
        elif lang == Language.ENGLISH:
            # If detected English but has secondary Tagalog markers, check confidence
            if TAGALOG_MARKERS_REGEX.search(orig_text):
                routed_results.append("CHECK")
                ambiguous_indices.append(idx)
            else:
                routed_results.append("ENGLISH")
        else:
            # Undetected / None
            routed_results.append("CHECK")
            ambiguous_indices.append(idx)

    # Resolve ambiguous cases in parallel
    if ambiguous_indices:
        with ThreadPoolExecutor() as executor:
            resolved = list(
                executor.map(route_single_confidence, [texts[i] for i in ambiguous_indices])
            )
        for i, res in zip(ambiguous_indices, resolved, strict=False):
            routed_results[i] = res

    return routed_results


def measure_batch_detection(texts: list[str]) -> tuple[list[str], float]:
    """
    Runs batch_detect_languages and measures elapsed time in milliseconds.
    Returns (languages, elapsed_ms).
    """
    t0 = time.perf_counter()
    languages = batch_detect_languages(texts)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return languages, elapsed_ms


__all__ = [
    "DETECTOR",
    "LANGUAGES",
    "TAGALOG_MARKERS_REGEX",
    "HIGH_CONFIDENCE_TAGALOG_REGEX",
    "route_single_confidence",
    "batch_detect_languages",
    "measure_batch_detection",
]
