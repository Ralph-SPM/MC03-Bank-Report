"""Focused tests for RCBC relation classification and safe routing."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from mc03.domain import csu_rank as exported_csu_rank
from mc03.domain import select_rfd as exported_select_rfd
from mc03.domain.hierarchy import (
    INFORMANT_RELATIONS,
    NOISE_REMARK_VALUES,
    NOISE_STATUS_VALUES,
    REPRESENTATIVE_RELATIONS,
    classify_exclusion,
    classify_relation,
    classify_relation_result,
    csu_rank,
    is_defined_noise,
    is_representative_refused_eligible,
    select_rfd,
)
from mc03.domain.models import (
    Disposition,
    ExceptionKind,
    ExclusionKind,
    Relation,
    RemarkFields,
)


@dataclass(frozen=True)
class _IdentifiedRemarkFields(RemarkFields):
    """Test-only candidate metadata supported by richer source adapters."""

    candidate_id: str = ""
    relation: Relation | None = None


def _rfd_candidate(
    type_of_rfd: str,
    rfd: str,
    source_timestamp: datetime | None = None,
    *,
    candidate_id: str | None = None,
    relation: Relation | None = None,
) -> RemarkFields:
    """Build a compact RemarkFields value for selector tests."""
    values = {
        "type_of_rfd": type_of_rfd,
        "rfd": rfd,
        "detailed_rfd": None,
        "remarks": None,
        "contact_person": None,
        "statement": None,
        "source_timestamp": source_timestamp,
    }
    if candidate_id is None and relation is None:
        return RemarkFields(**values)
    return _IdentifiedRemarkFields(
        **values,
        candidate_id=candidate_id or "",
        relation=relation,
    )


@pytest.mark.parametrize("contact_relation", sorted(REPRESENTATIVE_RELATIONS))
def test_approved_representative_relations_are_classified_as_representative(
    contact_relation: str,
) -> None:
    assert classify_relation(contact_relation) is Relation.REPRESENTATIVE


@pytest.mark.parametrize("contact_relation", sorted(INFORMANT_RELATIONS))
def test_approved_informant_relations_are_classified_as_informant(
    contact_relation: str,
) -> None:
    assert classify_relation(contact_relation) is Relation.INFORMANT


def test_relation_matching_is_case_insensitive_and_handles_documented_alias_spacing() -> None:
    assert classify_relation("  SECURITY   GUARD ") is Relation.INFORMANT
    assert classify_relation("AUNT / TITA") is Relation.REPRESENTATIVE
    assert classify_relation("uncle/tito") is Relation.REPRESENTATIVE


@pytest.mark.parametrize("contact_relation", ["", "friend", "neighboring", "family contact"])
def test_unapproved_relations_remain_unknown(contact_relation: str) -> None:
    assert classify_relation(contact_relation) is Relation.UNKNOWN


def test_informants_and_unknowns_are_never_eligible_for_representative_refused() -> None:
    assert not is_representative_refused_eligible("neighbor")
    assert not is_representative_refused_eligible("security guard")
    assert not is_representative_refused_eligible("friend")
    assert is_representative_refused_eligible("parent")
    assert is_representative_refused_eligible(Relation.REPRESENTATIVE)
    assert not is_representative_refused_eligible(Relation.INFORMANT)


def test_ambiguous_unknown_informant_is_routed_to_review_only() -> None:
    result = classify_relation_result("neighbor / friend")

    assert result.relation is Relation.UNKNOWN
    assert result.disposition is Disposition.REVIEW_ONLY
    assert result.exception_kinds == (ExceptionKind.AMBIGUOUS_INFORMANT,)
    assert result.review_only
    assert not result.representative_refused_eligible


def test_plain_unknown_is_not_reviewed_without_ambiguous_informant_evidence() -> None:
    result = classify_relation_result("friend")

    assert result.relation is Relation.UNKNOWN
    assert result.disposition is Disposition.CLEAN
    assert result.exception_kinds == ()


def test_upstream_ambiguity_override_routes_unknown_to_review_only() -> None:
    result = classify_relation_result("unstructured contact", ambiguous_informant=True)

    assert result.relation is Relation.UNKNOWN
    assert result.disposition is Disposition.REVIEW_ONLY
    assert result.exception_kinds == (ExceptionKind.AMBIGUOUS_INFORMANT,)


def test_recognized_informant_cannot_become_representative_or_ambiguous() -> None:
    result = classify_relation_result("colleague", ambiguous_informant=True)

    assert result.relation is Relation.INFORMANT
    assert result.disposition is Disposition.CLEAN
    assert result.exception_kinds == ()
    assert not result.representative_refused_eligible


@pytest.mark.parametrize("status", sorted(NOISE_STATUS_VALUES))
def test_defined_noise_statuses_are_excluded_case_insensitively(status: str) -> None:
    result = classify_exclusion(status=f"  {status.upper()}  ")

    assert is_defined_noise(status=status)
    assert result.disposition is Disposition.EXCLUDED
    assert result.exclusion_kind is ExclusionKind.NOISE
    assert result.exception_kinds == ()


@pytest.mark.parametrize("remark", sorted(NOISE_REMARK_VALUES))
def test_defined_noise_remarks_are_excluded_case_insensitively(remark: str) -> None:
    result = classify_exclusion(remark=f"  {remark.upper()}  ")

    assert is_defined_noise(remark=remark)
    assert result.disposition is Disposition.EXCLUDED
    assert result.exclusion_kind is ExclusionKind.NOISE
    assert result.exception_kinds == ()


def test_explicit_da_exclusion_is_the_only_non_noise_exclusion_signal() -> None:
    result = classify_exclusion(explicit_da_exclusion=True)

    assert result.disposition is Disposition.EXCLUDED
    assert result.exclusion_kind is ExclusionKind.DA_EXCLUSION
    assert result.excluded
    assert not result.review_only


@pytest.mark.parametrize(
    ("status", "remark"),
    [("new assignment status", "system automatic"), ("completed", "ordinary remark")],
)
def test_undefined_statuses_and_remarks_are_not_noise(
    status: str,
    remark: str,
) -> None:
    result = classify_exclusion(status=status, remark=remark)

    assert not is_defined_noise(status=status, remark=remark)
    assert result.disposition is Disposition.CLEAN
    assert result.exclusion_kind is None


@pytest.mark.parametrize(
    "exception_kind",
    [
        ExceptionKind.UNMAPPED_ACCOUNT,
        ExceptionKind.REMARK_TOO_LONG,
        ExceptionKind.AMBIGUOUS_INFORMANT,
        ExceptionKind.AMBIGUOUS_ACCOUNT,
    ],
)
def test_actionable_exceptions_override_noise_and_da_exclusion(
    exception_kind: ExceptionKind,
) -> None:
    result = classify_exclusion(
        status=" BP ",
        remark=" New Assignment ",
        explicit_da_exclusion=True,
        exception_kinds=(exception_kind,),
    )

    assert result.disposition is Disposition.REVIEW_ONLY
    assert result.exclusion_kind is None
    assert result.exception_kinds == (exception_kind,)
    assert result.review_only
    assert result.actionable


def test_empty_exclusion_signals_leave_a_row_clean() -> None:
    result = classify_exclusion()

    assert result.disposition is Disposition.CLEAN
    assert result.exclusion_kind is None
    assert result.exception_kinds == ()


def test_informant_ambiguity_remains_review_only_even_with_noise_evidence() -> None:
    relation_result = classify_relation_result("neighbor / friend")
    result = classify_exclusion(
        status="Failed",
        exception_kinds=relation_result.exception_kinds,
    )

    assert result.disposition is Disposition.REVIEW_ONLY
    assert result.exclusion_kind is None
    assert result.exception_kinds == (ExceptionKind.AMBIGUOUS_INFORMANT,)


def test_csu_rank_orders_positive_client_sentiment_above_negative_client_sentiment() -> None:
    client_positive_unit_positive = csu_rank("positive", "positive")
    client_positive_unit_negative = csu_rank("positive", "negative")
    client_negative_unit_positive = csu_rank("negative", "positive")
    client_negative_unit_negative = csu_rank("negative", "negative")

    assert client_positive_unit_positive > client_positive_unit_negative
    assert client_positive_unit_negative > client_negative_unit_positive
    assert client_positive_unit_negative > client_negative_unit_negative


@pytest.mark.parametrize(
    ("client_sentiment", "unit_sentiment"),
    [
        ("positive", "positive"),
        ("positive", "negative"),
        ("negative", "positive"),
        ("negative", "negative"),
    ],
)
def test_csu_rank_returns_equal_values_for_equal_sentiment_pairs(
    client_sentiment: str,
    unit_sentiment: str,
) -> None:
    assert csu_rank(client_sentiment, unit_sentiment) == csu_rank(
        f" {client_sentiment.upper()} ", f" {unit_sentiment.upper()} "
    )


@pytest.mark.parametrize(
    ("client_sentiment", "unit_sentiment", "invalid_field"),
    [
        (None, "positive", "client_sentiment"),
        ("positive", None, "unit_sentiment"),
        ("neutral", "positive", "client_sentiment"),
        ("negative", "unknown", "unit_sentiment"),
        ("", "positive", "client_sentiment"),
    ],
)
def test_csu_rank_rejects_invalid_sentiment_with_field_specific_error(
    client_sentiment: str | None,
    unit_sentiment: str | None,
    invalid_field: str,
) -> None:
    with pytest.raises(ValueError, match=invalid_field):
        csu_rank(client_sentiment, unit_sentiment)


def test_csu_rank_is_reexported_from_domain_package() -> None:
    assert exported_csu_rank is csu_rank


def test_select_rfd_returns_none_for_empty_candidates() -> None:
    assert select_rfd([]) is None


def test_select_rfd_applies_primary_hierarchy_independently_of_input_order() -> None:
    candidates = [
        _rfd_candidate("Moved Out", "moved"),
        _rfd_candidate("No Client/Representative Reached", "no-contact"),
        _rfd_candidate("Representative Refused", "representative"),
        _rfd_candidate("Borrower Refused", "borrower"),
        _rfd_candidate("Explicit", "Medical"),
    ]

    assert select_rfd(candidates) == "Medical"
    assert select_rfd(reversed(candidates)) == "Medical"


def test_select_rfd_uses_explicit_secondary_order() -> None:
    candidates = [
        _rfd_candidate("Explicit", "Third-Party User"),
        _rfd_candidate("Explicit", "Delayed Collection"),
        _rfd_candidate("Explicit", "Business Slowdown"),
        _rfd_candidate("Explicit", "Medical"),
        _rfd_candidate("Explicit", "Diversion of Funds"),
        _rfd_candidate("Explicit", "Delayed Salary"),
    ]

    assert select_rfd(candidates) == "Medical"


def test_select_rfd_uses_latest_timestamp_after_primary_and_secondary_ties() -> None:
    older = _rfd_candidate(
        "Borrower Refused",
        "older",
        datetime(2026, 9, 2, 9, 0, tzinfo=UTC),
    )
    newer = _rfd_candidate(
        "Borrower Refused",
        "newer",
        datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
    )

    assert select_rfd([newer, older]) == "newer"
    assert select_rfd([older, newer]) == "newer"


def test_select_rfd_uses_identifier_tie_break_after_equal_timestamps() -> None:
    later_identifier = _rfd_candidate(
        "Borrower Refused",
        "later identifier",
        datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
        candidate_id="z-row",
    )
    earlier_identifier = _rfd_candidate(
        "Borrower Refused",
        "earlier identifier",
        datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
        candidate_id="a-row",
    )

    assert select_rfd([later_identifier, earlier_identifier]) == "earlier identifier"
    assert select_rfd([earlier_identifier, later_identifier]) == "earlier identifier"


def test_informant_cannot_win_representative_refused_tier() -> None:
    informant_refusal = _rfd_candidate(
        "Representative Refused",
        "representative",
        relation=Relation.INFORMANT,
    )
    borrower_refusal = _rfd_candidate("Borrower Refused", "borrower")

    assert select_rfd([informant_refusal, borrower_refusal]) == "borrower"
    assert select_rfd([informant_refusal]) is None


def test_select_rfd_is_reexported_from_domain_package() -> None:
    assert exported_select_rfd is select_rfd
    assert exported_csu_rank is csu_rank
