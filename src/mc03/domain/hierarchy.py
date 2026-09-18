"""Deterministic RCBC relation classification primitives.

The relation allow-list is deliberately small and fail-closed.  A contact is a
representative only when its normalized relation is one of the approved
blood-relative or immediate-family terms, and an informant only when it is one
of the approved non-family terms.  Everything else remains ``UNKNOWN`` rather
than being promoted to a representative by fuzzy matching.

The design's public ``classify_relation`` API returns only :class:`Relation`.
``classify_relation_result`` carries the separate disposition metadata needed by
the pipeline when an unknown value is ambiguous with an informant, without
changing that API for existing callers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from mc03.domain.models import (
    Disposition,
    ExceptionKind,
    ExclusionKind,
    Relation,
    RemarkFields,
)

# These are the approved RCBC terms from the initial-demo design.  Keep the
# values exact and separate from the normalization performed by the classifier
# so adding an alias later is an explicit rule change rather than an accidental
# fuzzy match.
REPRESENTATIVE_RELATIONS: frozenset[str] = frozenset(
    {
        "parent",
        "sibling",
        "spouse",
        "child",
        "aunt",
        "tita",
        "uncle",
        "tito",
        "nephew",
        "niece",
        "kapamilya",
    }
)
INFORMANT_RELATIONS: frozenset[str] = frozenset(
    {
        "neighbor",
        "security guard",
        "barangay official",
        "colleague",
    }
)

# These are the only automatic noise signals defined by the RCBC initial-demo
# requirements.  Matching is exact after trimming and case-folding, as it is in
# the Volare ingestion adapter.  Unknown status/remark values are never noise.
NOISE_STATUS_VALUES: frozenset[str] = frozenset(
    {"bp", "new", "reactive", "abort", "lock", "failed", "field"}
)
NOISE_REMARK_VALUES: frozenset[str] = frozenset(
    {"new assignment", "subspecial", "system auto", "predictive"}
)

# The requirements name the paired labels "aunt/tita" and "uncle/tito".  The
# approved sets retain their individual terms, while these exact combined
# spellings are accepted as an unambiguous representation of the same rule.
_COMBINED_REPRESENTATIVE_RELATIONS: frozenset[str] = frozenset(
    {"aunt/tita", "uncle/tito"}
)

_CSU_SENTIMENTS: frozenset[str] = frozenset({"positive", "negative"})
_CSU_RANKS: dict[tuple[str, str], int] = {
    ("positive", "positive"): 3,
    ("positive", "negative"): 2,
    ("negative", "positive"): 1,
    ("negative", "negative"): 1,
}

# RFD labels are exposed as canonical identifiers so ranking is independent of
# source capitalization, whitespace, and the documented slash/or spelling.
PRIMARY_RFD_ORDER: tuple[str, ...] = (
    "explicit",
    "borrower_refused",
    "representative_refused",
    "no_contact",
    "moved_out",
)
EXPLICIT_SECONDARY_ORDER: tuple[str, ...] = (
    "medical_expense",
    "diversion_of_funds",
    "delayed_salary",
    "delayed_collection",
    "business_slowdown",
    "third_party_user",
)

_RFD_LABEL_PATTERN = re.compile(r"[^a-z0-9]+")
_PRIMARY_RFD_ALIASES: dict[str, str] = {
    "explicit": "explicit",
    "borrower_refused": "borrower_refused",
    "representative_refused": "representative_refused",
    "no_contact": "no_contact",
    "no_client_representative_reached": "no_contact",
    "no_client_or_representative_reached": "no_contact",
    "moved_out": "moved_out",
}
_EXPLICIT_SECONDARY_ALIASES: dict[str, str] = {
    "medical": "medical_expense",
    "medical_expense": "medical_expense",
    "diversion_of_funds": "diversion_of_funds",
    "delayed_salary": "delayed_salary",
    "delayed_collection": "delayed_collection",
    "business_slowdown": "business_slowdown",
    "third_party_user": "third_party_user",
}

# A separate identifier is not part of the initial-demo RemarkFields value
# object.  These optional attributes are accepted from richer source adapters;
# the structured fields remain the final fallback identity.
_CANDIDATE_IDENTIFIER_ATTRIBUTES: tuple[str, ...] = (
    "identifier",
    "candidate_id",
    "source_row_ref",
    "source_id",
    "id",
)
_CANDIDATE_FIELD_ATTRIBUTES: tuple[str, ...] = (
    "type_of_rfd",
    "rfd",
    "detailed_rfd",
    "remarks",
    "contact_person",
    "statement",
    "source_timestamp",
)
_RELATION_METADATA_ATTRIBUTES: tuple[str, ...] = ("relation", "contact_relation")
_MISSING = object()

# An unknown value that contains one of these markers may be an informant but
# cannot be safely classified as one.  It is therefore routed to Review_Only.
# Exact approved informant values are classified before this check and do not
# become exceptions merely because they contain a marker.
_AMBIGUOUS_INFORMANT_MARKERS: frozenset[str] = INFORMANT_RELATIONS | frozenset(
    {"informant", "guard"}
)
_RELATION_WHITESPACE = re.compile(r"\s+")
_SLASH_WHITESPACE = re.compile(r"\s*/\s*")


@dataclass(frozen=True, slots=True)
class RelationClassificationResult:
    """Relation classification plus safe pipeline routing metadata.

    ``classify_relation`` remains the small enum-only API required by the
    design.  Callers that are constructing a processed row can use this result
    to preserve the classification and route an ambiguous unknown to
    ``Review_Only`` with the mandated ``AMBIGUOUS_INFORMANT`` reason.
    """

    relation: Relation
    disposition: Disposition = Disposition.CLEAN
    exception_kinds: tuple[ExceptionKind, ...] = ()

    @property
    def review_only(self) -> bool:
        """Return whether the relation requires a DA review decision."""
        return self.disposition is Disposition.REVIEW_ONLY

    @property
    def representative_refused_eligible(self) -> bool:
        """Return whether this relation may use ``Representative Refused``."""
        return self.relation is Relation.REPRESENTATIVE


@dataclass(frozen=True, slots=True)
class ExclusionClassificationResult:
    """Disposition decision for one candidate row's exclusion signals.

    The classifier is deliberately fail-closed for actionable exceptions:
    ``exception_kinds`` are retained and always produce ``Review_Only``.  An
    ``Excluded_Row`` result therefore has exactly one of the two explicit
    reasons represented by :class:`ExclusionKind`, while a clean or review
    result has no exclusion kind.
    """

    disposition: Disposition
    exclusion_kind: ExclusionKind | None = None
    exception_kinds: tuple[ExceptionKind, ...] = ()

    @property
    def excluded(self) -> bool:
        """Return whether the row is eligible for the excluded-row bucket."""
        return self.disposition is Disposition.EXCLUDED

    @property
    def review_only(self) -> bool:
        """Return whether the row remains actionable for DA review."""
        return self.disposition is Disposition.REVIEW_ONLY

    @property
    def actionable(self) -> bool:
        """Return whether the result must remain in the actionable queue."""
        return self.review_only

    @property
    def reason(self) -> ExclusionKind | None:
        """Return the explicit exclusion reason, if this row was excluded."""
        return self.exclusion_kind


def _normalize_exclusion_text(value: str | None, field_name: str) -> str:
    """Normalize an optional status or remark for exact noise matching."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    return value.strip().casefold()


