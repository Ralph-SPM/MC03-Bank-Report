"""Guarded SQLAlchemy repositories for immutable facts and projections."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc03.domain.audit import JsonValue, build_safe_audit_payload
from mc03.domain.errors import ImmutableMutationError
from mc03.domain.identifiers import (
    ArtifactId,
    AuditEventId,
    ConfigHash,
    CorrelationId,
    OutcomeId,
    RunId,
    SnapshotId,
    SourceId,
)
from mc03.domain.models import (
    ArtifactRecord,
    ArtifactStatus,
    ArtifactType,
    AuditEvent,
    ConfigurationSnapshot,
    OutcomeDisposition,
    ProcessingOutcome,
    RawSourceRecord,
    ReportRun,
    RunState,
    RunStateEvent,
)
from mc03.persistence.models import (
    ArtifactModel,
    AuditLedgerModel,
    ProcessingOutcomeModel,
    RawSourceRecordModel,
    ReportRunConfigurationSnapshotModel,
    ReportRunModel,
    RunStateEventModel,
)


def _json(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str) -> dict[str, JsonValue]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted JSON object was not an object")
    return parsed


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _uuid(value: str) -> UUID:
    return UUID(value)


def _immutable(entity_type: str, entity_id: UUID | str | None = None) -> NoReturn:
    raise ImmutableMutationError(entity_type, str(entity_id) if entity_id is not None else None)


class RawSourceSqlRepository:
    """Append and retrieve immutable raw source records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, source: RawSourceRecord) -> RawSourceRecord:
        """Append a raw source fact to the current transaction."""
        self.session.add(
            RawSourceRecordModel(
                source_id=str(source.source_id),
                source_type=source.source_type,
                acquired_at=_utc(source.acquired_at),
                source_metadata_json=_json(source.source_metadata),
                content=source.content,
                content_path=source.content_path,
                source_hash=source.source_hash,
                source_token_hash=source.source_token_hash,
            )
        )
        self.session.flush()
        return source

    create = add

    def get(self, source_id: SourceId | UUID) -> RawSourceRecord | None:
        """Return one immutable raw source fact."""
        model = self.session.get(RawSourceRecordModel, str(source_id))
        if model is None:
            return None
        return RawSourceRecord(
            source_id=SourceId(_uuid(model.source_id)),
            source_type=model.source_type,
            acquired_at=model.acquired_at,
            source_hash=model.source_hash,
            source_metadata=_parse_json(model.source_metadata_json),
            content=model.content,
            content_path=model.content_path,
            source_token_hash=model.source_token_hash,
        )

    def update(self, _source: RawSourceRecord) -> NoReturn:
        """Reject an attempted raw-source mutation."""
        _immutable("raw_source_records", _source.source_id)

    def delete(self, source_id: SourceId | UUID) -> NoReturn:
        """Reject an attempted raw-source deletion."""
        _immutable("raw_source_records", source_id)


