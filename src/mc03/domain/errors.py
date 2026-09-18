"""Stable domain errors and DA-visible error contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class ErrorCode(StrEnum):
    """Stable machine-readable codes exposed by MC03 services."""

    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_UNSUPPORTED_BEHAVIOR = "CONFIG_UNSUPPORTED_BEHAVIOR"
    CONFIG_SNAPSHOT_MUTATION_REJECTED = "CONFIG_SNAPSHOT_MUTATION_REJECTED"
    RUN_REQUEST_INVALID = "RUN_REQUEST_INVALID"
    SOURCE_ACQUISITION_FAILED = "SOURCE_ACQUISITION_FAILED"
    SOURCE_PARSE_FAILED = "SOURCE_PARSE_FAILED"
    SOURCE_DUPLICATE_RETRY = "SOURCE_DUPLICATE_RETRY"
    IMMUTABLE_UPDATE_REJECTED = "IMMUTABLE_UPDATE_REJECTED"
    AUDIT_APPEND_FAILED = "AUDIT_APPEND_FAILED"
    SQLITE_WRITE_FAILURE = "SQLITE_WRITE_FAILURE"
    LLM_INPUT_PRIVACY_BLOCKED = "LLM_INPUT_PRIVACY_BLOCKED"
    REVIEW_REASON_REQUIRED = "REVIEW_REASON_REQUIRED"
    GATE_UNAPPROVED = "GATE_UNAPPROVED"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"


@dataclass(frozen=True, slots=True)
class DAError:
    """Safe error data suitable for a DA portal response or API boundary."""

    code: ErrorCode
    message: str
    action: str
    entity_id: UUID | str | None = None
    correlation_id: UUID | str | None = None
    audit_event_id: UUID | str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return a JSON-compatible error projection without diagnostics or secrets."""
        return {
            "code": self.code.value,
            "message": self.message,
            "action": self.action,
            "entity_id": str(self.entity_id) if self.entity_id is not None else None,
            "correlation_id": (
                str(self.correlation_id) if self.correlation_id is not None else None
            ),
            "audit_event_id": (
                str(self.audit_event_id) if self.audit_event_id is not None else None
            ),
        }


class MC03Error(Exception):
    """Base class for expected MC03 domain and persistence errors."""

    code = ErrorCode.SQLITE_WRITE_FAILURE


class ImmutableMutationError(MC03Error):
    """Raised when an append-only fact or completed artifact is changed."""

    code = ErrorCode.IMMUTABLE_UPDATE_REJECTED

    def __init__(self, entity_type: str, entity_id: str | None = None) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        suffix = f" {entity_id}" if entity_id else ""
        super().__init__(f"immutable entity cannot be changed: {entity_type}{suffix}")

    def to_da_error(self, *, correlation_id: UUID | str | None = None) -> DAError:
        """Create a safe DA-visible contract for the rejected operation."""
        return DAError(
            code=self.code,
            message="The requested evidence is immutable and was not changed.",
            action="Create a new linked version or reprocess outcome instead.",
            entity_id=self.entity_id,
            correlation_id=correlation_id,
        )


class AuditPayloadError(MC03Error, ValueError):
    """Raised when an audit payload contains a prohibited field or value."""

    code = ErrorCode.AUDIT_APPEND_FAILED


class PersistenceError(MC03Error):
    """Raised when a persistence operation cannot safely complete."""


# ``Any`` is intentionally kept as an import-compatible alias for integrations that
# previously used the error contract as a generic exception payload. New code should
# use ``DAError`` and typed fields above.
ErrorDetails = dict[str, Any]

__all__ = [
    "AuditPayloadError",
    "DAError",
    "ErrorCode",
    "ErrorDetails",
    "ImmutableMutationError",
    "MC03Error",
    "PersistenceError",
]