def _validated_exception_kinds(
    exception_kinds: Iterable[ExceptionKind],
) -> tuple[ExceptionKind, ...]:
    """Validate and materialize exception evidence without changing its order."""
    try:
        values = tuple(exception_kinds)
    except TypeError as exc:
        raise TypeError("exception_kinds must be an iterable of ExceptionKind") from exc
    if any(not isinstance(kind, ExceptionKind) for kind in values):
        raise TypeError("exception_kinds must contain only ExceptionKind values")
    return values


def is_defined_noise(
    *,
    status: str | None = None,
    remark: str | None = None,
) -> bool:
    """Return whether status or remark matches a defined RCBC noise rule.

    Noise matching is exact after trimming and case-folding.  The status rules
    are ``BP``, ``New``, ``Reactive``, ``Abort``, ``Lock``, ``Failed``, and
    ``Field``; the remark purge rules are ``New Assignment``, ``Subspecial``,
    ``System Auto``, and ``Predictive``.  No fuzzy or substring matching is
    performed, so an unmapped or otherwise unfamiliar value is not excluded.
    """
    normalized_status = _normalize_exclusion_text(status, "status")
    normalized_remark = _normalize_exclusion_text(remark, "remark")
    return (
        normalized_status in NOISE_STATUS_VALUES
        or normalized_remark in NOISE_REMARK_VALUES
    )