class ConfigurationSnapshotSqlRepository:
    """Append and retrieve immutable selected configuration snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, snapshot: ConfigurationSnapshot) -> ConfigurationSnapshot:
        """Append one exact per-run canonical configuration snapshot."""
        self.session.add(
            ReportRunConfigurationSnapshotModel(
                snapshot_id=str(snapshot.snapshot_id),
                run_id=str(snapshot.run_id),
                campaign_code=snapshot.campaign_code,
                config_version=snapshot.config_version,
                canonical_bytes=snapshot.canonical_bytes,
                config_hash=str(snapshot.config_hash),
                selected_at=_utc(snapshot.selected_at),
            )
        )
        self.session.flush()
        return snapshot

    create = add

    def get_for_run(self, run_id: RunId | UUID) -> ConfigurationSnapshot | None:
        """Return the selected snapshot for one report run."""
        model = self.session.scalar(
            select(ReportRunConfigurationSnapshotModel).where(
                ReportRunConfigurationSnapshotModel.run_id == str(run_id)
            )
        )
        if model is None:
            return None
        return ConfigurationSnapshot(
            snapshot_id=SnapshotId(_uuid(model.snapshot_id)),
            run_id=RunId(_uuid(model.run_id)),
            campaign_code=model.campaign_code,
            config_version=model.config_version,
            canonical_bytes=model.canonical_bytes,
            config_hash=ConfigHash(model.config_hash),
            selected_at=model.selected_at,
        )

    def update(self, _snapshot: ConfigurationSnapshot) -> NoReturn:
        """Reject an attempted selected-snapshot mutation."""
        _immutable("report_run_configuration_snapshots", _snapshot.snapshot_id)

    def delete(self, snapshot_id: SnapshotId | UUID) -> NoReturn:
        """Reject an attempted selected-snapshot deletion."""
        _immutable("report_run_configuration_snapshots", snapshot_id)


class ReportRunSqlRepository:
    """Persist run request facts and transactionally append state events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, run: ReportRun) -> ReportRun:
        """Append one immutable run request and its initial state projection."""
        self.session.add(
            ReportRunModel(
                run_id=str(run.run_id),
                campaign_code=run.campaign_code,
                config_version=run.config_version,
                config_hash=str(run.config_hash),
                requested_at=_utc(run.requested_at),
                reviewer_name=run.reviewer_name,
                state=run.state.value,
                correlation_id=str(run.correlation_id) if run.correlation_id else None,
                predecessor_run_id=(
                    str(run.predecessor_run_id) if run.predecessor_run_id else None
                ),
                report_window_start=(
                    _utc(run.report_window_start) if run.report_window_start else None
                ),
                report_window_end=_utc(run.report_window_end) if run.report_window_end else None,
            )
        )
        self.session.flush()
        return run

    create = add

    def record_state_event(self, event: RunStateEvent) -> RunStateEvent:
        """Append a state event and update the current-state projection in one session."""
        run = self.session.get(ReportRunModel, str(event.run_id))
        if run is None:
            raise ValueError(f"report run not found: {event.run_id}")
        self.session.add(
            RunStateEventModel(
                event_id=str(event.event_id),
                run_id=str(event.run_id),
                state=event.state.value,
                occurred_at=_utc(event.occurred_at),
                actor=event.actor,
                service=event.service,
                correlation_id=str(event.correlation_id) if event.correlation_id else None,
                reason=event.reason,
            )
        )
        run.state = event.state.value
        self.session.flush()
        return event

    def get(self, run_id: RunId | UUID) -> ReportRun | None:
        """Return the current report-run projection."""
        model = self.session.get(ReportRunModel, str(run_id))
        if model is None:
            return None
        return ReportRun(
            run_id=RunId(_uuid(model.run_id)),
            campaign_code=model.campaign_code,
            config_version=model.config_version,
            config_hash=ConfigHash(model.config_hash),
            requested_at=model.requested_at,
            reviewer_name=model.reviewer_name,
            state=RunState(model.state),
            correlation_id=(
                CorrelationId(_uuid(model.correlation_id))
                if model.correlation_id
                else None
            ),
            predecessor_run_id=(
                RunId(_uuid(model.predecessor_run_id)) if model.predecessor_run_id else None
            ),
            report_window_start=model.report_window_start,
            report_window_end=model.report_window_end,
        )


