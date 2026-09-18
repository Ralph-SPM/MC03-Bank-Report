"""Persistence-agnostic repository protocols for later services."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from mc03.domain.models import (
    ArtifactRecord,
    AuditEvent,
    ConfigurationSnapshot,
    ProcessingOutcome,
    RawSourceRecord,
    ReportRun,
    RunStateEvent,
)


class RawSourceRepository(Protocol):
    """Store and retrieve immutable raw-source evidence."""

    def add(self, source: RawSourceRecord) -> RawSourceRecord:
        """Append one raw-source fact."""
        ...

    def get(self, source_id: UUID) -> RawSourceRecord | None:
        """Retrieve one raw-source fact."""
        ...


class ConfigurationSnapshotRepository(Protocol):
    """Store selected per-run configuration bytes and hashes."""

    def add(self, snapshot: ConfigurationSnapshot) -> ConfigurationSnapshot:
        """Append one selected snapshot."""
        ...

    def get_for_run(self, run_id: UUID) -> ConfigurationSnapshot | None:
        """Retrieve the selected snapshot for a run."""
        ...


class ReportRunRepository(Protocol):
    """Manage run request facts and current-state projections."""

    def add(self, run: ReportRun) -> ReportRun:
        """Append a report-run request fact."""
        ...

    def record_state_event(self, event: RunStateEvent) -> RunStateEvent:
        """Append a state event and update the projection transactionally."""
        ...

    def get(self, run_id: UUID) -> ReportRun | None:
        """Retrieve a run projection."""
        ...


class OutcomeRepository(Protocol):
    """Append processing outcomes without overwriting predecessors."""

    def append(self, outcome: ProcessingOutcome) -> ProcessingOutcome:
        """Append one processing outcome."""
        ...

    def list_for_run(self, run_id: UUID) -> Iterable[ProcessingOutcome]:
        """Iterate outcomes for a run."""
        ...


class ArtifactRepository(Protocol):
    """Store artifact lifecycle evidence and protect completed artifacts."""

    def add(self, artifact: ArtifactRecord) -> ArtifactRecord:
        """Append a new artifact record."""
        ...

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        """Retrieve an artifact record."""
        ...


class AuditLedgerRepository(Protocol):
    """Append safe, linked audit events."""

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append one audit event in the caller's transaction."""
        ...

    def list_for_run(self, run_id: UUID) -> Iterable[AuditEvent]:
        """Iterate linked audit events."""
        ...


__all__ = [
    "ArtifactRepository",
    "AuditLedgerRepository",
    "ConfigurationSnapshotRepository",
    "OutcomeRepository",
    "RawSourceRepository",
    "ReportRunRepository",
]
