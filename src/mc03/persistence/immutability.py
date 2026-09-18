"""Database and ORM guards for append-only MC03 evidence."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper
from sqlalchemy.orm.attributes import get_history

from mc03.domain.errors import ImmutableMutationError
from mc03.persistence.models import (
    ArtifactModel,
    AuditLedgerModel,
    Base,
    GateEvidenceModel,
    OperationalConfigurationVersionModel,
    ProcessingOutcomeModel,
    RawSourceRecordModel,
    ReportRunConfigurationSnapshotModel,
    RunStateEventModel,
)

_IMMUTABLE_TABLES = (
    ("raw_source_records", "raw_source_records"),
    ("report_run_configuration_snapshots", "report_run_configuration_snapshots"),
    ("processing_outcomes", "processing_outcomes"),
    ("run_state_events", "run_state_events"),
    ("audit_ledger", "audit_ledger"),
    ("gate_evidence", "gate_evidence"),
    ("operational_configuration_versions", "operational_configuration_versions"),
)


def install_database_guards(connection: Connection) -> None:
    """Install SQLite triggers that protect append-only facts from direct SQL mutation."""
    execute = connection.exec_driver_sql
    for table, label in _IMMUTABLE_TABLES:
        execute(
            f"CREATE TRIGGER IF NOT EXISTS prevent_{table}_update "
            f"BEFORE UPDATE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, 'immutable entity: {label}'); END"
        )
        execute(
            f"CREATE TRIGGER IF NOT EXISTS prevent_{table}_delete "
            f"BEFORE DELETE ON {table} BEGIN "
            f"SELECT RAISE(ABORT, 'immutable entity: {label}'); END"
        )
    execute(
        "CREATE TRIGGER IF NOT EXISTS prevent_completed_artifact_update "
        "BEFORE UPDATE ON artifacts WHEN OLD.status = 'completed' BEGIN "
        "SELECT RAISE(ABORT, 'immutable entity: completed artifacts'); END"
    )
    execute(
        "CREATE TRIGGER IF NOT EXISTS prevent_completed_artifact_delete "
        "BEFORE DELETE ON artifacts WHEN OLD.status = 'completed' BEGIN "
        "SELECT RAISE(ABORT, 'immutable entity: completed artifacts'); END"
    )


def _raise_for(target: object, entity_type: str, identifier: str | None = None) -> None:
    raise ImmutableMutationError(entity_type, identifier)


def _id_for(target: object) -> str | None:
    for name in (
        "source_id",
        "snapshot_id",
        "outcome_id",
        "event_id",
        "artifact_id",
        "evidence_id",
        "version",
    ):
        value = getattr(target, name, None)
        if value is not None:
            return str(value)
    return None


@event.listens_for(RawSourceRecordModel, "before_update")
def _raw_source_update(
    _mapper: Mapper[RawSourceRecordModel],
    _connection: object,
    target: RawSourceRecordModel,
) -> None:
    _raise_for(target, "raw_source_records", _id_for(target))


@event.listens_for(RawSourceRecordModel, "before_delete")
def _raw_source_delete(
    _mapper: Mapper[RawSourceRecordModel],
    _connection: object,
    target: RawSourceRecordModel,
) -> None:
    _raise_for(target, "raw_source_records", _id_for(target))


@event.listens_for(ReportRunConfigurationSnapshotModel, "before_update")
def _snapshot_update(
    _mapper: Mapper[ReportRunConfigurationSnapshotModel],
    _connection: object,
    target: ReportRunConfigurationSnapshotModel,
) -> None:
    _raise_for(target, "report_run_configuration_snapshots", _id_for(target))


@event.listens_for(ReportRunConfigurationSnapshotModel, "before_delete")
def _snapshot_delete(
    _mapper: Mapper[ReportRunConfigurationSnapshotModel],
    _connection: object,
    target: ReportRunConfigurationSnapshotModel,
) -> None:
    _raise_for(target, "report_run_configuration_snapshots", _id_for(target))


@event.listens_for(ProcessingOutcomeModel, "before_update")
def _outcome_update(
    _mapper: Mapper[ProcessingOutcomeModel], _connection: object, target: ProcessingOutcomeModel
) -> None:
    _raise_for(target, "processing_outcomes", _id_for(target))


@event.listens_for(ProcessingOutcomeModel, "before_delete")
def _outcome_delete(
    _mapper: Mapper[ProcessingOutcomeModel], _connection: object, target: ProcessingOutcomeModel
) -> None:
    _raise_for(target, "processing_outcomes", _id_for(target))


@event.listens_for(RunStateEventModel, "before_update")
def _state_event_update(
    _mapper: Mapper[RunStateEventModel], _connection: object, target: RunStateEventModel
) -> None:
    _raise_for(target, "run_state_events", _id_for(target))


@event.listens_for(RunStateEventModel, "before_delete")
def _state_event_delete(
    _mapper: Mapper[RunStateEventModel], _connection: object, target: RunStateEventModel
) -> None:
    _raise_for(target, "run_state_events", _id_for(target))


@event.listens_for(AuditLedgerModel, "before_update")
def _audit_update(
    _mapper: Mapper[AuditLedgerModel], _connection: object, target: AuditLedgerModel
) -> None:
    _raise_for(target, "audit_ledger", _id_for(target))


@event.listens_for(AuditLedgerModel, "before_delete")
def _audit_delete(
    _mapper: Mapper[AuditLedgerModel], _connection: object, target: AuditLedgerModel
) -> None:
    _raise_for(target, "audit_ledger", _id_for(target))


@event.listens_for(GateEvidenceModel, "before_update")
def _gate_evidence_update(
    _mapper: Mapper[GateEvidenceModel], _connection: object, target: GateEvidenceModel
) -> None:
    _raise_for(target, "gate_evidence", _id_for(target))


@event.listens_for(GateEvidenceModel, "before_delete")
def _gate_evidence_delete(
    _mapper: Mapper[GateEvidenceModel], _connection: object, target: GateEvidenceModel
) -> None:
    _raise_for(target, "gate_evidence", _id_for(target))


@event.listens_for(OperationalConfigurationVersionModel, "before_update")
def _operational_version_update(
    _mapper: Mapper[OperationalConfigurationVersionModel],
    _connection: object,
    target: OperationalConfigurationVersionModel,
) -> None:
    _raise_for(target, "operational_configuration_versions", _id_for(target))


@event.listens_for(OperationalConfigurationVersionModel, "before_delete")
def _operational_version_delete(
    _mapper: Mapper[OperationalConfigurationVersionModel],
    _connection: object,
    target: OperationalConfigurationVersionModel,
) -> None:
    _raise_for(target, "operational_configuration_versions", _id_for(target))


@event.listens_for(ArtifactModel, "before_update")
def _artifact_update(
    _mapper: Mapper[ArtifactModel], _connection: object, target: ArtifactModel
) -> None:
    history = get_history(target, "status")
    old_status = history.deleted[0] if history.deleted else None
    if old_status == "completed":
        _raise_for(target, "completed artifacts", _id_for(target))


@event.listens_for(ArtifactModel, "before_delete")
def _artifact_delete(
    _mapper: Mapper[ArtifactModel], _connection: object, target: ArtifactModel
) -> None:
    if target.status == "completed":
        _raise_for(target, "completed artifacts", _id_for(target))


# Keep imports discoverable to migration tooling and prevent linters from treating
# event registration as an unused side effect.
GUARDED_MODELS: tuple[type[Base], ...] = (
    RawSourceRecordModel,
    ReportRunConfigurationSnapshotModel,
    ProcessingOutcomeModel,
    RunStateEventModel,
    AuditLedgerModel,
    ArtifactModel,
    GateEvidenceModel,
    OperationalConfigurationVersionModel,
)

__all__ = ["GUARDED_MODELS", "install_database_guards"]