def classify_exclusion(
    *,
    status: str | None = None,
    remark: str | None = None,
    explicit_da_exclusion: bool = False,
    exception_kinds: Iterable[ExceptionKind] = (),
) -> ExclusionClassificationResult:
    """Classify noise/DA exclusion without hiding actionable exceptions.

    A row receives ``Disposition.EXCLUDED`` only when it matches one of the
    defined noise values or ``explicit_da_exclusion`` is true.  Any supplied
    :class:`ExceptionKind` is actionable evidence and takes precedence over
    those exclusion signals, returning ``Review_Only`` with the evidence
    unchanged.  This protects unmapped accounts, remark overflow, ambiguous
    informants, and the other fail-closed review conditions from being dropped.

    When both a noise signal and an explicit DA exclusion are present, the
    disposition is still excluded and the reason is ``ExclusionKind.NOISE``;
    the source noise rule is retained as the most specific automatic reason.
    Unknown statuses and remarks never count as noise.

    Args:
        status: Optional source status used by the defined noise rules.
        remark: Optional source remark used by the defined purge rules.
        explicit_da_exclusion: Whether the DA explicitly selected exclusion.
        exception_kinds: Existing review evidence for this row.  Any non-empty
            iterable protects the row from automatic or explicit exclusion.

    Returns:
        A typed disposition and exclusion/review reason contract.

    Raises:
        TypeError: If a text signal, the DA flag, or exception evidence has an
            invalid runtime type.
    """
    if not isinstance(explicit_da_exclusion, bool):
        raise TypeError("explicit_da_exclusion must be a boolean")

    validated_exceptions = _validated_exception_kinds(exception_kinds)
    if validated_exceptions:
        return ExclusionClassificationResult(
            disposition=Disposition.REVIEW_ONLY,
            exception_kinds=validated_exceptions,
        )

    if is_defined_noise(status=status, remark=remark):
        return ExclusionClassificationResult(
            disposition=Disposition.EXCLUDED,
            exclusion_kind=ExclusionKind.NOISE,
        )
    if explicit_da_exclusion:
        return ExclusionClassificationResult(
            disposition=Disposition.EXCLUDED,
            exclusion_kind=ExclusionKind.DA_EXCLUSION,
        )
    return ExclusionClassificationResult(disposition=Disposition.CLEAN)


def _normalize_relation(contact_relation: str) -> str:
    """Normalize relation spelling without broadening the approved vocabulary."""
    if not isinstance(contact_relation, str):
        raise TypeError("contact_relation must be a string")

    normalized = _RELATION_WHITESPACE.sub(" ", contact_relation.strip().casefold())
    return _SLASH_WHITESPACE.sub("/", normalized)


def _relation_for_normalized(normalized: str) -> Relation:
    """Classify one already-normalized relation value."""
    if normalized in REPRESENTATIVE_RELATIONS or normalized in _COMBINED_REPRESENTATIVE_RELATIONS:
        return Relation.REPRESENTATIVE
    if normalized in INFORMANT_RELATIONS:
        return Relation.INFORMANT
    return Relation.UNKNOWN


