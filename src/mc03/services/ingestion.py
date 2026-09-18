"""Spreadsheet ingestion adapters for the RCBC Initial Demo pipeline.

pandas is intentionally confined to this module so that parsing concerns never
leak into the domain rule layer. Each adapter loads one manual ``.xlsx`` upload,
applies its date-window and noise filters, and returns a typed result that the
:class:`~mc03.services.orchestrator` can consume.

This module currently hosts the Volare DRR adapter (design ``load_volare_drr``,
Requirements 3.1-3.8). The Field Result adapter (task 1.9) is added separately;
helpers here are scoped to Volare to keep concurrent edits conflict-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from mc03.domain.models import ExceptionKind

# ---------------------------------------------------------------------------
# Volare DRR configuration constants
# ---------------------------------------------------------------------------

#: Logical column names required by the Volare DRR adapter. The adapter resolves
#: each logical name against the loaded frame case-insensitively (after trimming)
#: so minor header-casing differences in the upload do not halt the load.
VOLARE_CALL_DATE_COLUMN = "call date"
VOLARE_STATUS_COLUMN = "status"
VOLARE_REMARKS_COLUMN = "remarks"
VOLARE_VIBER_STATUS_COLUMN = "viber status"
VOLARE_EMAIL_STATUS_COLUMN = "email status"
VOLARE_RANK_KEY_COLUMN = "disposition"
VOLARE_RANK_COLUMN = "rank"

#: Columns that must be present for date, status, remarks, and rank processing.
#: Absence of any of these halts the load with a :class:`VolareColumnError`.
_REQUIRED_COLUMNS: tuple[str, ...] = (
    VOLARE_CALL_DATE_COLUMN,
    VOLARE_STATUS_COLUMN,
    VOLARE_REMARKS_COLUMN,
    VOLARE_RANK_KEY_COLUMN,
)

#: Statuses excluded case-insensitively after trimming (Requirement 3.2).
_EXCLUDED_STATUSES: frozenset[str] = frozenset(
    {"bp", "new", "reactive", "abort", "lock", "failed", "field"}
)

#: Remark purge patterns removed by case-insensitive exact match (Requirement 3.4).
_PURGE_PATTERNS: frozenset[str] = frozenset(
    {"new assignment", "subspecial", "system auto", "predictive"}
)

#: Substitution copy for negative Viber / Email statuses (Requirement 3.3).
_VIBER_SUBSTITUTION = "Sent notice via Viber"
_EMAIL_SUBSTITUTION = "Sent email notice"

#: Values in a Viber/Email status column treated as "negative" and substituted.
_NEGATIVE_STATUS_VALUES: frozenset[str] = frozenset(
    {"negative", "neg", "failed", "no", "false", "0"}
)

#: Reference sheet inside the DRR template that maps a disposition to a rank.
VOLARE_REF_SHEET_NAME = "REF"

#: Inclusive numeric rank bounds (Requirement 3.5).
_RANK_MIN = 1
_RANK_MAX = 999999


class VolareColumnError(ValueError):
    """Raised when the Volare DRR upload is missing a required column.

    The load halts and previously loaded pipeline data is left unchanged
    (Requirement 3.8). The offending column name is surfaced via
    :attr:`missing_column` and the exception message.
    """

    def __init__(self, missing_column: str) -> None:
        self.missing_column = missing_column
        super().__init__(f"Volare DRR upload is missing required column: {missing_column!r}")


@dataclass(frozen=True)
class VolareReviewException:
    """One row routed to ``Review_Only`` during Volare ingestion.

    Instead of terminating the load, unparseable/missing call dates and missing
    rank lookups are recorded here so the orchestrator can surface them in the
    exception review queue (Requirements 3.6, 3.7).
    """

    kind: ExceptionKind
    source_row_ref: str
    detail: str


@dataclass(frozen=True)
class VolareLoadResult:
    """Typed result of :func:`load_volare_drr`.

    The design signature returns a ``pandas.DataFrame``, but the adapter must
    also surface the ``Review_Only`` exceptions it records without terminating
    the load. This frozen dataclass keeps both together with a stable, typed
    contract for the orchestrator (task 5.3), preferred over a bare tuple.

    Attributes:
        frame: The filtered, normalized pipeline rows (excluded rows removed;
            rank column populated where a REF match exists).
        review_exceptions: Rows deferred to ``Review_Only`` for unparseable or
            missing call dates and missing rank lookups.
    """

    frame: pd.DataFrame
    review_exceptions: list[VolareReviewException] = field(default_factory=list)


def _resolve_column(frame: pd.DataFrame, logical_name: str) -> str | None:
    """Return the actual frame column matching ``logical_name``, if present.

    Matching is case-insensitive after trimming leading/trailing whitespace so
    that minor header-casing differences do not halt the load.
    """
    target = logical_name.strip().casefold()
    for column in frame.columns:
        if str(column).strip().casefold() == target:
            return str(column)
    return None


def _require_columns(frame: pd.DataFrame) -> dict[str, str]:
    """Resolve every required column or raise :class:`VolareColumnError`.

    Returns a mapping of logical name to the actual column label in ``frame``.
    """
    resolved: dict[str, str] = {}
    for logical_name in _REQUIRED_COLUMNS:
        actual = _resolve_column(frame, logical_name)
        if actual is None:
            raise VolareColumnError(logical_name)
        resolved[logical_name] = actual
    return resolved


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


def _load_rank_lookup(path: str) -> dict[str, int]:
    """Build a disposition -> rank lookup from the DRR template REF sheet.

    Only integer ranks within ``[1, 999999]`` are retained. A missing or
    unreadable REF sheet yields an empty lookup, so every row falls through to
    the missing-rank review path (Requirement 3.7) rather than halting the load.
    """
    try:
        ref = pd.read_excel(path, sheet_name=VOLARE_REF_SHEET_NAME)
    except (ValueError, KeyError, OSError):
        return {}
    if ref.empty or len(ref.columns) < 2:
        return {}

    key_column = str(ref.columns[0])
    rank_column = _resolve_column(ref, VOLARE_RANK_COLUMN)
    if rank_column is None:
        rank_column = str(ref.columns[1])

    lookup: dict[str, int] = {}
    for _, ref_row in ref.iterrows():
        key = ref_row[key_column]
        rank_value = ref_row[rank_column]
        if _is_blank(key) or _is_blank(rank_value):
            continue
        try:
            rank_int = int(rank_value)
        except (TypeError, ValueError):
            continue
        if _RANK_MIN <= rank_int <= _RANK_MAX:
            lookup[str(key).strip().casefold()] = rank_int
    return lookup


def _substitute_status_remarks(
    frame: pd.DataFrame,
    remarks_column: str,
    status_column: str | None,
    substitution: str,
) -> None:
    """Replace remarks in-place where a status column is negative.

    No-op when the status column is absent, keeping the substitution optional
    per Requirement 3.3 while never halting on its absence.
    """
    if status_column is None:
        return
    for index in frame.index:
        status_value = frame.at[index, status_column]
        if _is_blank(status_value):
            continue
        if str(status_value).strip().casefold() in _NEGATIVE_STATUS_VALUES:
            frame.at[index, remarks_column] = substitution


def load_volare_drr(
    path: str,
    date_from: date,
    date_to: date,
) -> VolareLoadResult:
    """Load and normalize the Volare DRR ``.xlsx`` upload.

    pandas is confined to this adapter. The load applies, in order: required
    column resolution, an inclusive call-date window filter, excluded-status
    removal, Viber/Email remark substitutions, purge-pattern removal, and REF
    sheet rank population. Rows with unparseable/missing call dates are excluded
    and recorded as ``Review_Only`` exceptions; rows with no REF rank match are
    retained with an empty rank and a ``Review_Only`` exception. A missing
    required column halts the load.

    Args:
        path: Readable ``.xlsx`` path under the protected storage root.
        date_from: Inclusive start of the report window.
        date_to: Inclusive end of the report window.

    Returns:
        A :class:`VolareLoadResult` with the filtered ``frame`` and the list of
        ``review_exceptions`` recorded during the load.

    Raises:
        VolareColumnError: If a column required for date, status, remarks, or
            rank processing is absent. The load halts and no partial frame is
            returned (Requirement 3.8).
    """
    if date_from > date_to:
        raise ValueError("date_from must not be later than date_to")

    frame = pd.read_excel(path)
    columns = _require_columns(frame)
    call_date_col = columns[VOLARE_CALL_DATE_COLUMN]
    status_col = columns[VOLARE_STATUS_COLUMN]
    remarks_col = columns[VOLARE_REMARKS_COLUMN]
    rank_key_col = columns[VOLARE_RANK_KEY_COLUMN]

    review_exceptions: list[VolareReviewException] = []
    window_start = pd.Timestamp(date_from)
    window_end = pd.Timestamp(date_to)

    # --- Call-date parsing and inclusive window filter (Req 3.1, 3.6) ---------
    kept_indices: list[object] = []
    for index in frame.index:
        raw_call_date = frame.at[index, call_date_col]
        if _is_blank(raw_call_date):
            review_exceptions.append(
                VolareReviewException(
                    kind=ExceptionKind.UNPARSEABLE_CALL_DATE,
                    source_row_ref=_row_ref(index),
                    detail="Call date is missing or empty.",
                )
            )
            continue
        parsed = pd.to_datetime(raw_call_date, errors="coerce")
        if pd.isna(parsed):
            review_exceptions.append(
                VolareReviewException(
                    kind=ExceptionKind.UNPARSEABLE_CALL_DATE,
                    source_row_ref=_row_ref(index),
                    detail=f"Call date could not be parsed: {raw_call_date!r}.",
                )
            )
            continue
        call_day = pd.Timestamp(parsed).normalize()
        if window_start <= call_day <= window_end:
            kept_indices.append(index)

    frame = frame.loc[kept_indices].copy()

    # --- Excluded-status removal (Req 3.2) ------------------------------------
    if not frame.empty:
        status_norm = frame[status_col].map(
            lambda value: "" if _is_blank(value) else str(value).strip().casefold()
        )
        frame = frame.loc[~status_norm.isin(_EXCLUDED_STATUSES)].copy()

    # --- Remark substitutions (Req 3.3) ---------------------------------------
    if not frame.empty:
        viber_status_col = _resolve_column(frame, VOLARE_VIBER_STATUS_COLUMN)
        email_status_col = _resolve_column(frame, VOLARE_EMAIL_STATUS_COLUMN)
        _substitute_status_remarks(frame, remarks_col, viber_status_col, _VIBER_SUBSTITUTION)
        _substitute_status_remarks(frame, remarks_col, email_status_col, _EMAIL_SUBSTITUTION)

    # --- Purge-pattern removal (Req 3.4) --------------------------------------
    if not frame.empty:
        remark_norm = frame[remarks_col].map(
            lambda value: "" if _is_blank(value) else str(value).strip().casefold()
        )
        frame = frame.loc[~remark_norm.isin(_PURGE_PATTERNS)].copy()

    # --- Rank population from REF sheet (Req 3.5, 3.7) ------------------------
    rank_lookup = _load_rank_lookup(path)
    rank_actual_col = _resolve_column(frame, VOLARE_RANK_COLUMN) or VOLARE_RANK_COLUMN
    ranks: list[int | None] = []
    for index in frame.index:
        key_value = frame.at[index, rank_key_col]
        key = "" if _is_blank(key_value) else str(key_value).strip().casefold()
        rank = rank_lookup.get(key)
        if rank is None:
            review_exceptions.append(
                VolareReviewException(
                    kind=ExceptionKind.MISSING_RANK,
                    source_row_ref=_row_ref(index),
                    detail=f"No REF rank match for disposition {key_value!r}.",
                )
            )
        ranks.append(rank)
    frame[rank_actual_col] = pd.array(ranks, dtype="Int64")

    return VolareLoadResult(frame=frame, review_exceptions=review_exceptions)


def load_master_file(path: str) -> pd.DataFrame:
    """Load the Master File workbook for the account-resolution adapter.

    Master data has no report-window filter.  Keeping this tiny reader beside
    the other pandas adapters ensures dataframe parsing remains outside the
    domain and orchestration layers.
    """
    return pd.read_excel(path)


__all__ = [
    "FieldResultColumnError",
    "VolareColumnError",
    "VolareLoadResult",
    "VolareReviewException",
    "load_field_result",
    "load_master_file",
    "load_volare_drr",
]


# ---------------------------------------------------------------------------
# Field Result adapter (task 1.9, Requirements 4.1-4.6)
# ---------------------------------------------------------------------------

_FIELD_RESULT_BANK_VALUE = "rcbc auto loan"
_FIELD_RESULT_CANCELLED_STATUS = "cancelled"

#: Candidate header spellings for the required Field Result filter columns.
#: Matched after trimming and case-folding so minor header variations resolve.
_FIELD_RESULT_BANK_COLUMNS = ("bank",)
_FIELD_RESULT_STATUS_COLUMNS = ("status",)
_FIELD_RESULT_VISIT_DATE_COLUMNS = ("visit_date", "visit date")

#: Boundaries of the contiguous column range preserved by the adapter (Req 4.4).
_FIELD_RESULT_RANGE_START = "ch code"
_FIELD_RESULT_RANGE_END = "ob"


class FieldResultColumnError(ValueError):
    """Raised when the Field Result upload is missing a required filter column.

    The filter operation halts, produces no output, and reports the offending
    column name via :attr:`missing_column` and the message (Requirement 4.5).
    """

    def __init__(self, missing_column: str) -> None:
        self.missing_column = missing_column
        super().__init__(
            f"Field Result upload is missing required column: {missing_column!r}"
        )


def _resolve_field_result_column(
    frame: pd.DataFrame, candidates: tuple[str, ...]
) -> str | None:
    """Return the actual column label matching one of ``candidates``.

    Matching trims surrounding whitespace and is case-insensitive. Returns
    ``None`` when no candidate matches.
    """
    normalized = {str(label).strip().casefold(): str(label) for label in frame.columns}
    for candidate in candidates:
        actual = normalized.get(candidate)
        if actual is not None:
            return actual
    return None


def _find_field_result_position(frame: pd.DataFrame, target: str) -> int | None:
    """Return the position of the column whose trimmed, folded label equals ``target``."""
    for position, label in enumerate(frame.columns):
        if str(label).strip().casefold() == target:
            return position
    return None


def _select_field_result_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Preserve the contiguous ``CH code``..``OB`` column range in original order.

    When either boundary column is absent the frame is returned unchanged so the
    filter output is never silently narrowed (Requirement 4.4).
    """
    start = _find_field_result_position(frame, _FIELD_RESULT_RANGE_START)
    end = _find_field_result_position(frame, _FIELD_RESULT_RANGE_END)
    if start is None or end is None or start > end:
        return frame
    selected = frame.columns[start : end + 1]
    return frame.loc[:, selected]


