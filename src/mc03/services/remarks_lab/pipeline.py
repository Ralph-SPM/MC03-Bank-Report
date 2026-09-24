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
from engine.router import measure_batch_detection
from engine.summarizer import TwoTierRemarksSummarizer, get_summarizer


from datetime import date
import re


def _normalize_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _is_csu_match(predicted: str, actual: str) -> bool:
    p = _normalize_norm(predicted)
    a = _normalize_norm(actual)
    if not a:
        return not p
    if not p:
        return not a
    if p == a:
        return True

    # 1. Guardrail against WITH vs WITHOUT mismatch
    # (WITH Actual Contact and WITHOUT Actual Contact are distinct operational states and must NEVER match)
    p_without = "without actual contact" in p or "without contact" in p
    p_with = ("with actual contact" in p or "with contact" in p) and not p_without

    a_without = "without actual contact" in a or "without contact" in a
    a_with = ("with actual contact" in a or "with contact" in a) and not a_without

    if (p_with and a_without) or (p_without and a_with):
        return False

    # 2. Guardrail against CLIENT POSITIVE vs CLIENT NEGATIVE mismatch
    p_c_pos = "client positive" in p
    p_c_neg = "client negative" in p
    a_c_pos = "client positive" in a
    a_c_neg = "client negative" in a

    if (p_c_pos and a_c_neg) or (p_c_neg and a_c_pos):
        return False

    # 3. Guardrail against UNIT POSITIVE vs UNIT NEGATIVE mismatch
    p_u_pos = "unit positive" in p
    p_u_neg = "unit negative" in p
    a_u_pos = "unit positive" in a
    a_u_neg = "unit negative" in a

    if (p_u_pos and a_u_neg) or (p_u_neg and a_u_pos):
        return False

    # 4. Structured agreement ignoring minor suffixes (e.g. "- Both", "- Client")
    if (p_c_pos == a_c_pos and p_c_neg == a_c_neg and
        p_u_pos == a_u_pos and p_u_neg == a_u_neg and
        p_with == a_with and p_without == a_without):
        return True

    # 5. Semantic alias groups for explicit outcomes / short codes
    repo_terms = {"repo", "repossessed", "pullout", "pulled out", "surrender", "surrendered"}
    p_repo = any(t in p.split() for t in repo_terms) or "repossessed" in p or "pullout" in p
    a_repo = any(t in a.split() for t in repo_terms) or "repossessed" in a or "pullout" in a
    if p_repo and a_repo:
        return True
    if a_repo and ("unit positive" in p or "unit negative" in p):
        return True

    resolved_terms = {"resolved", "already resolved", "paid off", "fully paid", "cleared"}
    p_res = any(t in p for t in resolved_terms)
    a_res = any(t in a for t in resolved_terms)
    if p_res and a_res:
        return True
    if a_res and p_c_pos:
        return True

    recon_terms = {"pending recon", "recon", "reconciliation"}
    p_recon = any(t in p for t in recon_terms)
    a_recon = any(t in a for t in recon_terms)
    if p_recon and a_recon:
        return True

    ptp_terms = {"ptp", "promise to pay"}
    p_ptp = "ptp" in p.split() or "promise to pay" in p
    a_ptp = "ptp" in a.split() or "promise to pay" in a
    if p_ptp and a_ptp:
        return True
    if a_ptp and p_c_pos and not p_without:
        return True

    # 6. Uncontacted / No-contact full negative visits
    if (p_c_neg and p_u_neg) and (a_c_neg and a_u_neg):
        return True
    if a in {"neg", "negative"} and p_c_neg:
        return True
    if a in {"pos", "positive"} and p_c_pos:
        return True

    return False


