"""Lazy per-run orchestration for the RCBC Initial Demo.

The orchestrator is intentionally small and deterministic.  Spreadsheet
parsing stays in the ingestion adapters; this module consumes their typed
results, applies the domain sanitizer/extractor/hierarchy helpers, and records
one and only one disposition for every candidate row.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from mc03.domain.hierarchy import (
    classify_exclusion,
    classify_relation_result,
    csu_rank,
    select_rfd,
)
from mc03.domain.models import (
    Disposition,
    ExceptionKind,
    ProcessedRow,
    Relation,
    RunResult,
)
from mc03.domain.remarks import extract_remark, trim_to_200
from mc03.domain.sanitizer import sanitize_remark
from mc03.persistence.run_store import DEFAULT_RUN_STORE, RunStore
from mc03.services.extraction_gateway import ExtractionGateway
from mc03.services.ingestion import (
    VolareLoadResult,
    VolareReviewException,
    load_field_result,
    load_master_file,
    load_volare_drr,
)
from mc03.services.resolution import (
    ResolutionResult,
    ResolutionReviewException,
    resolve_accounts,
)

DEFAULT_RUN_ATTRIBUTION = "DA"

# Header spellings accepted by the orchestration boundary.  The adapters own
# strict required-column validation; these aliases only let the domain stage
# consume common spellings from the two source workbooks.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "ch_code": ("ch code", "ch_code", "chcode", "ch"),
    "account_number": ("account number", "account no", "account no.", "account_number"),
    "remark": ("remarks", "remark", "notes", "visit remarks", "comment"),
    "status": ("status", "disposition"),
    "contact_person": ("contact person", "contact_person", "contact", "name"),
    "contact_relation": (
        "contact relation",
        "contact_relation",
        "relation",
        "relationship",
    ),
    "statement": ("statement", "contact statement", "visit statement"),
    "client_sentiment": (
        "client sentiment",
        "client_sentiment",
        "client sentiment status",
        "csu client",
    ),
    "unit_sentiment": (
        "unit sentiment",
        "unit_sentiment",
        "unit sentiment status",
        "csu unit",
    ),
    "source_timestamp": (
        "call date",
        "call_date",
        "visit date",
        "visit_date",
        "timestamp",
        "source timestamp",
    ),
}


class PipelineError(RuntimeError):
    """Base error for a failed demo pipeline execution."""


class DispositionReconciliationError(PipelineError):
    """Raised when candidate and terminal disposition counts do not reconcile."""


class SourceProcessingError(PipelineError):
    """Raised when a source row cannot be represented safely in the result."""


def _normalized_header(value: object) -> str:
    """Normalize a header for tolerant lookup without changing source values."""
    return " ".join(str(value).strip().casefold().replace("_", " ").split())


def _find_column(frame: object, logical_name: str) -> object | None:
    """Find the first source column matching a known logical alias."""
    aliases = _COLUMN_ALIASES.get(logical_name, (logical_name,))
    normalized_aliases = {_normalized_header(alias) for alias in aliases}
    columns = cast(Iterable[object], getattr(frame, "columns", ()))
    for column in columns:
        if _normalized_header(column) in normalized_aliases:
            return column
    return None


def _iter_frame_rows(frame: object) -> Iterator[tuple[object, object]]:
    """Yield dataframe rows without importing pandas outside an adapter."""
    iterator = getattr(frame, "iterrows", None)
    if not callable(iterator):
        raise TypeError("source adapter did not return a dataframe-like object")
    yield from cast(Iterator[tuple[object, object]], iterator())


def _row_value(frame: object, row: object, logical_name: str) -> object | None:
    """Read one logical field from a dataframe row, if it exists."""
    column = _find_column(frame, logical_name)
    if column is None:
        return None
    getter = getattr(row, "get", None)
    if callable(getter):
        return cast(object, getter(column, None))
    try:
        return cast(object, row[column])  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None


def _as_text(value: object) -> str:
    """Convert a source value to trimmed text, treating null-like values as empty."""
    if value is None:
        return ""
    try:
        if bool(value != value):  # NaN-like values without importing pandas.
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "nat", "none"} else text


def _as_optional_text(value: object) -> str | None:
    """Return source text or ``None`` for an empty value."""
    text = _as_text(value)
    return text or None


def _parse_timestamp(value: object) -> datetime | None:
    """Convert common spreadsheet timestamp values to a timezone-naive datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    to_datetime = getattr(value, "to_pydatetime", None)
    if callable(to_datetime):
        converted = to_datetime()
        if isinstance(converted, datetime):
            return converted
    text = _as_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_ref(index: object) -> str:
    """Use the same source-row label as the ingestion and resolution adapters."""
    return f"row {index}"


