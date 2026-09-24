"""Engine package providing classifier and trimmer interfaces."""

from engine.router import (
    DETECTOR,
    HIGH_CONFIDENCE_TAGALOG_REGEX,
    LANGUAGES,
    TAGALOG_MARKERS_REGEX,
    batch_detect_languages,
    measure_batch_detection,
    route_single_confidence,
)
from mc03.services.remarks_lab.classifier import ClassifiedRelation, classify_contact
from mc03.services.remarks_lab.ranker import (
    RankedResult,
    classify_csu,
    classify_rfd,
    map_csu_status,
    rank_rfd_and_csu,
)
from mc03.services.remarks_lab.trimmer import (
    TrimmedRemark,
    deduplicate_phrases,
    extract_core_sentence,
    strip_preamble,
    trim_and_format,
)

__all__ = [
    "classify_contact",
    "ClassifiedRelation",
    "classify_csu",
    "classify_rfd",
    "rank_rfd_and_csu",
    "map_csu_status",
    "RankedResult",
    "trim_and_format",
    "strip_preamble",
    "deduplicate_phrases",
    "extract_core_sentence",
    "TrimmedRemark",
    "DETECTOR",
    "LANGUAGES",
    "TAGALOG_MARKERS_REGEX",
    "HIGH_CONFIDENCE_TAGALOG_REGEX",
    "route_single_confidence",
    "batch_detect_languages",
    "measure_batch_detection",
]
