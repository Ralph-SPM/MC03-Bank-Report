"""Immutable domain value objects used by persistence and later services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum, StrEnum
from hashlib import sha256
from uuid import UUID

from mc03.domain.audit import JsonValue, SafeAuditPayload, build_safe_audit_payload
from mc03.domain.identifiers import (
    ArtifactId,
    AuditEventId,
    ConfigHash,
    CorrelationId,
    OutcomeId,
    RunId,
    SnapshotId,
    SourceId,
    new_artifact_id,
    new_audit_event_id,
    new_outcome_id,
    new_snapshot_id,
)


class RunState(StrEnum):
    """Persisted report-run projection states."""

    REQUESTED = "REQUESTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    READY_FOR_EXPORT = "READY_FOR_EXPORT"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    BLOCKED = "BLOCKED"


class OutcomeDisposition(StrEnum):
    """Safe terminal or review disposition for one accepted source row."""

    INCLUDED = "included"
    EXCLUDED = "excluded"
    REVIEW_ONLY = "review_only"
    FAILED = "failed"


class ArtifactType(StrEnum):
    """Artifact classes tracked independently for audit and hashing."""

    BANK_XLSX = "bank_xlsx"
    INTERNAL_XLSX = "internal_xlsx"
    ARCHIVE = "archive"


class ArtifactStatus(StrEnum):
    """Artifact lifecycle projection."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _hash(value: str, field_name: str) -> str:
    normalized = _required(value, field_name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 hexadecimal digest")
    return normalized


@dataclass(frozen=True, slots=True)
class RawSourceRecord:
    """Immutable original source evidence retained before transformation."""

    source_id: SourceId
    source_type: str
    acquired_at: datetime
    source_hash: str
    source_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    content: bytes | None = None
    content_path: str | None = None
    source_token_hash: str | None = None

    def __post_init__(self) -> None:
        _required(self.source_type, "source_type")
        object.__setattr__(self, "acquired_at", _utc(self.acquired_at))
        object.__setattr__(self, "source_hash", _hash(self.source_hash, "source_hash"))
        if self.content is None and self.content_path is None:
            raise ValueError("source content or content_path is required")
        if self.content is not None and sha256(self.content).hexdigest() != self.source_hash:
            raise ValueError("source_hash must match source content")


@dataclass(frozen=True, slots=True)
class ReportRun:
    """Immutable request fact plus its current state projection."""

    run_id: RunId
    campaign_code: str
    config_version: str
    config_hash: ConfigHash
    requested_at: datetime
    reviewer_name: str
    state: RunState = RunState.REQUESTED
    correlation_id: CorrelationId | None = None
    predecessor_run_id: RunId | None = None
    report_window_start: datetime | None = None
    report_window_end: datetime | None = None

    def __post_init__(self) -> None:
        _required(self.campaign_code, "campaign_code")
        _required(self.config_version, "config_version")
        _required(self.reviewer_name, "reviewer_name")
        object.__setattr__(self, "requested_at", _utc(self.requested_at))
        object.__setattr__(
            self,
            "config_hash",
            ConfigHash(_hash(self.config_hash, "config_hash")),
        )
        if self.report_window_start is not None:
            object.__setattr__(self, "report_window_start", _utc(self.report_window_start))
        if self.report_window_end is not None:
            object.__setattr__(self, "report_window_end", _utc(self.report_window_end))
        if (
            self.report_window_start is not None
            and self.report_window_end is not None
            and self.report_window_start >= self.report_window_end
        ):
            raise ValueError("report window start must precede report window end")


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Exact per-run canonical configuration evidence."""

    snapshot_id: SnapshotId
    run_id: RunId
    campaign_code: str
    config_version: str
    canonical_bytes: bytes
    config_hash: ConfigHash
    selected_at: datetime

    def __post_init__(self) -> None:
        _required(self.campaign_code, "campaign_code")
        _required(self.config_version, "config_version")
        if not self.canonical_bytes:
            raise ValueError("canonical_bytes must not be empty")
        object.__setattr__(self, "selected_at", _utc(self.selected_at))
        object.__setattr__(
            self,
            "config_hash",
            ConfigHash(_hash(self.config_hash, "config_hash")),
        )
        expected_hash = sha256(self.canonical_bytes).hexdigest()
        if expected_hash != self.config_hash:
            raise ValueError("config_hash must match canonical_bytes")


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    """Append-only processing outcome linked to raw source and configuration."""

    outcome_id: OutcomeId
    source_id: SourceId
    run_id: RunId
    config_hash: ConfigHash
    disposition: OutcomeDisposition
    created_at: datetime
    values: Mapping[str, JsonValue] = field(default_factory=dict)
    predecessor_outcome_id: OutcomeId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(
            self,
            "config_hash",
            ConfigHash(_hash(self.config_hash, "config_hash")),
        )
        build_safe_audit_payload(self.values)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Artifact evidence with a one-way transition to completed."""

    artifact_id: ArtifactId
    run_id: RunId
    artifact_type: ArtifactType
    status: ArtifactStatus
    config_hash: ConfigHash
    artifact_hash: str | None = None
    path: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "config_hash",
            ConfigHash(_hash(self.config_hash, "config_hash")),
        )
        if self.artifact_hash is not None:
            object.__setattr__(self, "artifact_hash", _hash(self.artifact_hash, "artifact_hash"))
        if self.status is ArtifactStatus.COMPLETED and self.artifact_hash is None:
            raise ValueError("completed artifacts require artifact_hash")
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _utc(self.completed_at))
        build_safe_audit_payload(self.metadata)