def _exception_row(
    *,
    kind: ExceptionKind,
    source_row_ref: str,
    detail: str,
    ch_code: str | None = None,
    sanitized_remark: str = "",
    relation: Relation = Relation.UNKNOWN,
    account_number: str | None = None,
) -> ProcessedRow:
    """Create a safe review row for an exception raised before hierarchy rules."""
    evidence = sanitized_remark or detail
    return ProcessedRow(
        ch_code=ch_code or source_row_ref,
        account_number=account_number,
        relation=relation,
        csu_rank=0,
        selected_rfd=None,
        sanitized_remark=evidence,
        remark_length=len(evidence),
        disposition=Disposition.REVIEW_ONLY,
        exception_kinds=(kind,),
        source_row_ref=source_row_ref,
    )


def _normalize_volare_result(value: object) -> VolareLoadResult:
    """Accept the typed adapter result and a dataframe for simple integrations."""
    if isinstance(value, VolareLoadResult):
        return value
    if hasattr(value, "columns") and hasattr(value, "iterrows"):
        return VolareLoadResult(frame=cast(Any, value))
    raise TypeError("Volare adapter did not return a VolareLoadResult")


def _normalize_resolution_result(value: object) -> ResolutionResult:
    """Accept the typed resolver result and a compatible two-tuple."""
    if isinstance(value, ResolutionResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        resolved, exceptions = value
        if not hasattr(resolved, "columns"):
            raise TypeError("account resolver returned an invalid resolved frame")
        normalized: list[ResolutionReviewException] = []
        for item in cast(Iterable[object], exceptions):
            if isinstance(item, ResolutionReviewException):
                normalized.append(item)
            elif isinstance(item, str):
                normalized.append(
                    ResolutionReviewException(
                        kind=ExceptionKind.UNMAPPED_ACCOUNT,
                        source_row_ref=item,
                        detail=f"No valid Master File match for {item!r}.",
                    )
                )
            else:
                raise TypeError("account resolver returned an invalid review exception")
        return ResolutionResult(resolved=resolved, review_exceptions=normalized)
    raise TypeError("account resolver did not return a ResolutionResult")


def _source_key(frame: object, row: object) -> tuple[str, ...]:
    """Return non-empty CH/account keys for source-row matching."""
    values = (
        _row_value(frame, row, "ch_code"),
        _row_value(frame, row, "account_number"),
    )
    return tuple(_as_text(value) for value in values if _as_text(value))


def _build_source_index(frame: object) -> dict[str, list[tuple[object, object]]]:
    """Index Volare rows by CH/account identifiers when available."""
    indexed: dict[str, list[tuple[object, object]]] = {}
    for index, row in _iter_frame_rows(frame):
        for key in _source_key(frame, row):
            indexed.setdefault(key, []).append((index, row))
    return indexed


def _choose_volare_row(
    field_frame: object,
    field_index: object,
    field_row: object,
    volare_frame: object,
    volare_rows: list[tuple[object, object]],
    volare_index: Mapping[str, list[tuple[object, object]]],
) -> tuple[object, object] | None:
    """Choose the most specific Volare row for one resolved Field row."""
    for key in _source_key(field_frame, field_row):
        matches = volare_index.get(key)
        if matches:
            return matches[0]
    for index, row in volare_rows:
        if index == field_index:
            return index, row
    return None


def _field_row_by_ref(field_frame: object, source_row_ref: str) -> tuple[object, object] | None:
    """Find the original filtered Field row referenced by a resolution exception."""
    for index, row in _iter_frame_rows(field_frame):
        if _row_ref(index) == source_row_ref:
            return index, row
    return None


def _row_inputs(
    field_frame: object,
    field_index: object,
    field_row: object,
    volare_frame: object,
    volare_row: object | None,
) -> dict[str, object]:
    """Collect normalized rule inputs from Field and optional Volare rows."""
    source_frame = volare_frame if volare_row is not None else field_frame
    source = volare_row if volare_row is not None else field_row

    def first(logical_name: str) -> object | None:
        source_value = _row_value(source_frame, source, logical_name)
        if _as_text(source_value):
            return source_value
        return _row_value(field_frame, field_row, logical_name)

    return {
        "ch_code": _as_text(_row_value(field_frame, field_row, "ch_code")),
        "account_number": _as_optional_text(
            _row_value(field_frame, field_row, "account_number")
        ),
        "raw_remark": _as_text(first("remark")),
        "status": _as_optional_text(first("status")),
        "contact_person": _as_optional_text(
            _row_value(field_frame, field_row, "contact_person")
        ),
        "contact_relation": _as_text(
            _row_value(field_frame, field_row, "contact_relation")
        ),
        "statement": _as_optional_text(_row_value(field_frame, field_row, "statement")),
        "client_sentiment": _as_optional_text(first("client_sentiment")),
        "unit_sentiment": _as_optional_text(first("unit_sentiment")),
        "source_timestamp": _parse_timestamp(first("source_timestamp")),
        "field_index": field_index,
    }


def _process_candidate(
    inputs: Mapping[str, object],
    gateway: ExtractionGateway,
) -> ProcessedRow:
    """Apply sanitization, extraction, relation, CSU, RFD, and trim rules."""
    raw_remark = cast(str, inputs["raw_remark"])
    sanitized = sanitize_remark(raw_remark)
    extraction = extract_remark(sanitized, gateway)
    exception_kinds: list[ExceptionKind] = list(extraction.exception_kinds)

    relation_text = cast(str, inputs["contact_relation"])
    relation_result = classify_relation_result(relation_text)
    exception_kinds.extend(relation_result.exception_kinds)

    client_sentiment = cast(str | None, inputs["client_sentiment"])
    unit_sentiment = cast(str | None, inputs["unit_sentiment"])
    rank = 0
    if client_sentiment is None and unit_sentiment is None:
        exception_kinds.append(ExceptionKind.INVALID_CSU)
    else:
        try:
            rank = csu_rank(client_sentiment, unit_sentiment)
        except ValueError:
            exception_kinds.append(ExceptionKind.INVALID_CSU)

    contact_person = cast(str | None, inputs["contact_person"])
    statement = cast(str | None, inputs["statement"]) or extraction.remarks
    if contact_person is None and extraction.contact_person:
        contact_person = extraction.contact_person
    if statement is None and extraction.statement:
        statement = extraction.statement

    trimmed_remark, fits = trim_to_200(contact_person, statement)
    if not contact_person or not statement:
        exception_kinds.append(ExceptionKind.MISSING_REMARK_CONTENT)
    elif not fits:
        exception_kinds.append(ExceptionKind.REMARK_TOO_LONG)

    # Preserve order while avoiding duplicate exception badges in the queue.
    unique_exceptions = tuple(dict.fromkeys(exception_kinds))
    exclusion = classify_exclusion(
        status=cast(str | None, inputs["status"]),
        remark=raw_remark,
        exception_kinds=unique_exceptions,
    )
    selected_rfd = select_rfd([extraction.fields])
    if fits and trimmed_remark:
        output_remark = trimmed_remark
    elif sanitized:
        output_remark = sanitized
    else:
        output_remark = trimmed_remark

    disposition = exclusion.disposition
    if unique_exceptions:
        disposition = Disposition.REVIEW_ONLY

    return ProcessedRow(
        ch_code=cast(str, inputs["ch_code"]),
        account_number=cast(str | None, inputs["account_number"]),
        relation=relation_result.relation,
        csu_rank=rank,
        selected_rfd=selected_rfd,
        sanitized_remark=output_remark,
        remark_length=len(output_remark),
        disposition=disposition,
        exception_kinds=unique_exceptions,
        source_row_ref=_row_ref(inputs["field_index"]),
        contact_person=contact_person,
        statement=statement,
        source_timestamp=cast(datetime | None, inputs["source_timestamp"]),
    )


def _review_from_resolution(
    exception: ResolutionReviewException,
    field_frame: object,
) -> ProcessedRow:
    """Materialize a resolution exception without dropping source row evidence."""
    source = _field_row_by_ref(field_frame, exception.source_row_ref)
    if source is None:
        return _exception_row(
            kind=exception.kind,
            source_row_ref=exception.source_row_ref,
            detail=exception.detail,
        )
    _, row = source
    ch_code = _as_optional_text(_row_value(field_frame, row, "ch_code"))
    raw_remark = _as_text(_row_value(field_frame, row, "remark"))
    relation_text = _as_text(_row_value(field_frame, row, "contact_relation"))
    relation = classify_relation_result(relation_text).relation
    return _exception_row(
        kind=exception.kind,
        source_row_ref=exception.source_row_ref,
        detail=exception.detail,
        ch_code=ch_code,
        sanitized_remark=sanitize_remark(raw_remark),
        relation=relation,
    )


def _review_from_volare(exception: VolareReviewException) -> ProcessedRow:
    """Materialize an ingestion exception for the review queue."""
    return _exception_row(
        kind=exception.kind,
        source_row_ref=exception.source_row_ref,
        detail=exception.detail,
    )


def _attach_volare_exception(
    exception: VolareReviewException,
    clean_rows: list[ProcessedRow],
    review_rows: list[ProcessedRow],
) -> bool:
    """Attach a Volare exception to an existing candidate when row refs match.

    Missing rank evidence commonly belongs to the same source row that is later
    resolved from Field Result.  Attaching it prevents that row from being
    counted twice while still forcing the combined candidate to Review_Only.
    """
    for index, row in enumerate(clean_rows):
        if row.source_row_ref != exception.source_row_ref:
            continue
        updated = replace(
            row,
            disposition=Disposition.REVIEW_ONLY,
            exception_kinds=tuple(dict.fromkeys((*row.exception_kinds, exception.kind))),
        )
        clean_rows.pop(index)
        review_rows.append(updated)
        return True
    for index, row in enumerate(review_rows):
        if row.source_row_ref != exception.source_row_ref:
            continue
        review_rows[index] = replace(
            row,
            exception_kinds=tuple(dict.fromkeys((*row.exception_kinds, exception.kind))),
        )
        return True
    return False


def process_run(
    date_from: date,
    date_to: date,
    volare_path: str | Path,
    field_path: str | Path,
    master_path: str | Path,
    gateway: ExtractionGateway | None = None,
    run_store: RunStore | None = None,
    *,
    store: RunStore | None = None,
    attribution: str = DEFAULT_RUN_ATTRIBUTION,
    run_id: str | None = None,
) -> RunResult:
    """Execute one lazy RCBC pipeline and store its reconciled result.

    The candidate population is the filtered Field Result population, including
    each resolution exception, plus the independent Volare ingestion exceptions.
    Every candidate is represented exactly once in a clean row, review row, or
    excluded count.  The result is stored only after the reconciliation check
    succeeds; a failed run cannot leave a partial result in the store.

    Args:
        date_from: Inclusive report-window start.
        date_to: Inclusive report-window end.
        volare_path: Path to the uploaded Volare DRR workbook.
        field_path: Path to the uploaded Field Result workbook.
        master_path: Path to the uploaded Master File workbook.
        gateway: Optional extraction gateway.  A safe unavailable gateway is
            created when omitted, causing free-text rows to require review.
        run_store: Optional process-lifetime store.  The application default is
            used when omitted.
        store: Compatibility alias for ``run_store``; both must not be supplied
            as different store instances.
        attribution: Accepted for integration compatibility but normalized to
            the required exact demo value ``"DA"``.
        run_id: Optional deterministic id for callers/tests; otherwise generated.

    Raises:
        DispositionReconciliationError: If terminal dispositions do not equal
            the candidate population.
        ValueError: If the date window is invalid.
    """
    del attribution  # Requirement 1.3 fixes the demo attribution to exactly DA.
    if date_from > date_to:
        raise ValueError("date_from must not be later than date_to")

    extraction_gateway = gateway if gateway is not None else ExtractionGateway()
    if run_store is not None and store is not None and run_store is not store:
        raise ValueError("run_store and store must refer to the same RunStore")
    selected_store = (
        run_store
        if run_store is not None
        else store
        if store is not None
        else DEFAULT_RUN_STORE
    )

    volare_loaded = _normalize_volare_result(
        load_volare_drr(str(volare_path), date_from, date_to)
    )
    field_frame = load_field_result(str(field_path), date_from, date_to)
    master_frame = load_master_file(str(master_path))
    resolution = _normalize_resolution_result(resolve_accounts(field_frame, master_frame))

    volare_rows = list(_iter_frame_rows(volare_loaded.frame))
    volare_index = _build_source_index(volare_loaded.frame)
    clean_rows: list[ProcessedRow] = []
    review_rows: list[ProcessedRow] = []
    excluded_count = 0

    for field_index, field_row in _iter_frame_rows(resolution.resolved):
        selected_volare = _choose_volare_row(
            field_frame,
            field_index,
            field_row,
            volare_loaded.frame,
            volare_rows,
            volare_index,
        )
        volare_row = selected_volare[1] if selected_volare is not None else None
        inputs = _row_inputs(
            field_frame,
            field_index,
            field_row,
            volare_loaded.frame,
            volare_row,
        )
        # Resolution adds the account column to the resolved frame; prefer that
        # assigned value over any similarly named source value.
        inputs = dict(inputs)
        inputs["account_number"] = _as_optional_text(
            _row_value(resolution.resolved, field_row, "account_number")
        ) or cast(str | None, inputs["account_number"])
        processed = _process_candidate(inputs, extraction_gateway)
        if processed.disposition is Disposition.CLEAN:
            clean_rows.append(processed)
        elif processed.disposition is Disposition.REVIEW_ONLY:
            review_rows.append(processed)
        else:
            excluded_count += 1

    for resolution_exception in resolution.review_exceptions:
        review_rows.append(_review_from_resolution(resolution_exception, field_frame))
    unmatched_volare_exceptions: list[VolareReviewException] = []
    for volare_exception in volare_loaded.review_exceptions:
        if not _attach_volare_exception(volare_exception, clean_rows, review_rows):
            unmatched_volare_exceptions.append(volare_exception)
            review_rows.append(_review_from_volare(volare_exception))

    expected_candidates = (
        len(resolution.resolved)
        + len(resolution.review_exceptions)
        + len(unmatched_volare_exceptions)
    )
    terminal_count = len(clean_rows) + len(review_rows) + excluded_count
    if terminal_count != expected_candidates:
        raise DispositionReconciliationError(
            "disposition reconciliation mismatch: "
            f"candidates={expected_candidates}, clean={len(clean_rows)}, "
            f"review={len(review_rows)}, excluded={excluded_count}"
        )

    result = RunResult(
        run_id=run_id or uuid4().hex,
        attribution=DEFAULT_RUN_ATTRIBUTION,
        date_from=date_from,
        date_to=date_to,
        clean_rows=clean_rows,
        review_rows=review_rows,
        excluded_count=excluded_count,
    )
    selected_store.store(result)
    return result


__all__ = [
    "DEFAULT_RUN_ATTRIBUTION",
    "DispositionReconciliationError",
    "PipelineError",
    "SourceProcessingError",
    "process_run",
]
