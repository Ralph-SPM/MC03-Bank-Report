"""Capability-scoped production gates and operational configuration models.

A production gate is immutable evidence with a ``pending``, ``approved``, or
``rejected`` state. Configuration documents cannot self-approve gates: gate
evidence is owned outside the Campaign_Configuration and evaluated separately.

The GateEvaluator returns an explicit ``approved``, ``blocked``, ``deferred``,
or ``review_only`` decision per capability. Missing, incomplete, or rejected
evidence always fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from mc03.domain.configuration import GateState


class GateFamily(StrEnum):
    """Capability families that require production-gate evidence."""

    VIBER_FEASIBILITY = "viber_feasibility"
    LLM_PRIVACY = "llm_privacy"
    TFS_BUSINESS_RULES = "tfs_business_rules"
    RCBC_SOURCE_RULES = "rcbc_source_rules"
    CBS_RULES = "cbs_rules"
    ARTIFACTS_EXPORT = "artifacts_export"
    ARCHIVE = "archive"
    OPERATIONS_ACCESS = "operations_access"


class GateDecisionKind(StrEnum):
    """Fail-closed decision emitted by the GateEvaluator for a capability."""

    APPROVED = "approved"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    REVIEW_ONLY = "review_only"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """One immutable gate-evidence record owned outside configuration."""

    gate_id: str
    family: GateFamily
    scope: str
    state: GateState
    recorded_at: datetime
    owner: str | None = None
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.gate_id.strip():
            raise ValueError("gate_id must not be blank")
        if not self.scope.strip():
            raise ValueError("scope must not be blank")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))

    @property
    def is_approved(self) -> bool:
        """Return whether this evidence record is in the approved state."""
        return self.state is GateState.APPROVED


@dataclass(frozen=True, slots=True)
class GateRequirement:
    """One required gate for a capability, resolved against recorded evidence."""

    gate_id: str
    family: GateFamily
    scope: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Result of evaluating a capability's required gates.

    ``outstanding`` lists gate identifiers that are missing, pending, or
    rejected, so the DA portal can display outstanding or rejected evidence.
    """

    family: GateFamily
    capability: str
    kind: GateDecisionKind
    outstanding: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_approved(self) -> bool:
        """Return whether the capability is approved for production behavior."""
        return self.kind is GateDecisionKind.APPROVED

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible projection for DA display and audit."""
        return {
            "family": self.family.value,
            "capability": self.capability,
            "kind": self.kind.value,
            "outstanding": list(self.outstanding),
            "rejected": list(self.rejected),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class OperationalConfiguration:
    """Approved operational thresholds; values come only from approved snapshots.

    Busy handling, batch sizes, retry budgets, lease durations, heartbeat
    intervals, registration deadlines, and repeated-failure thresholds are
    never hard-coded defaults; they are explicit approved values.
    """

    version: str
    lock_lease_seconds: int
    heartbeat_interval_seconds: int
    heartbeat_stale_seconds: int
    listener_heartbeat_stale_seconds: int
    registration_validation_deadline_seconds: int
    repeated_failure_threshold: int
    provider_timeout_seconds: float
    provider_retry_budget: int
    batch_size: int

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("operational configuration version must not be blank")
        positive_int_fields = (
            ("lock_lease_seconds", self.lock_lease_seconds),
            ("heartbeat_interval_seconds", self.heartbeat_interval_seconds),
            ("heartbeat_stale_seconds", self.heartbeat_stale_seconds),
            ("listener_heartbeat_stale_seconds", self.listener_heartbeat_stale_seconds),
            (
                "registration_validation_deadline_seconds",
                self.registration_validation_deadline_seconds,
            ),
            ("repeated_failure_threshold", self.repeated_failure_threshold),
            ("provider_retry_budget", self.provider_retry_budget),
            ("batch_size", self.batch_size),
        )
        for name, value in positive_int_fields:
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class OperationalConfigurationSnapshot:
    """Immutable per-service/per-run selection of an operational configuration."""

    snapshot_id: str
    selected_by: str
    operational_configuration: OperationalConfiguration
    selected_at: datetime
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.selected_by.strip():
            raise ValueError("selected_by must not be blank")
        object.__setattr__(self, "selected_at", _utc(self.selected_at))


@dataclass(frozen=True, slots=True)
class GateEvidenceLedger:
    """Read-only view of recorded gate evidence for evaluation.

    The ledger holds only the effective (latest) state per ``gate_id``. It never
    derives approval from configuration; it simply reports recorded evidence.
    """

    _by_gate: Mapping[str, GateEvidence] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Sequence[GateEvidence]) -> GateEvidenceLedger:
        """Build a ledger keeping the latest evidence per gate identifier."""
        latest: dict[str, GateEvidence] = {}
        for record in records:
            current = latest.get(record.gate_id)
            if current is None or record.recorded_at >= current.recorded_at:
                latest[record.gate_id] = record
        return cls(_by_gate=latest)

    def state_of(self, gate_id: str) -> GateState | None:
        """Return the effective state of a gate, or ``None`` if no evidence exists."""
        evidence = self._by_gate.get(gate_id)
        return evidence.state if evidence is not None else None


__all__ = [
    "GateDecision",
    "GateDecisionKind",
    "GateEvidence",
    "GateEvidenceLedger",
    "GateFamily",
    "GateRequirement",
    "OperationalConfiguration",
    "OperationalConfigurationSnapshot",
]