def _is_rfd_match(predicted: str, actual: str) -> bool:
    p = _normalize_norm(predicted)
    a = _normalize_norm(actual)
    if not a:
        return not p
    if not p:
        return not a
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
        ("closure", "closure"),
        ("remittance", "remittance"),
        ("third party", "third party"),
        ("moved", "moved"),
        ("calamity", "calamity"),
        ("flood", "calamity"),
        ("deceased", "deceased"),
        ("death", "death"),
        ("lto", "lto"),
        ("impounded", "lto"),
        ("scam", "scam"),
        ("relocation", "relocation"),
        ("recon", "recon"),
        ("pension", "pension"),
        ("reduction", "reduction"),
        ("migration", "migration"),
        ("unemployment", "unemployment"),
        ("garnishment", "garnishment"),
        ("on hold", "on hold"),
        ("dealer", "dealer"),
        ("collateral", "collateral"),
        ("insurance", "insurance"),
        ("family", "family"),
    ]
    for h1, h2 in hardship_pairs:
        if (h1 in p and h2 in a) or (h2 in p and h1 in a):
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
    trim_method: str = "none"         # "none" | "llm" | "fallback"
    detected_language: str = "ENGLISH"
    language_route: str = "ENGLISH"
    model_used: str = ""
    classification_source: str = "RULE"  # "AI" | "RULE" | "EVALUATED_EXPORT"
    csu_reasoning: str = ""
    rfd_reasoning: str = ""


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
    ai_classified_count: int = 0
    rule_based_fallback_count: int = 0
    untrimmed_count: int = 0
    ai_summarizer_model: str = ""
    ai_batch_enabled: bool = False

    # Language routing metrics
    english_count: int = 0
    tagalog_count: int = 0
    detection_latency_ms: float = 0.0
    model_tagalog: str = "minimax-m2.5"
    model_english: str = "minimax-m2.5"

    # Date filter metadata
    total_unfiltered_rows: int = 0
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    test_mode: bool = False
    is_already_evaluated: bool = False


