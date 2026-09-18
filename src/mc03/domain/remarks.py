"""Deterministic remark extraction with a safe LLM fallback.

The RCBC remark stage is intentionally conservative: structured pipe tags are
parsed locally, while free text is delegated to the provider-neutral
:class:`~mc03.services.extraction_gateway.ExtractionGateway`.  The gateway
already owns request minimization and response validation; this module maps its
safe/unsafe result into the demo's domain disposition contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mc03.domain.models import Disposition, ExceptionKind, RemarkFields
from mc03.services.extraction_gateway import (
    CONFIDENCE_THRESHOLD,
    ExtractionGateway,
    ExtractionResult,
)

# The documented fast-path shape.  ``extract_remark`` uses a token parser so
# absent tags can be null-filled, but this public pattern remains available for
# callers that need to recognize the complete design form directly.
PIPE_PATTERN = re.compile(
    r"TYPE OF RFD\s*:\s*(?P<type>.*?)\|"
    r"\s*RFD\s*:\s*(?P<rfd>.*?)\|"
    r"\s*DETAILED RFD\s*:\s*(?P<detail>.*?)\|"
    r"\s*REMARKS\s*:\s*(?P<remarks>.*)",
    re.IGNORECASE | re.DOTALL,
)

_PIPE_LABELS: tuple[str, ...] = (
    "type_of_rfd",
    "rfd",
    "detailed_rfd",
    "remarks",
)
_LABEL_TO_INDEX = {label: index for index, label in enumerate(_PIPE_LABELS)}
_LABEL_DISPLAY_NAMES = {
    "type_of_rfd": "TYPE OF RFD",
    "rfd": "RFD",
    "detailed_rfd": "DETAILED RFD",
    "remarks": "REMARKS",
}
_TAG_WITH_VALUE_PATTERN = re.compile(
    r"^\s*(?P<label>TYPE\s+OF\s+RFD|DETAILED\s+RFD|RFD|REMARKS)"
    r"\s*[:=]\s*(?P<value>.*?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MAX_REMARK_LENGTH = 200
_REMARK_SEPARATOR = " - "
_NON_SUBSTANTIVE_EDGE_CHARS = " \t\r\n|,;:/\\-"


class RemarkGateway(Protocol):
    """Minimal extraction boundary consumed by :func:`extract_remark`.

    :class:`ExtractionGateway` satisfies this protocol.  Keeping the domain
    contract structural also lets tests and application wiring provide a
    provider-neutral gateway without importing a concrete provider SDK.
    """

    def extract(self, remark_text: str) -> ExtractionResult:
        """Extract the four structured remark fields from free text."""
        ...


@dataclass(frozen=True, slots=True, eq=False)
class RemarkExtractionResult(RemarkFields):
    """Remark fields plus the pipeline routing decision.

    The class subclasses :class:`RemarkFields`, so existing consumers can read
    the seven structured fields directly.  The additional metadata preserves
    the sanitized source text and makes safe incompleteness explicit for the
    orchestrator: malformed or uncertain provider output is never promoted to
    a clean row.
    """

    sanitized_remark: str
    disposition: Disposition = Disposition.CLEAN
    exception_kinds: tuple[ExceptionKind, ...] = ()
    validation_errors: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        """Remain comparable with the design's base ``RemarkFields`` value."""
        if isinstance(other, RemarkExtractionResult):
            return (
                self.fields == other.fields
                and self.sanitized_remark == other.sanitized_remark
                and self.disposition is other.disposition
                and self.exception_kinds == other.exception_kinds
                and self.validation_errors == other.validation_errors
            )
        if isinstance(other, RemarkFields):
            return self.fields == other
        return NotImplemented

    @property
    def fields(self) -> RemarkFields:
        """Return the structured fields as the base domain value object."""
        return RemarkFields(
            type_of_rfd=self.type_of_rfd,
            rfd=self.rfd,
            detailed_rfd=self.detailed_rfd,
            remarks=self.remarks,
            contact_person=self.contact_person,
            statement=self.statement,
            source_timestamp=self.source_timestamp,
        )

    @property
    def values(self) -> dict[str, str | None]:
        """Return the four extracted fields in gateway-compatible form."""
        return {
            "type_of_rfd": self.type_of_rfd,
            "rfd": self.rfd,
            "detailed_rfd": self.detailed_rfd,
            "remarks": self.remarks,
        }

    @property
    def original_sanitized_remark(self) -> str:
        """Return the unchanged sanitized text retained for review."""
        return self.sanitized_remark

    @property
    def review_only(self) -> bool:
        """Return whether this extraction must be reviewed by the DA."""
        return self.disposition is Disposition.REVIEW_ONLY

    @property
    def extraction_failed(self) -> bool:
        """Return whether the result carries an extraction failure reason."""
        return ExceptionKind.EXTRACTION_FAILED in self.exception_kinds

    @property
    def kind(self) -> ExceptionKind | None:
        """Return the first Review_Only reason, when one is present."""
        return self.exception_kinds[0] if self.exception_kinds else None


