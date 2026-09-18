"""Capability-scoped GateEvaluator.

Resolves the required production gates for a capability against recorded gate
evidence and returns an explicit fail-closed decision. Configuration cannot
self-approve: the evaluator reads only externally owned ``GateEvidence`` and
never treats a Campaign_Configuration reference as approval.

Fail-closed mapping by gate family (from the approved design):

- Viber feasibility  -> deferred (TFS live chat deferred; Phase 1 pivots to RCBC)
- LLM privacy        -> review_only (LLM-assisted production extraction blocked)
- TFS business rules -> review_only (affected fields remain null/Review_Only)
- RCBC source/rules  -> review_only (affected automation/export default blocked)
- CBS rules          -> review_only (CBS affected outcomes remain Review_Only)
- Artifacts/export   -> blocked (affected artifact or Full_Export blocked)
- Archive            -> blocked (production-default archive blocked; XLSX kept)
- Operations/access  -> blocked (production release/unauth portal blocked)
"""

from __future__ import annotations

from collections.abc import Sequence

from mc03.domain.configuration import GateState
from mc03.domain.gates import (
    GateDecision,
    GateDecisionKind,
    GateEvidenceLedger,
    GateFamily,
    GateRequirement,
)

# Fail-closed decision kind applied when a family's required gates are not all
# approved. Every mapping is intentionally conservative.
_FAIL_CLOSED_KIND: dict[GateFamily, GateDecisionKind] = {
    GateFamily.VIBER_FEASIBILITY: GateDecisionKind.DEFERRED,
    GateFamily.LLM_PRIVACY: GateDecisionKind.REVIEW_ONLY,
    GateFamily.TFS_BUSINESS_RULES: GateDecisionKind.REVIEW_ONLY,
    GateFamily.RCBC_SOURCE_RULES: GateDecisionKind.REVIEW_ONLY,
    GateFamily.CBS_RULES: GateDecisionKind.REVIEW_ONLY,
    GateFamily.ARTIFACTS_EXPORT: GateDecisionKind.BLOCKED,
    GateFamily.ARCHIVE: GateDecisionKind.BLOCKED,
    GateFamily.OPERATIONS_ACCESS: GateDecisionKind.BLOCKED,
}


class GateEvaluator:
    """Resolve capability gate decisions against recorded evidence."""

    def __init__(self, ledger: GateEvidenceLedger) -> None:
        self._ledger = ledger

    def evaluate(
        self,
        *,
        family: GateFamily,
        capability: str,
        requirements: Sequence[GateRequirement],
    ) -> GateDecision:
        """Return the fail-closed decision for one capability.

        A capability with no required gates is treated as blocked/deferred by
        its family rather than implicitly approved, so an empty requirement set
        never grants production behavior by omission.
        """
        outstanding: list[str] = []
        rejected: list[str] = []
        for requirement in requirements:
            state = self._ledger.state_of(requirement.gate_id)
            if state is GateState.APPROVED:
                continue
            if state is GateState.REJECTED:
                rejected.append(requirement.gate_id)
            else:
                # Missing (None) or pending evidence is outstanding.
                outstanding.append(requirement.gate_id)

        fail_kind = _FAIL_CLOSED_KIND[family]

        if requirements and not outstanding and not rejected:
            return GateDecision(
                family=family,
                capability=capability,
                kind=GateDecisionKind.APPROVED,
                reason="All required gates are approved.",
            )

        reason = self._explain(family, requirements, outstanding, rejected)
        return GateDecision(
            family=family,
            capability=capability,
            kind=fail_kind,
            outstanding=tuple(outstanding),
            rejected=tuple(rejected),
            reason=reason,
        )

    @staticmethod
    def _explain(
        family: GateFamily,
        requirements: Sequence[GateRequirement],
        outstanding: Sequence[str],
        rejected: Sequence[str],
    ) -> str:
        if not requirements:
            return f"No approved gates recorded for {family.value}; capability withheld."
        parts: list[str] = []
        if outstanding:
            parts.append(f"outstanding: {', '.join(outstanding)}")
        if rejected:
            parts.append(f"rejected: {', '.join(rejected)}")
        return f"{family.value} not approved ({'; '.join(parts)})."


__all__ = ["GateEvaluator"]
