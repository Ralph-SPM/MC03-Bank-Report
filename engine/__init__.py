"""Engine package providing classifier and trimmer interfaces."""

from mc03.services.remarks_lab.classifier import classify_contact, ClassifiedRelation
from mc03.services.remarks_lab.ranker import classify_csu, classify_rfd, rank_rfd_and_csu, map_csu_status, RankedResult
from mc03.services.remarks_lab.trimmer import trim_and_format, strip_preamble, deduplicate_phrases, extract_core_sentence, TrimmedRemark

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
]