def _canonical_label(value: str) -> str | None:
    """Convert a human-readable pipe tag into its canonical field name."""
    normalized = _WHITESPACE_PATTERN.sub(" ", value.strip()).casefold()
    for field_name, display_name in _LABEL_DISPLAY_NAMES.items():
        if normalized == display_name.casefold():
            return field_name
    return None


def _nullable_segment(value: str | None) -> str | None:
    """Strip tag padding and represent empty segments as null."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _ordered_values(values: Mapping[str, str | None]) -> dict[str, str | None]:
    """Return all four fields in stable order, null-filling absent tags."""
    return {field_name: values.get(field_name) for field_name in _PIPE_LABELS}


def _parse_colon_tags(text: str) -> dict[str, str | None] | None:
    """Parse ``LABEL: value | LABEL: value`` tags, including partial tags."""
    values: dict[str, str | None] = {}
    previous_index = -1
    saw_tag = False

    for part in text.split("|"):
        if not part.strip():
            continue
        match = _TAG_WITH_VALUE_PATTERN.fullmatch(part)
        if match is None:
            return None
        label = _canonical_label(match.group("label"))
        if label is None:
            return None
        label_index = _LABEL_TO_INDEX[label]
        if label_index <= previous_index:
            return None
        values[label] = _nullable_segment(match.group("value"))
        previous_index = label_index
        saw_tag = True

    return _ordered_values(values) if saw_tag else None


def _parse_label_value_tags(text: str) -> dict[str, str | None] | None:
    """Parse ``LABEL | value | LABEL | value`` tags, including empty values."""
    parts = text.split("|")
    values: dict[str, str | None] = {}
    previous_index = -1
    saw_tag = False
    position = 0

    while position < len(parts):
        token = parts[position]
        if not token.strip():
            position += 1
            continue
        label = _canonical_label(token)
        if label is None:
            return None
        label_index = _LABEL_TO_INDEX[label]
        if label_index <= previous_index:
            return None
        previous_index = label_index
        saw_tag = True
        position += 1

        # A following tag means the current segment was omitted/empty.  An
        # ordinary following token is this tag's value, including an empty
        # token produced by consecutive pipes.
        if position < len(parts) and _canonical_label(parts[position]) is None:
            values[label] = _nullable_segment(parts[position])
            position += 1
        else:
            values[label] = None

    return _ordered_values(values) if saw_tag else None


def _parse_pipe_tags(text: str) -> dict[str, str | None] | None:
    """Return parsed values when text is a recognized ordered pipe-tag form."""
    return _parse_colon_tags(text) or _parse_label_value_tags(text)


def _make_result(
    text: str,
    values: Mapping[str, str | None],
    *,
    disposition: Disposition = Disposition.CLEAN,
    exception_kinds: tuple[ExceptionKind, ...] = (),
    validation_errors: tuple[str, ...] = (),
) -> RemarkExtractionResult:
    """Build a complete result while keeping non-schema fields null."""
    normalized = _ordered_values(values)
    return RemarkExtractionResult(
        type_of_rfd=normalized["type_of_rfd"],
        rfd=normalized["rfd"],
        detailed_rfd=normalized["detailed_rfd"],
        remarks=normalized["remarks"],
        contact_person=None,
        statement=None,
        source_timestamp=None,
        sanitized_remark=text,
        disposition=disposition,
        exception_kinds=exception_kinds,
        validation_errors=validation_errors,
    )


def _failed_result(text: str, errors: tuple[str, ...] = ()) -> RemarkExtractionResult:
    """Build the fail-closed null-field result for an unsafe extraction."""
    return _make_result(
        text,
        {},
        disposition=Disposition.REVIEW_ONLY,
        exception_kinds=(ExceptionKind.EXTRACTION_FAILED,),
        validation_errors=errors,
    )


def _gateway_result(text: str, gateway: RemarkGateway) -> RemarkExtractionResult:
    """Invoke and interpret the already-validated gateway response."""
    try:
        result = gateway.extract(text)
    except Exception as exc:  # Provider/gateway failures are safe review outcomes.
        return _failed_result(text, (f"gateway invocation failed: {type(exc).__name__}",))

    if not isinstance(result, ExtractionResult):
        return _failed_result(text, ("gateway result has an invalid schema",))

    confidence = result.confidence
    if (
        not result.schema_valid
        or not result.confidence_valid
        or not result.is_usable
        or confidence is None
        or confidence < CONFIDENCE_THRESHOLD
    ):
        return _failed_result(text, result.validation_errors)

    return _make_result(text, result.values)


def extract_remark(text: str, gateway: ExtractionGateway | RemarkGateway) -> RemarkExtractionResult:
    """Extract structured remark fields with a deterministic fast path.

    ``text`` is expected to be the already-sanitized remark.  Ordered pipe tags
    are parsed locally and never invoke ``gateway``.  Free text is sent through
    the provider-neutral gateway, which performs PII minimization and strict
    schema/confidence validation.  Any invalid or below-``0.70`` response is
    represented by null structured fields, the unchanged sanitized text, and a
    ``Review_Only``/``EXTRACTION_FAILED`` routing result.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    parsed = _parse_pipe_tags(text)
    if parsed is not None:
        return _make_result(text, parsed)
    return _gateway_result(text, gateway)


