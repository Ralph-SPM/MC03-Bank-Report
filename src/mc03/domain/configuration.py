"""Typed Campaign_Configuration models, canonicalization, and hashing.

This module defines the validated, schema-bound representation of a Campaign
configuration. It intentionally rejects unknown fields and unrepresentable
behavior so that unapproved rules cannot be smuggled into the shared
``Report_Processor`` through arbitrary extension keys.

Canonicalization produces deterministic UTF-8 JSON bytes with sorted object
keys; ``Config_Hash`` is the SHA-256 of exactly those bytes. The canonical
bytes, not the original YAML comments or spacing, are the source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mc03.domain.identifiers import ConfigHash

SCHEMA_VERSION: Literal["2020-12-mc03-1"] = "2020-12-mc03-1"

_NonBlank = Annotated[str, Field(min_length=1)]

type ConfigScalar = None | bool | int | float | str


class RedactionMode(StrEnum):
    """Approved LLM redaction modes; exactly one may be selected per capability."""

    RAW = "raw"
    TEXT_REDACTED = "text-redacted"


class EncryptionMode(StrEnum):
    """Archive encryption modes; ZipCrypto is compatibility-only."""

    DISABLED = "disabled"
    AES_256 = "aes_256"
    ZIPCRYPTO = "zipcrypto"


class GateState(StrEnum):
    """Production-gate evidence states; configuration cannot self-approve."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class _Frozen(BaseModel):
    """Base for immutable, unknown-field-rejecting configuration models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSetting(_Frozen):
    """One allowed source adapter and its metadata/coverage contract."""

    adapter: _NonBlank
    timezone: _NonBlank
    coverage_required: bool = False
    authorization_gate: str | None = None


class ExtractionSetting(_Frozen):
    """Extraction policy: enabled state, schema, redaction, and constraints."""

    enabled: bool = False
    result_schema_id: str | None = None
    redaction_mode: RedactionMode | None = None
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    allowed_context: tuple[str, ...] = ()
    forbidden_output_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_schema_when_enabled(self) -> ExtractionSetting:
        if self.enabled and (self.result_schema_id is None or self.redaction_mode is None):
            raise ValueError(
                "enabled extraction requires result_schema_id and redaction_mode"
            )
        return self


class ValidationRule(_Frozen):
    """One generic, configuration-driven validation/transformation rule."""

    rule_id: _NonBlank
    kind: _NonBlank
    parameters: Mapping[str, ConfigScalar | tuple[ConfigScalar, ...]] = Field(
        default_factory=dict
    )


class RuleSettings(_Frozen):
    """Vocabulary, rankings, tie-breaks, exclusions, and banned terms."""

    vocabulary: tuple[str, ...] = ()
    primary_ranking: tuple[str, ...] = ()
    secondary_ranking: tuple[str, ...] = ()
    tie_break: str | None = None
    exclusions: tuple[str, ...] = ()
    banned_terms: tuple[str, ...] = ()
    review_only_fallback: bool = True


class ExportSetting(_Frozen):
    """Full-export policy state and eligible dispositions."""

    full_export_policy_approved: bool = False
    eligible_dispositions: tuple[str, ...] = ()


class TemplateSetting(_Frozen):
    """Fixed template identity/version and approved mapping regions."""

    template_id: _NonBlank
    template_version: _NonBlank
    output_mapping_regions: tuple[str, ...] = ()
    template_gate: str | None = None


class ArchiveSetting(_Frozen):
    """Archive policy; no secret values ever appear in configuration."""

    encryption_mode: EncryptionMode = EncryptionMode.DISABLED
    password_format_id: str | None = None
    recipient_compatibility_gate: str | None = None

    @model_validator(mode="after")
    def _require_password_format_when_encrypted(self) -> ArchiveSetting:
        if self.encryption_mode is not EncryptionMode.DISABLED and not self.password_format_id:
            raise ValueError("encrypted archive requires password_format_id")
        return self


class ProductionGateReference(_Frozen):
    """Capability-scoped reference to externally owned gate evidence."""

    gate_id: _NonBlank
    scope: _NonBlank
    state: GateState = GateState.PENDING


class CampaignConfiguration(_Frozen):
    """Validated, canonicalizable Campaign configuration.

    Every campaign is processed by the same pipeline; campaign identity is data
    in this validated configuration, never a processing branch.
    """

    schema_version: Literal["2020-12-mc03-1"] = SCHEMA_VERSION
    campaign_code: _NonBlank
    version: _NonBlank
    sources: tuple[SourceSetting, ...] = ()
    extraction: ExtractionSetting = Field(default_factory=ExtractionSetting)
    rules: RuleSettings = Field(default_factory=RuleSettings)
    validation: tuple[ValidationRule, ...] = ()
    export: ExportSetting = Field(default_factory=ExportSetting)
    templates: tuple[TemplateSetting, ...] = ()
    archive: ArchiveSetting = Field(default_factory=ArchiveSetting)
    production_gates: tuple[ProductionGateReference, ...] = ()

    @field_validator("campaign_code", "version")
    @classmethod
    def _strip_identity(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def canonical_bytes(self) -> bytes:
        """Return deterministic canonical UTF-8 JSON bytes for this configuration."""
        return canonicalize(self.model_dump(mode="json"))

    def config_hash(self) -> ConfigHash:
        """Return the SHA-256 of the exact canonical bytes."""
        return ConfigHash(sha256(self.canonical_bytes()).hexdigest())


def _normalize(value: object) -> object:
    """Recursively normalize a JSON-compatible value for canonical output."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonicalize(data: Mapping[str, object]) -> bytes:
    """Serialize a validated mapping to deterministic canonical UTF-8 JSON bytes.

    Object keys are sorted, separators are compact, and non-ASCII characters are
    preserved so semantically equivalent configurations produce identical bytes
    and therefore identical ``Config_Hash`` values.
    """
    normalized = _normalize(dict(data))
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


def compute_config_hash(canonical_bytes: bytes) -> ConfigHash:
    """Return the SHA-256 hex digest of the exact canonical bytes."""
    return ConfigHash(sha256(canonical_bytes).hexdigest())


__all__ = [
    "SCHEMA_VERSION",
    "ArchiveSetting",
    "CampaignConfiguration",
    "ConfigScalar",
    "EncryptionMode",
    "ExportSetting",
    "ExtractionSetting",
    "GateState",
    "ProductionGateReference",
    "RedactionMode",
    "RuleSettings",
    "SourceSetting",
    "TemplateSetting",
    "ValidationRule",
    "canonicalize",
    "compute_config_hash",
]
