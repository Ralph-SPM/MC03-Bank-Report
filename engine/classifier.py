"""RCBC Remark Lab classifier engine for CSU, RFD, and contact relationships."""

from __future__ import annotations

from mc03.services.remarks_lab.classifier import (
    classify_contact,
    ClassifiedRelation,
    LAB_CARDHOLDER_KEYWORDS,
    LAB_REPRESENTATIVE_KEYWORDS,
    LAB_INFORMANT_KEYWORDS,
)
from mc03.services.remarks_lab.ranker import (
    classify_csu,
    classify_rfd,
    rank_rfd_and_csu,
    map_csu_status,
    RankedResult,
    CSU_REPO,
    CSU_PTP,
    CSU_RESOLVED,
    CSU_NO_CONTACT,
    CSU_CLIENT_POSITIVE,
    RFD_BORROWER_REFUSED,
    RFD_REPRESENTATIVE_REFUSED,
    RFD_NO_REACH,
    RFD_MOVED_OUT,
)

__all__ = [
    "classify_contact",
    "ClassifiedRelation",
    "classify_csu",
    "classify_rfd",
    "rank_rfd_and_csu",
    "map_csu_status",
    "RankedResult",
    "CSU_REPO",
    "CSU_PTP",
    "CSU_RESOLVED",
    "CSU_NO_CONTACT",
    "CSU_CLIENT_POSITIVE",
    "RFD_BORROWER_REFUSED",
    "RFD_REPRESENTATIVE_REFUSED",
    "RFD_NO_REACH",
    "RFD_MOVED_OUT",
    "LAB_CARDHOLDER_KEYWORDS",
    "LAB_REPRESENTATIVE_KEYWORDS",
    "LAB_INFORMANT_KEYWORDS",
]
