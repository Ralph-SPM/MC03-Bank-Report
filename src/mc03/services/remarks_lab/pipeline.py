"""End-to-end Remarks Lab Pipeline and Benchmark comparison engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional
import io

from .parser import parse_field_result_sheet, RawRemarkRow
from .cleaner import clean_remark, CleanedRemark
from .classifier import classify_contact, ClassifiedRelation
from .ranker import rank_rfd_and_csu, RankedResult
from .trimmer import trim_and_format, TrimmedRemark, ECA_HEADER_PATTERN
from .groq_summarizer import GroqRemarksSummarizer


from datetime import date
import re


def _normalize_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _is_csu_match(predicted: str, actual: str) -> bool:
    if not actual or not actual.strip():
        return True
    p = _normalize_norm(predicted)
    a = _normalize_norm(actual)
    if not p or not a:
        return False
    if p == a or p in a or a in p:
        return True

    # Semantic alias groups
    repo_terms = {"repo", "repossessed", "pullout", "pulled out", "surrender", "surrendered"}
    if any(t in p for t in repo_terms) and any(t in a for t in repo_terms):
        return True

    ptp_terms = {"ptp", "promise to pay", "client positive"}
    if any(t in p for t in ptp_terms) and any(t in a for t in ptp_terms):
        return True

    resolved_terms = {"resolved", "already resolved", "paid off", "fully paid", "cleared"}
    if any(t in p for t in resolved_terms) and any(t in a for t in resolved_terms):
        return True

    no_contact_terms = {
        "no contact", "client negative", "unit negative", "house closed",
        "unlocated", "for further visit", "probing", "uncontacted", "not around"
    }
    if any(t in p for t in no_contact_terms) and any(t in a for t in no_contact_terms):
        return True

    return False


def _is_rfd_match(predicted: str, actual: str) -> bool:
    if not actual or not actual.strip():
        return True
    p = _normalize_norm(predicted)
    a = _normalize_norm(actual)
    if not p or not a:
        return False
    if p == a or p in a or a in p:
        return True

    # Borrower Refused
    if "borrower refused" in p and ("borrower refused" in a or "client refused" in a or "ch refused" in a):
        return True

    # Representative Refused
    if "representative refused" in p and "representative refused" in a:
        return True

    # No Reach / Uncontacted
    no_reach_terms = {"no client", "no client representative reached", "no representative reached", "no contact", "uncontacted"}
    if any(t in p for t in no_reach_terms) and any(t in a for t in no_reach_terms):
        return True

    # Hardship mappings
    hardship_pairs = [
        ("medical", "medical"),
        ("diversion", "diversion"),
        ("salary", "salary"),
        ("collection", "collection"),
        ("business", "business"),
        ("slowdown", "slowdown"),
        ("remittance", "remittance"),
        ("third party", "third party"),
        ("moved", "moved"),
        ("calamity", "calamity"),
        ("flood", "calamity"),
        ("deceased", "deceased"),
        ("lto", "lto"),
        ("impounded", "lto"),
        ("scam", "scam"),
        ("relocation", "relocation"),
        ("recon", "recon"),
    ]
    for h1, h2 in hardship_pairs:
        if h1 in p and h2 in a:
            return True

    return False



@dataclass
class RowResult:
    """Consolidated analysis for a single row in the benchmark view."""
    row_index: int
    account_number: str
    ch_code: str
    contact_person: str
    contact_relation: str
    raw_remarks: str

    # Cleaned & Stripped
    cleaned_remarks: str
    prohibited_tags_stripped: List[str]

    # Relation Classification
    category_label: str       # "Representative", "Informant", "Cardholder", "Unknown"
    normalized_role: str
    matched_relation_keyword: Optional[str]

    # Predicted Hierarchy
    predicted_rfd: str
    predicted_csu: str
    hierarchy_source: str

    # Trimmed Remark (Bank Upload Formatter)
    trimmed_statement: str
    original_char_count: int
    final_char_count: int
    truncated: bool

    # Ground Truth Comparison
    manual_csu: str
    manual_rfd: str
    detailed_rfd: str = ""
    csu_match: bool = False
    rfd_match: bool = False
    discrepancy_flag: bool = False    # True if either CSU or RFD diverges from ground truth (when manual provided)
    row_date: str = ""
    raw_message: str = ""
    ai_summarized: bool = False


@dataclass
class BenchmarkReport:
    """Summary metrics and row details for the benchmark view."""
    total_rows: int
    sanitized_tag_count: int
    csu_accuracy_pct: float
    rfd_accuracy_pct: float
    char_overflow_prevented_count: int
    rows: List[RowResult] = field(default_factory=list)

    # Breakdown counters
    representative_count: int = 0
    informant_count: int = 0
    cardholder_count: int = 0
    unknown_relation_count: int = 0
    discrepancy_count: int = 0
    rows_with_ground_truth: int = 0
    ai_summarized_count: int = 0
    ai_summarizer_model: str = ""
    ai_batch_enabled: bool = False

    # Date filter metadata
    total_unfiltered_rows: int = 0
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class RemarksLabPipeline:
    """Coordinates parsing, cleaning, classification, ranking, and benchmarking."""

    def __init__(self, summarizer: Optional[GroqRemarksSummarizer] = None):
        self.summarizer = summarizer if summarizer is not None else GroqRemarksSummarizer()

    def process_file(
        self,
        source: str | Path | bytes | io.BytesIO,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> BenchmarkReport:
        raw_rows = parse_field_result_sheet(source, date_from=date_from, date_to=date_to)
        report = self.process_rows(raw_rows)
        report.total_unfiltered_rows = getattr(raw_rows, "total_unfiltered_rows", len(raw_rows))
        report.date_from = date_from.isoformat() if date_from else None
        report.date_to = date_to.isoformat() if date_to else None
        return report

    def process_rows(self, raw_rows: List[RawRemarkRow]) -> BenchmarkReport:
        results: List[RowResult] = []

        total_tags_stripped = 0
        overflow_prevented = 0

        csu_matches = 0
        rfd_matches = 0
        ground_truth_rows = 0

        rep_count = 0
        inf_count = 0
        ch_count = 0
        unk_count = 0
        discrepancies = 0
        ai_summarized_count = 0

        for r in raw_rows:
            # Determine narrative text sources
            has_raw_msg = bool(r.raw_message and len(r.raw_message.strip()) > 0)
            eca_match = ECA_HEADER_PATTERN.match(r.raw_remarks)
            eca_header = eca_match.group(1) if eca_match else ""

            if has_raw_msg:
                raw_msg_clean = r.raw_message.strip()
                if eca_header and not raw_msg_clean.upper().startswith("ECA "):
                    trim_source = f"{eca_header}{raw_msg_clean}"
                else:
                    trim_source = raw_msg_clean
                analysis_narrative = f"{r.raw_remarks} {raw_msg_clean}".strip()
            else:
                trim_source = r.raw_remarks
                analysis_narrative = r.raw_remarks

            # 1. Clean & Sanitize
            cleaned_res: CleanedRemark = clean_remark(trim_source)
            total_tags_stripped += len(cleaned_res.detected_prohibited_tags)

            # 2. Classify Relation
            relation_res: ClassifiedRelation = classify_contact(
                r.contact_person,
                r.contact_relation,
                analysis_narrative,
                extra_fields=r.extra_fields,
            )

            if relation_res.category_label == "Representative":
                rep_count += 1
            elif relation_res.category_label == "Informant":
                inf_count += 1
            elif relation_res.category_label in ("Borrower", "Cardholder"):
                ch_count += 1
            else:
                unk_count += 1

            # 3. Hierarchy & RFD Ranker
            ranked_res: RankedResult = rank_rfd_and_csu(
                analysis_narrative,
                cleaned_res.cleaned,
                relation_res.category_label,
                relation_res.normalized_role,
            )

            # 4. 200-char Trimmer with Groq AI Summarizer
            llm_prov = self.summarizer.summarize if (self.summarizer and self.summarizer.is_available) else None
            trimmed_res: TrimmedRemark = trim_and_format(
                cleaned_res.cleaned,
                contact_person=r.contact_person,
                llm_provider=llm_prov,
            )
            if trimmed_res.original_length > 200 and trimmed_res.fits_within_200:
                overflow_prevented += 1

            is_ai_used = bool(
                self.summarizer and self.summarizer.is_available and trimmed_res.was_truncated
            )
            if is_ai_used:
                ai_summarized_count += 1

            # 5. Compare with Manual Ground Truth
            has_gt = bool(r.manual_csu or r.manual_rfd)
            csu_match = False
            rfd_match = False

            if r.manual_csu:
                csu_match = _is_csu_match(ranked_res.csu_status, r.manual_csu)
            else:
                csu_match = True  # neutral if no ground truth

            if r.manual_rfd:
                rfd_match = _is_rfd_match(ranked_res.rfd_code, r.manual_rfd)
            else:
                rfd_match = True

            if has_gt:
                ground_truth_rows += 1
                if csu_match:
                    csu_matches += 1
                if rfd_match:
                    rfd_matches += 1

            discrepancy = has_gt and (not csu_match or not rfd_match)
            if discrepancy:
                discrepancies += 1

            row_res = RowResult(
                row_index=r.row_index,
                account_number=r.account_number,
                ch_code=r.ch_code,
                contact_person=r.contact_person,
                contact_relation=r.contact_relation,
                raw_remarks=r.raw_remarks,
                row_date=r.row_date,
                cleaned_remarks=cleaned_res.cleaned,
                prohibited_tags_stripped=cleaned_res.detected_prohibited_tags,
                category_label=relation_res.category_label,
                normalized_role=relation_res.normalized_role,
                matched_relation_keyword=relation_res.matched_keyword,
                predicted_rfd=ranked_res.rfd_code,
                predicted_csu=ranked_res.csu_status,
                hierarchy_source=ranked_res.rule_source,
                trimmed_statement=trimmed_res.trimmed_text,
                original_char_count=trimmed_res.original_length,
                final_char_count=trimmed_res.trimmed_length,
                truncated=trimmed_res.was_truncated,
                manual_csu=r.manual_csu,
                manual_rfd=r.manual_rfd,
                detailed_rfd=ranked_res.detailed_rfd,
                csu_match=csu_match,
                rfd_match=rfd_match,
                discrepancy_flag=discrepancy,
                raw_message=r.raw_message,
                ai_summarized=is_ai_used,
            )
            results.append(row_res)

        total_rows = len(results)
        csu_acc = round((csu_matches / ground_truth_rows * 100), 1) if ground_truth_rows > 0 else 100.0
        rfd_acc = round((rfd_matches / ground_truth_rows * 100), 1) if ground_truth_rows > 0 else 100.0

        return BenchmarkReport(
            total_rows=total_rows,
            sanitized_tag_count=total_tags_stripped,
            csu_accuracy_pct=csu_acc,
            rfd_accuracy_pct=rfd_acc,
            char_overflow_prevented_count=overflow_prevented,
            rows=results,
            representative_count=rep_count,
            informant_count=inf_count,
            cardholder_count=ch_count,
            unknown_relation_count=unk_count,
            discrepancy_count=discrepancies,
            rows_with_ground_truth=ground_truth_rows,
            ai_summarized_count=ai_summarized_count,
            ai_summarizer_model=self.summarizer.model if self.summarizer and self.summarizer.is_available else "",
            ai_batch_enabled=self.summarizer.batch_processing if self.summarizer else False,
        )

    def export_to_excel(self, report: BenchmarkReport) -> io.BytesIO:
        """Export evaluated rows into bank-compliant Excel format with OKAY NA TO checker."""
        import pandas as pd
        data = []
        for r in report.rows:
            data.append({
                "Account Number": r.account_number,
                "CH Code": r.ch_code,
                "Contact Person": r.contact_person,
                "Contact Relation": r.contact_relation,
                "Category Label": r.category_label,
                "COLLECTION STATUS UPDATE": r.predicted_csu,
                "RFD": r.predicted_rfd,
                "DETAILED RFD": r.detailed_rfd,
                "FINAL REMARKS": r.trimmed_statement,
                "Character Count": r.final_char_count,
                "char checker": "OKAY NA TO" if r.final_char_count <= 200 else "PLS REVISE",
            })
        df = pd.DataFrame(data)
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="FIELD RESULT (EVALUATED)", index=False)
        out.seek(0)
        return out
