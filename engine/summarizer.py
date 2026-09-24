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
    """Extract JSON object from LLM output, handling markdown blocks or conversational wrapper."""
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    # If wrapped in markdown ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Direct JSON search
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass

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
) -> tuple[str | None, str]:
    """Sanitizes LLM outputs against official RCBC matrices and enforces operational consistency."""
    csu_str = (csu or "").strip()
    rfd_str = (rfd or "").strip()
    rem_lower = (remark or "").lower()

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

    # 4b. Account Confirmed Resolved / Settled per Agent or Bank
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

    # 5. MOVED OUT absolute operational priority over hardships
    is_moved_out = bool(
        re.search(
            r"\bmoved\s+out\b|\bleft\s+(?:the\s+)?area\b|\bno\s+longer\s+at\s+house\b|\bnot\s+living\b|\bvacated\b|\brenters?\s+(?:don't|dont)\s+know\b",
            rem_lower,
        )
    )
    if is_moved_out:
        matched_rfd = "MOVED OUT"
        csu_final = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"

    # 6. Informant vs Representative refusal guard
    has_family = any(
        w in rem_lower
        for w in [
            "wife", "husband", "spouse", "father", "mother", "sister", "brother",
            "sibling", "child", "son", "daughter", "niece", "nephew", "relative",
            "in-law", "inlaw", "in law", "nanay", "tatay", "kapatid", "asawa", "pinsan"
        ]
    )
    has_informant = any(
        w in rem_lower
        for w in [
            "sg ", "security guard", "guard ", "informant", "neighbor", "kapitbahay",
            "maid", "helper", "caretaker", "driver", "tenant", "renter", "landlord",
            "brgy", "barangay", "purok"
        ]
    )
    if has_informant and not has_family:
        if matched_rfd == "REPRESENTATIVE REFUSED TO DISCLOSE RFD":
            matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 7. Calamity overrides insurance claim when calamity was root cause
    if matched_rfd.upper() == "PENDING INSURANCE CLAIM" and any(w in rem_lower for w in ["flood", "baha", "typhoon", "bagyo", "calamity"]):
        matched_rfd = "CALAMITY"

    # 8. Operational Consistency: Presumed residency on closed house / unanswered visit
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

    # 9. Operational Consistency: CLIENT POSITIVE requires an RFD (never empty)
    if csu_final and "CLIENT POSITIVE" in csu_final.upper() and not matched_rfd:
        matched_rfd = "NO CLIENT/ REPRESENTATIVE"

    # 10. Operational Consistency: Empty RFD must only be paired with NEG/NEG (FOR FURTHER VISIT/PROBING)
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
        max_concurrency: int | None = None,
        timeout: float | None = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (api_key or DEFAULT_API_KEY).strip()
        self.model_tagalog = model_tagalog or MODEL_TAGALOG
        self.model_english = model_english or MODEL_ENGLISH
        self.max_concurrency = max_concurrency or MAX_CONCURRENCY
        self.timeout = timeout or REQUEST_TIMEOUT

    @property
    def is_available(self) -> bool:
        """True if an API key is configured."""
        return bool(self.api_key and not self.api_key.startswith("sk-placeholder"))

    def _build_prompt(
        self,
        remark: str,
        contact_person: str = "",
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

        system_prompt = (
            "You are an expert Data Analyst and Credit Operations Specialist for RCBC Auto Loan field collection reports.\n"
            "Your task is to analyze the collector's remark and return a valid JSON object strictly matching this schema:\n"
            "{\n"
            '  "csu": "<Exact CSU verbatim from ALLOWED_CSU>",\n'
            '  "rfd": "<Exact RFD verbatim from ALLOWED_RFD, or empty string \"\" if zero info/unknown/unlocated>",\n'
            '  "csu_reasoning": "<1-2 sentence explanation stating primary reason for the selected CSU and why alternatives were disqualified>",\n'
            '  "rfd_reasoning": "<1-2 sentence explanation stating primary reason for the selected RFD and why alternatives were disqualified>",\n'
            '  "detailed_rfd": "<3-clause string: [RFD clause]; [TALK TO clause]; [Statement]>",\n'
            '  "summary": "<concise summary note under max_chars>"\n'
            "}\n\n"
            f"ALLOWED_CSU (Select EXACTLY one verbatim from this official RCBC list):\n"
            f"* PRIMARY MATRIX (Default for field visit outcomes):\n{primary_csu_formatted}\n"
            f"* SECONDARY/SPECIALIZED (Only if explicitly stated in note):\n{secondary_csu_formatted}\n\n"
            f"ALLOWED_RFD (Select EXACTLY one verbatim from this official RCBC list):\n"
            f"(Note: Select exactly one verbatim, or empty string \"\" if address/client unlocated or unknown):\n"
            f"* PRIMARY TIER (Most common operational RFDs):\n{primary_rfd_formatted}\n"
            f"* SECONDARY/SPECIALIZED TIER (Specific hardships):\n{secondary_rfd_formatted}\n\n"
            "CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):\n\n"
            "RULE 1: CONTACT ENTITY CLASSIFICATION & BASELINE RFD:\n"
            "- COMPLETED REPOSSESSION: If remark indicates unit repossessed / surrendered ('Done repo', 'successfully repossessed', 'repo unit') -> RFD MUST BE 'BORROWER REFUSED TO DISCLOSE RFD' and CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'.\n"
            "- ACTUAL CONTACT - BORROWER: Direct contact with borrower (in person or phone/transfer). If no explicit hardship is stated -> baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.\n"
            "- ACTUAL CONTACT - FAMILY REPRESENTATIVE:\n"
            "  * Representatives are STRICTLY family members / relatives (spouse, mother, father, sibling, child, relative, in-laws, niece, nephew).\n"
            "  * If family rep interviewed and gives general info or refuses -> baseline RFD is 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.\n"
            "  * WORK RELOCATION vs REP REFUSED: If a family rep says the borrower relocated or is working in another province/abroad -> RFD is 'WORK RELOCATION'. BUT if the relative explicitly states they don't know the client's auto loan or refuses to engage with the loan issue (e.g. 'relative said in Dubai for work, unaware of auto loan') -> baseline remains 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.\n"
            "- INFORMANTS (STRICTLY NOT REPRESENTATIVES):\n"
            "  * Security guards (SG), Barangay Health Workers (BHW), purok leaders, barangay staff, caretakers, maids, helpers, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.\n"
            "  * An informant's refusal or reluctance to talk is NOT a representative refusal. NEVER assign 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' for an informant!\n"
            "  * If an informant is interviewed, confirms client lives there (or at work/out of area), or if informant refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.\n"
            "- CLOSED HOUSE / UNANSWERED VISITS (DA PRESUMED RESIDENCY BASELINE):\n"
            "  * If the remark indicates house closed ('HC', 'house closed', 'house close'), gate padlocked, no one answering, or neighbor verifies resident is not around/at work:\n"
            "  * The DA operational standard PRESUMES the borrower still resides at the address, just not home at that time.\n"
            "  * CSU MUST BE: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or UNIT POSITIVE if unit seen/confirmed).\n"
            "  * RFD MUST BE: 'NO CLIENT/ REPRESENTATIVE'.\n"
            "  * NEVER classify a simple closed house or unanswered visit as negative probing with empty RFD!\n\n"
            "RULE 2: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:\n"
            "- RANK 1: MOVED OUT (Primary Operational Fact):\n"
            "  * If informant, neighbor, landlord, or new tenant confirms the borrower completely moved out / vacated / left the area / renters don't know client: RFD is STRICTLY 'MOVED OUT', and CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.\n"
            "  * 'MOVED OUT' ALWAYS OVERRIDES HARDSHIPS. (e.g. 'moved out due to family issues' or 'tenants ch had stroke moved out' -> RFD is 'MOVED OUT', NOT 'FAMILY PROBLEM' or 'MEDICAL EXPENSE').\n"
            "- RANK 2: DECEASED BORROWER:\n"
            "  * Confirmed deceased -> RFD is 'DECEASED BORROWER', CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.\n"
            "- RANK 3: EMPTY RFD \"\" (Strictly Constrained to Unlocated/Unknown):\n"
            "  * Empty RFD \"\" is ALLOWED ONLY when the address itself is unlocated, incorrect, unknown in area ('client unknown at brgy'), or details are incomplete such that neither residency nor absence can be determined (and borrower is not confirmed dead or moved out).\n"
            "  * An empty RFD MUST ALWAYS have CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.\n"
            "  * If CSU is 'CLIENT POSITIVE...', the RFD must NEVER be empty (use 'NO CLIENT/ REPRESENTATIVE' if no contact).\n"
            "- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has NOT moved out):\n"
            "  * An explicit hardship stated by borrower or family rep overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):\n"
            "    - Flood, typhoon, earthquake, natural disaster -> 'CALAMITY' (CALAMITY overrides 'PENDING INSURANCE CLAIM' when calamity was the root cause!).\n"
            "    - Carnapped, stolen, scammed -> 'SCAMMED'.\n"
            "    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.\n"
            "    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).\n"
            "    - Illness, hospitalization, surgery, medical stroke -> 'MEDICAL EXPENSE'.\n"
            "    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.\n"
            "    - Business slowdown, mahina benta, income drop -> 'BUSINESS SLOWDOWN'.\n"
            "    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.\n"
            "    - Explicit claim of already settled / payment dispute by client undergoing bank verification -> 'PENDING RECON'.\n"
            "    - Secondary: Bank Account On-Hold, Business Closure, Death-Family Member, Delayed Pension, Reduction of Salary, Unemployment, Migration, Collateral Issue, Family Problem.\n\n"
            "RULE 3: CSU MATRIX & SUFFIX FORMATTING RULES:\n"
            "- DIRECT CONTACT WITH BORROWER:\n"
            "  * Unit seen parked in premises, or surrendered/repossessed -> 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'.\n"
            "  * Unit NOT seen in premises (or unit impounded, flooded, in repair shop, with third party) -> 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)'.\n"
            "- CONTACT WITH FAMILY REPRESENTATIVE:\n"
            "  * Unit seen parked in premises -> 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'.\n"
            "  * Unit NOT seen in premises -> 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.\n"
            "- INFORMANT INTERVIEW OR CLOSED HOUSE:\n"
            "  * Unit confirmed parked / seen in premises, or neighbor confirms client regularly parks/uses unit -> 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)'.\n"
            "  * Unit NOT seen -> 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.\n"
            "- ACCOUNT CONFIRMED RESOLVED / SETTLED BY AGENT OR BANK:\n"
            "  * When the remark indicates that the account has been resolved, settled, or cleared according to the agent, collector, or bank (e.g., 'according to the agent the account has been resolved', 'account resolved per agent', 'confirmed resolved by bank/agent'):\n"
            "    - CSU is STRICTLY: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'.\n"
            "    - DO NOT use 'PENDING RECON' or 'CLIENT POSITIVE/UNIT NEGATIVE'.\n"
            "      Reason: The agent/bank explicitly confirmed the account is resolved (not merely an unverified borrower claim or dispute). Because the account is confirmed resolved, it validates positive client and unit status without requiring direct contact with the borrower.\n"
            "    - RFD: Output 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' or 'NO CLIENT / REPRESENTATIVE REACHED' if no specific prior hardship was stated.\n"
            "- STRICT SUFFIX PROHIBITION:\n"
            "  * Suffixes '- Both', '- Client', '- Unit' exist ONLY AND EXCLUSIVELY for 'CLIENT POSITIVE/UNIT POSITIVE'.\n"
            "  * NEVER append '- Both', '- Client', or '- Unit' to 'CLIENT POSITIVE/UNIT NEGATIVE'. (Valid choices are ONLY 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)' or 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)').\n"
            "- SPECIALIZED CSU RESTRICTIONS:\n"
            "  * NEVER use 'For CASE FILING-...' CSUs unless the remark explicitly mentions 'case filing' or 'for filing' by the bank. (e.g. voluntary surrender negotiations or filing case against an assumer does NOT make CSU 'For CASE FILING').\n"
            "  * Use 'PENDING RECON' CSU ONLY when client/borrower explicitly claims account is already settled / paid and needs bank reconciliation (unverified by the agent). Do NOT use 'PENDING RECON' when the agent/collector confirms the resolution.\n\n"
            f"{custom_rules_section}\n"
            "RULE 4: SUMMARY, REASONING & DETAILED RFD FORMAT:\n"
            "- 'csu_reasoning': 1-2 concise sentences stating primary reason for the selected CSU and explicitly why alternative candidate CSUs were disqualified.\n"
            "- 'rfd_reasoning': 1-2 concise sentences stating primary reason for the selected RFD and explicitly why alternative candidate RFDs were disqualified.\n"
            f"- 'summary': Shortened note under {max_summary_chars} characters preserving what was stated, promised, or observed. Do NOT include borrower name, collector name, or prohibited tags (BCAL, BKAL, L3, INB, OBD, phone numbers).\n"
            "- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'. Example: 'BORROWER REFUSED TO DISCLOSE RFD; Contact made with borrower directly; PTP on Sept 15'. (If RFD is empty, format as '; [Contacted Entity]; [Action/Statement]').\n"
            "Return ONLY the raw JSON object. Do not include Markdown code blocks (no ```json), explanations, or preamble."
        )

        user_content = f"Field Remark: {remark.strip()}"

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
                        csu_val, rfd_val = canonicalize_csu_rfd(raw_csu, raw_rfd, remark=remark)
                        csu_reasoning = str(parsed.get("csu_reasoning", "")).strip()
                        rfd_reasoning = str(parsed.get("rfd_reasoning", "")).strip()
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
                            "detailed_rfd": detailed_rfd if detailed_rfd else None,
                            "model": model,
                            "raw_content": content,
                        }
                    else:
                        # Fallback parsing if plain text was returned
                        cleaned_line = content.strip().split("\n")[0].replace('"', "")
                        cand = (
                            f"{eca_header}{cleaned_line}".strip()
                            if eca_header and not cleaned_line.upper().startswith("ECA ")
                            else cleaned_line
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
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the fast/compact English model (nova-2-lite)."""
        return await self.call_llm(
            client=client,
            model=self.model_english,
            remark=remark,
            contact_person=contact_person,
            eca_header=eca_header,
        )

    async def call_multilingual_llm(
        self,
        client: httpx.AsyncClient,
        remark: str,
        contact_person: str = "",
        eca_header: str = "",
    ) -> dict[str, Any]:
        """Invokes the multilingual model for Tagalog/Taglish (qwen3-32b)."""
        return await self.call_llm(
            client=client,
            model=self.model_tagalog,
            remark=remark,
            contact_person=contact_person,
            eca_header=eca_header,
        )

    async def parallel_process_records(
        self,
        records: list[dict[str, Any]],
        force_all: bool = False,
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

        async def _bounded_call(coro: Any) -> Any:
            async with semaphore:
                return await coro

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = []
            task_indices = []

            for idx, (record, lang) in enumerate(zip(records, languages, strict=False)):
                record["detected_language"] = lang
                record["language_route"] = lang  # "ENGLISH" | "TAGALOG"
                remark_text = record.get("cleaned_remark", "") or record.get("raw_remark", "")
                contact_person = record.get("contact_person", "")
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
                        client, remark_text, contact_person=contact_person, eca_header=eca_header
                    )
                else:
                    coro = self.call_small_english_llm(
                        client, remark_text, contact_person=contact_person, eca_header=eca_header
                    )
                tasks.append(_bounded_call(coro))

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
