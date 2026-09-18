"""Viber eligibility evaluation and deterministic Phase-1 RCBC pivot planning.

This service evaluates each required Viber condition separately and derives the
deterministic Phase-1 decision. It never treats registration validation as
proof of actual-group delivery and never manufactures missing Viber, commercial,
or metadata evidence: a condition is satisfied only when recorded evidence
confirms it.

When any required Viber condition fails, TFS live chat ingestion is deferred and
RCBC Auto Loan is selected with a Volare_Export plus Master_File design. The
RCBC source uses an authorized automated Volare adapter when available, or a
manual-upload fallback with recorded fallback evidence. RCBC never starts
chat-bot processing.
"""

from __future__ import annotations

from mc03.domain.phase1 import (
    REQUIRED_VIBER_CONDITIONS,
    Phase1Decision,
    RcbcPivotPlan,
    RcbcSourceMode,
    SelectedCampaign,
    ViberCondition,
    ViberEligibilityResult,
    ViberEvidence,
)


def evaluate_viber_eligibility(evidence: ViberEvidence) -> ViberEligibilityResult:
    """Evaluate every required Viber condition separately against recorded evidence.

    Returns the eligibility result with the exact list of failed conditions.
    Registration validation is one required condition among the others; it does
    not substitute for actual-group delivery or metadata completeness.
    """
    failed = tuple(
        condition
        for condition in REQUIRED_VIBER_CONDITIONS
        if not evidence.is_confirmed(condition)
    )
    return ViberEligibilityResult(eligible=not failed, failed_conditions=failed)


def plan_rcbc_pivot(
    *,
    automated_volare_available: bool,
    volare_adapter: str | None,
    manual_upload_fallback_recorded: bool,
) -> RcbcPivotPlan:
    """Build the RCBC Phase-1 source plan.

    Prefers an authorized automated Volare adapter when available; otherwise
    requires a recorded manual-upload fallback. Chat-bot processing is never
    enabled for RCBC Auto Loan.
    """
    if automated_volare_available and volare_adapter:
        return RcbcPivotPlan(
            source_mode=RcbcSourceMode.AUTOMATED_VOLARE,
            volare_adapter=volare_adapter,
            manual_upload_fallback_recorded=manual_upload_fallback_recorded,
        )
    if not manual_upload_fallback_recorded:
        raise ValueError(
            "RCBC pivot requires either an authorized automated Volare adapter "
            "or a recorded manual-upload fallback"
        )
    return RcbcPivotPlan(
        source_mode=RcbcSourceMode.MANUAL_UPLOAD_FALLBACK,
        volare_adapter=None,
        manual_upload_fallback_recorded=True,
    )


def decide_phase1(
    evidence: ViberEvidence,
    *,
    automated_volare_available: bool = False,
    volare_adapter: str | None = None,
    manual_upload_fallback_recorded: bool = False,
) -> Phase1Decision:
    """Return the deterministic Phase-1 decision from Viber eligibility.

    When TFS is eligible, TFS Auto is selected (still subject to its remaining
    gates) and no RCBC plan is produced. When any Viber condition fails, TFS is
    deferred and RCBC Auto Loan is selected with a safe source plan.
    """
    result = evaluate_viber_eligibility(evidence)
    if result.eligible:
        return Phase1Decision(
            selected_campaign=SelectedCampaign.TFS_AUTO,
            viber_result=result,
            rcbc_plan=None,
        )
    plan = plan_rcbc_pivot(
        automated_volare_available=automated_volare_available,
        volare_adapter=volare_adapter,
        manual_upload_fallback_recorded=manual_upload_fallback_recorded,
    )
    return Phase1Decision(
        selected_campaign=SelectedCampaign.RCBC_AUTO_LOAN,
        viber_result=result,
        rcbc_plan=plan,
    )


def registration_alone_proves_delivery() -> bool:
    """A registration-validation callback never proves actual-group delivery.

    Provided as an explicit, self-documenting constant used by callers and tests
    to assert the separation between registration validation and delivery proof.
    """
    return False


__all__ = [
    "ViberCondition",
    "decide_phase1",
    "evaluate_viber_eligibility",
    "plan_rcbc_pivot",
    "registration_alone_proves_delivery",
]
