"""Viber eligibility conditions and the deterministic Phase-1 pivot plan.

The Viber_Eligibility_Gate evaluates each required Viber condition separately
against recorded evidence. A Viber_Registration_Validation_Callback proves only
route availability; it is never treated as proof of actual TFS group-message
delivery or of complete Raw_Payload_Metadata.

If any required Viber condition is pending, absent, rejected, or unsuccessful,
Phase 1 deterministically pivots to RCBC Auto Loan using Volare_Export plus an
approved Master_File resolution design. RCBC never starts chat-bot processing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

TFS_CAMPAIGN_CODE = "TFS_AUTO"
RCBC_CAMPAIGN_CODE = "RCBC_AUTO_LOAN"


class ViberCondition(StrEnum):
    """Independent conditions required before enabling TFS live chat ingestion.

    Each condition is evaluated separately so a registration callback cannot be
    mistaken for delivery proof, and so missing metadata is never manufactured.
    """

    OFFICIAL_PROVISIONING = "official_provisioning"
    COMMERCIAL_VERIFICATION = "commercial_verification"
    GROUP_PERMISSION = "group_permission"
    ACTUAL_GROUP_DELIVERY = "actual_group_delivery"
    TRUSTED_TLS_ENDPOINT = "trusted_tls_endpoint"
    PERSISTENT_ENDPOINT = "persistent_endpoint"
    REGISTRATION_VALIDATION = "registration_validation"
    METADATA_COMPLETENESS = "metadata_completeness"


# All conditions are required. Registration validation is included as a required
# condition but is explicitly insufficient on its own to prove delivery.
REQUIRED_VIBER_CONDITIONS: tuple[ViberCondition, ...] = tuple(ViberCondition)


class SelectedCampaign(StrEnum):
    """Deterministic Phase-1 campaign selection outcome."""

    TFS_AUTO = TFS_CAMPAIGN_CODE
    RCBC_AUTO_LOAN = RCBC_CAMPAIGN_CODE


class RcbcSourceMode(StrEnum):
    """How the RCBC Volare_Export source is acquired for the pivot."""

    AUTOMATED_VOLARE = "automated_volare"
    MANUAL_UPLOAD_FALLBACK = "manual_upload_fallback"


@dataclass(frozen=True, slots=True)
class ViberEligibilityResult:
    """Result of evaluating the Viber_Eligibility_Gate.

    ``failed_conditions`` preserves the exact recorded evidence gaps so the
    failed gate evidence can be recorded and displayed; nothing is inferred or
    manufactured to fill a missing condition.
    """

    eligible: bool
    failed_conditions: tuple[ViberCondition, ...]

    @property
    def tfs_deferred(self) -> bool:
        """Return whether TFS live chat ingestion must be deferred."""
        return not self.eligible


@dataclass(frozen=True, slots=True)
class RcbcPivotPlan:
    """RCBC Auto Loan Phase-1 design: Volare_Export + Master_File resolution."""

    source_mode: RcbcSourceMode
    volare_adapter: str | None
    manual_upload_fallback_recorded: bool
    uses_master_file_resolution: bool = True
    chat_bot_processing_enabled: bool = False

    def __post_init__(self) -> None:
        if self.source_mode is RcbcSourceMode.AUTOMATED_VOLARE and not self.volare_adapter:
            raise ValueError("automated Volare source mode requires a configured adapter")
        if (
            self.source_mode is RcbcSourceMode.MANUAL_UPLOAD_FALLBACK
            and not self.manual_upload_fallback_recorded
        ):
            raise ValueError("manual upload fallback requires recorded fallback evidence")
        if self.chat_bot_processing_enabled:
            raise ValueError("RCBC Auto Loan must never enable chat-bot processing")


@dataclass(frozen=True, slots=True)
class Phase1Decision:
    """Deterministic Phase-1 decision derived from Viber eligibility.

    When TFS is eligible, the TFS branch is selected but remains subject to its
    still-applicable LLM, business-rule, template, export, and operational
    gates. When TFS is deferred, RCBC Auto Loan is selected with a safe source
    plan and chat-bot processing is prohibited.
    """

    selected_campaign: SelectedCampaign
    viber_result: ViberEligibilityResult
    rcbc_plan: RcbcPivotPlan | None

    @property
    def tfs_deferred(self) -> bool:
        """Return whether TFS live chat ingestion is deferred for Phase 1."""
        return self.viber_result.tfs_deferred

    def as_dict(self) -> dict[str, object]:
        """Return a safe JSON-compatible projection for audit and DA display."""
        projection: dict[str, object] = {
            "selected_campaign": self.selected_campaign.value,
            "tfs_deferred": self.tfs_deferred,
            "failed_viber_conditions": [c.value for c in self.viber_result.failed_conditions],
        }
        if self.rcbc_plan is not None:
            projection["rcbc_source_mode"] = self.rcbc_plan.source_mode.value
            projection["rcbc_manual_fallback_recorded"] = (
                self.rcbc_plan.manual_upload_fallback_recorded
            )
            projection["rcbc_chat_bot_enabled"] = self.rcbc_plan.chat_bot_processing_enabled
        return projection


@dataclass(frozen=True, slots=True)
class ViberEvidence:
    """Recorded per-condition Viber evidence, keyed by condition.

    Each value is ``True`` only when recorded evidence confirms the condition.
    Absent conditions default to ``False`` (fail-closed); missing evidence is
    never manufactured into a positive result.
    """

    confirmed: Mapping[ViberCondition, bool]

    def is_confirmed(self, condition: ViberCondition) -> bool:
        """Return whether recorded evidence confirms one condition."""
        return bool(self.confirmed.get(condition, False))

    @classmethod
    def from_confirmed(cls, confirmed: Sequence[ViberCondition]) -> ViberEvidence:
        """Build evidence from the set of conditions with positive evidence."""
        return cls(confirmed={condition: True for condition in confirmed})


__all__ = [
    "RCBC_CAMPAIGN_CODE",
    "REQUIRED_VIBER_CONDITIONS",
    "TFS_CAMPAIGN_CODE",
    "Phase1Decision",
    "RcbcPivotPlan",
    "RcbcSourceMode",
    "SelectedCampaign",
    "ViberCondition",
    "ViberEligibilityResult",
    "ViberEvidence",
]
