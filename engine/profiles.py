"""LLM Prompt Profiles & Configuration Management for RCBC Remark Intelligence.

Provides persistent storage, profile switching, custom directives editing,
model selection (English vs Tagalog), and full prompt preview rendering.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

PROFILES_FILE = Path("storage/prompt_profiles.json")

DEFAULT_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

RULE 1: CONTACT ENTITY CLASSIFICATION & BASELINE RFD:
- COMPLETED REPOSSESSION: If remark indicates unit repossessed / surrendered ('Done repo', 'successfully repossessed', 'repo unit') -> RFD MUST BE 'BORROWER REFUSED TO DISCLOSE RFD' and CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'.
- ACTUAL CONTACT - BORROWER: Direct contact with borrower (in person, deep skip, or phone/transfer).
  * If a Promise to Pay (PTP) is arranged ('ptp as per client talk to agent', 'ch is ptp') -> CSU is STRICTLY 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'! NEVER use 'With Commitment to Pay' as a CSU! Baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.
  * If client is met in person (including deep skip to workplace/college) and discusses surrender, settlement, or payment -> CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE' if unit explicitly not at premises), baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.
  * Note on borrower identification: Always check the Contact Person / CH name. If the remark states 'as per ch [Name]' and that name matches the borrower, the person interviewed is the BORROWER himself, NOT an informant!
- ACTUAL CONTACT - FAMILY REPRESENTATIVE:
  * Representatives are STRICTLY family members / relatives (spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, niece, nephew, relative, in-law, brother-in-law).
  * If a family representative is interviewed and gives general info, states client is not around / at work / out of area (or in Dubai, Batangas, Sorsogon, rarely visits), or refuses to give contact info -> baseline RFD is STRICTLY 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * NEVER assign 'NO CLIENT/ REPRESENTATIVE' when a family representative was reached! 'NO CLIENT/ REPRESENTATIVE' is ONLY for informants or unopened doors.
  * WORK RELOCATION vs REP REFUSED: Assign 'WORK RELOCATION' ONLY when there is clear confirmation that the borrower has permanently relocated employment to another province/region (e.g., 'working in Palawan and rarely visits'). General statements of working away (Dubai, Batangas, out of area, arrives 7-8pm) without permanent relocation keep the baseline 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * UNIT SIGHTING WITH REPRESENTATIVE: If the unit is NOT physically seen at the premises (or unit is carnapped, or surrendered/VS to another ECA, or at repair shop), CSU is STRICTLY 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'!
- INFORMANTS (STRICTLY NOT REPRESENTATIVES):
  * Security guards (SG), Barangay Health Workers (BHW), purok leaders, barangay staff, caretakers, maids, helpers, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
  * Note common typos in collector notes: 'niehnor', 'neigbor', 'impormant' = neighbor / informant.
  * An informant's refusal or reluctance to talk is NOT a representative refusal. NEVER assign 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' for an informant!
  * If an informant is interviewed, confirms client lives there (or at work/out of area), or if informant refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * Helper or resident leaving without details ('client left without informing where or when she will return') is an absence, NOT moved out -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * Temporary absence for business trip ('business trip, no contacts') -> CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)', RFD is 'NO CLIENT/ REPRESENTATIVE'.
- DENIED ENTRY / BLOCKED ACCESS / GATED SUBDIVISIONS:
  * If the collector was denied entry, not allowed to enter, blocked by guard ('guard refused to provide info and did not let me proceed', 'admin refused to let me enter', 'refuse to entry need confirmation from client'), or prevented by gate pass/ticket fees, or remark simply notes 'uncooperative' at gate:
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- UNIT-ONLY SCAN / NO CLIENT SEARCH CONDUCTED:
  * If the field note ONLY reports that the unit was not seen in the area ('our unit not seen in the area', 'upon visiting the area our unit is not seen', 'unit is nowhere to be found in the area looked and scanned around for any leads but unit status is negative', 'went around the area but still can't see it') with NO resident or client contact:
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- UNVERIFIED RESIDENCY / UNCONFIRMED HOUSE CLOSED:
  * If the remark states that residency could NOT be confirmed ('no available person to confirm residency', 'client unverified by neighbor', 'address unverified - does not know client', 'client is unknown at brgy', 'possible moved out - name not listed on resident list'):
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- VERIFIED RESIDENCY CLOSED HOUSE / UNANSWERED VISITS (DA PRESUMED RESIDENCY BASELINE):
  * ONLY when the address is verified and residency is confirmed/known (e.g. neighbor confirms client still resides there, or established house closed where client is confirmed to live):
  * CSU MUST BE: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or UNIT POSITIVE ONLY if unit is physically sighted parked at premises).
  * RFD MUST BE: 'NO CLIENT/ REPRESENTATIVE'.

RULE 2: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:
- RANK 1: MOVED OUT (Primary Operational Fact):
  * If informant, neighbor, landlord, new tenant, or family confirms the borrower completely moved out / vacated / left the area / renters don't know client: RFD is STRICTLY 'MOVED OUT' (always UPPERCASE), and CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * 'MOVED OUT' ALWAYS OVERRIDES HARDSHIPS. (e.g. 'moved out due to family issues' or 'tenants ch had stroke moved out' -> RFD is 'MOVED OUT', NOT 'FAMILY PROBLEM' or 'MEDICAL EXPENSE').
- RANK 2: DECEASED BORROWER:
  * Confirmed deceased -> RFD is STRICTLY 'DECEASED BORROWER' (always UPPERCASE), CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 3: EMPTY RFD "" (Strictly Constrained to Unlocated/Unknown/Blocked/Unit-only):
  * Empty RFD "" is ALLOWED ONLY when:
    (a) Address itself is unlocated, incorrect, or client unknown in area ('client unknown at brgy', 'name not listed');
    (b) Access is blocked / denied entry by security guard, gate pass fee, or ticket fee;
    (c) Field note is unit scan only ('our unit not seen in the area');
    (d) Residency could not be verified ('no available person to confirm residency').
  * An empty RFD MUST ALWAYS have CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * If CSU is 'CLIENT POSITIVE...', the RFD must NEVER be empty (use 'NO CLIENT/ REPRESENTATIVE' if no contact).
- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has NOT moved out):
  * An explicit hardship stated by borrower or family rep overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):
    - Flood, typhoon, earthquake, natural disaster -> 'CALAMITY' (CALAMITY overrides 'PENDING INSURANCE CLAIM' when calamity was the root cause!).
    - Carnapped, stolen, scammed -> 'SCAMMED'.
    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
    - Illness, hospitalization, surgery, medical stroke -> 'MEDICAL EXPENSE'.
    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
    - Business slowdown, mahina benta, income drop -> 'BUSINESS SLOWDOWN'.
    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
    - Explicit claim of already settled / payment dispute by client undergoing bank verification -> 'PENDING RECON'.
    - Secondary: BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT, BUSINESS CLOSURE, DEATH-FAMILY MEMBER, DELAYED PENSION, REDUCTION OF SALARY, UNEMPLOYMENT, MIGRATION, WORK RELOCATION, COLLATERAL/DEALER ISSUE (AUTO), FAMILY PROBLEM.
  * ALL RFD VALUES MUST BE OUTPUT IN EXACT STANDARD UPPERCASE FORMAT (e.g. 'DECEASED BORROWER', 'MOVED OUT', NOT 'Deceased Borrower' or 'Moved Out').

RULE 3: CSU MATRIX & SUFFIX FORMATTING RULES:
- VALID CSUS ARE STRICTLY LIMITED TO THE RCBC OFFICIAL SET:
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Unit)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)'
  * 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)'
  * 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT POSITIVE (WITH Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT POSITIVE (WITHOUT Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'
  * 'PENDING RECON'
  * DO NOT output 'With Commitment to Pay', 'Negative Unit', or custom strings as CSU!
- PHYSICAL UNIT SIGHTING REQUIREMENT:
  * 'UNIT POSITIVE' requires the unit to be PHYSICALLY OBSERVED / SIGHTED parked at the premises during the visit.
  * If a neighbor or informant merely mentions that the client uses the unit or parked it last night, but the unit is NOT physically seen at the time of the visit, the unit is 'UNIT NEGATIVE'!
- CARNAPPED / IMPOUNDED / SURRENDERED TO OTHER ECA:
  * When unit is reported carnapped, stolen, impounded, or already surrendered (VS) to another ECA, the unit is NOT at the premises -> CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'!
- ACCOUNT CONFIRMED RESOLVED / SETTLED BY AGENT OR BANK:
  * When remark indicates that the account has been resolved, settled, or cleared according to the agent, collector, or bank:
    - CSU is STRICTLY: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'.
    - RFD: Output 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
- STRICT SUFFIX PROHIBITION:
  * Suffixes '- Both', '- Client', '- Unit' exist ONLY AND EXCLUSIVELY for 'CLIENT POSITIVE/UNIT POSITIVE'.
  * NEVER append '- Both', '- Client', or '- Unit' to 'CLIENT POSITIVE/UNIT NEGATIVE'.
- SPECIALIZED CSU RESTRICTIONS:
  * NEVER use 'For CASE FILING-...' CSUs unless the remark explicitly mentions 'case filing' or 'for filing' by the bank.
  * Use 'PENDING RECON' CSU ONLY when client/borrower explicitly claims account is already settled / paid and needs bank reconciliation.

RULE 4: SUMMARY, REASONING & DETAILED RFD FORMAT:
- 'csu_reasoning': 1-2 concise sentences stating primary reason for the selected CSU and explicitly why alternative candidate CSUs were disqualified.
- 'rfd_reasoning': 1-2 concise sentences stating primary reason for the selected RFD and explicitly why alternative candidate RFDs were disqualified.
- 'summary': Shortened note under 180 characters preserving what was stated, promised, or observed. Do NOT include borrower name, collector name, or prohibited tags (BCAL, BKAL, L3, INB, OBD, phone numbers).
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

FACTORY_DEFAULT_PROFILE: dict[str, Any] = {
    "id": "default",
    "name": "Default RCBC (System)",
    "description": "Standard RCBC Auto Loan operational rules and 2-tier models",
    "model_english": os.getenv("MODEL_ENGLISH", "glm-5").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": DEFAULT_OPERATIONAL_DIRECTIVES,
    "is_default": True,
    "created_at": "2026-09-24T00:00:00Z",
    "updated_at": "2026-09-24T00:00:00Z",
}

FALLBACK_MODELS: list[str] = [
    "minimax-m2.5",
    "claude-haiku-4-5",
    "nova-2-lite",
    "nova-pro",
    "glm-4.7-flash",
    "qwen3-32b",
    "nova-micro",
    "gemma-4-e2b",
    "nemotron-3-nano",
    "glm-5",
]


def load_profiles_data() -> dict[str, Any]:
    """Loads profiles configuration from persistent storage, creating defaults if missing."""
    if not PROFILES_FILE.exists():
        data = {
            "active_profile_id": "default",
            "profiles": [dict(FACTORY_DEFAULT_PROFILE)],
        }
        _write_profiles_file(data)
        return data

    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Profiles file content must be a JSON dictionary")
    except Exception:
        data = {
            "active_profile_id": "default",
            "profiles": [dict(FACTORY_DEFAULT_PROFILE)],
        }
        _write_profiles_file(data)
        return data

    profiles = data.get("profiles", [])
    if not isinstance(profiles, list) or not profiles:
        profiles = [dict(FACTORY_DEFAULT_PROFILE)]
        data["profiles"] = profiles

    # Ensure default profile is always present
    has_default = any(p.get("id") == "default" for p in profiles)
    if not has_default:
        profiles.insert(0, dict(FACTORY_DEFAULT_PROFILE))

    active_id = data.get("active_profile_id")
    if not active_id or not any(p.get("id") == active_id for p in profiles):
        data["active_profile_id"] = "default"

    return data


def _write_profiles_file(data: dict[str, Any]) -> None:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_active_profile() -> dict[str, Any]:
    """Returns the currently active prompt and model profile."""
    data = load_profiles_data()
    active_id = data.get("active_profile_id", "default")
    for p in data.get("profiles", []):
        if p.get("id") == active_id:
            return dict(p)
    return dict(FACTORY_DEFAULT_PROFILE)


def save_profile(profile_data: dict[str, Any], set_active: bool = False) -> dict[str, Any]:
    """Creates or updates a profile in persistent storage."""
    data = load_profiles_data()
    profiles: list[dict[str, Any]] = data.get("profiles", [])
    now_iso = datetime.now(timezone.utc).isoformat()

    pid = str(profile_data.get("id", "")).strip()
    is_new = not pid or pid == "new"

    if is_new:
        pid = f"profile_{uuid4().hex[:8]}"
        is_default = False
    else:
        is_default = pid == "default" or bool(profile_data.get("is_default", False))

    name = str(profile_data.get("name", "")).strip() or ("Default RCBC (System)" if is_default else "Untitled Profile")
    description = str(profile_data.get("description", "")).strip()
    model_english = str(profile_data.get("model_english", "minimax-m2.5")).strip() or "minimax-m2.5"
    model_tagalog = str(profile_data.get("model_tagalog", "minimax-m2.5")).strip() or "minimax-m2.5"
    system_instructions = str(profile_data.get("system_instructions", "")).strip()
    if not system_instructions:
        system_instructions = DEFAULT_OPERATIONAL_DIRECTIVES

    updated_record: dict[str, Any] = {
        "id": pid,
        "name": name,
        "description": description,
        "model_english": model_english,
        "model_tagalog": model_tagalog,
        "system_instructions": system_instructions,
        "is_default": is_default,
        "created_at": profile_data.get("created_at") or now_iso,
        "updated_at": now_iso,
    }

    replaced = False
    for i, existing in enumerate(profiles):
        if existing.get("id") == pid:
            profiles[i] = updated_record
            replaced = True
            break

    if not replaced:
        profiles.append(updated_record)

    data["profiles"] = profiles
    if set_active or is_new or data.get("active_profile_id") == pid:
        data["active_profile_id"] = pid

    _write_profiles_file(data)
    return updated_record


def activate_profile(profile_id: str) -> dict[str, Any]:
    """Sets a given profile ID as active."""
    data = load_profiles_data()
    profiles = data.get("profiles", [])
    target = None
    for p in profiles:
        if p.get("id") == profile_id:
            target = p
            break

    if not target:
        raise ValueError(f"Profile '{profile_id}' not found.")

    data["active_profile_id"] = profile_id
    _write_profiles_file(data)
    return dict(target)


def delete_profile(profile_id: str) -> dict[str, Any]:
    """Deletes a custom profile (cannot delete system default)."""
    if profile_id == "default":
        raise ValueError("Cannot delete the system default profile.")

    data = load_profiles_data()
    profiles = data.get("profiles", [])
    target_idx = None
    for idx, p in enumerate(profiles):
        if p.get("id") == profile_id:
            if p.get("is_default"):
                raise ValueError("Cannot delete a protected default profile.")
            target_idx = idx
            break

    if target_idx is None:
        raise ValueError(f"Profile '{profile_id}' not found.")

    deleted = profiles.pop(target_idx)
    if data.get("active_profile_id") == profile_id:
        data["active_profile_id"] = "default"

    data["profiles"] = profiles
    _write_profiles_file(data)
    return deleted


def reset_profiles_to_default() -> dict[str, Any]:
    """Restores the profiles file to factory default state."""
    data = {
        "active_profile_id": "default",
        "profiles": [dict(FACTORY_DEFAULT_PROFILE)],
    }
    _write_profiles_file(data)
    return data


async def fetch_available_models(
    base_url: str | None = None, api_key: str | None = None
) -> list[str]:
    """Queries LiteLLM proxy for available models with robust fallbacks."""
    url = (base_url or os.getenv("LITELLM_BASE_URL", "https://litellm.spmadridph.com/v1")).rstrip("/")
    key = (api_key or os.getenv("LITELLM_API_KEY", "")).strip()

    if not key or key.startswith("sk-placeholder"):
        return list(FALLBACK_MODELS)

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                if models:
                    # Clean and sort unique models, prioritizing common models
                    unique = sorted(list(set(models)))
                    return unique
    except Exception:
        pass

    return list(FALLBACK_MODELS)


def render_full_prompt(
    instructions: str = "",
    primary_csus: list[str] | None = None,
    secondary_csus: list[str] | None = None,
    primary_rfds: list[str] | None = None,
    secondary_rfds: list[str] | None = None,
    custom_rules: dict[str, list[str]] | None = None,
    max_summary_chars: int = 180,
) -> str:
    """Renders the full system prompt exactly as will be provided to LLM."""
    from engine.summarizer import (
        PRIMARY_CSU_OPTIONS,
        PRIMARY_RFD_OPTIONS,
        SECONDARY_CSU_OPTIONS,
        SECONDARY_RFD_OPTIONS,
        _load_active_custom_rules,
    )

    p_csu = primary_csus if primary_csus is not None else PRIMARY_CSU_OPTIONS
    s_csu = secondary_csus if secondary_csus is not None else SECONDARY_CSU_OPTIONS
    p_rfd = primary_rfds if primary_rfds is not None else PRIMARY_RFD_OPTIONS
    s_rfd = secondary_rfds if secondary_rfds is not None else SECONDARY_RFD_OPTIONS

    primary_csu_formatted = "\n".join(f"  - {c}" for c in p_csu)
    secondary_csu_formatted = "\n".join(f"  - {c}" for c in s_csu)
    primary_rfd_formatted = "\n".join(f"  - {r}" for r in p_rfd)
    secondary_rfd_formatted = "\n".join(f"  - {r}" for r in s_rfd)

    active_instructions = instructions.strip() if instructions and instructions.strip() else DEFAULT_OPERATIONAL_DIRECTIVES

    c_rules = custom_rules if custom_rules is not None else _load_active_custom_rules()
    custom_rules_section = ""
    if c_rules.get("csu_rfd_rules") or c_rules.get("summary_rules"):
        lines = []
        if c_rules.get("csu_rfd_rules"):
            lines.append("Reviewer CSU/RFD Directives:")
            for rule in c_rules["csu_rfd_rules"]:
                lines.append(f"  * {rule}")
        if c_rules.get("summary_rules"):
            lines.append("Reviewer Summary Directives:")
            for rule in c_rules["summary_rules"]:
                lines.append(f"  * {rule}")
        custom_rules_section = f"\n### ACTIVE HUMAN REVIEWER TUNED DIRECTIVES (HIGHEST PRIORITY):\n" + "\n".join(lines) + "\n"

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
        f"{active_instructions}\n"
        f"{custom_rules_section}\n"
        "Return ONLY the raw JSON object. Do not include Markdown code blocks (no ```json), explanations, or preamble."
    )
    return system_prompt
