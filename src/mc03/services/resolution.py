"""Master File account-resolution adapter for the RCBC Initial Demo pipeline.

pandas is intentionally confined to this module so that join concerns never leak
into the domain rule layer. :func:`resolve_accounts` left-merges Field Result rows
to the Master File on a trimmed ``CH code`` value (matched case-sensitively) and
assigns an ``Account Number`` when exactly one valid Master File match exists.

Design reference: ``load_field_result``/``resolve_accounts`` in
``.kiro/specs/rcbc-initial-demo/design.md`` and Requirements 5.1-5.6.

Return contract
---------------
The design docstring for ``resolve_accounts`` states it returns
``(resolved_df, unmapped_ch_codes)``. That bare-tuple-of-strings shape is
insufficient here: task 1.11 requires routing BOTH unmapped and ambiguous rows to
``Review_Only`` with distinct reasons, and the orchestrator (task 5.3) must consume
that output uniformly with the ingestion adapters. This module therefore returns a
frozen :class:`ResolutionResult` carrying the ``resolved`` frame plus a list of
structured :class:`ResolutionReviewException` records (``kind`` / ``source_row_ref``
/ ``detail``), mirroring the ``VolareReviewException`` / ``VolareLoadResult`` style
in :mod:`mc03.services.ingestion`. This preserves the design intent (the resolved
frame plus the unresolved set) while carrying the reason enum the task mandates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from mc03.domain.models import ExceptionKind

# ---------------------------------------------------------------------------
# Master File resolution configuration constants
# ---------------------------------------------------------------------------

#: Join key present in both the Field Result and Master File frames.
CH_CODE_COLUMN = "CH code"

#: Column assigned onto resolved Field Result rows.
ACCOUNT_NUMBER_COLUMN = "Account Number"

#: Master File status column carrying the validity signal.
MASTER_STATUS_COLUMN = "status"

#: Master File statuses that qualify a row as a valid match (Requirement 5.2).
#: Comparison is exact after trimming (case-sensitive), matching the requirement's
#: "equals exactly one of" wording.
_VALID_MASTER_STATUSES: frozenset[str] = frozenset({"RETAIN", "RESOLVED", "PULLOUT"})


class ResolutionColumnError(ValueError):
    """Raised when a frame is missing a column required for account resolution.

    Resolution halts and no partial output is produced. The offending column name
    is surfaced via :attr:`missing_column` and the exception message.
    """

    def __init__(self, missing_column: str) -> None:
        self.missing_column = missing_column
        super().__init__(
            f"Account resolution input is missing required column: {missing_column!r}"
        )


@dataclass(frozen=True)
class ResolutionReviewException:
    """One Field Result row routed to ``Review_Only`` during account resolution.

    Mirrors :class:`mc03.services.ingestion.VolareReviewException` so the
    orchestrator can consume ingestion and resolution exceptions uniformly.

    Attributes:
        kind: Either :attr:`ExceptionKind.UNMAPPED_ACCOUNT` (no valid Master match)
            or :attr:`ExceptionKind.AMBIGUOUS_ACCOUNT` (more than one valid match).
        source_row_ref: Stable, human-readable reference to the originating Field
            Result row.
        detail: Human-readable explanation for the exception queue.
    """

    kind: ExceptionKind
    source_row_ref: str
    detail: str


@dataclass(frozen=True)
class ResolutionResult:
    """Typed result of :func:`resolve_accounts`.

    Preferred over the design's bare ``tuple[DataFrame, list[str]]`` so both the
    resolved rows and the structured review exceptions travel together with a
    stable contract for the orchestrator (task 5.3).

    Attributes:
        resolved: Field Result rows that resolved to exactly one valid Master File
            match, each carrying an assigned ``Account Number``. Rows routed to
            ``Review_Only`` are excluded from this frame.
        review_exceptions: Rows deferred to ``Review_Only`` for an unmapped or an
            ambiguous account, in original Field Result row order.
    """

    resolved: pd.DataFrame
    review_exceptions: list[ResolutionReviewException] = field(default_factory=list)


def _row_ref(index: object) -> str:
    """Return a stable, human-readable source-row reference for an index label."""
    return f"row {index}"


def _is_blank(value: object) -> bool:
    """Return ``True`` when a cell value is missing or effectively empty."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if pd.isna(value):
        return True
    return isinstance(value, str) and not value.strip()


def _trimmed_ch_code(value: object) -> str | None:
    """Return the trimmed ``CH code`` string, or ``None`` when blank.

    Trimming strips leading/trailing whitespace only; the resulting comparison is
    case-sensitive per Requirement 5.1.
    """
    if _is_blank(value):
        return None
    return str(value).strip()


