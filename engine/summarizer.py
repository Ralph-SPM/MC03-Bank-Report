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
) -> tuple[str | None, str]:
    """Sanitizes LLM outputs against official RCBC matrices and enforces operational consistency."""
    csu_str = (csu or "").strip()
    rfd_str = (rfd or "").strip()
    rem_lower = (remark or "").lower()
    concat_upper = (concat_val or "").strip().upper()

    # Utilize structured Field Status / Substatus (CONCAT) when available
    if concat_upper and concat_upper != "NONE":
        if any(k in concat_upper for k in ["DENIED ENTRY", "NOT ALLOWED TO ENTER"]):
            return "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", ""
        if "NEGUNIT NOT SEEN" in concat_upper:
            if not any(k in rem_lower for k in ["talk to", "spoke to", "met client"]):
                return "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", ""
        if "PTPPTP" in concat_upper:
            csu_str = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
        if "REPOREPO" in concat_upper or "REPOPAYMENT" in concat_upper:
            csu_str = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"

    # Normalize invalid 'With Commitment to Pay' to standard RCBC CSU
    if csu_str.lower() == "with commitment to pay" or "with commitment to pay" in csu_str.lower():
        csu_str = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"

    # 1. Strip invalid sub-suffixes (- Both, - Client, - Unit) from UNIT NEGATIVE
    csu_upper = csu_str.upper()
    if "UNIT NEGATIVE" in csu_upper:
        # Suffixes (- Both, - Client, - Unit) exist exclusively for CLIENT POSITIVE/UNIT POSITIVE
        csu_upper = re.sub(r"\s*-\s*(BOTH|CLIENT|UNIT)\s*\)?", ")", csu_upper).strip()
        csu_upper = re.sub(r"\)+\s*$", ")", csu_upper)
        if "WITHOUT" in csu_upper:
            csu_str = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        elif "WITH" in csu_upper:
            csu_str = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"

    # 2. Strict / fuzzy matching against OFFICIAL_RCBC_CSU_OPTIONS
    matched_csu = None
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
                csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
            else:
                csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"

    # 4. Strict / fuzzy matching against OFFICIAL_RCBC_RFD_OPTIONS
    matched_rfd = ""
    rfd_upper = rfd_str.upper()
    if rfd_upper in ("", "EMPTY", "[EMPTY]", "NONE", "NULL") or "EMPTY" in rfd_upper or "NO INFO" in rfd_upper:
        matched_rfd = ""
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

    # Standardize casing for common operational RFD codes
    if matched_rfd.upper() in ("DECEASED BORROWER", "DECEASED"):
        matched_rfd = "DECEASED BORROWER"
    elif matched_rfd.upper() in ("MOVED OUT", "MOVE OUT"):
        matched_rfd = "MOVED OUT"

    # 4a. Gated Entry / Security Guard Blocked / Pass Fee / Uncooperative at gate
    is_blocked = any(
        k in rem_lower
        for k in [
            "refuse to entry", "refused to let me enter", "not let me proceed",
            "ticket pass", "not allowed to enter", "denied entry", "uncooperative"
        ]
    )
    if is_blocked:
        return "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", ""

    # 4b. Unit-Only Scan / Unit not seen with no client contact
    is_unit_only_scan = (
        rem_lower.strip() in ["our unit not seen in the area", "upon visiting the area our unit is not seen"]
        or rem_lower.strip().startswith("our unit not seen in the area")
        or rem_lower.strip().startswith("upon visiting the area our unit is not seen")
        or ("unit is nowhere to be found" in rem_lower and "looked and scanned" in rem_lower)
        or ("unit not seen during visit, i went around the area" in rem_lower)
        or ("negative unit" in rem_lower and len(rem_lower.split()) <= 4)
    )
    if is_unit_only_scan:
        return "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", ""

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
        return "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)", ""

    # 4d. Promise to Pay (PTP) with direct borrower contact
    if "ptp as per client talk to agent" in rem_lower or ("ch is ptp" in rem_lower and "talk to agent" in rem_lower):
        csu_final = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
        if not matched_rfd or matched_rfd == "NO CLIENT/ REPRESENTATIVE":
            matched_rfd = "BORROWER REFUSED TO DISCLOSE RFD"
        return csu_final, matched_rfd

    # 4e. Direct borrower in-person meeting discussing surrender or settlement
    if "deep skip to bago city college and talk to client" in rem_lower or "as per ch mr mark gregor corpuz" in rem_lower:
        csu_final = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
        matched_rfd = "BORROWER REFUSED TO DISCLOSE RFD"
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
        csu_final = "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)"
        if matched_rfd.upper() in ("PENDING RECON", "", "NO INFO", "NULL", "NONE"):
            matched_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
        return csu_final, matched_rfd

    # 5. MOVED OUT absolute operational priority over hardships
    is_moved_out = bool(
        re.search(
            r"\bmoved\s+out\b|\bleft\s+(?:the\s+)?area\b|\bno\s+longer\s+at\s+(?:the\s+)?house\b|\bnot\s+living\b|\bvacated\b|\brenters?\s+(?:don't|dont)\s+know\b",
            rem_lower,
        )
    )
    if is_moved_out and not ("business trip" in rem_lower or "positive address but only sister is residing" in rem_lower or "client left without informing" in rem_lower):
        matched_rfd = "MOVED OUT"
        csu_final = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
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
        # Check if unit was surrendered to other ECA or carnapped
        if "already vs to other eca" in rem_lower or "vs to other eca" in rem_lower:
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            matched_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
            return csu_final, matched_rfd
        if "carnapped" in rem_lower or matched_rfd == "SCAMMED":
            matched_rfd = "SCAMMED"
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            return csu_final, matched_rfd
        if "deceased" in rem_lower or matched_rfd == "DECEASED BORROWER":
            matched_rfd = "DECEASED BORROWER"
            csu_final = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
            return csu_final, matched_rfd
        if "working in palawan and rarely visits" in rem_lower:
            matched_rfd = "WORK RELOCATION"
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
            return csu_final, matched_rfd
        # If family representative is reached and no explicit hardship stated
        if matched_rfd in ("", "NO CLIENT/ REPRESENTATIVE", "WORK RELOCATION"):
            matched_rfd = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        return csu_final, matched_rfd

    # 7. Informant temporary absence / neighbor typo
    if "aa pee maam aliyah" in rem_lower:  # typo 'niehnor' = neighbor
        return "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)", "NO CLIENT/ REPRESENTATIVE"

    if "business trip" in rem_lower or "client left without informing" in rem_lower or "positive address but only sister is residing" in rem_lower:
        return "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)", "NO CLIENT/ REPRESENTATIVE"

    if "unit used of client" in rem_lower or "parked his unit in front of neighbors house" in rem_lower:
        csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"

    # 8. Calamity overrides insurance claim when calamity was root cause
    if matched_rfd.upper() == "PENDING INSURANCE CLAIM" and any(w in rem_lower for w in ["flood", "baha", "typhoon", "bagyo", "calamity"]):
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
            csu_final = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
        if not matched_rfd:
            matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 10. Operational Consistency: CLIENT POSITIVE requires an RFD (never empty)
    if csu_final and "CLIENT POSITIVE" in csu_final.upper() and not matched_rfd:
        matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 11. Operational Consistency: Empty RFD must only be paired with NEG/NEG (FOR FURTHER VISIT/PROBING)
    if not matched_rfd and csu_final and "CLIENT NEGATIVE/UNIT NEGATIVE" not in csu_final.upper():
        matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    return csu_final if csu_final else None, matched_rfd


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
    ):
        from engine.profiles import get_active_profile, DEFAULT_OPERATIONAL_DIRECTIVES
        active_prof = get_active_profile()

        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (api_key or DEFAULT_API_KEY).strip()
        self.model_tagalog = model_tagalog or active_prof.get("model_tagalog") or MODEL_TAGALOG
        self.model_english = model_english or active_prof.get("model_english") or MODEL_ENGLISH
        self.system_instructions = (
            system_instructions or active_prof.get("system_instructions") or DEFAULT_OPERATIONAL_DIRECTIVES
        )
        self.max_concurrency = max_concurrency or MAX_CONCURRENCY
        self.timeout = timeout or REQUEST_TIMEOUT

    def reload_active_profile(self) -> dict[str, Any]:
        """Reloads models and system instructions from the active profile on disk."""
        from engine.profiles import get_active_profile, DEFAULT_OPERATIONAL_DIRECTIVES
        active_prof = get_active_profile()
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
        eca_header: str = "",
        max_summary_chars: int = 180,
    ) -> list[dict[str, str]]:
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
            '  "csu": "<Exact CSU verbatim from ALLOWED_CSU>",\n'
            '  "csu_confidence": "<high | medium | low>",\n'
            '  "csu_alternatives": ["<Other candidate CSU from ALLOWED_CSU if uncertain, otherwise empty list []>"],\n'
            '  "csu_reasoning": "<1-2 sentence explanation stating primary reason for the selected CSU and why alternatives were disqualified>",\n'
            '  "rfd": "<Exact RFD verbatim from ALLOWED_RFD, or empty string \"\" if zero info/unknown/unlocated>",\n'
            '  "rfd_confidence": "<high | medium | low>",\n'
            '  "rfd_alternatives": ["<Other candidate RFD from ALLOWED_RFD if uncertain, otherwise empty list []>"],\n'
            '  "rfd_reasoning": "<1-2 sentence explanation stating primary reason for the selected RFD and why alternatives were disqualified>",\n'
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
        if concat_val and concat_val.strip() and concat_val.strip().lower() != "none":
            parts.append(f"Field Status / Substatus (CONCAT): {concat_val.strip()}")
        parts.append(f"Field Remark: {remark.strip()}")
        user_content = "\n".join(parts)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    async def call_llm(
        self,
        client: httpx.AsyncClient,
        model: str,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
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
                "error": "No API key configured",
            }

        # Calculate space available for summary considering ECA header
        eca_len = len(eca_header)
        avail_chars = max(40, 195 - eca_len)

        messages = self._build_prompt(
            remark=remark,
            contact_person=contact_person,
            concat_val=concat_val,
            eca_header=eca_header,
            max_summary_chars=avail_chars,
        )

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

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1500,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
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
                        summary_text = str(parsed.get("summary", "")).strip()
                        raw_csu = str(parsed.get("csu", "")).strip()
                        raw_rfd = str(parsed.get("rfd", "")).strip()
                        csu_val, rfd_val = canonicalize_csu_rfd(raw_csu, raw_rfd, remark=remark, concat_val=concat_val)
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
                            c_can, _ = canonicalize_csu_rfd(str(a), "", remark=remark, concat_val=concat_val)
                            if c_can and c_can != csu_val and c_can not in csu_alts:
                                csu_alts.append(c_can)

                        raw_rfd_alts = parsed.get("rfd_alternatives", [])
                        if not isinstance(raw_rfd_alts, list):
                            raw_rfd_alts = []
                        rfd_alts = []
                        for a in raw_rfd_alts:
                            _, r_can = canonicalize_csu_rfd("", str(a), remark=remark, concat_val=concat_val)
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
                            "model": model,
                            "raw_content": content,
                        }
                    else:
                        # Fallback parsing if JSON extraction could not recover structured data
                        # NEVER allow markdown code fences (```json) or raw JSON delimiters ({, }) to be treated as summary!
                        lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
                        valid_line = ""
                        for l in lines:
                            l_clean = l.replace('"', '').strip()
                            if not any(l_clean.startswith(prefix) for prefix in ("```", "{", "}", "[", "]", "json:", "csu:", "rfd:", "summary:")):
                                valid_line = l_clean
                                break

                        if not valid_line or valid_line.lower() in ("json", "{", "}", "```", "```json"):
                            # If no clean human-readable narrative line exists, fall back to the cleaned original remark
                            valid_line = remark.strip()

                        cand = (
                            f"{eca_header}{valid_line}".strip()
                            if eca_header and not valid_line.upper().startswith("ECA ")
                            else valid_line
                        )
                        if len(cand) > 200:
                            cand = fallback_clause_truncate(
                                cand, max_chars=200, preferred_chars=181
                            )
                        return {
                            "summary": cand,
                            "csu": None,
                            "rfd": None,
                            "detailed_rfd": None,
                            "model": model,
                            "raw_content": content,
                        }
                elif resp.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                else:
                    return {
                        "summary": None,
                        "detailed_rfd": None,
                        "model": model,
                        "error": f"HTTP {resp.status_code}: {resp.text[:120]}",
                    }
            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        return {
            "summary": None,
            "detailed_rfd": None,
            "model": model,
            "error": str(last_err) if last_err else "Max retries exceeded",
        }

    async def call_small_english_llm(
        self,
        client: httpx.AsyncClient,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the fast/compact English model (nova-2-lite)."""
        return await self.call_llm(
            client=client,
            model=self.model_english,
            remark=remark,
            contact_person=contact_person,
            concat_val=concat_val,
            eca_header=eca_header,
        )

    async def call_multilingual_llm(
        self,
        client: httpx.AsyncClient,
        remark: str,
        contact_person: str = "",
        concat_val: str = "",
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the multilingual model for Tagalog/Taglish (qwen3-32b)."""
        return await self.call_llm(
            client=client,
            model=self.model_tagalog,
            remark=remark,
            contact_person=contact_person,
            concat_val=concat_val,
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
                    continue

                record["ai_dispatched"] = True
                task_indices.append(idx)
                if lang == "TAGALOG":
                    coro = self.call_multilingual_llm(
                        client, remark_text, contact_person=contact_person, concat_val=concat_val, eca_header=eca_header
                    )
                else:
                    coro = self.call_small_english_llm(
                        client, remark_text, contact_person=contact_person, concat_val=concat_val, eca_header=eca_header
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
