"""Provider-neutral, privacy-minimized remark extraction boundary.

The RCBC demo only permits an LLM to extract the four structured remark
fields.  This module owns the transport-neutral request/response contract and
its safety checks; it deliberately does not classify contacts, select an RFD,
or assign a pipeline disposition.  Those are deterministic domain concerns
owned by later stages of the pipeline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIDENCE_THRESHOLD = 0.70
"""Minimum confidence accepted by the demo extraction boundary."""

EXTRACTION_FIELDS: tuple[str, ...] = (
    "type_of_rfd",
    "rfd",
    "detailed_rfd",
    "remarks",
)


class ExtractionRequest(BaseModel):
    """Allow-listed payload sent to an extraction provider.

    The request has no account, CH-code, borrower-name, source-file, or source
    row fields.  ``context`` is limited to caller-supplied non-identifying
    vocabulary.  ``extra='forbid'`` is intentional: adding a PII field to the
    request is an explicit validation failure rather than an ignored field.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,
    )

    remark_text: str = Field(min_length=1)
    context: tuple[str, ...] = ()

    @field_validator("remark_text")
    @classmethod
    def _require_remark_text(cls, value: str) -> str:
        """Reject an empty or whitespace-only extraction request."""
        if not value.strip():
            raise ValueError("remark_text must not be blank")
        return value

    @field_validator("context")
    @classmethod
    def _validate_context(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject context values that name a structured identifier field."""
        for item in value:
            if not item.strip():
                raise ValueError("context values must not be blank")
            if _IDENTIFIER_FIELD_PATTERN.search(item):
                raise ValueError("context must not contain identifier fields")
        return value

    @property
    def text(self) -> str:
        """Return the remark text under the shorter provider-facing name."""
        return self.remark_text

    def to_payload(self) -> dict[str, object]:
        """Return the exact allow-listed provider payload.

        Empty context is omitted so the provider receives no accidental
        metadata field.  The method is the canonical serialization boundary;
        callers must not serialize arbitrary source or settings objects.
        """
        payload: dict[str, object] = {"remark_text": self.remark_text}
        if self.context:
            payload["context"] = list(self.context)
        return payload

    def __getitem__(self, key: str) -> object:
        """Allow simple mapping-style access for provider adapters."""
        return self.to_payload()[key]

    def keys(self) -> tuple[str, ...]:
        """Return the names present in the allow-listed payload."""
        return tuple(self.to_payload())

    def items(self) -> tuple[tuple[str, object], ...]:
        """Return the items present in the allow-listed payload."""
        return tuple(self.to_payload().items())


class ExtractionProvider(Protocol):
    """Structural provider contract used by :class:`ExtractionGateway`.

    Concrete SDKs are deliberately not imported here.  An adapter may expose
    ``extract(request)`` or callers may provide a callable with the same
    signature; both receive only :class:`ExtractionRequest`.
    """

    def extract(self, request: ExtractionRequest) -> object:
        """Return a JSON object matching the approved extraction schema."""
        raise NotImplementedError


class ExtractionResult(BaseModel):
    """Validated extraction values and non-business safety status.

    ``schema_valid`` and ``confidence_valid`` describe the gateway checks only.
    The gateway never turns these flags into a disposition or review decision;
    the remark extraction/orchestration layer owns that business routing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type_of_rfd: str | None = None
    rfd: str | None = None
    detailed_rfd: str | None = None
    remarks: str | None = None
    confidence: float | None = None
    schema_valid: bool = False
    confidence_valid: bool = False
    validation_errors: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        """Return whether the response passed both gateway safety checks."""
        return self.schema_valid and self.confidence_valid

    @property
    def valid(self) -> bool:
        """Compatibility alias for :attr:`is_usable`."""
        return self.is_usable

    @property
    def values(self) -> dict[str, str | None]:
        """Return only the four extracted business fields, without status data."""
        return {
            "type_of_rfd": self.type_of_rfd,
            "rfd": self.rfd,
            "detailed_rfd": self.detailed_rfd,
            "remarks": self.remarks,
        }


# The response name is useful at integration boundaries where the caller does
# not need to distinguish the internal schema model from the public result.
ExtractionResponse = ExtractionResult


class ExtractionGateway:
    """Provider-neutral gateway for PII-minimized structured extraction."""

    def __init__(
        self,
        provider: Callable[[ExtractionRequest], object] | ExtractionProvider | None = None,
        *,
        context: Sequence[str] = (),
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ) -> None:
        """Create a gateway around an injected provider adapter.

        A provider is injected rather than selected by this class, keeping
        provider SDKs and provider-specific behavior outside the gateway.  The
        demo threshold is fixed at ``0.70`` so a caller cannot silently weaken
        the uncertainty boundary.
        """
        if confidence_threshold != CONFIDENCE_THRESHOLD:
            raise ValueError(
                f"confidence_threshold must equal {CONFIDENCE_THRESHOLD:.2f} for the RCBC demo"
            )
        self._provider = provider if provider is not None else _UnavailableProvider()
        self._context = _normalize_context(context)
        self._confidence_threshold = CONFIDENCE_THRESHOLD

    @classmethod
    def from_settings(
        cls,
        settings: object,
        *,
        provider: Callable[[ExtractionRequest], object] | ExtractionProvider | None = None,
    ) -> ExtractionGateway:
        """Build a gateway from settings without serializing settings to a provider.

        ``RuntimeSettings`` intentionally contains no provider SDK.  Deployments
        that have an adapter may expose it as ``extraction_provider`` or
        ``llm_provider``; tests and application wiring can inject ``provider``
        directly.  If neither is present, the returned gateway remains safe and
        reports a provider-unavailable result when invoked instead of making a
        network call or selecting a provider itself.
        """
        selected_provider = provider or _provider_from_settings(settings)
        configured_context = getattr(settings, "extraction_context", ())
        if configured_context is None:
            configured_context = ()
        if isinstance(configured_context, str):
            configured_context = (configured_context,)
        if not isinstance(configured_context, Sequence):
            raise TypeError("settings.extraction_context must be a sequence of strings")
        return cls(
            selected_provider,
            context=cast(Sequence[str], configured_context),
        )

    @property
    def confidence_threshold(self) -> float:
        """Return the fixed minimum confidence accepted by this gateway."""
        return self._confidence_threshold

    @property
    def context(self) -> tuple[str, ...]:
        """Return the non-identifying context copied into each request."""
        return self._context

    def build_request(
        self,
        remark_text: str,
        *,
        context: Sequence[str] = (),
        sensitive_values: Sequence[str] = (),
    ) -> ExtractionRequest:
        """Construct an allow-listed request and locally redact known values.

        ``sensitive_values`` is an optional caller-side redaction aid for values
        such as a borrower name, account number, or CH code.  It is never put
        in the request object.  The gateway also removes common labelled
        account/CH/name forms as a second, conservative minimization guard.
        """
        if not isinstance(remark_text, str):
            raise TypeError("remark_text must be a string")
        caller_context = _normalize_context(context)
        all_context = self._context + caller_context
        safe_values = _normalize_sensitive_values(sensitive_values)
        minimized_text = _minimize_text(remark_text, safe_values)
        minimized_context = tuple(_minimize_text(item, safe_values) for item in all_context)
        return ExtractionRequest(
            remark_text=minimized_text,
            context=minimized_context,
        )

    def extract(
        self,
        remark_text: str | ExtractionRequest,
        *,
        context: Sequence[str] = (),
        sensitive_values: Sequence[str] = (),
    ) -> ExtractionResult:
        """Invoke the provider and validate its structured extraction response.

        The method returns null extracted fields for malformed/schema-invalid or
        below-threshold responses.  It does not assign a disposition, select a
        hierarchy winner, or otherwise make a business decision.
        """
        if isinstance(remark_text, ExtractionRequest):
            request = self.build_request(
                remark_text.remark_text,
                context=remark_text.context,
                sensitive_values=sensitive_values,
            )
        else:
            request = self.build_request(
                remark_text,
                context=context,
                sensitive_values=sensitive_values,
            )

        try:
            response = self._invoke_provider(request)
        except Exception as exc:  # Provider failures are safe extraction failures.
            return self._invalid_result(
                (f"provider invocation failed: {type(exc).__name__}",)
            )
        return self.validate_response(response)

    def validate_response(self, response: object) -> ExtractionResult:
        """Validate one provider response without invoking or selecting a provider."""
        try:
            payload = _response_mapping(response)
            parsed = _ProviderExtraction.model_validate(payload)
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                errors = _safe_validation_errors(exc)
            elif isinstance(exc, json.JSONDecodeError):
                errors = ("provider response is not valid JSON",)
            else:
                errors = ("provider response must be a JSON object",)
            return self._invalid_result(errors)

        if parsed.confidence < self._confidence_threshold:
            return ExtractionResult(
                confidence=parsed.confidence,
                schema_valid=True,
                confidence_valid=False,
                validation_errors=(
                    "confidence is below the minimum threshold of 0.70",
                ),
            )

        return ExtractionResult(
            type_of_rfd=parsed.type_of_rfd,
            rfd=parsed.rfd,
            detailed_rfd=parsed.detailed_rfd,
            remarks=parsed.remarks,
            confidence=parsed.confidence,
            schema_valid=True,
            confidence_valid=True,
        )

    def _invoke_provider(self, request: ExtractionRequest) -> object:
        """Invoke either the structural adapter method or a compatible callable."""
        provider = self._provider
        extract_method = getattr(provider, "extract", None)
        if callable(extract_method):
            method = cast(Callable[[ExtractionRequest], object], extract_method)
            return method(request)
        callable_provider = cast(Callable[[ExtractionRequest], object], provider)
        return callable_provider(request)

    @staticmethod
    def _invalid_result(errors: tuple[str, ...]) -> ExtractionResult:
        """Build a null-valued result containing only safe validation details."""
        return ExtractionResult(
            schema_valid=False,
            confidence_valid=False,
            validation_errors=errors,
        )


class _UnavailableProvider(ExtractionProvider):
    """Safe default used when application wiring has not supplied an adapter."""

    def extract(self, request: ExtractionRequest) -> object:
        """Fail closed without making a network request."""
        del request
        raise RuntimeError("no extraction provider is configured")


class _ProviderExtraction(BaseModel):
    """Strict schema accepted from an injected provider."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )

    type_of_rfd: str | None
    rfd: str | None
    detailed_rfd: str | None
    remarks: str | None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator(*EXTRACTION_FIELDS, mode="before")
    @classmethod
    def _empty_strings_are_null(cls, value: object) -> object:
        """Normalize provider-empty field values to null without inventing data."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


_IDENTIFIER_FIELD_PATTERN = re.compile(
    r"(?i)\b(?:account\s*(?:number|no\.?|#)|acct\s*(?:number|no\.?|#)|"
    r"ch\s*(?:code|#)|borrower\s+name|customer\s+name|client\s+name)\b"
)
_ACCOUNT_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:account\s*(?:number|no\.?|#)|acct\s*(?:number|no\.?|#))"
    r"\s*[:#=-]?\s*[A-Z0-9][A-Z0-9/-]{3,}\b"
)
_CH_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:b[ck]al[-\s]?[A-Z0-9-]+|ch\s*(?:code|#)?\s*[:#=-]?\s*"
    r"[A-Z0-9][A-Z0-9/-]{2,})\b"
)
_NAMED_PERSON_PATTERN = re.compile(
    r"(?i)\b(?:borrower(?:\s+name)?|customer(?:\s+name)?|client(?:\s+name)?)"
    r"\s*[:=-]\s*[^\n|;,]+"
)


def _normalize_context(context: Sequence[str]) -> tuple[str, ...]:
    """Validate and copy non-identifying context values."""
    if isinstance(context, str):
        raise TypeError("context must be a sequence of strings, not a string")
    normalized: list[str] = []
    for item in context:
        if not isinstance(item, str):
            raise TypeError("context values must be strings")
        stripped = item.strip()
        if not stripped:
            raise ValueError("context values must not be blank")
        if _IDENTIFIER_FIELD_PATTERN.search(stripped):
            raise ValueError("context must not contain identifier fields")
        normalized.append(stripped)
    return tuple(normalized)


def _normalize_sensitive_values(values: Sequence[str]) -> tuple[str, ...]:
    """Return nonblank redaction values in longest-first order."""
    if isinstance(values, str):
        raise TypeError("sensitive_values must be a sequence of strings, not a string")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("sensitive_values values must be strings")
        if value:
            normalized.add(value)
    return tuple(sorted(normalized, key=len, reverse=True))


def _minimize_text(text: str, sensitive_values: Sequence[str]) -> str:
    """Remove known and common labelled identifiers before provider transport."""
    minimized = text
    for value in sensitive_values:
        minimized = minimized.replace(value, "[REDACTED]")
    minimized = _ACCOUNT_VALUE_PATTERN.sub("[REDACTED]", minimized)
    minimized = _CH_VALUE_PATTERN.sub("[REDACTED]", minimized)
    minimized = _NAMED_PERSON_PATTERN.sub("[REDACTED]", minimized)
    return minimized


def _provider_from_settings(settings: object) -> (
    Callable[[ExtractionRequest], object] | ExtractionProvider | None
):
    """Read only an already-built provider adapter from settings-like objects."""
    for attribute in ("extraction_provider", "llm_provider", "provider"):
        candidate = getattr(settings, attribute, None)
        if callable(candidate) or callable(getattr(candidate, "extract", None)):
            return cast(
                Callable[[ExtractionRequest], object] | ExtractionProvider,
                candidate,
            )
    return None


def _response_mapping(response: object) -> Mapping[str, object]:
    """Convert supported provider response forms into a JSON-object mapping."""
    if isinstance(response, BaseModel):
        dumped: object = response.model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return cast(Mapping[str, object], dumped)
        raise TypeError("provider response must be a JSON object")
    if isinstance(response, Mapping):
        return response
    if isinstance(response, bytes):
        decoded = response.decode("utf-8")
        parsed: object = cast(object, json.loads(decoded))
        return _response_mapping(parsed)
    if isinstance(response, str):
        parsed = cast(object, json.loads(response))
        return _response_mapping(parsed)
    raise TypeError("provider response must be a JSON object")


def _safe_validation_errors(error: ValidationError) -> tuple[str, ...]:
    """Expose schema locations/types without echoing provider values or PII."""
    messages: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail.get("loc", ())) or "<root>"
        error_type = str(detail.get("type", "invalid"))
        messages.append(f"{location}: {error_type}")
    return tuple(messages)


__all__ = [
    "CONFIDENCE_THRESHOLD",
    "EXTRACTION_FIELDS",
    "ExtractionGateway",
    "ExtractionProvider",
    "ExtractionRequest",
    "ExtractionResponse",
    "ExtractionResult",
]
