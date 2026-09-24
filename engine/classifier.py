"""RCBC Remark Lab classifier engine for CSU, RFD, and contact relationships."""

from __future__ import annotations

from mc03.services.remarks_lab.classifier import (
    LAB_CARDHOLDER_KEYWORDS,
    LAB_INFORMANT_KEYWORDS,
    LAB_REPRESENTATIVE_KEYWORDS,
    ClassifiedRelation,
    classify_contact,
)
from mc03.services.remarks_lab.ranker import (
    CSU_CLIENT_POSITIVE,
    CSU_NO_CONTACT,
    CSU_PTP,
    CSU_REPO,
    CSU_RESOLVED,
    RFD_BORROWER_REFUSED,
    RFD_MOVED_OUT,
    RFD_NO_REACH,
    RFD_REPRESENTATIVE_REFUSED,
    RankedResult,
    classify_csu,
    classify_rfd,
    map_csu_status,
    rank_rfd_and_csu,
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
