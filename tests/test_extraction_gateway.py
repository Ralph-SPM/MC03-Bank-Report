"""Focused tests for the RCBC provider-neutral extraction gateway."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mc03.services.extraction_gateway import (
    CONFIDENCE_THRESHOLD,
    ExtractionGateway,
    ExtractionRequest,
)


class RecordingProvider:
    """Small provider fake that records the typed, minimized request."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.request: ExtractionRequest | None = None

    def extract(self, request: ExtractionRequest) -> object:
        self.request = request
        return self.response


def _valid_response(confidence: float = CONFIDENCE_THRESHOLD) -> dict[str, object]:
    return {
        "type_of_rfd": "Borrower Refused",
        "rfd": "Medical",
        "detailed_rfd": "Delayed treatment",
        "remarks": "Called contact",
        "confidence": confidence,
    }


def test_request_contract_rejects_identifier_fields() -> None:
    """Account, CH-code, and borrower-name fields cannot enter the payload."""
    with pytest.raises(ValidationError):
        ExtractionRequest.model_validate(
            {
                "remark_text": "safe remark",
                "account_number": "123456",
                "ch_code": "CH-123",
                "borrower_name": "Example Person",
            }
        )


def test_gateway_sends_only_minimized_allow_list() -> None:
    """Known identifiers are redacted and never appear in provider fields."""
    provider = RecordingProvider(_valid_response())
    gateway = ExtractionGateway(provider)

    result = gateway.extract(
        "Borrower Name: Example Person; Account No: 12345678; CH Code: CH-123; paid",
        sensitive_values=("Example Person", "12345678", "CH-123"),
    )

    assert result.is_usable
    assert provider.request is not None
    payload = provider.request.to_payload()
    assert set(payload) == {"remark_text"}
    assert "Example Person" not in str(payload)
    assert "12345678" not in str(payload)
    assert "CH-123" not in str(payload)


def test_gateway_from_settings_uses_injected_provider_without_serializing_settings() -> None:
    """Settings construction chooses an adapter but does not send settings data."""
    provider = RecordingProvider(_valid_response())
    settings = SimpleNamespace(
        extraction_provider=provider,
        extraction_context=("RCBC Auto Loan",),
        secret_value="must not be serialized",
    )

    gateway = ExtractionGateway.from_settings(settings)
    result = gateway.extract("free text")

    assert result.is_usable
    assert provider.request is not None
    assert provider.request.to_payload() == {
        "remark_text": "free text",
        "context": ["RCBC Auto Loan"],
    }
    assert "secret_value" not in str(provider.request.to_payload())


def test_confidence_boundary_is_accepted() -> None:
    """The exact 0.70 boundary is valid."""
    result = ExtractionGateway(RecordingProvider(_valid_response(0.70))).extract("free text")

    assert result.confidence == 0.70
    assert result.confidence_valid
    assert result.is_usable
    assert result.rfd == "Medical"


def test_low_confidence_nulls_fields_without_assigning_a_business_disposition() -> None:
    """Below-threshold output is reported as an extraction failure only."""
    result = ExtractionGateway(RecordingProvider(_valid_response(0.69))).extract("free text")

    assert result.schema_valid
    assert not result.confidence_valid
    assert not result.is_usable
    assert result.confidence == 0.69
    assert result.values == {
        "type_of_rfd": None,
        "rfd": None,
        "detailed_rfd": None,
        "remarks": None,
    }
    assert not hasattr(result, "disposition")


def test_schema_invalid_output_is_null_and_reports_safe_validation_location() -> None:
    """Missing fields and unknown fields never reach later rule processing."""
    result = ExtractionGateway(
        RecordingProvider(
            {
                "type_of_rfd": "Borrower Refused",
                "rfd": "Medical",
                "detailed_rfd": None,
                "confidence": 0.90,
                "account_number": "12345678",
            }
        )
    ).extract("free text")

    assert not result.schema_valid
    assert not result.confidence_valid
    assert not result.is_usable
    assert result.values == {
        "type_of_rfd": None,
        "rfd": None,
        "detailed_rfd": None,
        "remarks": None,
    }
    assert any(
        "remarks" in error or "account_number" in error
        for error in result.validation_errors
    )
