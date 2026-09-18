"""Safe, JSON-compatible audit payload construction."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from mc03.domain.errors import AuditPayloadError

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type SafeAuditPayload = dict[str, JsonValue]

_FORBIDDEN_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "session",
    "cookie",
    "authorization",
    "credential",
    "access_token",
    "refresh_token",
    "bearer",
    "raw_payload",
    "llm_input",
    "prompt",
    "remark_text",
    "account_number",
    "phone_number",
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|session[_ -]?id|authorization|bearer)\s*[:=]"
)


def _check_key(key: str) -> str:
    normalized = key.casefold().replace("-", "_")
    if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
        raise AuditPayloadError(f"prohibited audit field: {key}")
    return key


def _convert(value: object, *, key: str | None = None) -> JsonValue:
    if key is not None:
        _check_key(key)
    if value is None or isinstance(value, bool | int | float):
        if isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))
        ):
            raise AuditPayloadError("non-finite numbers are not valid audit values")
        return value
    if isinstance(value, str):
        if _SECRET_VALUE_PATTERN.search(value):
            raise AuditPayloadError("secret-like value is not permitted in audit payloads")
        return value
    if isinstance(value, UUID | datetime | date | StrEnum):
        return value.isoformat() if isinstance(value, datetime | date) else str(value)
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise AuditPayloadError("audit object keys must be strings")
            converted[_check_key(nested_key)] = _convert(nested_value, key=nested_key)
        return converted
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | memoryview):
        return [_convert(item) for item in value]
    raise AuditPayloadError(f"unsupported audit value type: {type(value).__name__}")


def build_safe_audit_payload(values: Mapping[str, object]) -> SafeAuditPayload:
    """Validate and normalize an audit mapping without retaining sensitive values.

    The function rejects unsafe fields rather than silently dropping them. Callers
    should pass hashes, identifiers, counts, statuses, and other minimal evidence;
    raw source content, LLM input, credentials, sessions, and archive secrets are
    intentionally outside this contract.
    """
    converted = _convert(values)
    if not isinstance(converted, dict):
        raise AuditPayloadError("audit payload must be a JSON object")
    json.dumps(converted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return converted


def build_lineage_audit_payload(
    values: Mapping[str, object],
    *,
    source_id: UUID | str | None = None,
    run_id: UUID | str | None = None,
    config_hash: str | None = None,
    actor: str | None = None,
    service: str | None = None,
    correlation_id: UUID | str | None = None,
    occurred_at: datetime | None = None,
) -> SafeAuditPayload:
    """Build a safe payload with the standard lineage and operational links."""
    enriched = dict(values)
    links: dict[str, object] = {}
    if source_id is not None:
        links["source_id"] = source_id
    if run_id is not None:
        links["run_id"] = run_id
    if config_hash is not None:
        links["config_hash"] = config_hash
    if actor is not None:
        links["actor"] = actor
    if service is not None:
        links["service"] = service
    if correlation_id is not None:
        links["correlation_id"] = correlation_id
    if occurred_at is not None:
        links["occurred_at"] = occurred_at.astimezone(UTC)
    enriched.update(links)
    return build_safe_audit_payload(enriched)


__all__ = [
    "JsonValue",
    "SafeAuditPayload",
    "build_lineage_audit_payload",
    "build_safe_audit_payload",
]
