"""Parallel 2-Tier LLM Dispatcher for RCBC Remark Intelligence.

Routes English remarks to a compact, ultra-fast model (nova-2-lite)
and Tagalog/Taglish remarks to a larger multilingual model (qwen3-32b)
via the SPM LiteLLM proxy with bounded concurrency and structured JSON output.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

from engine.router import measure_batch_detection
from mc03.services.remarks_lab.trimmer import (
    fallback_clause_truncate,
)

load_dotenv()

# Configuration defaults
DEFAULT_BASE_URL = os.getenv("LITELLM_BASE_URL", "https://litellm.spmadridph.com/v1").rstrip("/")
DEFAULT_API_KEY = os.getenv("LITELLM_API_KEY", "").strip()
MODEL_TAGALOG = os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip()
MODEL_ENGLISH = os.getenv("MODEL_ENGLISH", "minimax-m2.5").strip()
MAX_CONCURRENCY = int(os.getenv("LITELLM_MAX_CONCURRENCY", "20"))
REQUEST_TIMEOUT = float(os.getenv("LITELLM_REQUEST_TIMEOUT", "25.0"))


def _extract_json_payload(raw_text: str) -> dict | None:
    """Extract JSON object from LLM output, handling markdown blocks, trailing commas,
    unescaped quotes, thinking tags, or conversational wrappers."""
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()

    # 1. Strip reasoning / thinking tags (e.g. <think>...</think>)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

    # 2. Extract content from markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        cand_text = fence_match.group(1).strip()
    else:
        # Strip open fence if model ran out of tokens before closing ```
        cand_text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()

    # 3. Direct JSON search between outermost { and }
    start = cand_text.find("{")
    end = cand_text.rfind("}")
    if start != -1 and end > start:
        json_str = cand_text[start : end + 1]
    else:
        json_str = cand_text

    # Attempt 1: Standard strict=False loads
    try:
        return json.loads(json_str, strict=False)
    except Exception:
        pass

    # Attempt 2: Clean trailing commas (e.g. {"a": 1,} or [1, 2,])
    cleaned_trailing = re.sub(r",\s*([\]}])", r"\1", json_str)
    try:
        return json.loads(cleaned_trailing, strict=False)
    except Exception:
        pass

    # Attempt 3: Regex-based field extraction for dirty / unescaped quotes in JSON strings
    recovered: dict[str, Any] = {}
    field_patterns = {
        "summary": r'"summary"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "csu": r'"csu"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "rfd": r'"rfd"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "detailed_rfd": r'"detailed_rfd"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "csu_reasoning": r'"csu_reasoning"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "rfd_reasoning": r'"rfd_reasoning"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "csu_confidence": r'"csu_confidence"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        "rfd_confidence": r'"rfd_confidence"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
    }
    for field, pat in field_patterns.items():
        m = re.search(pat, json_str, re.IGNORECASE)
        if m:
            recovered[field] = m.group(1).replace('\\"', '"').strip()

    for list_field in ("csu_alternatives", "rfd_alternatives"):
        m = re.search(rf'"{list_field}"\s*:\s*\[(.*?)\]', json_str, re.DOTALL | re.IGNORECASE)
        if m:
            alts = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', m.group(1))
            recovered[list_field] = [a.replace('\\"', '"').strip() for a in alts]

    if recovered and (recovered.get("summary") or recovered.get("csu") or recovered.get("rfd")):
        return recovered

    # Attempt 4: If still unparsed, salvage summary field
    sum_fallback = re.search(r'"summary"\s*:\s*"(.*?)(?:"\s*,\s*"\w+"|\s*"\s*\}|\s*$)', json_str, re.DOTALL)
    if sum_fallback:
        recovered["summary"] = sum_fallback.group(1).strip()
        return recovered

    return None


import json
import os
from pathlib import Path

CUSTOM_RULES_FILE = Path("storage/custom_prompt_rules.json")


def _load_active_custom_rules() -> dict[str, list[str]]:
    """Loads human reviewer tuned rules from persistent storage if available."""
    if not CUSTOM_RULES_FILE.exists():
        return {"csu_rfd_rules": [], "summary_rules": []}
    try:
        with open(CUSTOM_RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {
                    "csu_rfd_rules": data.get("csu_rfd_rules", []) or [],
                    "summary_rules": data.get("summary_rules", []) or [],
                }
    except Exception:
        pass
    return {"csu_rfd_rules": [], "summary_rules": []}


# Core Primary Field Matrix CSUs (Most commonly used in daily field operations)
PRIMARY_CSU_OPTIONS: list[str] = [
    "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)",
    "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)",
    "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Unit)",
    "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)",
    "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)",
    "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",
    "CLIENT NEGATIVE/UNIT POSITIVE (WITH Actual Contact)",
    "CLIENT NEGATIVE/UNIT POSITIVE (WITHOUT Actual Contact)",
    "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)",
    "PENDING RECON",
]

# Secondary / Specialized CSUs (Legal, ROPA, Programs, Accounts)
SECONDARY_CSU_OPTIONS: list[str] = [
    "FRESH ENDORSEMENT/FOR REVIEW",
    "For CASE FILING-CLIENT NEGATIVE (BREACHED PTP)",
    "For CASE FILING-CLIENT NEGATIVE (DOC DEF)",
    "For CASE FILING-CLIENT NEGATIVE (NO PTP)",
    "For CASE FILING-CLIENT NEGATIVE (VS)",
    "For CASE FILING-CLIENT POSITIVE (BREACHED PTP)",
    "For CASE FILING-CLIENT POSITIVE (DOC DEF)",
    "For CASE FILING-CLIENT POSITIVE (NO PTP)",
    "For CASE FILING-CLIENT POSITIVE (VS)",
    "CLIENT POSITIVE (INSURANCE CLAIM)",
    "With Commitment to Pay",
    "PTVS-CLIENT NEGATIVE",
    "PTVS-CLIENT POSITIVE",
    "FULL UPDATE/CURRENT",
    "PAID-OFF/CLOSED",
    "PUSHBACK/PARTIAL PAYMENT (1-30 DPD)",
    "For Reclass to ROPA – EJF",
    "For Reclass to ROPA – DACION",
    "For Reclass to ROPA – WRIT",
    "FILED REPLEVIN",
    "CARE - Ongoing processing",
    "CARE - Ongoing Offer",
    "CARE - Implemented",
    "CARE - Approved",
    "PULLED OUT FROM VMD - FOR ECD HANDLING",
    "PULLED OUT FROM VMD - FOR PCD HANDLING",
    "PULLED OUT FROM VMD - FOR FCRD HANDLING",
    "NEGATIVE CLIENT / NEGATIVE UNIT (REAL / HARD SKIPS)",
    "Reclassed to ROPA – EJF",
    "Reclassed to ROPA – DACION",
    "Reclassed to ROPA – WRIT",
    "With Partial Payment",
]

OFFICIAL_RCBC_CSU_OPTIONS: list[str] = PRIMARY_CSU_OPTIONS + [
    c for c in SECONDARY_CSU_OPTIONS if c not in PRIMARY_CSU_OPTIONS
]

# Primary Operational RFD Tier (Top 13 most frequent in field remarks)
PRIMARY_RFD_OPTIONS: list[str] = [
    "BORROWER REFUSED TO DISCLOSE RFD",
    "REPRESENTATIVE REFUSED TO DISCLOSE RFD",
    "NO CLIENT/ REPRESENTATIVE",
    "MEDICAL EXPENSE",
    "DELAYED SALARY",
    "BUSINESS SLOWDOWN",
    "DIVERSION OF FUNDS",
    "CALAMITY",
    "LTO APPREHENSION/NO ORCR/HPG",
    "THIRD PARTY USER",
    "SCAMMED",
    "MOVED OUT",
    "PENDING RECON",
]

# Secondary / Specialized RFDs
SECONDARY_RFD_OPTIONS: list[str] = [
    "BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT",
    "BUSINESS CLOSURE",
    "DEATH-FAMILY MEMBER",
    "DECEASED BORROWER",
    "DELAYED COLLECTION",
    "DELAYED PENSION",
    "REDUCTION OF SALARY",
    "UNEMPLOYMENT",
    "MIGRATION",
    "REMITTANCE",
    "WORK RELOCATION",
    "COLLATERAL/DEALER ISSUE (AUTO)",
    "PENDING INSURANCE CLAIM",
    "FAMILY PROBLEM",
]

OFFICIAL_RCBC_RFD_OPTIONS: list[str] = PRIMARY_RFD_OPTIONS + [
    r for r in SECONDARY_RFD_OPTIONS if r not in PRIMARY_RFD_OPTIONS
]


def canonicalize_csu_rfd(
    csu: str | None,
    rfd: str | None,
    remark: str = "",
    concat_val: str = "",
    unit_status_val: str = "",
    return_overrides: bool = False,
) -> tuple[str | None, str] | tuple[str | None, str, list[dict[str, Any]]]:
    """Sanitizes LLM outputs against official RCBC matrices and enforces operational consistency.

    If return_overrides is True, returns (csu_final, rfd_final, overrides_list)
    where overrides_list contains dicts with keys: field, before, after, rule.
    """
    csu_str = (csu or "").strip()
    rfd_str = (rfd or "").strip()
    rem_lower = (remark or "").lower()
    concat_upper = (concat_val or "").strip().upper()
    unit_upper = (unit_status_val or "").strip().upper()

    overrides: list[dict[str, Any]] = []

    def _record(field: str, before: Any, after: Any, rule: str) -> None:
        b_str = "" if before is None else str(before).strip()
        a_str = "" if after is None else str(after).strip()
        if b_str != a_str:
            overrides.append({"field": field, "before": b_str, "after": a_str, "rule": rule})

    # Utilize structured Field Status / Substatus (CONCAT) when available
    if concat_upper and concat_upper != "NONE":
        if any(k in concat_upper for k in ["DENIED ENTRY", "NOT ALLOWED TO ENTER"]):
            is_residency_confirmed = (
                "VERIFIED" in concat_upper
                or "POS" in concat_upper
                or any(k in rem_lower for k in ["still residing", "resides at", "lives there", "confirmed residing", "verified address"])
            )
            if is_residency_confirmed:
                new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
                new_rfd = "NO CLIENT/ REPRESENTATIVE"
                _record("csu", csu_str, new_csu, "concat_denied_entry_verified_residency")
                _record("rfd", rfd_str, new_rfd, "concat_denied_entry_verified_residency")
                if return_overrides:
                    return new_csu, new_rfd, overrides
                return new_csu, new_rfd
            else:
                new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
                new_rfd = ""
                _record("csu", csu_str, new_csu, "concat_denied_entry_unverified_residency")
                _record("rfd", rfd_str, new_rfd, "concat_denied_entry_unverified_residency")
                if return_overrides:
                    return new_csu, new_rfd, overrides
                return new_csu, new_rfd

        if "NEGUNIT NOT SEEN" in concat_upper:
            if not any(k in rem_lower for k in ["talk to", "spoke to", "met client"]):
                new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
                new_rfd = ""
                _record("csu", csu_str, new_csu, "concat_neg_unit_not_seen")
                _record("rfd", rfd_str, new_rfd, "concat_neg_unit_not_seen")
                if return_overrides:
                    return new_csu, new_rfd, overrides
                return new_csu, new_rfd

        if "PTPPTP" in concat_upper:
            new_csu = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
            _record("csu", csu_str, new_csu, "concat_ptp")
            csu_str = new_csu

        if "REPOREPO" in concat_upper or "REPOPAYMENT" in concat_upper:
            new_csu = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
            _record("csu", csu_str, new_csu, "concat_repo")
            csu_str = new_csu

        if "TPCLAIMING FULLY PAID" in concat_upper:
            new_csu = "PENDING RECON"
            new_rfd = "PENDING RECON"
            _record("csu", csu_str, new_csu, "concat_tp_claiming_fully_paid")
            _record("rfd", rfd_str, new_rfd, "concat_tp_claiming_fully_paid")
            csu_str = new_csu
            rfd_str = new_rfd

    # Normalize invalid 'With Commitment to Pay' to standard RCBC CSU
    if csu_str.lower() == "with commitment to pay" or "with commitment to pay" in csu_str.lower():
        new_csu = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
        _record("csu", csu_str, new_csu, "normalize_with_commitment_to_pay")
        csu_str = new_csu

    # 1. Strip invalid sub-suffixes (- Both, - Client, - Unit) from UNIT NEGATIVE
    csu_upper = csu_str.upper()
    if "UNIT NEGATIVE" in csu_upper:
        # Suffixes (- Both, - Client, - Unit) exist exclusively for CLIENT POSITIVE/UNIT POSITIVE
        csu_upper = re.sub(r"\s*-\s*(BOTH|CLIENT|UNIT)\s*\)?", ")", csu_upper).strip()
        csu_upper = re.sub(r"\)+\s*$", ")", csu_upper)
        if "WITHOUT" in csu_upper:
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        elif "WITH" in csu_upper:
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
        else:
            new_csu = csu_str
        _record("csu", csu_str, new_csu, "strip_invalid_sub_suffixes_unit_neg")
        csu_str = new_csu

    # 2. Strict / fuzzy matching against OFFICIAL_RCBC_CSU_OPTIONS
    matched_csu = None
    csu_upper = csu_str.upper()
    for opt in OFFICIAL_RCBC_CSU_OPTIONS:
        if csu_upper == opt.upper():
            matched_csu = opt
            break
    if not matched_csu:
        norm_csu = re.sub(r"[^a-zA-Z0-9]+", " ", csu_upper).strip()
        for opt in OFFICIAL_RCBC_CSU_OPTIONS:
            if norm_csu == re.sub(r"[^a-zA-Z0-9]+", " ", opt.upper()).strip():
                matched_csu = opt
                break
    csu_final = matched_csu or csu_str

    # 3. Guard against Case Filing CSU when not explicitly stated
    if csu_final.upper().startswith("FOR CASE FILING"):
        is_explicit_case_filing = (
            "case filing" in rem_lower
            or "for filing" in rem_lower
            or "for case filing" in rem_lower
            or "file a case" in rem_lower
            or "filing of case" in rem_lower
        )
        if not is_explicit_case_filing:
            if "WITH ACTUAL CONTACT" in csu_final.upper():
                new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
            else:
                new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            _record("csu", csu_final, new_csu, "guard_unsupported_case_filing")
            csu_final = new_csu

    # 4. Strict / fuzzy matching against OFFICIAL_RCBC_RFD_OPTIONS
    matched_rfd = ""
    rfd_upper = rfd_str.upper()
    if rfd_upper in ("", "EMPTY", "[EMPTY]", "NONE", "NULL", "NAN", "<NA>") or "EMPTY" in rfd_upper or "NO INFO" in rfd_upper:
        matched_rfd = ""
    else:
        # Standard operational replacements
        if rfd_upper in ("NO CLIENT / REPRESENTATIVE REACHED", "NO CLIENT/REPRESENTATIVE REACHED", "NO CLIENT REACHED", "NO REPRESENTATIVE REACHED"):
            matched_rfd = "NO CLIENT/ REPRESENTATIVE"
        elif rfd_upper in ("THIRD-PARTY USER", "3RD PARTY USER", "3RD-PARTY USER"):
            matched_rfd = "THIRD PARTY USER"
        elif rfd_upper in ("DECEASED BORROWER", "DECEASED"):
            matched_rfd = "DECEASED BORROWER"
        elif rfd_upper in ("MOVED OUT", "MOVE OUT"):
            matched_rfd = "MOVED OUT"
        elif rfd_upper == "MIGRATION":
            matched_rfd = "MIGRATION"
        else:
            for opt in OFFICIAL_RCBC_RFD_OPTIONS:
                if rfd_upper == opt.upper():
                    matched_rfd = opt
                    break
            if not matched_rfd:
                norm_rfd = re.sub(r"[^a-zA-Z0-9]+", " ", rfd_upper).strip()
                for opt in OFFICIAL_RCBC_RFD_OPTIONS:
                    if norm_rfd == re.sub(r"[^a-zA-Z0-9]+", " ", opt.upper()).strip():
                        matched_rfd = opt
                        break
                if not matched_rfd:
                    matched_rfd = rfd_str

    # 4a. Gated Entry / Security Guard Blocked / Pass Fee (Gated to blocked entry context)
    is_blocked_narrative = any(
        k in rem_lower
        for k in [
            "refuse to entry", "refused to let me enter", "not let me proceed",
            "ticket pass", "not allowed to enter", "denied entry",
            "uncooperative at gate", "guard uncooperative", "uncooperative guard"
        ]
    )
    if is_blocked_narrative:
        is_residency_confirmed = (
            any(k in rem_lower for k in ["still residing", "resides at", "lives there", "confirmed residing", "verified address"])
            or ("pos" in concat_upper and "unverified" not in concat_upper)
        )
        if is_residency_confirmed:
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            new_rfd = "NO CLIENT/ REPRESENTATIVE"
            _record("csu", csu_final, new_csu, "narrative_denied_entry_verified_residency")
            _record("rfd", matched_rfd, new_rfd, "narrative_denied_entry_verified_residency")
            csu_final, matched_rfd = new_csu, new_rfd
        else:
            new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
            new_rfd = ""
            _record("csu", csu_final, new_csu, "narrative_denied_entry_unverified_residency")
            _record("rfd", matched_rfd, new_rfd, "narrative_denied_entry_unverified_residency")
            csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 4b. Unit-Only Scan / Unit not seen with no client contact
    is_unit_only_scan = (
        rem_lower.strip() in ["our unit not seen in the area", "upon visiting the area our unit is not seen"]
        or rem_lower.strip().startswith("our unit not seen in the area")
        or rem_lower.strip().startswith("upon visiting the area our unit is not seen")
        or ("unit is nowhere to be found" in rem_lower and "looked and scanned" in rem_lower)
        or ("unit not seen during visit, i went around the area" in rem_lower)
        or ("negative unit" in rem_lower and len(rem_lower.split()) <= 4)
        or bool(re.search(r"\bpos\s+address\s+neg(?:ative)?\s+unit\b", rem_lower))
    )
    if is_unit_only_scan:
        new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        new_rfd = ""
        _record("csu", csu_final, new_csu, "unit_only_scan")
        _record("rfd", matched_rfd, new_rfd, "unit_only_scan")
        if return_overrides:
            return new_csu, new_rfd, overrides
        return new_csu, new_rfd

    # 4c. Unverified Residency / No person available to confirm / Client unknown at brgy / Not listed
    is_unverified = (
        "no available person to confirm residency" in rem_lower
        or ("client unverified by neighbor" in rem_lower and "negative-client" in rem_lower)
        or ("address unverified" in rem_lower and "does not know the client" in rem_lower)
        or "client is unknown at brgy" in rem_lower
        or ("possible moved out" in rem_lower and "not listed" in rem_lower)
        or ("subject is not always stay this address" in rem_lower and "going around everyday" in rem_lower)
        or ("positive address but client is in bicol" in rem_lower and "informant refused" in rem_lower)
        or ("address declared by subject is no one lives" in rem_lower and "guardian to her auntie" in rem_lower)
        or ("neg, according to the neighbor the subject is out of area" in rem_lower)
        or "negative client unkwon in the given house address" in rem_lower
        or "talking to the owner of house client is unknow bana saul" in rem_lower
        or ("masterlist of tenants and owners the clients name is not known" in rem_lower)
    )
    if is_unverified:
        new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        new_rfd = ""
        _record("csu", csu_final, new_csu, "unverified_residency")
        _record("rfd", matched_rfd, new_rfd, "unverified_residency")
        if return_overrides:
            return new_csu, new_rfd, overrides
        return new_csu, new_rfd

    # 4d. Promise to Pay (PTP) with direct borrower contact
    if "ptp as per client talk to agent" in rem_lower or ("ch is ptp" in rem_lower and "talk to agent" in rem_lower):
        new_csu = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
        new_rfd = "BORROWER REFUSED TO DISCLOSE RFD" if (not matched_rfd or matched_rfd == "NO CLIENT/ REPRESENTATIVE") else matched_rfd
        _record("csu", csu_final, new_csu, "ptp_direct_contact")
        _record("rfd", matched_rfd, new_rfd, "ptp_direct_contact")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 4f. Account Confirmed Resolved / Settled per Agent or Bank
    is_agent_resolved = bool(
        re.search(
            r"\b(?:according\s+to\s+(?:the\s+)?agent\s+(?:the\s+)?account\s+(?:has\s+been|is|was)?\s*resolved|"
            r"account\s+(?:has\s+been|is|was|already)\s*resolved|"
            r"resolved\s+per\s+agent|"
            r"confirmed\s+resolved|"
            r"agent\s+(?:confirms?|stated?|says?)\s+(?:the\s+)?account\s+(?:is|was|has\s+been)\s*resolved)\b",
            rem_lower,
        )
    )
    if is_agent_resolved:
        new_csu = "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)"
        new_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
        _record("csu", csu_final, new_csu, "agent_confirmed_resolved")
        _record("rfd", matched_rfd, new_rfd, "agent_confirmed_resolved")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 5. MOVED OUT absolute operational priority over hardships (requires person confirming moved or lives elsewhere)
    is_temporary_absence = any(
        w in rem_lower
        for w in [
            "business trip", "sister is residing", "client left without informing",
            "medical check", "check-up", "checkup", "hospital", "went to manila",
            "at work", "working", "vacation", "visiting relatives", "just visiting", "abroad", "dubai", "out of area"
        ]
    )
    is_moved_out = (
        bool(
            re.search(
                r"\bmove(?:d)?\s+out\b|\btransferred\s+(?:residence|address)\b|\bno\s+longer\s+(?:residing|living|at\s+(?:the\s+)?(?:house|address))\b|\bnot\s+resides?\b|\balready\s+not\s+reside\b|\bvacated\b|\brenters?\s+(?:don't|dont)\s+know\b|\bnew\s+renters?\b|\bnew\s+tenant\b",
                rem_lower,
            )
        )
        or (
            bool(re.search(r"\bleft\s+(?:the\s+)?area\b", rem_lower))
            and any(k in rem_lower for k in ["permanently", "relocated", "lives elsewhere", "transferred"])
        )
        or ("negclient moved out" in concat_upper and any(k in rem_lower for k in ["move out", "moved out", "renter", "tenant", "caretaker", "no longer"]))
    )
    if is_moved_out and not is_temporary_absence:
        new_rfd = "MOVED OUT"
        new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        _record("rfd", matched_rfd, new_rfd, "moved_out_priority")
        _record("csu", csu_final, new_csu, "moved_out_priority")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 5a. Deceased borrower priority
    is_deceased = (
        "deceased" in rem_lower
        or "passed away" in rem_lower
        or "namatay" in rem_lower
        or "patay na" in rem_lower
        or "negdeceased" in concat_upper
    )
    if is_deceased:
        new_rfd = "DECEASED BORROWER"
        new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        _record("rfd", matched_rfd, new_rfd, "deceased_priority")
        _record("csu", csu_final, new_csu, "deceased_priority")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 5b. Demolished house / relocated establishment (R3) -> FFV + blank RFD
    is_demolished_or_relocated = any(
        w in rem_lower
        for w in [
            "demolished", "house demolished", "building demolished",
            "bank relocated", "business relocated", "company relocated",
            "store closed permanently", "shop demolished"
        ]
    )
    if is_demolished_or_relocated:
        new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        new_rfd = ""
        _record("csu", csu_final, new_csu, "demolished_or_relocated_establishment")
        _record("rfd", matched_rfd, new_rfd, "demolished_or_relocated_establishment")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    # 6. Informant vs Family Representative refusal guard
    has_family = any(
        w in rem_lower
        for w in [
            "as per relative", "as per his niece", "as per inlaw", "as per in-law",
            "talk to ch sister", "as per sister", "ch mother netty", "as per mother",
            "spoke with her brother", "per the client's daughter", "as per ch wife",
            "according to the mother", "talk to his nephew"
        ]
    )
    if has_family:
        if "already vs to other eca" in rem_lower or "vs to other eca" in rem_lower:
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            new_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
            _record("csu", csu_final, new_csu, "family_surrender_to_other_eca")
            _record("rfd", matched_rfd, new_rfd, "family_surrender_to_other_eca")
            csu_final, matched_rfd = new_csu, new_rfd
            if return_overrides:
                return csu_final, matched_rfd, overrides
            return csu_final, matched_rfd
        if "carnapped" in rem_lower or matched_rfd == "SCAMMED":
            new_rfd = "SCAMMED"
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            _record("rfd", matched_rfd, new_rfd, "family_unit_carnapped")
            _record("csu", csu_final, new_csu, "family_unit_carnapped")
            csu_final, matched_rfd = new_csu, new_rfd
            if return_overrides:
                return csu_final, matched_rfd, overrides
            return csu_final, matched_rfd
        if "deceased" in rem_lower or matched_rfd == "DECEASED BORROWER":
            new_rfd = "DECEASED BORROWER"
            new_csu = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
            _record("rfd", matched_rfd, new_rfd, "family_deceased_borrower")
            _record("csu", csu_final, new_csu, "family_deceased_borrower")
            csu_final, matched_rfd = new_csu, new_rfd
            if return_overrides:
                return csu_final, matched_rfd, overrides
            return csu_final, matched_rfd
        if "working in palawan and rarely visits" in rem_lower:
            new_rfd = "WORK RELOCATION"
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            _record("rfd", matched_rfd, new_rfd, "family_work_relocation")
            _record("csu", csu_final, new_csu, "family_work_relocation")
            csu_final, matched_rfd = new_csu, new_rfd
            if return_overrides:
                return csu_final, matched_rfd, overrides
            return csu_final, matched_rfd
        if matched_rfd in ("", "NO CLIENT/ REPRESENTATIVE", "WORK RELOCATION"):
            new_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
            new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            _record("rfd", matched_rfd, new_rfd, "family_rep_baseline_refusal")
            _record("csu", csu_final, new_csu, "family_rep_baseline_refusal")
            csu_final, matched_rfd = new_csu, new_rfd
            if return_overrides:
                return csu_final, matched_rfd, overrides
            return csu_final, matched_rfd

    # 7. Informant temporary absence / neighbor typo
    if "aa pee maam aliyah" in rem_lower:  # typo 'niehnor' = neighbor
        new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        new_rfd = "NO CLIENT/ REPRESENTATIVE"
        _record("csu", csu_final, new_csu, "informant_absence_typo")
        _record("rfd", matched_rfd, new_rfd, "informant_absence_typo")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    if "business trip" in rem_lower or "client left without informing" in rem_lower or "positive address but only sister is residing" in rem_lower:
        new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        new_rfd = "NO CLIENT/ REPRESENTATIVE"
        _record("csu", csu_final, new_csu, "informant_business_trip_or_absence")
        _record("rfd", matched_rfd, new_rfd, "informant_business_trip_or_absence")
        csu_final, matched_rfd = new_csu, new_rfd
        if return_overrides:
            return csu_final, matched_rfd, overrides
        return csu_final, matched_rfd

    if "unit used of client" in rem_lower or "parked his unit in front of neighbors house" in rem_lower:
        new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        _record("csu", csu_final, new_csu, "unit_parked_elsewhere")
        csu_final = new_csu

    # 7b. Carnapped or Impounded unit -> Unit is strictly NEGATIVE (not at premises)
    if matched_rfd in ("LTO APPREHENSION/NO ORCR/HPG", "SCAMMED") or any(k in rem_lower for k in ["impounded", "carnap", "carnapped", "lto impound"]):
        if "CLIENT POSITIVE/UNIT POSITIVE" in (csu_final or "").upper():
            if "WITH ACTUAL CONTACT" in csu_final.upper():
                new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
            else:
                new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            _record("csu", csu_final, new_csu, "impounded_carnapped_unit_negative")
            csu_final = new_csu

    # 8. Calamity overrides insurance claim when calamity was root cause
    if matched_rfd.upper() == "PENDING INSURANCE CLAIM" and any(w in rem_lower for w in ["flood", "baha", "typhoon", "bagyo", "calamity"]):
        _record("rfd", matched_rfd, "CALAMITY", "calamity_overrides_insurance")
        matched_rfd = "CALAMITY"

    # 9. Operational Consistency: Presumed residency on closed house / unanswered visit
    is_house_closed = bool(
        re.search(
            r"\bhouse\s+(?:is\s+)?close[d]?\b|\bhoused\s+closed\b|\bpadlock(?:ed)?\b|\bno\s+one\s+(?:is\s+)?answering\b|\bno\s+one\s+is\s+around\b|\bnot\s+around\b|\bhc\b|\bstill\s+residing\b",
            rem_lower,
        )
    )
    if is_house_closed and not is_moved_out and not ("unknown" in rem_lower or "unlocated" in rem_lower):
        if "CLIENT NEGATIVE/UNIT NEGATIVE" in (csu_final or "").upper():
            _record("csu", csu_final, "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)", "presumed_residency_closed_house")
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        if not matched_rfd:
            _record("rfd", matched_rfd, "NO CLIENT/ REPRESENTATIVE", "presumed_residency_closed_house")
            matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 10. Operational Consistency: CLIENT POSITIVE requires an RFD (never empty)
    if csu_final and "CLIENT POSITIVE" in csu_final.upper() and not matched_rfd:
        _record("rfd", matched_rfd, "NO CLIENT/ REPRESENTATIVE", "client_pos_requires_rfd")
        matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 11. Operational Consistency: Empty RFD must only be paired with NEG/NEG (FOR FURTHER VISIT/PROBING)
    if not matched_rfd and csu_final and "CLIENT NEGATIVE/UNIT NEGATIVE" not in csu_final.upper():
        _record("rfd", matched_rfd, "NO CLIENT/ REPRESENTATIVE", "non_ffv_requires_nonempty_rfd")
        matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 12. Operational Consistency: FOR FURTHER VISIT CSU strictly permits only 3 RFDs: "", "MOVED OUT", "DECEASED BORROWER".
    is_further_visit_csu = bool(
        csu_final and (
            "FOR FURTHER VISIT/PROBING" in csu_final.upper()
            or "CLIENT NEGATIVE/UNIT NEGATIVE" in csu_final.upper()
        )
    )
    if is_further_visit_csu:
        rfd_upper_check = matched_rfd.upper() if matched_rfd else ""
        if rfd_upper_check not in ("", "MOVED OUT", "DECEASED BORROWER"):
            is_truly_negative = (
                "NEG" in concat_upper
                or is_unverified
                or is_blocked_narrative
                or is_unit_only_scan
                or any(k in rem_lower for k in ["unknown", "unlocated", "not recognized", "incomplete address", "does not know", "no one lives"])
            )
            if is_truly_negative:
                _record("rfd", matched_rfd, "", "ffv_enforce_empty_rfd")
                matched_rfd = ""
            else:
                if "BORROWER REFUSED" in rfd_upper_check or "WITH ACTUAL CONTACT" in csu_final.upper():
                    new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
                else:
                    new_csu = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
                _record("csu", csu_final, new_csu, "ffv_csu_flipped_to_client_pos_due_to_hardship_or_refusal")
                csu_final = new_csu

    # 13. Operational Consistency & Strict Coupling:
    # (a) FFV allows ONLY "", "MOVED OUT", or "DECEASED BORROWER"
    # (b) Blank RFD is ALWAYS paired with FFV
    is_ffv = csu_final == "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
    if is_ffv:
        if matched_rfd not in ("", "MOVED OUT", "DECEASED BORROWER"):
            _record("rfd", matched_rfd, "", "ffv_allows_only_blank_moved_out_deceased")
            matched_rfd = ""
    elif matched_rfd == "":
        _record("csu", csu_final, "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", "blank_rfd_paired_with_ffv")
        csu_final = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"

    # If RFD is MOVED OUT or DECEASED BORROWER, CSU must be FFV
    if matched_rfd in ("MOVED OUT", "DECEASED BORROWER") and not is_ffv:
        _record("csu", csu_final, "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", "moved_out_deceased_pairs_with_ffv")
        csu_final = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"

    # Final normalization of RFD string to exact RCBC uppercase standard
    final_rfd_clean = matched_rfd.strip()
    for opt in OFFICIAL_RCBC_RFD_OPTIONS:
        if final_rfd_clean.upper() == opt.upper():
            final_rfd_clean = opt
            break
    if final_rfd_clean.upper() in ("", "EMPTY", "NONE", "NULL", "NAN", "<NA>"):
        final_rfd_clean = ""
    matched_rfd = final_rfd_clean

    if return_overrides:
        return (csu_final if csu_final else None, matched_rfd, overrides)
    return (csu_final if csu_final else None, matched_rfd)


class TwoTierRemarksSummarizer:
    """
    Coordinates 2-Tier Language Routed Summarization across:
      - Model A (Multilingual / Taglish): minimax-m2.5 (or qwen3-32b)
      - Model B (English-Only): qwen3-32b
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model_tagalog: str | None = None,
        model_english: str | None = None,
        system_instructions: str | None = None,
        max_concurrency: int | None = None,
        timeout: float | None = None,
        profile_id: str | None = None,
    ):
        from engine.profiles import get_profile_by_id, get_active_profile, DEFAULT_OPERATIONAL_DIRECTIVES
        self.profile_id = profile_id or get_active_profile().get("id", "default")
        active_prof = get_profile_by_id(self.profile_id)

        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (api_key or DEFAULT_API_KEY).strip()
        self.model_tagalog = model_tagalog or active_prof.get("model_tagalog") or MODEL_TAGALOG
        self.model_english = model_english or active_prof.get("model_english") or MODEL_ENGLISH
        self.system_instructions = (
            system_instructions or active_prof.get("system_instructions") or DEFAULT_OPERATIONAL_DIRECTIVES
        )
        self.max_concurrency = max_concurrency or MAX_CONCURRENCY
        self.timeout = timeout or REQUEST_TIMEOUT

    def reload_active_profile(self, profile_id: str | None = None) -> dict[str, Any]:
        """Reloads models and system instructions from the active profile on disk."""
        from engine.profiles import get_profile_by_id, get_active_profile, DEFAULT_OPERATIONAL_DIRECTIVES
        if profile_id:
            self.profile_id = profile_id
        else:
            self.profile_id = get_active_profile().get("id", "default")
        active_prof = get_profile_by_id(self.profile_id)
        self.model_tagalog = active_prof.get("model_tagalog") or MODEL_TAGALOG
        self.model_english = active_prof.get("model_english") or MODEL_ENGLISH
        self.system_instructions = active_prof.get("system_instructions") or DEFAULT_OPERATIONAL_DIRECTIVES
        return active_prof

    @property
    def is_available(self) -> bool:
        """True if an API key is configured."""
        return bool(self.api_key and not self.api_key.startswith("sk-placeholder"))

    def _build_prompt(
        self,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        unit_status_val: str = "",
        eca_header: str = "",
        max_summary_chars: int = 180,
    ) -> tuple[list[dict[str, str]], str]:
        primary_csu_formatted = "\n".join(f"  - {c}" for c in PRIMARY_CSU_OPTIONS)
        secondary_csu_formatted = "\n".join(f"  - {c}" for c in SECONDARY_CSU_OPTIONS)

        primary_rfd_formatted = "\n".join(f"  - {r}" for r in PRIMARY_RFD_OPTIONS)
        secondary_rfd_formatted = "\n".join(f"  - {r}" for r in SECONDARY_RFD_OPTIONS)

        custom_rules = _load_active_custom_rules()
        custom_rules_section = ""
        if custom_rules["csu_rfd_rules"] or custom_rules["summary_rules"]:
            lines = []
            if custom_rules["csu_rfd_rules"]:
                lines.append("Reviewer CSU/RFD Directives:")
                for rule in custom_rules["csu_rfd_rules"]:
                    lines.append(f"  * {rule}")
            if custom_rules["summary_rules"]:
                lines.append("Reviewer Summary Directives:")
                for rule in custom_rules["summary_rules"]:
                    lines.append(f"  * {rule}")
            custom_rules_section = f"\n### ACTIVE HUMAN REVIEWER TUNED DIRECTIVES (HIGHEST PRIORITY):\n" + "\n".join(lines) + "\n"

        directives = (self.system_instructions or "").strip()
        if not directives:
            from engine.profiles import DEFAULT_OPERATIONAL_DIRECTIVES
            directives = DEFAULT_OPERATIONAL_DIRECTIVES

        directives = directives.replace("{max_summary_chars}", str(max_summary_chars))

        system_prompt = (
            "You are an expert Data Analyst and Credit Operations Specialist for RCBC Auto Loan field collection reports.\n"
            "Your task is to analyze the collector's remark and return a valid JSON object strictly matching this schema:\n"
            "{\n"
            '  "csu_reasoning": "<1-2 sentence explanation analyzing contact entity, unit sighting, and dispute vs refusal first, and why alternatives were disqualified>",\n'
            '  "csu": "<Exact CSU verbatim from ALLOWED_CSU determined from the reasoning above>",\n'
            '  "csu_confidence": "<high | medium | low>",\n'
            '  "csu_alternatives": ["<Other candidate CSU from ALLOWED_CSU if uncertain, otherwise empty list []>"],\n'
            '  "rfd_reasoning": "<1-2 sentence explanation analyzing whether dispute/hardship overrides refusal first, and why alternatives were disqualified>",\n'
            '  "rfd": "<Exact RFD verbatim from ALLOWED_RFD, or empty string \\"\\" if zero info/unknown/unlocated, determined from the reasoning above>",\n'
            '  "rfd_confidence": "<high | medium | low>",\n'
            '  "rfd_alternatives": ["<Other candidate RFD from ALLOWED_RFD if uncertain, otherwise empty list []>"],\n'
            '  "detailed_rfd": "<3-clause string: [RFD clause]; [TALK TO clause]; [Statement]>",\n'
            '  "summary": "<concise summary note under max_chars>"\n'
            "}\n\n"
            "DECISION UNCERTAINTY & ALTERNATIVE HANDLING:\n"
            "- If multiple classifications are plausible or you cannot decide definitively:\n"
            "  1. Select the single option you are MOST confident in for 'csu' and 'rfd'.\n"
            "  2. Set 'csu_confidence' and/or 'rfd_confidence' to 'medium' or 'low'.\n"
            "  3. Populate 'csu_alternatives' and/or 'rfd_alternatives' with the other viable candidates.\n"
            "  4. In 'csu_reasoning' and 'rfd_reasoning', clearly explain why the top candidate won and how the alternatives differ.\n"
            "- If completely certain, set confidence to 'high' and alternatives to [].\n\n"
            f"ALLOWED_CSU (Select EXACTLY one verbatim from this official RCBC list):\n"
            f"* PRIMARY MATRIX (Default for field visit outcomes):\n{primary_csu_formatted}\n"
            f"* SECONDARY/SPECIALIZED (Only if explicitly stated in note):\n{secondary_csu_formatted}\n\n"
            f"ALLOWED_RFD (Select EXACTLY one verbatim from this official RCBC list):\n"
            f"(Note: Select exactly one verbatim, or empty string \"\" if address/client unlocated or unknown):\n"
            f"* PRIMARY TIER (Most common operational RFDs):\n{primary_rfd_formatted}\n"
            f"* SECONDARY/SPECIALIZED TIER (Specific hardships):\n{secondary_rfd_formatted}\n\n"
            f"{directives}\n\n"
            f"{custom_rules_section}"
            "Return ONLY the raw JSON object. Do not include Markdown code blocks (no ```json), explanations, or preamble."
        )

        parts = []
        if contact_person and contact_person.strip():
            parts.append(f"Borrower / Contact Person: {contact_person.strip()}")
        if "no_concat" not in self.profile_id and concat_val and concat_val.strip() and concat_val.strip().lower() != "none":
            parts.append(f"Field Status / Substatus (CONCAT): {concat_val.strip()}")
        if unit_status_val and unit_status_val.strip() and unit_status_val.strip().lower() != "none":
            parts.append(f"Unit Status: {unit_status_val.strip()}")
        parts.append(f"Field Remark: {remark.strip()}")
        user_content = "\n".join(parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        return messages

    async def call_llm(
        self,
        client: httpx.AsyncClient,
        model: str,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        unit_status_val: str = "",
        eca_header: str = "",
        retries: int = 2,
    ) -> dict[str, Any]:
        """Invokes a specific model tier over LiteLLM proxy."""
        if not self.is_available:
            return {
                "summary": None,
                "csu": None,
                "rfd": None,
                "detailed_rfd": None,
                "model": model,
                "provider_model": model,
                "user_prompt": "",
                "raw_model_json": "",
                "final_json": "",
                "overrides_applied": [],
                "prompt_profile": self.profile_id,
                "error": "No API key configured",
            }

        # Calculate space available for summary considering ECA header
        eca_len = len(eca_header)
        avail_chars = max(40, 195 - eca_len)

        effective_concat = "" if "no_concat" in self.profile_id else concat_val

        messages = self._build_prompt(
            remark=remark,
            contact_person=contact_person,
            concat_val=effective_concat,
            unit_status_val=unit_status_val,
            eca_header=eca_header,
            max_summary_chars=avail_chars,
        )
        user_content = messages[1]["content"] if len(messages) > 1 else ""

        # Auto-normalize model alias quirks
        norm_model = (model or "").strip().lower()
        if norm_model in ("nova-lite", "nova_lite"):
            target_model = "nova-2-lite"
        elif norm_model in ("qwen3-32b", "qwen-3-32b", "qwen3_32b", "qwen32b"):
            target_model = "qwen3-32b"
        elif norm_model in ("minimax", "minimax-2.5", "minimax_m2.5", "minimax-m2.5"):
            target_model = "minimax-m2.5"
        else:
            target_model = model.strip()

        # Part 0d: Determinism: lowest temperature and fixed seed where supported
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.0,
            "seed": 42,
            "max_tokens": 1500,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                # Part 0b: Exponential backoff on retries
                if attempt > 0:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    msg_obj = data["choices"][0]["message"]
                    content = (msg_obj.get("content") or "").strip()
                    # If model returned reasoning_content but empty content (or reasoning tokens exceeded)
                    if not content and msg_obj.get("reasoning_content"):
                        content = msg_obj.get("reasoning_content", "").strip()
                    parsed = _extract_json_payload(content)
                    if parsed and isinstance(parsed, dict):
                        raw_csu = str(parsed.get("csu", "")).strip()
                        raw_rfd = str(parsed.get("rfd", "")).strip()

                        # Part 0e: Enforce coupling: FFV allows only RFD "", MOVED OUT, or DECEASED BORROWER.
                        # Blank RFD is always paired with FFV. If violated, reject and re-ask once quoting the violated rule.
                        is_ffv = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)" in raw_csu.upper()
                        is_blank_rfd = raw_rfd.upper() in ("", "NONE", "NULL", "EMPTY", "NAN", "<NA>")
                        coupling_violation = (
                            (is_ffv and raw_rfd.upper() not in ("", "NONE", "NULL", "EMPTY", "NAN", "<NA>", "MOVED OUT", "DECEASED BORROWER"))
                            or (is_blank_rfd and not is_ffv)
                        )

                        if coupling_violation and attempt < retries:
                            retry_messages = list(messages) + [
                                {"role": "assistant", "content": content},
                                {
                                    "role": "user",
                                    "content": (
                                        "VIOLATION OF COUPLING RULE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' "
                                        "strictly permits only 3 RFDs: '', 'MOVED OUT', or 'DECEASED BORROWER'. "
                                        "A blank RFD '' is always paired with 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'. "
                                        f"Your output had CSU: '{raw_csu}' and RFD: '{raw_rfd}'. "
                                        "Please correct your classification and return valid JSON adhering strictly to this rule."
                                    ),
                                },
                            ]
                            try:
                                re_payload = dict(payload)
                                re_payload["messages"] = retry_messages
                                re_resp = await client.post(
                                    f"{self.base_url}/chat/completions",
                                    headers=headers,
                                    json=re_payload,
                                    timeout=self.timeout,
                                )
                                if re_resp.status_code == 200:
                                    re_data = re_resp.json()
                                    re_content = (re_data["choices"][0]["message"].get("content") or "").strip()
                                    if not re_content and re_data["choices"][0]["message"].get("reasoning_content"):
                                        re_content = re_data["choices"][0]["message"].get("reasoning_content", "").strip()
                                    re_parsed = _extract_json_payload(re_content)
                                    if re_parsed and isinstance(re_parsed, dict):
                                        parsed = re_parsed
                                        content = re_content
                                        raw_csu = str(parsed.get("csu", "")).strip()
                                        raw_rfd = str(parsed.get("rfd", "")).strip()
                            except Exception:
                                pass

                        summary_text = str(parsed.get("summary", "")).strip()
                        csu_val, rfd_val, applied_overrides = canonicalize_csu_rfd(
                            raw_csu, raw_rfd, remark=remark, concat_val=effective_concat, unit_status_val=unit_status_val, return_overrides=True
                        )
                        csu_reasoning = str(parsed.get("csu_reasoning", "")).strip()
                        rfd_reasoning = str(parsed.get("rfd_reasoning", "")).strip()
                        csu_conf = str(parsed.get("csu_confidence", "high")).strip().lower()
                        if csu_conf not in ("high", "medium", "low"):
                            csu_conf = "high"
                        rfd_conf = str(parsed.get("rfd_confidence", "high")).strip().lower()
                        if rfd_conf not in ("high", "medium", "low"):
                            rfd_conf = "high"

                        raw_csu_alts = parsed.get("csu_alternatives", [])
                        if not isinstance(raw_csu_alts, list):
                            raw_csu_alts = []
                        csu_alts = []
                        for a in raw_csu_alts:
                            c_can, _ = canonicalize_csu_rfd(str(a), "", remark=remark, concat_val=effective_concat, unit_status_val=unit_status_val)
                            if c_can and c_can != csu_val and c_can not in csu_alts:
                                csu_alts.append(c_can)

                        raw_rfd_alts = parsed.get("rfd_alternatives", [])
                        if not isinstance(raw_rfd_alts, list):
                            raw_rfd_alts = []
                        rfd_alts = []
                        for a in raw_rfd_alts:
                            _, r_can = canonicalize_csu_rfd("", str(a), remark=remark, concat_val=effective_concat, unit_status_val=unit_status_val)
                            if r_can and r_can != rfd_val and r_can not in rfd_alts:
                                rfd_alts.append(r_can)

                        detailed_rfd = str(parsed.get("detailed_rfd", "")).strip()
                        # Enforce ECA header join & length limit
                        if eca_header and not summary_text.upper().startswith("ECA "):
                            candidate = f"{eca_header}{summary_text}".strip()
                        else:
                            candidate = summary_text

                        if len(candidate) > 200:
                            candidate = fallback_clause_truncate(
                                candidate, max_chars=200, preferred_chars=181
                            )

                        final_json_payload = json.dumps({
                            "csu": csu_val,
                            "rfd": rfd_val,
                            "detailed_rfd": detailed_rfd,
                            "summary": candidate,
                            "csu_reasoning": csu_reasoning,
                            "rfd_reasoning": rfd_reasoning,
                            "csu_confidence": csu_conf,
                            "csu_alternatives": csu_alts,
                            "rfd_confidence": rfd_conf,
                            "rfd_alternatives": rfd_alts,
                        }, ensure_ascii=False)

                        # Part 2: derive needs_review flag
                        needs_review = (
                            csu_conf != "high"
                            or rfd_conf != "high"
                            or bool(csu_alts)
                            or bool(rfd_alts)
                        )

                        return {
                            "summary": candidate,
                            "csu": csu_val if csu_val else None,
                            "rfd": rfd_val,
                            "csu_reasoning": csu_reasoning,
                            "rfd_reasoning": rfd_reasoning,
                            "csu_confidence": csu_conf,
                            "csu_alternatives": csu_alts,
                            "rfd_confidence": rfd_conf,
                            "rfd_alternatives": rfd_alts,
                            "detailed_rfd": detailed_rfd if detailed_rfd else None,
                            "model": target_model,
                            "provider_model": target_model,
                            "raw_content": content,
                            "user_prompt": user_content,
                            "raw_model_json": content,
                            "final_json": final_json_payload,
                            "overrides_applied": applied_overrides,
                            "prompt_profile": self.profile_id,
                            "is_fallback": False,
                            "needs_review": needs_review,
                        }
                elif resp.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                else:
                    last_err = Exception(f"HTTP {resp.status_code}: {resp.text[:120]}")
            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        # Part 0b: NEVER silently substitute rule-engine output when an LLM call fails.
        # Mark row as FALLBACK visibly so it can be excluded from stats and rerun.
        candidate_fallback = fallback_clause_truncate(
            f"{eca_header}{remark}".strip() if eca_header else remark.strip(),
            max_chars=200,
            preferred_chars=181,
        )
        return {
            "summary": candidate_fallback,
            "csu": "FALLBACK",
            "rfd": "FALLBACK",
            "detailed_rfd": None,
            "csu_reasoning": "LLM call failed after retries; marked FALLBACK.",
            "rfd_reasoning": "LLM call failed after retries; marked FALLBACK.",
            "csu_confidence": "low",
            "csu_alternatives": [],
            "rfd_confidence": "low",
            "rfd_alternatives": [],
            "model": target_model,
            "provider_model": target_model,
            "raw_content": "",
            "user_prompt": user_content,
            "raw_model_json": "",
            "final_json": "",
            "overrides_applied": [],
            "prompt_profile": self.profile_id,
            "is_fallback": True,
            "needs_review": True,
            "error": str(last_err) if last_err else "Failed after retries",
        }

    async def call_small_english_llm(
        self,
        client: httpx.AsyncClient,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        unit_status_val: str = "",
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the fast/compact English model (nova-2-lite)."""
        return await self.call_llm(
            client=client,
            model=self.model_english,
            remark=remark,
            contact_person=contact_person,
            concat_val=concat_val,
            unit_status_val=unit_status_val,
            eca_header=eca_header,
        )

    async def call_multilingual_llm(
        self,
        client: httpx.AsyncClient,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        unit_status_val: str = "",
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the multilingual model for Tagalog/Taglish (qwen3-32b)."""
        return await self.call_llm(
            client=client,
            model=self.model_tagalog,
            remark=remark,
            contact_person=contact_person,
            concat_val=concat_val,
            unit_status_val=unit_status_val,
            eca_header=eca_header,
        )

    async def parallel_process_records(
        self,
        records: list[dict[str, Any]],
        force_all: bool = False,
        progress_callback: Any = None,
    ) -> tuple[list[dict[str, Any]], float]:
        """
        1. Batch detects languages in parallel using Lingua's Rust parallel engine.
        2. Dispatches LLM tasks concurrently via asyncio.gather with an asyncio.Semaphore.
        Returns (processed_records, detection_latency_ms).
        """
        if not records:
            return [], 0.0

        # Extract text for language detection
        raw_remarks = [
            str(r.get("cleaned_remark", "") or r.get("raw_remark", "")).strip() for r in records
        ]

        # 1. Parallel Language Routing with Lingua (sub-50ms)
        languages, detection_latency_ms = measure_batch_detection(raw_remarks)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        completed_tasks = 0
        total_tasks = 0

        async def _bounded_call(coro: Any) -> Any:
            nonlocal completed_tasks
            async with semaphore:
                try:
                    return await coro
                finally:
                    completed_tasks += 1
                    if progress_callback:
                        try:
                            progress_callback({
                                "stage": "ai_inference",
                                "completed": completed_tasks,
                                "total": total_tasks,
                                "message": f"Processed {completed_tasks} of {total_tasks} remarks ({int(completed_tasks / max(1, total_tasks) * 100)}%)",
                            })
                        except Exception:
                            pass

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = []
            task_indices = []

            for idx, (record, lang) in enumerate(zip(records, languages, strict=False)):
                record["detected_language"] = lang
                record["language_route"] = lang  # "ENGLISH" | "TAGALOG"
                remark_text = record.get("cleaned_remark", "") or record.get("raw_remark", "")
                contact_person = record.get("contact_person", "")
                concat_val = record.get("concat_val", "")
                unit_status_val = record.get("unit_status_val", "")
                eca_header = record.get("eca_header", "")

                # If remark already fits within 200 chars and force_all=False, pass through
                total_len = len(eca_header) + len(remark_text)
                needs_llm = force_all or (total_len > 200)

                if not self.is_available or not needs_llm:
                    record["ai_dispatched"] = False
                    record["model_used"] = "passthrough"
                    # Default summary to ECA + remark if fits, else clause truncate
                    cand = f"{eca_header}{remark_text}".strip() if eca_header else remark_text
                    if len(cand) > 200:
                        cand = fallback_clause_truncate(cand, max_chars=200, preferred_chars=181)
                    record["summary"] = cand
                    record["is_fallback"] = False
                    record["needs_review"] = False
                    record["prompt_profile"] = self.profile_id
                    record["provider_model"] = "passthrough"
                    record["user_prompt"] = ""
                    record["raw_model_json"] = ""
                    record["final_json"] = ""
                    record["overrides_applied"] = []
                    continue

                record["ai_dispatched"] = True
                task_indices.append(idx)
                if lang == "TAGALOG":
                    coro = self.call_multilingual_llm(
                        client, remark_text, contact_person=contact_person, concat_val=concat_val, unit_status_val=unit_status_val, eca_header=eca_header
                    )
                else:
                    coro = self.call_small_english_llm(
                        client, remark_text, contact_person=contact_person, concat_val=concat_val, unit_status_val=unit_status_val, eca_header=eca_header
                    )
                tasks.append(_bounded_call(coro))

            total_tasks = len(tasks)
            if total_tasks > 0 and progress_callback:
                try:
                    progress_callback({
                        "stage": "ai_inference",
                        "completed": 0,
                        "total": total_tasks,
                        "message": f"Dispatching {total_tasks} remarks to AI engine across {self.max_concurrency} parallel workers...",
                    })
                except Exception:
                    pass

            # Execute LLM tasks concurrently
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for task_idx, res in zip(task_indices, results, strict=False):
                    rec = records[task_idx]
                    if isinstance(res, Exception):
                        rec["error"] = str(res)
                        rec["model_used"] = "error_fallback"
                        cand = f"{rec.get('eca_header', '')}{rec.get('cleaned_remark', '')}".strip()
                        rec["summary"] = fallback_clause_truncate(
                            cand, max_chars=200, preferred_chars=181
                        )
                        rec["csu"] = "FALLBACK"
                        rec["rfd"] = "FALLBACK"
                        rec["csu_reasoning"] = f"LLM exception: {res}"
                        rec["rfd_reasoning"] = f"LLM exception: {res}"
                        rec["csu_confidence"] = "low"
                        rec["csu_alternatives"] = []
                        rec["rfd_confidence"] = "low"
                        rec["rfd_alternatives"] = []
                        rec["user_prompt"] = ""
                        rec["raw_model_json"] = ""
                        rec["final_json"] = ""
                        rec["overrides_applied"] = []
                        rec["prompt_profile"] = self.profile_id
                        rec["provider_model"] = self.model_tagalog if rec.get("detected_language") == "TAGALOG" else self.model_english
                        rec["is_fallback"] = True
                        rec["needs_review"] = True
                    elif isinstance(res, dict):
                        if res.get("csu"):
                            rec["csu"] = res.get("csu")
                        if "rfd" in res and res.get("rfd") is not None:
                            rec["rfd"] = res.get("rfd")
                        if res.get("csu_reasoning"):
                            rec["csu_reasoning"] = res.get("csu_reasoning")
                        if res.get("rfd_reasoning"):
                            rec["rfd_reasoning"] = res.get("rfd_reasoning")
                        if "csu_confidence" in res:
                            rec["csu_confidence"] = res.get("csu_confidence", "high")
                        if "csu_alternatives" in res:
                            rec["csu_alternatives"] = res.get("csu_alternatives", [])
                        if "rfd_confidence" in res:
                            rec["rfd_confidence"] = res.get("rfd_confidence", "high")
                        if "rfd_alternatives" in res:
                            rec["rfd_alternatives"] = res.get("rfd_alternatives", [])
                        if res.get("detailed_rfd"):
                            rec["detailed_rfd"] = res.get("detailed_rfd")
                        if res.get("summary"):
                            rec["summary"] = res.get("summary")
                            rec["model_used"] = res.get("model")
                        else:
                            rec["model_used"] = "rule_fallback"
                            header = rec.get("eca_header", "")
                            body = rec.get("cleaned_remark", "")
                            cand = f"{header}{body}".strip()
                            rec["summary"] = fallback_clause_truncate(
                                cand, max_chars=200, preferred_chars=181
                            )
                            if res.get("error"):
                                rec["error"] = res.get("error")

                        # Attach Step 2 Logging fields
                        rec["user_prompt"] = res.get("user_prompt", "")
                        rec["raw_model_json"] = res.get("raw_model_json", "")
                        rec["final_json"] = res.get("final_json", "")
                        rec["overrides_applied"] = res.get("overrides_applied", [])
                        rec["prompt_profile"] = res.get("prompt_profile", self.profile_id)
                        rec["provider_model"] = res.get("provider_model", res.get("model", ""))
                        rec["is_fallback"] = res.get("is_fallback", False)
                        rec["needs_review"] = res.get("needs_review", False)

        return records, detection_latency_ms


# Module-level convenience functions
_GLOBAL_SUMMARIZER: TwoTierRemarksSummarizer | None = None


def get_summarizer() -> TwoTierRemarksSummarizer:
    global _GLOBAL_SUMMARIZER
    if _GLOBAL_SUMMARIZER is None:
        _GLOBAL_SUMMARIZER = TwoTierRemarksSummarizer()
    return _GLOBAL_SUMMARIZER


def parallel_process_remarks(
    records: list[dict[str, Any]], force_all: bool = False
) -> list[dict[str, Any]]:
    """Synchronous wrapper for parallel_process_records."""
    summarizer = get_summarizer()
    processed, _ = asyncio.run(summarizer.parallel_process_records(records, force_all=force_all))
    return processed


__all__ = [
    "TwoTierRemarksSummarizer",
    "get_summarizer",
    "parallel_process_remarks",
    "canonicalize_csu_rfd",
    "MODEL_TAGALOG",
    "MODEL_ENGLISH",
    "DEFAULT_BASE_URL",
    "DEFAULT_API_KEY",
    "OFFICIAL_RCBC_CSU_OPTIONS",
    "OFFICIAL_RCBC_RFD_OPTIONS",
]
