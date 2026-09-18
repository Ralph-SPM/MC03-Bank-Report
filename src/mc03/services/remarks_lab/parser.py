"""XLSX parser for RCBC FIELD RSULT sheet with flexible column resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
import io
import pandas as pd


from datetime import date, datetime


@dataclass
class RawRemarkRow:
    """Extracted raw row from the FIELD RSULT sheet."""
    row_index: int
    account_number: str
    ch_code: str
    contact_person: str
    contact_relation: str
    raw_remarks: str
    manual_csu: str
    manual_rfd: str
    extra_fields: dict[str, Any]
    row_date: str = ""
    client_status: str = ""
    unit_status: str = ""
    raw_message: str = ""


class ParsedRows(list):
    """List of RawRemarkRow that also tracks total rows before date filtering."""
    total_unfiltered_rows: int = 0


COLUMN_ALIASES = {
    "account_number": [
        "account number", "account_number", "acct no", "acct_no", "acct#", "account_no",
        "account", "loan account no", "loan_account_no", "acct", "loan no", "account_num"
    ],
    "ch_code": [
        "ch code", "ch_code", "chcode", "cardholder code", "card holder code", "client code", "ch_id", "ch"
    ],
    "contact_person": [
        "contact person", "contact_person", "contact name", "person contacted", "contact",
        "spoke to", "contact person name", "name", "borrower"
    ],
    "contact_relation": [
        "contact relation", "contact_relation", "relation", "relationship", "relation to ch",
        "contact's relation", "contact relation to borrower"
    ],
    "remarks": [
        "remarks", "field remarks", "raw remarks", "statement", "collector remarks",
        "field result remarks", "field notes", "result remarks", "notes", "remark",
        "sanitized_remark", "sanitized remark", "remark_text"
    ],
    "manual_csu": [
        "collection status update", "collection status", "manual csu", "manual_csu",
        "csu status", "csu result", "csu code", "da csu", "csu_rank", "csu rank", "csu", "status"
    ],
    "manual_rfd": [
        "manual rfd", "manual_rfd", "rfd", "rfd code", "rfd result", "da rfd",
        "reason for default", "selected_rfd", "selected rfd"
    ],
    "client_status": [
        "client status", "client_status", "client status_1"
    ],
    "unit_status": [
        "unit status", "unit_status", "unit status_1"
    ],
    "raw_message": [
        "message", "raw_message", "collector_message", "field_message", "raw message"
    ],
    "date": [
        "visit_date", "visit date", "visit_datetime", "call date", "call_date", "date",
        "source timestamp", "source_timestamp", "timestamp", "report_date", "report date",
        "date of visit", "trans_date", "transaction date", "created_date", "visit date / time",
        "endorsement date", "date visited", "datetime", "call_datetime"
    ]
}


def _find_column_match(columns: list[str], alias_key: str) -> Optional[str]:
    """Find matching column name case-insensitively using aliases, prioritizing exact matches then substring matches."""
    normalized_cols = {str(c).strip().lower(): c for c in columns if c is not None}
    # 1. Exact match check against aliases
    for alias in COLUMN_ALIASES.get(alias_key, []):
        a_low = alias.lower()
        if a_low in normalized_cols:
            return normalized_cols[a_low]
    # 2. Substring match for alias within header
    for alias in COLUMN_ALIASES.get(alias_key, []):
        a_low = alias.lower()
        if len(a_low) > 3:  # avoid short alias false positives like 'ch' or 'csu'
            for norm, original in normalized_cols.items():
                if a_low in norm:
                    return original
    # 3. Fallback check for alias_key
    for norm, original in normalized_cols.items():
        if alias_key.replace("_", " ") in norm:
            return original
    return None


def _is_null_value(val: Any) -> bool:
    """Safely test for None/NaN without raising ValueError on numpy arrays or Series."""
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (ValueError, TypeError):
        return False


def _deduplicate_headers(headers: list[Any]) -> list[str]:
    """Ensure column names are unique so row[col] returns a scalar, not a Series."""
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for h in headers:
        base = str(h).strip() if not _is_null_value(h) else ""
        if not base:
            base = "Unnamed"
        lower_base = base.lower()
        if lower_base in seen:
            seen[lower_base] += 1
            deduped.append(f"{base}_{seen[lower_base]}")
        else:
            seen[lower_base] = 0
            deduped.append(base)
    return deduped


def _parse_row_date(val: Any) -> Optional[pd.Timestamp]:
    """Parse cell value into a normalized date Timestamp."""
    if _is_null_value(val):
        return None
    if isinstance(val, (pd.Timestamp, datetime, date)):
        try:
            return pd.Timestamp(val).normalize()
        except Exception:
            return None
    if isinstance(val, (int, float)):
        if 35000 <= val <= 60000:
            try:
                return pd.to_datetime(val, unit="D", origin="1899-12-30").normalize()
            except Exception:
                pass
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        parsed = pd.to_datetime(val_str, errors="coerce")
        if pd.notna(parsed):
            return pd.Timestamp(parsed).normalize()
    except Exception:
        pass
    return None


def parse_field_result_sheet(
    source: str | Path | bytes | io.BytesIO,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> ParsedRows:
    """
    Parse the RCBC workbook targeting the FIELD RSULT (or similar) sheet.
    Handles typos like 'FIELD RSULT', 'FIELD RESULT', 'FIELD_RESULT', or falls back to sheet 0.
    Supports metadata header offsets (e.g., Output_Mapping_Regions=A2:H10 on row 1),
    duplicate column names, flexible column aliases, and optional date-window filtering.
    """
    if isinstance(source, (str, Path)):
        excel_file = pd.ExcelFile(source)
    else:
        if isinstance(source, bytes):
            source = io.BytesIO(source)
        excel_file = pd.ExcelFile(source)

    target_sheet = None
    sheet_names = excel_file.sheet_names

    # Priority sheet name matching
    preferred = ["FIELD RSULT", "FIELD RESULT", "FIELD_RESULT", "FIELD RESULTS", "FIELD_RSULT"]
    for pref in preferred:
        for s in sheet_names:
            if s.strip().upper() == pref.upper():
                target_sheet = s
                break
        if target_sheet:
            break

    if not target_sheet:
        # Fallback search for sheet containing 'FIELD'
        for s in sheet_names:
            if "FIELD" in s.strip().upper():
                target_sheet = s
                break

    if not target_sheet:
        # Fallback to first sheet
        target_sheet = sheet_names[0]

    # Parse raw without header to discover true header row offset
    df_raw = excel_file.parse(sheet_name=target_sheet, header=None)
    if df_raw.empty:
        return ParsedRows()

    # Flatten all alias terms for header detection
    all_alias_terms = set()
    for aliases in COLUMN_ALIASES.values():
        for a in aliases:
            all_alias_terms.add(a.lower())

    best_header_row = 0
    max_matches = 0

    scan_limit = min(15, len(df_raw))
    for r_idx in range(scan_limit):
        row_vals = [str(v).strip().lower() for v in df_raw.iloc[r_idx].values if not _is_null_value(v)]
        matches = 0
        for val in row_vals:
            if val in all_alias_terms or any(alias in val for alias in all_alias_terms):
                matches += 1
        if matches > max_matches:
            max_matches = matches
            best_header_row = r_idx

    # Extract headers and data, deduplicating duplicate headers
    raw_headers = df_raw.iloc[best_header_row].values
    headers = _deduplicate_headers(list(raw_headers))
    df = df_raw.iloc[best_header_row + 1:].copy()
    df.columns = headers

    # Map columns
    col_map = {
        "account_number": _find_column_match(df.columns, "account_number"),
        "ch_code": _find_column_match(df.columns, "ch_code"),
        "contact_person": _find_column_match(df.columns, "contact_person"),
        "contact_relation": _find_column_match(df.columns, "contact_relation"),
        "remarks": _find_column_match(df.columns, "remarks"),
        "manual_csu": _find_column_match(df.columns, "manual_csu"),
        "manual_rfd": _find_column_match(df.columns, "manual_rfd"),
        "date": _find_column_match(df.columns, "date"),
        "client_status": _find_column_match(df.columns, "client_status"),
        "unit_status": _find_column_match(df.columns, "unit_status"),
        "raw_message": _find_column_match(df.columns, "raw_message"),
    }

    # Fallback search for date column if not matched via aliases
    if not col_map.get("date"):
        for c in df.columns:
            c_low = str(c).lower()
            if "date" in c_low or "time" in c_low:
                col_map["date"] = c
                break

    # Date filter window normalization
    d_from = date_from
    d_to = date_to
    if d_from is not None and d_to is None:
        d_to = d_from
    elif d_to is not None and d_from is None:
        d_from = d_to

    window_start = pd.Timestamp(d_from).normalize() if d_from is not None else None
    window_end = pd.Timestamp(d_to).normalize() if d_to is not None else None
    has_date_filter = window_start is not None and window_end is not None

    rows = ParsedRows()
    total_valid_rows = 0

    def _get_val(row_data: Any, col_key: str) -> str:
        col = col_map.get(col_key)
        if not col or col not in row_data:
            return ""
        val = row_data[col]
        if isinstance(val, (pd.Series, list, tuple)):
            items = [str(v).strip() for v in val if not _is_null_value(v)]
            val = items[0] if items else ""
        if _is_null_value(val):
            return ""
        text = str(val).strip()
        return "" if text.casefold() in {"nan", "nat", "none", "<na>"} else text

    header_excel_offset = best_header_row + 1  # 1-indexed

    for idx, row in df.iterrows():
        remarks_val = _get_val(row, "remarks")
        acct_val = _get_val(row, "account_number")
        ch_val = _get_val(row, "ch_code")

        # Skip only if all key identifying fields are empty
        if not remarks_val and not acct_val and not ch_val:
            continue

        total_valid_rows += 1

        # Resolve row date
        date_raw = _get_val(row, "date")
        parsed_dt = _parse_row_date(date_raw)

        if parsed_dt is None:
            # Fallback scan other columns for valid date
            for col_name in df.columns:
                c_low = str(col_name).lower()
                if "date" in c_low or "time" in c_low or "visit" in c_low:
                    parsed_dt = _parse_row_date(row[col_name])
                    if parsed_dt is not None:
                        break

        # Apply date filter
        if has_date_filter:
            if parsed_dt is None:
                continue
            if not (window_start <= parsed_dt <= window_end):
                continue

        row_date_str = parsed_dt.strftime("%Y-%m-%d") if parsed_dt is not None else (str(date_raw) if date_raw else "")
        excel_row_num = header_excel_offset + (int(idx) - best_header_row)

        raw_row = RawRemarkRow(
            row_index=excel_row_num,
            account_number=acct_val,
            ch_code=ch_val,
            contact_person=_get_val(row, "contact_person"),
            contact_relation=_get_val(row, "contact_relation"),
            raw_remarks=remarks_val,
            manual_csu=_get_val(row, "manual_csu"),
            manual_rfd=_get_val(row, "manual_rfd"),
            extra_fields={str(k): str(v) for k, v in row.items() if not _is_null_value(v)},
            row_date=row_date_str,
            client_status=_get_val(row, "client_status"),
            unit_status=_get_val(row, "unit_status"),
            raw_message=_get_val(row, "raw_message"),
        )
        rows.append(raw_row)

    rows.total_unfiltered_rows = total_valid_rows
    return rows