def _contains_informant_marker(normalized: str) -> bool:
    """Return whether unknown text contains a whole approved informant marker."""
    return any(
        re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized) is not None
        for marker in _AMBIGUOUS_INFORMANT_MARKERS
    )


def classify_relation(contact_relation: str) -> Relation:
    """Map a contact relation to ``REPRESENTATIVE``, ``INFORMANT``, or ``UNKNOWN``.

    Matching is case-insensitive and ignores leading/trailing or repeated
    whitespace, but remains exact after normalization.  The approved
    representative terms are parent, sibling, spouse, child, aunt/tita,
    uncle/tito, nephew, niece, and kapamilya.  The approved informant terms are
    neighbor, security guard, barangay official, and colleague.  Unknown or
    empty values are never promoted to representative.
    """
    return _relation_for_normalized(_normalize_relation(contact_relation))


def csu_rank(
    client_sentiment: str | None = None,
    unit_sentiment: str | None = None,
) -> int:
    """Return the RCBC CSU rank, with a higher integer meaning stronger sentiment.

    The valid pairs form the strict order ``CP+UP > CP+UN > CN``.  Both
    sentiment fields are validated even though the unit sentiment does not
    change the rank for a client-negative CSU, so malformed input cannot be
    silently converted into a usable rank.

    Args:
        client_sentiment: The client sentiment, either ``"positive"`` or
            ``"negative"`` (case-insensitive and whitespace-tolerant).
        unit_sentiment: The unit sentiment, either ``"positive"`` or
            ``"negative"`` (case-insensitive and whitespace-tolerant).

    Returns:
        An integer rank: ``3`` for CP+UP, ``2`` for CP+UN, and ``1`` for CN.

    Raises:
        ValueError: If either sentiment is missing or is not one of the
            recognized values.  The error message identifies the invalid field,
            and no rank is returned.
    """
    normalized_client = _normalize_sentiment(client_sentiment, "client_sentiment")
    normalized_unit = _normalize_sentiment(unit_sentiment, "unit_sentiment")
    return _CSU_RANKS[(normalized_client, normalized_unit)]


def _normalize_sentiment(value: str | None, field_name: str) -> str:
    """Validate and normalize one CSU sentiment input."""
    if not isinstance(value, str):
        raise ValueError(
            f"invalid {field_name}: expected 'positive' or 'negative', got {value!r}"
        )

    normalized = value.strip().casefold()
    if normalized not in _CSU_SENTIMENTS:
        raise ValueError(
            f"invalid {field_name}: expected 'positive' or 'negative', got {value!r}"
        )
    return normalized


def _normalize_rfd_label(value: str | None) -> str:
    """Normalize a primary or secondary RFD label to snake-case text."""
    if not isinstance(value, str):
        return ""
    return _RFD_LABEL_PATTERN.sub("_", value.strip().casefold()).strip("_")


def _primary_rfd_class(candidate: RemarkFields) -> str | None:
    """Return the canonical primary RFD class for one candidate."""
    return _PRIMARY_RFD_ALIASES.get(_normalize_rfd_label(candidate.type_of_rfd))


def _secondary_rfd_class(candidate: RemarkFields) -> str | None:
    """Return the canonical explicit-secondary RFD class for one candidate."""
    return _EXPLICIT_SECONDARY_ALIASES.get(_normalize_rfd_label(candidate.rfd))