class ProcessingOutcomeSqlRepository:
    """Append processing outcomes and preserve predecessor links."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, outcome: ProcessingOutcome) -> ProcessingOutcome:
        """Append one outcome without updating an earlier outcome."""
        self.session.add(
            ProcessingOutcomeModel(
                outcome_id=str(outcome.outcome_id),
                source_id=str(outcome.source_id),
                run_id=str(outcome.run_id),
                config_hash=str(outcome.config_hash),
                disposition=outcome.disposition.value,
                values_json=_json(outcome.values),
                created_at=_utc(outcome.created_at),
                predecessor_outcome_id=(
                    str(outcome.predecessor_outcome_id)
                    if outcome.predecessor_outcome_id
                    else None
                ),
            )
        )
        self.session.flush()
        return outcome

    add = append

    def list_for_run(self, run_id: RunId | UUID) -> Iterable[ProcessingOutcome]:
        """Return immutable outcomes in creation order."""
        models = self.session.scalars(
            select(ProcessingOutcomeModel)
            .where(ProcessingOutcomeModel.run_id == str(run_id))
            .order_by(ProcessingOutcomeModel.created_at, ProcessingOutcomeModel.outcome_id)
        )
        return tuple(self._to_value(model) for model in models)

    @staticmethod
    def _to_value(model: ProcessingOutcomeModel) -> ProcessingOutcome:
        return ProcessingOutcome(
            outcome_id=OutcomeId(_uuid(model.outcome_id)),
            source_id=SourceId(_uuid(model.source_id)),
            run_id=RunId(_uuid(model.run_id)),
            config_hash=ConfigHash(model.config_hash),
            disposition=OutcomeDisposition(model.disposition),
            created_at=model.created_at,
            values=_parse_json(model.values_json),
            predecessor_outcome_id=(
                OutcomeId(_uuid(model.predecessor_outcome_id))
                if model.predecessor_outcome_id
                else None
            ),
        )

    def update(self, _outcome: ProcessingOutcome) -> NoReturn:
        """Reject an attempted outcome mutation."""
        _immutable("processing_outcomes", _outcome.outcome_id)

    def delete(self, outcome_id: OutcomeId | UUID) -> NoReturn:
        """Reject an attempted outcome deletion."""
        _immutable("processing_outcomes", outcome_id)


class ArtifactSqlRepository:
    """Store artifact lifecycle evidence and guard completed records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, artifact: ArtifactRecord) -> ArtifactRecord:
        """Append a started, failed, or already-completed artifact record."""
        self.session.add(
            ArtifactModel(
                artifact_id=str(artifact.artifact_id),
                run_id=str(artifact.run_id),
                artifact_type=artifact.artifact_type.value,
                status=artifact.status.value,
                config_hash=str(artifact.config_hash),
                artifact_hash=artifact.artifact_hash,
                path=artifact.path,
                metadata_json=_json(artifact.metadata),
                created_at=_utc(artifact.created_at or datetime.now(UTC)),
                completed_at=_utc(artifact.completed_at) if artifact.completed_at else None,
            )
        )
        self.session.flush()
        return artifact

    create = add

    def complete(
        self,
        artifact_id: ArtifactId | UUID,
        artifact_hash: str,
        completed_at: datetime,
    ) -> ArtifactRecord:
        """Complete an artifact exactly once; later mutation is rejected."""
        model = self.session.get(ArtifactModel, str(artifact_id))
        if model is None:
            raise ValueError(f"artifact not found: {artifact_id}")
        if model.status == ArtifactStatus.COMPLETED.value:
            _immutable("completed artifacts", artifact_id)
        model.status = ArtifactStatus.COMPLETED.value
        model.artifact_hash = artifact_hash
        model.completed_at = _utc(completed_at)
        self.session.flush()
        return self._to_value(model)

    def get(self, artifact_id: ArtifactId | UUID) -> ArtifactRecord | None:
        """Return one artifact record."""
        model = self.session.get(ArtifactModel, str(artifact_id))
        return self._to_value(model) if model is not None else None

    def update(self, _artifact: ArtifactRecord) -> NoReturn:
        """Reject a generic artifact update; use the guarded lifecycle methods."""
        _immutable("artifacts", _artifact.artifact_id)

    def delete(self, artifact_id: ArtifactId | UUID) -> None:
        """Reject deletion of completed artifact evidence."""
        model = self.session.get(ArtifactModel, str(artifact_id))
        if model is not None and model.status == ArtifactStatus.COMPLETED.value:
            _immutable("completed artifacts", artifact_id)
        self.session.delete(model)
        self.session.flush()

    @staticmethod
    def _to_value(model: ArtifactModel) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=ArtifactId(_uuid(model.artifact_id)),
            run_id=RunId(_uuid(model.run_id)),
            artifact_type=ArtifactType(model.artifact_type),
            status=ArtifactStatus(model.status),
            config_hash=ConfigHash(model.config_hash),
            artifact_hash=model.artifact_hash,
            path=model.path,
            metadata=_parse_json(model.metadata_json),
            created_at=model.created_at,
            completed_at=model.completed_at,
        )