def load_field_result(path: str, date_from: date, date_to: date) -> pd.DataFrame:
    """Load the Field Result upload filtered to in-scope RCBC Auto Loan visits.

    pandas is confined to this adapter. The load keeps only rows whose bank
    column (trimmed, case-insensitive) equals ``RCBC Auto Loan``, drops rows
    whose status column (trimmed, case-insensitive) equals ``Cancelled``, and
    retains rows whose ``visit_date`` falls within the inclusive window
    ``[date_from, date_to]``. Rows with an empty or unparseable visit date are
    dropped. The contiguous ``CH code``..``OB`` column range is preserved in its
    original left-to-right order.

    Args:
        path: Readable ``.xlsx`` path under the protected storage root.
        date_from: Inclusive start of the report window.
        date_to: Inclusive end of the report window.

    Returns:
        The filtered frame limited to the preserved ``CH code``..``OB`` columns.

    Raises:
        FieldResultColumnError: If the bank, status, or visit-date column is
            absent. The operation halts and produces no output (Requirement 4.5).
    """
    frame = pd.read_excel(path)

    bank_column = _resolve_field_result_column(frame, _FIELD_RESULT_BANK_COLUMNS)
    if bank_column is None:
        raise FieldResultColumnError("bank")
    status_column = _resolve_field_result_column(frame, _FIELD_RESULT_STATUS_COLUMNS)
    if status_column is None:
        raise FieldResultColumnError("status")
    visit_date_column = _resolve_field_result_column(frame, _FIELD_RESULT_VISIT_DATE_COLUMNS)
    if visit_date_column is None:
        raise FieldResultColumnError("visit_date")

    bank_normalized = frame[bank_column].astype("string").str.strip().str.casefold()
    status_normalized = frame[status_column].astype("string").str.strip().str.casefold()
    visit_dates = pd.to_datetime(frame[visit_date_column], errors="coerce").dt.normalize()

    window_start = pd.Timestamp(date_from)
    window_end = pd.Timestamp(date_to)

    keep = (
        bank_normalized.eq(_FIELD_RESULT_BANK_VALUE)
        & ~status_normalized.eq(_FIELD_RESULT_CANCELLED_STATUS)
        & visit_dates.notna()
        & visit_dates.ge(window_start)
        & visit_dates.le(window_end)
    )

    filtered = frame.loc[keep.fillna(False)]
    return _select_field_result_columns(filtered)
