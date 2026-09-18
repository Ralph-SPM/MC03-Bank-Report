"""Persistence for operational configuration and production-gate evidence.

Operational configuration versions and gate evidence are append-only. Selecting
an operational configuration for a service or run writes an immutable snapshot.
Gate evidence is recorded as append-only facts; the effective state per gate is
the latest recorded evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc03.domain.configuration import GateState
from mc03.domain.gates import (
    GateEvidence,
    GateEvidenceLedger,
    GateFamily,
    OperationalConfiguration,
    OperationalConfigurationSnapshot,
)
from mc03.persistence.models import (
    GateEvidenceModel,
    OperationalConfigurationSnapshotModel,
    OperationalConfigurationVersionModel,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class OperationalConfigurationStore:
    """Append operational configuration versions and immutable selections."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_version(
        self, configuration: OperationalConfiguration, *, approved_at: datetime | None = None
    ) -> OperationalConfiguration:
        """Store an approved operational configuration version."""
        self.session.add(
            OperationalConfigurationVersionModel(
                version=configuration.version,
                lock_lease_seconds=configuration.lock_lease_seconds,
                heartbeat_interval_seconds=configuration.heartbeat_interval_seconds,
                heartbeat_stale_seconds=configuration.heartbeat_stale_seconds,
                listener_heartbeat_stale_seconds=configuration.listener_heartbeat_stale_seconds,
                registration_validation_deadline_seconds=(
                    configuration.registration_validation_deadline_seconds
                ),
                repeated_failure_threshold=configuration.repeated_failure_threshold,
                provider_timeout_seconds=configuration.provider_timeout_seconds,
                provider_retry_budget=configuration.provider_retry_budget,
                batch_size=configuration.batch_size,
                approved_at=_utc(approved_at or datetime.now(UTC)),
            )
        )
        self.session.flush()
        return configuration

    def get_version(self, version: str) -> OperationalConfiguration | None:
        """Return one approved operational configuration version."""
        model = self.session.get(OperationalConfigurationVersionModel, version)
        return self._to_configuration(model) if model is not None else None

    def select(
        self,
        version: str,
        *,
        selected_by: str,
        run_id: str | None = None,
        selected_at: datetime | None = None,
    ) -> OperationalConfigurationSnapshot:
        """Create an immutable snapshot selecting an approved version."""
        configuration = self.get_version(version)
        if configuration is None:
            raise ValueError(f"operational configuration version not found: {version}")
        snapshot_id = str(uuid4())
        chosen_at = _utc(selected_at or datetime.now(UTC))
        self.session.add(
            OperationalConfigurationSnapshotModel(
                snapshot_id=snapshot_id,
                operational_version=version,
                selected_by=selected_by,
                run_id=run_id,
                selected_at=chosen_at,
            )
        )
        self.session.flush()
        return OperationalConfigurationSnapshot(
            snapshot_id=snapshot_id,
            selected_by=selected_by,
            operational_configuration=configuration,
            selected_at=chosen_at,
            run_id=run_id,
        )

    @staticmethod
    def _to_configuration(
        model: OperationalConfigurationVersionModel,
    ) -> OperationalConfiguration:
        return OperationalConfiguration(
            version=model.version,
            lock_lease_seconds=model.lock_lease_seconds,
            heartbeat_interval_seconds=model.heartbeat_interval_seconds,
            heartbeat_stale_seconds=model.heartbeat_stale_seconds,
            listener_heartbeat_stale_seconds=model.listener_heartbeat_stale_seconds,
            registration_validation_deadline_seconds=(
                model.registration_validation_deadline_seconds
            ),
            repeated_failure_threshold=model.repeated_failure_threshold,
            provider_timeout_seconds=model.provider_timeout_seconds,
            provider_retry_budget=model.provider_retry_budget,
            batch_size=model.batch_size,
        )


class GateEvidenceStore:
    """Append-only production-gate evidence store and ledger builder."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, evidence: GateEvidence) -> GateEvidence:
        """Append one immutable gate-evidence fact."""
        self.session.add(
            GateEvidenceModel(
                evidence_id=str(uuid4()),
                gate_id=evidence.gate_id,
                family=evidence.family.value,
                scope=evidence.scope,
                state=evidence.state.value,
                owner=evidence.owner,
                evidence_reference=evidence.evidence_reference,
                recorded_at=_utc(evidence.recorded_at),
            )
        )
        self.session.flush()
        return evidence

    def list_for_family(self, family: GateFamily) -> Sequence[GateEvidence]:
        """Return recorded evidence for one gate family in record order."""
        models = self.session.scalars(
            select(GateEvidenceModel)
            .where(GateEvidenceModel.family == family.value)
            .order_by(GateEvidenceModel.recorded_at)
        )
        return tuple(self._to_value(model) for model in models)

    def build_ledger(self) -> GateEvidenceLedger:
        """Build an evidence ledger with the latest state per gate identifier."""
        models = self.session.scalars(
            select(GateEvidenceModel).order_by(GateEvidenceModel.recorded_at)
        )
        return GateEvidenceLedger.from_records([self._to_value(model) for model in models])

    @staticmethod
    def _to_value(model: GateEvidenceModel) -> GateEvidence:
        return GateEvidence(
            gate_id=model.gate_id,
            family=GateFamily(model.family),
            scope=model.scope,
            state=GateState(model.state),
            recorded_at=model.recorded_at,
            owner=model.owner,
            evidence_reference=model.evidence_reference,
        )


__all__ = ["GateEvidenceStore", "OperationalConfigurationStore"]