class AuditLedgerSqlRepository:
    """Append safe audit events in the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, event: AuditEvent) -> AuditEvent:
        """Validate and append one linked audit event."""
        payload = build_safe_audit_payload(event.payload)
        self.session.add(
            AuditLedgerModel(
                event_id=str(event.event_id),
                event_type=event.event_type,
                occurred_at=_utc(event.occurred_at),
                actor=event.actor,
                service=event.service,
                correlation_id=str(event.correlation_id),
                entity_type=event.entity_type,
                entity_id=str(event.entity_id) if event.entity_id is not None else None,
                source_id=str(event.source_id) if event.source_id is not None else None,
                run_id=str(event.run_id) if event.run_id is not None else None,
                config_hash=str(event.config_hash) if event.config_hash is not None else None,
                payload_json=_json(payload),
            )
        )
        self.session.flush()
        return event

    def append_event(
        self,
        *,
        event_type: str,
        actor: str,
        service: str,
        correlation_id: CorrelationId,
        payload: Mapping[str, object],
        occurred_at: datetime,
        entity_type: str | None = None,
        entity_id: UUID | str | None = None,
        source_id: SourceId | None = None,
        run_id: RunId | None = None,
        config_hash: ConfigHash | None = None,
    ) -> AuditEvent:
        """Construct, validate, and append an event without exposing unsafe fields."""
        event = AuditEvent(
            event_id=AuditEventId(uuid4()),
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            service=service,
            correlation_id=correlation_id,
            payload=build_safe_audit_payload(payload),
            entity_type=entity_type,
            entity_id=entity_id,
            source_id=source_id,
            run_id=run_id,
            config_hash=config_hash,
        )
        return self.append(event)

    def list_for_run(self, run_id: RunId | UUID) -> Iterable[AuditEvent]:
        """Return linked audit events in append order."""
        models = self.session.scalars(
            select(AuditLedgerModel)
            .where(AuditLedgerModel.run_id == str(run_id))
            .order_by(AuditLedgerModel.sequence_id)
        )
        return tuple(self._to_value(model) for model in models)

    @staticmethod
    def _to_value(model: AuditLedgerModel) -> AuditEvent:
        return AuditEvent(
            event_id=AuditEventId(_uuid(model.event_id)),
            event_type=model.event_type,
            occurred_at=model.occurred_at,
            actor=model.actor,
            service=model.service,
            correlation_id=CorrelationId(_uuid(model.correlation_id)),
            payload=build_safe_audit_payload(_parse_json(model.payload_json)),
            entity_type=model.entity_type,
            entity_id=model.entity_id,
            source_id=SourceId(_uuid(model.source_id)) if model.source_id else None,
            run_id=RunId(_uuid(model.run_id)) if model.run_id else None,
            config_hash=ConfigHash(model.config_hash) if model.config_hash else None,
        )

    def update(self, _event: AuditEvent) -> NoReturn:
        """Reject an attempted audit mutation."""
        _immutable("audit_ledger", _event.event_id)

    def delete(self, event_id: AuditEventId | UUID) -> NoReturn:
        """Reject an attempted audit deletion."""
        _immutable("audit_ledger", event_id)


# Names with a short ``Sql`` suffix are the concrete implementations; these aliases
# make the repository boundary pleasant to import without hiding persistence details.
RawSourceRepository = RawSourceSqlRepository
ConfigurationSnapshotRepository = ConfigurationSnapshotSqlRepository
ReportRunRepository = ReportRunSqlRepository
OutcomeRepository = ProcessingOutcomeSqlRepository
ArtifactRepository = ArtifactSqlRepository
AuditLedgerRepository = AuditLedgerSqlRepository

__all__ = [
    "ArtifactRepository",
    "ArtifactSqlRepository",
    "AuditLedgerRepository",
    "AuditLedgerSqlRepository",
    "ConfigurationSnapshotRepository",
    "ConfigurationSnapshotSqlRepository",
    "OutcomeRepository",
    "ProcessingOutcomeSqlRepository",
    "RawSourceRepository",
    "RawSourceSqlRepository",
    "ReportRunRepository",
    "ReportRunSqlRepository",
]