def _build_valid_master_index(master_df: pd.DataFrame) -> dict[str, list[object]]:
    """Map each trimmed ``CH code`` to the valid ``Account Number`` values.

    A Master File row contributes only when its trimmed status equals exactly one
    of ``RETAIN``/``RESOLVED``/``PULLOUT`` and it carries a non-empty
    ``Account Number`` (Requirement 5.2). Blank CH codes are ignored. Values are
    accumulated per key so that multiple valid matches surface as ambiguity
    (Requirement 5.5).
    """
    index: dict[str, list[object]] = {}
    for row_index in master_df.index:
        status_value = master_df.at[row_index, MASTER_STATUS_COLUMN]
        if _is_blank(status_value) or str(status_value).strip() not in _VALID_MASTER_STATUSES:
            continue
        account_value = master_df.at[row_index, ACCOUNT_NUMBER_COLUMN]
        if _is_blank(account_value):
            continue
        ch_code = _trimmed_ch_code(master_df.at[row_index, CH_CODE_COLUMN])
        if ch_code is None:
            continue
        index.setdefault(ch_code, []).append(account_value)
    return index


def resolve_accounts(
    field_df: pd.DataFrame, master_df: pd.DataFrame
) -> ResolutionResult:
    """Resolve Field Result rows to Master File account numbers.

    pandas is confined to this adapter. Each Field Result row is matched to the
    Master File on its trimmed ``CH code`` value (case-sensitive, Requirement 5.1).
    A Master File row is a valid match only when its trimmed status equals exactly
    one of ``RETAIN``/``RESOLVED``/``PULLOUT`` and it carries a non-empty
    ``Account Number`` (Requirement 5.2).

    Behavior:
        * Exactly one valid match: the match's ``Account Number`` is assigned to the
          row and the row appears in :attr:`ResolutionResult.resolved`
          (Requirement 5.3).
        * No valid match: the row is flagged :attr:`ExceptionKind.UNMAPPED_ACCOUNT`
          and routed to ``Review_Only`` with all original field values preserved;
          it is excluded from the resolved frame (Requirement 5.4).
        * More than one valid match: the row is flagged
          :attr:`ExceptionKind.AMBIGUOUS_ACCOUNT` and routed to ``Review_Only``
          without an assigned ``Account Number`` (Requirement 5.5).

    Row-count conservation (Requirement 5.6): the count of resolved rows plus the
    count of review exceptions equals the input Field Result row count; no row is
    dropped.

    Args:
        field_df: Filtered Field Result rows (see ``load_field_result``). Must
            contain a ``CH code`` column.
        master_df: Master File rows. Must contain ``CH code``, ``Account Number``,
            and ``status`` columns.

    Returns:
        A :class:`ResolutionResult` with the ``resolved`` frame (carrying assigned
        account numbers) and the list of ``review_exceptions``.

    Raises:
        ResolutionColumnError: If a required column is absent from either input.
            Resolution halts and produces no partial output.
    """
    if CH_CODE_COLUMN not in field_df.columns:
        raise ResolutionColumnError(CH_CODE_COLUMN)
    for required in (CH_CODE_COLUMN, ACCOUNT_NUMBER_COLUMN, MASTER_STATUS_COLUMN):
        if required not in master_df.columns:
            raise ResolutionColumnError(required)

    valid_master = _build_valid_master_index(master_df)

    resolved_indices: list[object] = []
    assigned_accounts: dict[object, object] = {}
    review_exceptions: list[ResolutionReviewException] = []

    for row_index in field_df.index:
        ch_code = _trimmed_ch_code(field_df.at[row_index, CH_CODE_COLUMN])
        matches = valid_master.get(ch_code, []) if ch_code is not None else []

        if len(matches) == 1:
            resolved_indices.append(row_index)
            assigned_accounts[row_index] = matches[0]
        elif len(matches) == 0:
            review_exceptions.append(
                ResolutionReviewException(
                    kind=ExceptionKind.UNMAPPED_ACCOUNT,
                    source_row_ref=_row_ref(row_index),
                    detail=(
                        "No valid Master File match for CH code "
                        f"{ch_code!r}."
                    ),
                )
            )
        else:
            review_exceptions.append(
                ResolutionReviewException(
                    kind=ExceptionKind.AMBIGUOUS_ACCOUNT,
                    source_row_ref=_row_ref(row_index),
                    detail=(
                        f"{len(matches)} valid Master File matches for CH code "
                        f"{ch_code!r}; no Account Number assigned."
                    ),
                )
            )

    resolved = field_df.loc[resolved_indices].copy()
    account_series = pd.Series(
        [assigned_accounts[row_index] for row_index in resolved_indices],
        index=resolved.index,
        dtype="object",
    )
    resolved[ACCOUNT_NUMBER_COLUMN] = account_series

    return ResolutionResult(resolved=resolved, review_exceptions=review_exceptions)


__all__ = [
    "ACCOUNT_NUMBER_COLUMN",
    "CH_CODE_COLUMN",
    "MASTER_STATUS_COLUMN",
    "ResolutionColumnError",
    "ResolutionResult",
    "ResolutionReviewException",
    "resolve_accounts",
]
