"""Focused tests for the RCBC remark extraction pipeline."""

from collections.abc import Mapping

import pytest

from mc03.domain.models import Disposition, ExceptionKind
from mc03.domain.remarks import RemarkExtractionResult, extract_remark, trim_to_200
from mc03.services.extraction_gateway import ExtractionResult


class RecordingGateway:
    """Gateway fake that records calls without involving a provider SDK."""

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def extract(self, remark_text: str) -> ExtractionResult:
        self.calls.append(remark_text)
        return self.result


def _usable_result(**values: str | None) -> ExtractionResult:
    """Build a valid gateway response for the four supported fields."""
    payload: Mapping[str, str | None] = {
        "type_of_rfd": values.get("type_of_rfd", "Borrower Refused"),
        "rfd": values.get("rfd", "Medical"),
        "detailed_rfd": values.get("detailed_rfd", "Delayed treatment"),
        "remarks": values.get("remarks", "Contacted representative"),
    }
    return ExtractionResult(
        **payload,
        confidence=0.90,
        schema_valid=True,
        confidence_valid=True,
    )


def test_full_pipe_tags_are_parsed_without_gateway_call() -> None:
    gateway = RecordingGateway(_usable_result())
    text = (
        "TYPE OF RFD: Borrower Refused | RFD: Medical | "
        "DETAILED RFD: Delayed treatment | REMARKS: Contacted representative"
    )

    result = extract_remark(text, gateway)

    assert isinstance(result, RemarkExtractionResult)
    assert result.type_of_rfd == "Borrower Refused"
    assert result.rfd == "Medical"
    assert result.detailed_rfd == "Delayed treatment"
    assert result.remarks == "Contacted representative"
    assert result.disposition is Disposition.CLEAN
    assert gateway.calls == []


def test_label_value_pipe_tags_support_the_documented_pipe_shape() -> None:
    gateway = RecordingGateway(_usable_result())
    text = (
        "TYPE OF RFD | Borrower Refused | RFD | Medical | "
        "DETAILED RFD | Delayed treatment | REMARKS | Contacted representative"
    )

    result = extract_remark(text, gateway)

    assert result.values == {
        "type_of_rfd": "Borrower Refused",
        "rfd": "Medical",
        "detailed_rfd": "Delayed treatment",
        "remarks": "Contacted representative",
    }
    assert gateway.calls == []


def test_partial_or_empty_pipe_tags_are_null_filled_without_gateway_call() -> None:
    gateway = RecordingGateway(_usable_result())
    text = "TYPE OF RFD: Borrower Refused | RFD: | REMARKS: Contacted representative"

    result = extract_remark(text, gateway)

    assert result.type_of_rfd == "Borrower Refused"
    assert result.rfd is None
    assert result.detailed_rfd is None
    assert result.remarks == "Contacted representative"
    assert result.disposition is Disposition.CLEAN
    assert gateway.calls == []


def test_free_text_uses_gateway_fallback() -> None:
    gateway = RecordingGateway(_usable_result())
    text = "Spoke with the representative and arranged a follow-up call."

    result = extract_remark(text, gateway)

    assert result.type_of_rfd == "Borrower Refused"
    assert result.rfd == "Medical"
    assert result.sanitized_remark == text
    assert result.disposition is Disposition.CLEAN
    assert gateway.calls == [text]


@pytest.mark.parametrize(
    "gateway_result",
    [
        ExtractionResult(schema_valid=False, validation_errors=("remarks: missing",)),
        ExtractionResult(
            confidence=0.69,
            schema_valid=True,
            confidence_valid=False,
            validation_errors=("confidence is below the minimum threshold of 0.70",),
        ),
    ],
)
def test_invalid_or_low_confidence_gateway_output_routes_to_review(
    gateway_result: ExtractionResult,
) -> None:
    gateway = RecordingGateway(gateway_result)
    text = "Free text that must remain available to the reviewer."

    result = extract_remark(text, gateway)

    assert result.values == {
        "type_of_rfd": None,
        "rfd": None,
        "detailed_rfd": None,
        "remarks": None,
    }
    assert result.sanitized_remark == text
    assert result.disposition is Disposition.REVIEW_ONLY
    assert result.exception_kinds == (ExceptionKind.EXTRACTION_FAILED,)
    assert result.extraction_failed
    assert result.review_only
    assert gateway.calls == [text]


def test_trim_to_200_preserves_both_fields_for_a_fitting_remark() -> None:
    remark, fits = trim_to_200("Jane Doe", "Promised to pay on Friday")

    assert remark == "Jane Doe - Promised to pay on Friday"
    assert fits


def test_trim_to_200_removes_only_non_substantive_edges_when_needed() -> None:
    contact = " | " + ("C" * 95) + " | "
    statement = " ; " + ("S" * 100) + " ; "

    remark, fits = trim_to_200(contact, statement)

    assert fits
    assert len(remark) <= 200
    assert ("C" * 95) in remark
    assert ("S" * 100) in remark
    assert " | " not in remark
    assert " ; " not in remark


def test_trim_to_200_reports_overflow_without_truncating_required_fields() -> None:
    contact = "Contact " + ("C" * 100)
    statement = "Statement " + ("S" * 100)

    remark, fits = trim_to_200(contact, statement)

    assert not fits
    assert len(remark) > 200
    assert contact in remark
    assert statement in remark


@pytest.mark.parametrize(
    ("contact_person", "statement"),
    [("", "A statement"), ("A contact", ""), (None, "A statement")],
)
def test_trim_to_200_reports_missing_required_content(
    contact_person: str | None,
    statement: str | None,
) -> None:
    assert trim_to_200(contact_person, statement) == ("", False)