def _trim_non_substantive_edges(value: str) -> str:
    """Remove only edge whitespace and separator characters from a field."""
    return value.strip(_NON_SUBSTANTIVE_EDGE_CHARS)


def _remark_candidates(contact_person: str, statement: str) -> tuple[str, ...]:
    """Build readable-to-compact joins without changing either field."""
    return (
        f"{contact_person}{_REMARK_SEPARATOR}{statement}",
        f"{contact_person} {statement}",
        f"{contact_person}{statement}",
    )


def _first_fitting_remark(contact_person: str, statement: str) -> str | None:
    """Return the first joined representation that fits the field limit."""
    for candidate in _remark_candidates(contact_person, statement):
        if len(candidate) <= _MAX_REMARK_LENGTH:
            return candidate
    return None


def trim_to_200(
    contact_person: str | None,
    statement: str | None,
) -> tuple[str, bool]:
    """Join the required remark fields without silently losing either field.

    The returned boolean is the routing signal: ``True`` means the returned
    remark is safe for the 200-character export field, while ``False`` means
    the caller must route the row to ``Review_Only``.  For an overflow, the
    returned text contains both original fields in full so review retains the
    evidence; it is intentionally not truncated.  Missing or blank required
    fields return ``("", False)`` and should be recorded with
    :attr:`ExceptionKind.MISSING_REMARK_CONTENT` by the caller.

    Leading/trailing whitespace and separator characters are removed only when
    the original join does not fit.  This permits non-substantive formatting to
    be cleaned while preserving complete substantive field values.
    """
    if contact_person is None or statement is None:
        return "", False
    if not isinstance(contact_person, str) or not isinstance(statement, str):
        raise TypeError("contact_person and statement must be strings or None")

    normalized_contact = _trim_non_substantive_edges(contact_person)
    normalized_statement = _trim_non_substantive_edges(statement)
    if not normalized_contact or not normalized_statement:
        return "", False

    original_remark = _first_fitting_remark(contact_person, statement)
    if original_remark is not None:
        return original_remark, True

    normalized_remark = _first_fitting_remark(normalized_contact, normalized_statement)
    if normalized_remark is not None:
        return normalized_remark, True

    # Keep the unmodified fields available to the review path.  The caller
    # uses ``fits=False`` to assign Review_Only and must not export this value
    # as a clean <=200-character remark.
    return f"{contact_person}{_REMARK_SEPARATOR}{statement}", False


__all__ = [
    "PIPE_PATTERN",
    "RemarkExtractionResult",
    "RemarkGateway",
    "extract_remark",
    "trim_to_200",
]