class RemarksLabPipeline:
    """Coordinates parsing, cleaning, classification, ranking, and benchmarking."""

    def __init__(
        self,
        summarizer: Optional[Any] = None,
        two_tier_summarizer: Optional[TwoTierRemarksSummarizer] = None,
    ):
        if two_tier_summarizer is not None:
            self.two_tier_summarizer = two_tier_summarizer
        elif summarizer is not None:
            if isinstance(summarizer, TwoTierRemarksSummarizer):
                self.two_tier_summarizer = summarizer
            else:
                self.two_tier_summarizer = None
        else:
            self.two_tier_summarizer = get_summarizer()
        self.summarizer = summarizer if summarizer is not None else self.two_tier_summarizer

    def process_file(
        self,
        source: str | Path | bytes | io.BytesIO,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        test_mode: bool = False,
        run_ai: bool = False,
    ) -> BenchmarkReport:
        raw_rows = parse_field_result_sheet(source, date_from=date_from, date_to=date_to, test_mode=test_mode)
        report = self.process_rows(raw_rows, test_mode=test_mode, run_ai=run_ai)
        report.test_mode = test_mode
        report.total_unfiltered_rows = getattr(raw_rows, "total_unfiltered_rows", len(raw_rows))
        report.date_from = date_from.isoformat() if date_from else None
        report.date_to = date_to.isoformat() if date_to else None
        report.is_already_evaluated = getattr(raw_rows, "is_already_evaluated", False)
        return report

    def process_rows(
        self,
        raw_rows: List[RawRemarkRow],
        test_mode: bool = False,
        run_ai: bool = False,
    ) -> BenchmarkReport:
        if not raw_rows:
            return BenchmarkReport(
                total_rows=0,
                sanitized_tag_count=0,
                csu_accuracy_pct=100.0,
                rfd_accuracy_pct=100.0,
                char_overflow_prevented_count=0,
                rows=[],
                test_mode=test_mode,
            )

        is_already_evaluated = getattr(raw_rows, "is_already_evaluated", False)

        # 1. Preprocess rows: extract narrative, strip prohibited tags, classify relations, compute initial baseline RFD/CSU
        preprocessed = []
        total_tags_stripped = 0
        rep_count = 0
        inf_count = 0
        ch_count = 0
        unk_count = 0

        for r in raw_rows:
            if test_mode:
                active_source = (r.raw_message.strip() if r.raw_message else "") or r.raw_remarks.strip()
            else:
                active_source = (r.final_remarks.strip() if r.final_remarks else "") or r.raw_remarks.strip()

            eca_match = (
                ECA_HEADER_PATTERN.match(active_source)
                or ECA_HEADER_PATTERN.match(r.raw_remarks)
                or (ECA_HEADER_PATTERN.match(r.final_remarks) if r.final_remarks else None)
            )
            eca_header = eca_match.group(1) if eca_match else ""

            if eca_header and not active_source.upper().startswith("ECA "):
                trim_source = f"{eca_header}{active_source}"
            else:
                trim_source = active_source

            if eca_header and active_source.startswith(eca_header):
                narrative_for_llm = active_source[len(eca_header):].strip()
            else:
                narrative_for_llm = active_source

            analysis_narrative = active_source
            cleaned_res: CleanedRemark = clean_remark(trim_source)
            total_tags_stripped += len(cleaned_res.detected_prohibited_tags)

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

            ranked_res: RankedResult = rank_rfd_and_csu(
                analysis_narrative,
                cleaned_res.cleaned,
                relation_res.category_label,
                relation_res.normalized_role,
            )

            preprocessed.append({
                "raw": r,
                "active_source": active_source,
                "narrative_for_llm": narrative_for_llm,
                "eca_header": eca_header,
                "cleaned_res": cleaned_res,
                "relation_res": relation_res,
                "ranked_res": ranked_res,
            })

        # 2. Parallel Language Routing via Lingua (Native Rust multi-threaded batch)
        narrative_texts = [p["narrative_for_llm"] for p in preprocessed]
        languages, detection_latency_ms = measure_batch_detection(narrative_texts)
        english_count = sum(1 for l in languages if l == "ENGLISH")
        tagalog_count = len(languages) - english_count

        # 3. Parallel 2-Tier LLM Inference (Full AI CSU, RFD & Summarization)
        llm_results_map: Dict[int, Dict[str, Any]] = {}
        use_two_tier = (
            run_ai
            and self.two_tier_summarizer
            and self.two_tier_summarizer.is_available
        )
        legacy_llm_prov = getattr(self.summarizer, "summarize", None) if (self.summarizer and getattr(self.summarizer, "is_available", False) and not test_mode) else None

        if use_two_tier:
            records_for_llm = [
                {
                    "cleaned_remark": p["narrative_for_llm"],
                    "raw_remark": p["narrative_for_llm"],
                    "contact_person": p["raw"].contact_person,
                    "eca_header": p["eca_header"],
                }
                for p in preprocessed
            ]

            try:
                import concurrent.futures
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        dispatched_records, _ = executor.submit(
                            lambda: asyncio.run(
                                self.two_tier_summarizer.parallel_process_records(records_for_llm, force_all=True)
                            )
                        ).result()
                else:
                    dispatched_records, _ = asyncio.run(
                        self.two_tier_summarizer.parallel_process_records(records_for_llm, force_all=True)
                    )

                for idx, d_rec in enumerate(dispatched_records):
                    llm_results_map[idx] = d_rec
            except Exception:
                pass

        # 4. Assemble Row Results & Track Metrics
        results: List[RowResult] = []
        csu_matches = 0
        rfd_matches = 0
        ground_truth_rows = 0
        discrepancies = 0
        ai_summarized_count = 0
        ai_classified_count = 0
        rule_based_fallback_count = 0
        untrimmed_count = 0
        overflow_prevented = 0

        for idx, (item, lang) in enumerate(zip(preprocessed, languages)):
            r = item["raw"]
            cleaned_res = item["cleaned_res"]
            relation_res = item["relation_res"]
            ranked_res = item["ranked_res"]

            llm_res = llm_results_map.get(idx)

            if llm_res and llm_res.get("ai_dispatched") and (llm_res.get("csu") or llm_res.get("summary")):
                predicted_csu = llm_res.get("csu") or ranked_res.csu_status
                if "rfd" in llm_res and llm_res["rfd"] is not None:
                    predicted_rfd = str(llm_res["rfd"]).strip()
                else:
                    predicted_rfd = ranked_res.rfd_code

                if "detailed_rfd" in llm_res and llm_res["detailed_rfd"] is not None:
                    detailed_rfd_val = str(llm_res["detailed_rfd"]).strip()
                else:
                    detailed_rfd_val = ranked_res.detailed_rfd
                csu_reasoning = llm_res.get("csu_reasoning") or r.evaluated_csu_why or ranked_res.csu_reasoning
                rfd_reasoning = llm_res.get("rfd_reasoning") or r.evaluated_rfd_why or ranked_res.rfd_reasoning
                trimmed_text = llm_res.get("summary") or item["active_source"]
                model_used = llm_res.get("model_used", "")
                trim_method = "llm"
                classification_source = "AI"
                ai_summarized_count += 1
                ai_classified_count += 1
                orig_len = len(item["active_source"])
                trimmed_len = len(trimmed_text)
                if orig_len > 200 and trimmed_len <= 200:
                    overflow_prevented += 1
            else:
                predicted_csu = ranked_res.csu_status
                predicted_rfd = ranked_res.rfd_code
                detailed_rfd_val = ranked_res.detailed_rfd
                csu_reasoning = r.evaluated_csu_why or ranked_res.csu_reasoning
                rfd_reasoning = r.evaluated_rfd_why or ranked_res.rfd_reasoning
                classification_source = "RULE"
                trimmed_res: TrimmedRemark = trim_and_format(
                    cleaned_res.cleaned,
                    contact_person=r.contact_person,
                    llm_provider=legacy_llm_prov,
                    strip_name=True,
                )
                trimmed_text = trimmed_res.trimmed_text
                model_used = "passthrough" if trimmed_res.trim_method == "none" else "rule_fallback"
                trim_method = trimmed_res.trim_method
                orig_len = trimmed_res.original_length
                trimmed_len = trimmed_res.trimmed_length
                if trimmed_res.original_length > 200 and trimmed_res.fits_within_200:
                    overflow_prevented += 1
                if trim_method == "llm":
                    ai_summarized_count += 1
                elif trim_method == "fallback":
                    rule_based_fallback_count += 1
                else:
                    untrimmed_count += 1

            has_csu_gt = bool(getattr(raw_rows, "has_manual_csu", False)) or bool(r.manual_csu)
            has_rfd_gt = bool(getattr(raw_rows, "has_manual_rfd", False)) or bool(r.manual_rfd)
            has_gt = has_csu_gt or has_rfd_gt
            csu_match = _is_csu_match(predicted_csu, r.manual_csu) if has_csu_gt else True
            rfd_match = _is_rfd_match(predicted_rfd, r.manual_rfd) if has_rfd_gt else True

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
                predicted_rfd=predicted_rfd,
                predicted_csu=predicted_csu,
                hierarchy_source=ranked_res.rule_source,
                trimmed_statement=trimmed_text,
                original_char_count=orig_len,
                final_char_count=trimmed_len,
                truncated=(trimmed_len < orig_len),
                manual_csu=r.manual_csu,
                manual_rfd=r.manual_rfd,
                detailed_rfd=detailed_rfd_val,
                csu_match=csu_match,
                rfd_match=rfd_match,
                discrepancy_flag=discrepancy,
                raw_message=r.raw_message,
                ai_summarized=(trim_method == "llm"),
                trim_method=trim_method,
                detected_language=lang,
                language_route=lang,
                model_used=model_used,
                classification_source=classification_source,
                csu_reasoning=csu_reasoning,
                rfd_reasoning=rfd_reasoning,
            )
            results.append(row_res)

        total_rows = len(results)
        csu_acc = round((csu_matches / ground_truth_rows * 100), 1) if ground_truth_rows > 0 else 100.0
        rfd_acc = round((rfd_matches / ground_truth_rows * 100), 1) if ground_truth_rows > 0 else 100.0

        model_display = f"{self.two_tier_summarizer.model_tagalog} (TL) / {self.two_tier_summarizer.model_english} (EN)" if (self.two_tier_summarizer and self.two_tier_summarizer.is_available) else ""

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
            ai_classified_count=ai_classified_count,
            rule_based_fallback_count=rule_based_fallback_count,
            untrimmed_count=untrimmed_count,
            ai_summarizer_model=model_display,
            ai_batch_enabled=True,
            test_mode=test_mode,
            is_already_evaluated=is_already_evaluated,
            english_count=english_count,
            tagalog_count=tagalog_count,
            detection_latency_ms=detection_latency_ms,
            model_tagalog=self.two_tier_summarizer.model_tagalog if self.two_tier_summarizer else "minimax-m2.5",
            model_english=self.two_tier_summarizer.model_english if self.two_tier_summarizer else "minimax-m2.5",
        )

    def export_to_excel(self, report: BenchmarkReport) -> io.BytesIO:
        """Export evaluated rows into bank-compliant Excel format."""
        import pandas as pd
        data = []
        for r in report.rows:
            row_dict = {
                "Row Index": r.row_index,
                "Account Number": r.account_number,
                "CH Code": r.ch_code,
                "Contact Person": r.contact_person,
                "Contact Relation": r.contact_relation,
                "Category Label": r.category_label,
            }
            if report.test_mode:
                row_dict["Raw Remark"] = r.raw_remarks
                row_dict["Original CSU"] = r.manual_csu
                row_dict["Original RFD"] = r.manual_rfd
            row_dict["COLLECTION STATUS UPDATE"] = r.predicted_csu
            if report.test_mode:
                row_dict["CSU Why"] = r.csu_reasoning
            row_dict["RFD"] = r.predicted_rfd
            if report.test_mode:
                row_dict["RFD Why"] = r.rfd_reasoning
            row_dict.update({
                "DETAILED RFD": r.detailed_rfd,
                "FINAL REMARKS": r.trimmed_statement,
                "Character Count": r.final_char_count,
                "char checker": "" if r.final_char_count <= 200 else "PLS REVISE",
            })
            data.append(row_dict)
        df = pd.DataFrame(data)
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="FIELD RESULT (EVALUATED)", index=False)
        out.seek(0)
        return out
