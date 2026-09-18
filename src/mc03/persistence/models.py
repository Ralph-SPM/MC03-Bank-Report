"""SQLAlchemy tables for immutable evidence and current-state projections."""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import uuid4

from sqlalchemy import (
    BLOB,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata for the versioned MC03 persistence schema."""


def _new_uuid() -> str:
    return str(uuid4())


UUID_LENGTH: Final[int] = 36
HASH_LENGTH: Final[int] = 64


class CampaignConfigurationVersionModel(Base):
    """Validated configuration version evidence."""

    __tablename__ = "campaign_configuration_versions"

    configuration_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    campaign_code: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    parsed_json: Mapped[str] = mapped_column(Text, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_code", "version", name="uq_campaign_configuration_version"),
        UniqueConstraint("config_hash", name="uq_campaign_configuration_hash"),
        CheckConstraint("length(config_hash) = 64", name="ck_campaign_configuration_hash"),
    )


class OperationalConfigurationVersionModel(Base):
    """Approved operational thresholds; explicitly selected by service/run."""

    __tablename__ = "operational_configuration_versions"

    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    lock_lease_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    heartbeat_stale_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    listener_heartbeat_stale_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    registration_validation_deadline_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    repeated_failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    provider_retry_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("lock_lease_seconds > 0", name="ck_operational_lock_lease"),
        CheckConstraint("batch_size > 0", name="ck_operational_batch_size"),
    )


class OperationalConfigurationSnapshotModel(Base):
    """Immutable per-service/per-run selection of an operational configuration."""

    __tablename__ = "operational_configuration_snapshots"

    snapshot_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    operational_version: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("operational_configuration_versions.version", ondelete="RESTRICT"),
        nullable=False,
    )
    selected_by: Mapped[str] = mapped_column(String(256), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=True
    )
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_operational_snapshot_run", "run_id"),)


class GateEvidenceModel(Base):
    """Append-only production-gate evidence owned outside configuration."""

    __tablename__ = "gate_evidence"

    evidence_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    gate_id: Mapped[str] = mapped_column(String(256), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    evidence_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'approved', 'rejected')", name="ck_gate_evidence_state"
        ),
        Index("ix_gate_evidence_gate", "gate_id"),
        Index("ix_gate_evidence_family", "family"),
    )


class ReportRunModel(Base):
    """Immutable run request fact plus mutable current-state projection."""

    __tablename__ = "report_runs"

    run_id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=_new_uuid)
    campaign_code: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    predecessor_run_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id"), nullable=True
    )
    report_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    report_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("length(config_hash) = 64", name="ck_report_run_config_hash"),
        CheckConstraint("length(trim(reviewer_name)) > 0", name="ck_report_run_reviewer"),
    )


class ReportRunConfigurationSnapshotModel(Base):
    """Exact immutable canonical configuration selected by one run."""

    __tablename__ = "report_run_configuration_snapshots"

    snapshot_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    run_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    campaign_code: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_report_run_configuration_snapshot_run"),
        CheckConstraint("length(config_hash) = 64", name="ck_snapshot_config_hash"),
    )


class RawSourceRecordModel(Base):
    """Immutable original source content or protected content reference."""

    __tablename__ = "raw_source_records"

    source_id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=_new_uuid)
    source_type: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes | None] = mapped_column(BLOB, nullable=True)
    content_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    source_token_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "content IS NOT NULL OR content_path IS NOT NULL",
            name="ck_raw_source_content",
        ),
        CheckConstraint("length(source_hash) = 64", name="ck_raw_source_hash"),
        Index("ix_raw_source_hash", "source_hash"),
        Index("ix_raw_source_token_hash", "source_token_hash"),
    )


class AcceptedSourceRowModel(Base):
    """Accepted source row membership retained separately from raw evidence."""

    __tablename__ = "accepted_source_rows"

    row_id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=_new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH),
        ForeignKey("raw_source_records.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_file_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    original_row_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)

    __table_args__ = (
        CheckConstraint("length(source_hash) = 64", name="ck_accepted_source_row_hash"),
        Index("ix_accepted_source_source", "source_id"),
    )


class SourceFailureEventModel(Base):
    """Visible source acquisition/parsing failure, even without an accepted row."""

    __tablename__ = "source_failure_events"

    failure_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    run_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(128), nullable=False)
    input_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    error_code: Mapped[str] = mapped_column(String(128), nullable=False)
    safe_message: Mapped[str] = mapped_column(String(2048), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)


class RunStateEventModel(Base):
    """Append-only run state event; ReportRunModel.state is only a projection."""

    __tablename__ = "run_state_events"

    event_id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=_new_uuid)
    run_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class ProcessingOutcomeModel(Base):
    """Append-only outcome linked to source, run, and configuration hash."""

    __tablename__ = "processing_outcomes"

    outcome_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    source_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH),
        ForeignKey("raw_source_records.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    config_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    values_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predecessor_outcome_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH),
        ForeignKey("processing_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("length(config_hash) = 64", name="ck_processing_outcome_hash"),
        Index("ix_processing_outcome_run", "run_id"),
        Index("ix_processing_outcome_source", "source_id"),
    )


class ArtifactModel(Base):
    """Artifact lifecycle record; completed rows become immutable evidence."""

    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), primary_key=True, default=_new_uuid
    )
    run_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("report_runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    artifact_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("length(config_hash) = 64", name="ck_artifact_config_hash"),
        CheckConstraint(
            "status <> 'completed' OR (artifact_hash IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_completed_artifact_evidence",
        ),
        CheckConstraint(
            "artifact_hash IS NULL OR length(artifact_hash) = 64",
            name="ck_artifact_hash",
        ),
        Index("ix_artifact_run", "run_id"),
    )


class AuditLedgerModel(Base):
    """Ordered append-only cross-entity audit evidence."""

    __tablename__ = "audit_ledger"

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), unique=True, nullable=False, default=_new_uuid
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(UUID_LENGTH), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "config_hash IS NULL OR length(config_hash) = 64", name="ck_audit_config_hash"
        ),
        Index("ix_audit_ledger_run", "run_id"),
        Index("ix_audit_ledger_source", "source_id"),
        Index("ix_audit_ledger_type", "event_type"),
    )


__all__ = [
    "AcceptedSourceRowModel",
    "ArtifactModel",
    "AuditLedgerModel",
    "Base",
    "CampaignConfigurationVersionModel",
    "GateEvidenceModel",
    "OperationalConfigurationSnapshotModel",
    "OperationalConfigurationVersionModel",
    "ProcessingOutcomeModel",
    "RawSourceRecordModel",
    "ReportRunConfigurationSnapshotModel",
    "ReportRunModel",
    "RunStateEventModel",
    "SourceFailureEventModel",
]
