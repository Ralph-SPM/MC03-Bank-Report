"""Strongly named identifiers shared across MC03 domain boundaries."""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

SourceId = NewType("SourceId", UUID)
RunId = NewType("RunId", UUID)
OutcomeId = NewType("OutcomeId", UUID)
ArtifactId = NewType("ArtifactId", UUID)
AuditEventId = NewType("AuditEventId", UUID)
SnapshotId = NewType("SnapshotId", UUID)
CorrelationId = NewType("CorrelationId", UUID)
ConfigHash = NewType("ConfigHash", str)


def new_source_id() -> SourceId:
    """Create a new immutable raw-source identifier."""
    return SourceId(uuid4())


def new_run_id() -> RunId:
    """Create a new immutable report-run identifier."""
    return RunId(uuid4())


def new_outcome_id() -> OutcomeId:
    """Create a new processing-outcome identifier."""
    return OutcomeId(uuid4())


def new_artifact_id() -> ArtifactId:
    """Create a new artifact identifier."""
    return ArtifactId(uuid4())


def new_audit_event_id() -> AuditEventId:
    """Create a new audit-event identifier."""
    return AuditEventId(uuid4())


def new_snapshot_id() -> SnapshotId:
    """Create a new selected-configuration snapshot identifier."""
    return SnapshotId(uuid4())


def new_correlation_id() -> CorrelationId:
    """Create a correlation identifier for one operation or request."""
    return CorrelationId(uuid4())


def as_identifier_text(value: UUID | str) -> str:
    """Return the stable text representation used by persistence adapters."""
    return str(value)