def _normalize_candidate_timestamp(value: datetime) -> datetime:
    """Normalize a source timestamp so naive and aware values compare safely."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timestamp_sort_key(candidate: RemarkFields) -> tuple[int, datetime]:
    """Return a sortable timestamp key with missing values ranked oldest."""
    timestamp = candidate.source_timestamp
    if not isinstance(timestamp, datetime):
        return (0, datetime.min.replace(tzinfo=UTC))
    return (1, _normalize_candidate_timestamp(timestamp))


def _relation_from_candidate_metadata(value: object) -> Relation:
    """Convert optional candidate relation metadata to the domain relation enum."""
    if isinstance(value, Relation):
        return value
    if not isinstance(value, str):
        return Relation.UNKNOWN

    normalized = _normalize_relation(value)
    if normalized == "representative":
        return Relation.REPRESENTATIVE
    if normalized == "informant":
        return Relation.INFORMANT
    return _relation_for_normalized(normalized)


def _candidate_relation(candidate: RemarkFields) -> Relation | None:
    """Read optional relation metadata without changing ``RemarkFields``."""
    for attribute in _RELATION_METADATA_ATTRIBUTES:
        value = getattr(candidate, attribute, _MISSING)
        if value is not _MISSING and value is not None:
            return _relation_from_candidate_metadata(value)

    # Some source adapters carry the relation token in the contact-person slot.
    # Treat only exact approved relation markers as metadata; ordinary names are
    # left without a relation signal for backwards compatibility.
    contact_person = candidate.contact_person
    if isinstance(contact_person, str):
        normalized = _normalize_relation(contact_person)
        relation = _relation_from_candidate_metadata(contact_person)
        if (
            relation is not Relation.UNKNOWN
            or normalized in {"informant", "representative"}
            or _contains_informant_marker(normalized)
        ):
            return relation
    return None


def _stable_identifier_value(value: object) -> str | None:
    """Return a stable textual representation for an optional identifier."""
    if isinstance(value, str | int | float | bool | UUID):
        text = str(value)
        return text or None
    return None


def _stable_field_value(value: object) -> str:
    """Return a stable textual representation for a RemarkFields value."""
    if isinstance(value, datetime):
        return _normalize_candidate_timestamp(value).isoformat()
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _candidate_identifier(candidate: RemarkFields) -> tuple[str, ...]:
    """Build a deterministic total-order key for a candidate.

    Richer adapters may expose an explicit identifier.  The immutable
    ``RemarkFields`` contract has no such field, so all structured fields are
    included as a stable fallback.  Including the fields after an explicit ID
    also keeps the ordering total if malformed input reuses an identifier.
    """
    identity: list[str] = []
    for attribute in _CANDIDATE_IDENTIFIER_ATTRIBUTES:
        value = getattr(candidate, attribute, _MISSING)
        if value is _MISSING:
            continue
        stable_value = _stable_identifier_value(value)
        if stable_value is not None:
            identity.append(f"{attribute}={stable_value}")

    for attribute in _CANDIDATE_FIELD_ATTRIBUTES:
        identity.append(f"{attribute}={_stable_field_value(getattr(candidate, attribute))}")
    return tuple(identity)


def _primary_rank(candidate: RemarkFields) -> tuple[str, int] | None:
    """Return the canonical primary class and its descending rank."""
    primary = _primary_rfd_class(candidate)
    if primary is None:
        return None
    return primary, len(PRIMARY_RFD_ORDER) - PRIMARY_RFD_ORDER.index(primary)


def _secondary_rank(candidate: RemarkFields, primary: str) -> int:
    """Return the explicit secondary rank, or zero outside the explicit tier."""
    if primary != "explicit":
        return 0
    secondary = _secondary_rfd_class(candidate)
    if secondary is None:
        return 0
    return len(EXPLICIT_SECONDARY_ORDER) - EXPLICIT_SECONDARY_ORDER.index(secondary)


def select_rfd(candidates: Iterable[RemarkFields]) -> str | None:
    """Select one deterministic RFD from the supplied candidates.

    Candidates are ranked by the primary hierarchy, then by the explicit-only
    secondary hierarchy, latest normalized source timestamp, and finally the
    lexicographically smallest stable candidate identifier.  A candidate with
    relation metadata cannot enter the ``Representative Refused`` tier unless
    it is a representative; this prevents an informant from winning that tier.
    Unknown primary labels and blank RFD values are safely ignored.  With no
    selectable candidates, including an empty iterable, ``None`` is returned.

    The input is consumed but never reordered or mutated, so equivalent
    permutations produce the same selected RFD.
    """
    ranked: list[
        tuple[tuple[int, int, tuple[int, datetime]], tuple[str, ...], str]
    ] = []

    for candidate in candidates:
        rfd = candidate.rfd
        if not isinstance(rfd, str) or not rfd.strip():
            continue

        primary_result = _primary_rank(candidate)
        if primary_result is None:
            continue
        primary, primary_rank = primary_result

        if primary == "representative_refused":
            relation = _candidate_relation(candidate)
            if relation is not None and relation is not Relation.REPRESENTATIVE:
                continue

        rank = (
            primary_rank,
            _secondary_rank(candidate, primary),
            _timestamp_sort_key(candidate),
        )
        ranked.append((rank, _candidate_identifier(candidate), rfd))

    if not ranked:
        return None

    highest_rank = max(item[0] for item in ranked)
    tied = (item for item in ranked if item[0] == highest_rank)
    _, _, selected_rfd = min(tied, key=lambda item: item[1])
    return selected_rfd


def is_ambiguous_informant(contact_relation: str) -> bool:
    """Return whether an unknown relation contains an ambiguous informant hint.

    A recognized informant is not ambiguous: it is classified directly as
    :attr:`Relation.INFORMANT`.  An unknown value such as ``"neighbor / friend"``
    or ``"guard"`` contains enough informant evidence to require review, but not
    enough evidence for an automatic informant classification.
    """
    normalized = _normalize_relation(contact_relation)
    return (
        _relation_for_normalized(normalized) is Relation.UNKNOWN
        and _contains_informant_marker(normalized)
    )


def classify_relation_result(
    contact_relation: str,
    *,
    ambiguous_informant: bool | None = None,
) -> RelationClassificationResult:
    """Classify a relation and attach the required Review_Only routing.

    ``ambiguous_informant`` may be supplied by an upstream parser when source
    evidence identifies an ambiguity that is not expressible in the raw
    relation text.  When omitted, the helper detects whole informant markers in
    an otherwise unknown relation.  Only an ``UNKNOWN`` relation can receive
    the ``AMBIGUOUS_INFORMANT`` exception; a recognized informant can never be
    converted into ``Representative Refused`` or an ambiguity by this helper.
    """
    relation = classify_relation(contact_relation)
    if ambiguous_informant is not None and not isinstance(ambiguous_informant, bool):
        raise TypeError("ambiguous_informant must be a boolean or None")

    needs_review = (
        relation is Relation.UNKNOWN
        and (
            ambiguous_informant
            if ambiguous_informant is not None
            else is_ambiguous_informant(contact_relation)
        )
    )
    if needs_review:
        return RelationClassificationResult(
            relation=relation,
            disposition=Disposition.REVIEW_ONLY,
            exception_kinds=(ExceptionKind.AMBIGUOUS_INFORMANT,),
        )
    return RelationClassificationResult(relation=relation)


def is_representative_refused_eligible(contact_relation: str | Relation) -> bool:
    """Return whether a contact may receive the ``Representative Refused`` RFD.

    This predicate intentionally allows only :attr:`Relation.REPRESENTATIVE`.
    Informants and unknown contacts therefore cannot enter the representative
    refusal branch, including when their raw relation text is ambiguous.
    """
    if isinstance(contact_relation, Relation):
        relation = contact_relation
    else:
        relation = classify_relation(contact_relation)
    return relation is Relation.REPRESENTATIVE


__all__ = [
    "EXPLICIT_SECONDARY_ORDER",
    "ExclusionClassificationResult",
    "INFORMANT_RELATIONS",
    "NOISE_REMARK_VALUES",
    "NOISE_STATUS_VALUES",
    "PRIMARY_RFD_ORDER",
    "REPRESENTATIVE_RELATIONS",
    "RelationClassificationResult",
    "classify_exclusion",
    "classify_relation",
    "classify_relation_result",
    "csu_rank",
    "is_ambiguous_informant",
    "is_defined_noise",
    "is_representative_refused_eligible",
    "select_rfd",
]