@dataclass(frozen=True, slots=True)
class RunStateEvent:
    """Append-only state transition event for a report run."""

    event_id: AuditEventId
    run_id: RunId
    state: RunState
    occurred_at: datetime
    actor: str
    service: str
    correlation_id: CorrelationId | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _required(self.actor, "actor")
        _required(self.service, "service")
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        if self.reason is not None:
            _required(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Append-only cross-entity lineage event with a safe payload."""

    event_id: AuditEventId
    event_type: str
    occurred_at: datetime
    actor: str
    service: str
    correlation_id: CorrelationId
    payload: SafeAuditPayload
    entity_type: str | None = None
    entity_id: UUID | str | None = None
    source_id: SourceId | None = None
    run_id: RunId | None = None
    config_hash: ConfigHash | None = None

    def __post_init__(self) -> None:
        _required(self.event_type, "event_type")
        _required(self.actor, "actor")
        _required(self.service, "service")
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        object.__setattr__(self, "payload", build_safe_audit_payload(self.payload))
        if self.config_hash is not None:
            object.__setattr__(
                self,
                "config_hash",
                ConfigHash(_hash(self.config_hash, "config_hash")),
            )


def new_artifact_record(
    *,
    run_id: RunId,
    artifact_type: ArtifactType,
    status: ArtifactStatus,
    config_hash: ConfigHash,
    artifact_hash: str | None = None,
    path: str | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ArtifactRecord:
    """Create an artifact value object with a generated identifier."""
    return ArtifactRecord(
        artifact_id=new_artifact_id(),
        run_id=run_id,
        artifact_type=artifact_type,
        status=status,
        config_hash=config_hash,
        artifact_hash=artifact_hash,
        path=path,
        created_at=created_at,
        completed_at=completed_at,
        metadata=metadata or {},
    )


def new_audit_event(
    *,
    event_type: str,
    occurred_at: datetime,
    actor: str,
    service: str,
    correlation_id: CorrelationId,
    payload: SafeAuditPayload,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    source_id: SourceId | None = None,
    run_id: RunId | None = None,
    config_hash: ConfigHash | None = None,
) -> AuditEvent:
    """Create an audit event value object with a generated identifier."""
    return AuditEvent(
        event_id=new_audit_event_id(),
        event_type=event_type,
        occurred_at=occurred_at,
        actor=actor,
        service=service,
        correlation_id=correlation_id,
        payload=payload,
        entity_type=entity_type,
        entity_id=entity_id,
        source_id=source_id,
        run_id=run_id,
        config_hash=config_hash,
    )


def new_outcome(
    *,
    source_id: SourceId,
    run_id: RunId,
    config_hash: ConfigHash,
    disposition: OutcomeDisposition,
    created_at: datetime,
    values: Mapping[str, JsonValue] | None = None,
    predecessor_outcome_id: OutcomeId | None = None,
) -> ProcessingOutcome:
    """Create a processing outcome value object with a generated identifier."""
    return ProcessingOutcome(
        outcome_id=new_outcome_id(),
        source_id=source_id,
        run_id=run_id,
        config_hash=config_hash,
        disposition=disposition,
        created_at=created_at,
        values=values or {},
        predecessor_outcome_id=predecessor_outcome_id,
    )


def new_snapshot(
    *,
    run_id: RunId,
    campaign_code: str,
    config_version: str,
    canonical_bytes: bytes,
    config_hash: ConfigHash,
    selected_at: datetime,
) -> ConfigurationSnapshot:
    """Create a selected configuration snapshot with a generated identifier."""
    return ConfigurationSnapshot(
        snapshot_id=new_snapshot_id(),
        run_id=run_id,
        campaign_code=campaign_code,
        config_version=config_version,
        canonical_bytes=canonical_bytes,
        config_hash=config_hash,
        selected_at=selected_at,
    )


# ---------------------------------------------------------------------------
# RCBC Initial Demo (on-demand vertical slice) domain types
#
# These value objects support the de-scoped, in-memory demo pipeline defined in
# ``.kiro/specs/rcbc-initial-demo/design.md``. They are intentionally distinct
# from the parent-spec persistence value objects above: the demo has no durable
# store, no reviewer identity, and defaults run attribution to ``"DA"``.
# ---------------------------------------------------------------------------


class Disposition(str, Enum):
    """Export disposition for a processed demo row."""

    CLEAN = "clean"  # auto-approved, compliant
    REVIEW_ONLY = "review"  # actionable exception, needs DA decision
    EXCLUDED = "excluded"  # dropped as noise or DA exclusion


class ExceptionKind(str, Enum):
    """Why a demo row needs DA review.

    Members cover both the actionable exceptions raised by the hierarchy engine
    (remark overflow, unmapped/ambiguous accounts, ambiguous informant) and the
    ingestion/extraction safe-incompleteness cases that route rows to
    ``Review_Only`` rather than dropping them.
    """

    REMARK_TOO_LONG = "remark_too_long"
    UNMAPPED_ACCOUNT = "unmapped_account"
    AMBIGUOUS_INFORMANT = "ambiguous_informant"
    AMBIGUOUS_ACCOUNT = "ambiguous_account"
    UNPARSEABLE_CALL_DATE = "unparseable_call_date"
    MISSING_RANK = "missing_rank"
    EXTRACTION_FAILED = "extraction_failed"
    MISSING_REMARK_CONTENT = "missing_remark_content"
    INVALID_CSU = "invalid_csu"


class ExclusionKind(str, Enum):
    """Why a row may receive the demo's ``Excluded_Row`` disposition."""

    NOISE = "noise"
    DA_EXCLUSION = "da_exclusion"


class Relation(str, Enum):
    """Classification of the contacted person."""

    REPRESENTATIVE = "representative"  # blood relative / immediate family
    INFORMANT = "informant"  # neighbor, guard, barangay, colleague
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RemarkFields:
    """Structured remark parsed from pipe tags or LLM extraction."""

    type_of_rfd: str | None
    rfd: str | None
    detailed_rfd: str | None
    remarks: str | None
    contact_person: str | None
    statement: str | None
    source_timestamp: datetime | None


@dataclass(frozen=True)
class ProcessedRow:
    """One resolved, sanitized, ranked demo row ready for export or review."""

    ch_code: str
    account_number: str | None
    relation: Relation
    csu_rank: int
    selected_rfd: str | None
    sanitized_remark: str
    remark_length: int
    disposition: Disposition
    exception_kinds: tuple[ExceptionKind, ...] = ()
    source_row_ref: str = ""
    contact_person: str | None = None
    statement: str | None = None
    source_timestamp: datetime | None = None


@dataclass
class RunResult:
    """In-memory result of one on-demand demo run, keyed by run_id."""

    run_id: str
    attribution: str = "DA"
    date_from: date | None = None
    date_to: date | None = None
    clean_rows: list[ProcessedRow] = field(default_factory=list)
    review_rows: list[ProcessedRow] = field(default_factory=list)
    excluded_count: int = 0
    artifacts: dict[str, str] = field(default_factory=dict)  # name -> protected path


__all__ = [
    "ArtifactRecord",
    "ArtifactStatus",
    "ArtifactType",
    "AuditEvent",
    "ConfigurationSnapshot",
    "Disposition",
    "ExceptionKind",
    "ExclusionKind",
    "OutcomeDisposition",
    "ProcessedRow",
    "ProcessingOutcome",
    "RawSourceRecord",
    "Relation",
    "RemarkFields",
    "ReportRun",
    "RunResult",
    "RunState",
    "RunStateEvent",
    "new_artifact_record",
    "new_audit_event",
    "new_outcome",
    "new_snapshot",
]
