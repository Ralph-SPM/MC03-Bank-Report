"""Config_Parser and Config_Formatter for Campaign configurations.

The parser accepts UTF-8 YAML or JSON and rejects duplicate mapping keys,
unsafe YAML tags, syntax errors, unsupported schema versions, unknown fields,
invalid types, invalid cross-field combinations, and behavior that cannot be
represented by the approved schema. A schema-valid document becomes a typed
``CampaignConfiguration`` plus deterministic canonical bytes and a
``Config_Hash``.

``parse(format(config))`` must be semantically equivalent for every supported
field and must reproduce the same canonical bytes and hash.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from mc03.domain.configuration import (
    SCHEMA_VERSION,
    CampaignConfiguration,
    compute_config_hash,
)
from mc03.domain.errors import ErrorCode, MC03Error
from mc03.domain.identifiers import ConfigHash


class ConfigParseError(MC03Error):
    """Raised when a Campaign configuration cannot be safely validated."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.CONFIG_INVALID,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail

    def as_finding(self) -> dict[str, str]:
        """Return a safe, DA-visible validation finding without diagnostics."""
        finding = {"code": self.code.value, "message": str(self)}
        if self.detail is not None:
            finding["detail"] = self.detail
        return finding


class _SafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    ``yaml.SafeLoader`` already refuses unknown/unsafe tags such as
    ``!!python/object``. This subclass additionally treats a duplicate mapping
    key as an error rather than silently keeping the last value.
    """


def _construct_mapping_no_duplicates(
    loader: _SafeLoader, node: yaml.MappingNode
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key: object = loader.construct_object(key_node, deep=True)  # type: ignore[no-untyped-call]
        if key in mapping:
            raise ConfigParseError(
                "Configuration contains a duplicate mapping key.",
                detail=f"duplicate key: {key!r}",
            )
        mapping[key] = loader.construct_object(  # type: ignore[no-untyped-call]
            value_node, deep=True
        )
    return mapping


_SafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


@dataclass(frozen=True, slots=True)
class ParsedConfiguration:
    """Result of a successful parse: typed model plus canonical evidence."""

    configuration: CampaignConfiguration
    canonical_bytes: bytes
    config_hash: ConfigHash


def _load_document(raw: str | bytes) -> object:
    """Load a YAML/JSON document with duplicate-key and unsafe-tag protection."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if not text.strip():
        raise ConfigParseError("Configuration document is empty.")
    try:
        # A single YAML document; JSON is a subset of YAML, so this also parses JSON.
        document = yaml.load(text, Loader=_SafeLoader)  # noqa: S506 - custom SafeLoader
    except ConfigParseError:
        raise
    except yaml.YAMLError as exc:
        raise ConfigParseError(
            "Configuration is not valid YAML or JSON.",
            detail=_safe_yaml_detail(exc),
        ) from exc
    if not isinstance(document, dict):
        raise ConfigParseError("Configuration root must be a mapping of fields.")
    return document


def _safe_yaml_detail(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        return f"syntax error at line {mark.line + 1}, column {mark.column + 1}"
    return "syntax error"


def parse(raw: str | bytes) -> ParsedConfiguration:
    """Parse and validate a Campaign configuration into typed canonical evidence.

    Raises ``ConfigParseError`` for any missing, malformed, schema-invalid,
    unknown-field, or unrepresentable configuration so the affected run is
    blocked before processing.
    """
    document = _load_document(raw)
    if isinstance(document, dict):
        declared = document.get("schema_version", SCHEMA_VERSION)
        if declared != SCHEMA_VERSION:
            raise ConfigParseError(
                "Configuration schema version is not supported.",
                code=ErrorCode.CONFIG_UNSUPPORTED_BEHAVIOR,
                detail=f"unsupported schema_version: {declared!r}",
            )
    try:
        configuration = CampaignConfiguration.model_validate(document)
    except ValidationError as exc:
        raise ConfigParseError(
            "Configuration failed schema validation.",
            code=ErrorCode.CONFIG_INVALID,
            detail=_safe_validation_detail(exc),
        ) from exc
    canonical_bytes = configuration.canonical_bytes()
    return ParsedConfiguration(
        configuration=configuration,
        canonical_bytes=canonical_bytes,
        config_hash=compute_config_hash(canonical_bytes),
    )


def _safe_validation_detail(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def format_configuration(configuration: CampaignConfiguration) -> str:
    """Render a validated configuration as human-readable YAML.

    The rendering round-trips: ``parse(format_configuration(config))`` yields a
    semantically equivalent configuration and reproduces the same canonical
    bytes and ``Config_Hash``.
    """
    data = configuration.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=True, allow_unicode=True, default_flow_style=False)


def supported_fields() -> tuple[str, ...]:
    """Return the generic supported configuration field names.

    Field discovery is generic; it does not enumerate campaign-specific
    branches, so a new Campaign is expressed purely as configuration data.
    """
    return tuple(CampaignConfiguration.model_fields.keys())


__all__ = [
    "ConfigParseError",
    "ParsedConfiguration",
    "format_configuration",
    "parse",
    "supported_fields",
]
