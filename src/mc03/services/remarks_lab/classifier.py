"""Relation classifier with 3-tier priority, extended Filipino vocabulary, and hard guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Mapping, Any
import re
from mc03.domain.hierarchy import classify_relation_result
from mc03.domain.models import Relation


@dataclass
class ClassifiedRelation:
    relation: Relation
    category_label: str       # "Borrower", "Representative", "Informant", "none reached"
    normalized_role: str      # "Kapamilya", "Neighbor", "Borrower (Self)", "none reached"
    matched_keyword: Optional[str]
    guardrail_applied: bool


# Tier 1: Borrower / Client Direct Contact Patterns & Whitelist
LAB_BORROWER_REMARK_PATTERNS = [
    r"\b(?:client|borrower|cardholder|principal)\s+(?:ptp|promised?|refused?|stated?|claims?|declared?|advised?|declined?|agreed?|requested?|coordinated?|present|took\s+transfer\s+call)\b",
    r"\bspoke\s+(?:with|to)\s+(?:client|borrower|cardholder|principal)\b",
    r"\btalked\s+to\s+(?:client|borrower|cardholder|principal)\b",
    r"\b(?:direct\s+contact\s+with|contact\s+made\s+with|actual\s+contact\s+with)\s+(?:client|borrower|cardholder|principal)\b",
    r"\bper\s+(?:client|borrower|cardholder|principal)(?!['’]s)\b",
    r"\b(?:client|borrower|cardholder)\s+(?:himself|herself)\b",
]

LAB_CARDHOLDER_KEYWORDS = [
    "ch", "cardholder", "borrower", "principal", "client", "customer", "self", "myself", "owner", "client-0-0"
]

# Tier 2: Representative (Blood relative OR ANY in-law)
LAB_REPRESENTATIVE_KEYWORDS = [
    # In-laws (blood relative OR in-law count equally as Representative)
    "mother-in-law", "father-in-law", "brother-in-law", "sister-in-law",
    "son-in-law", "daughter-in-law", "in-law", "in_law", "in law",
    "biyanan", "hipag", "bayaw", "manugang",
    # Core family & relatives
    "spouse", "asawa", "misis", "mister", "wife", "husband",
    "common_law_partner", "common-law partner", "partner",
    "mother", "nanay", "ina", "mom", "mama", "mommy",
    "father", "tatay", "ama", "dad", "papa", "daddy",
    "parent", "parents", "magulang",
    "child", "children", "anak", "daughter", "son",
    "brother", "kapatid", "kuya", "sister", "ate", "sibling", "siblings",
    "aunt", "auntie", "tita", "uncle", "tito",
    "nephew", "niece", "pamangkin",
    "cousin", "pinsan",
    "kapamilya", "relative", "relatives", "kamag-anak", "kamaganak", "kamag anak",
    "authorized representative", "auth rep", "representative", "legal counsel", "attorney"
]

# Tier 3: Informants (apartment/building guard, neighbor, barangay official, colleague, household staff)
# Informants can NEVER be classified as Representative, and can never produce a "Representative Refused to Disclose" RFD.
LAB_INFORMANT_KEYWORDS = [
    # Guards & building staff
    "security guard", "building guard", "apartment guard", "subdivision guard", "gate guard", "lobby guard", "doorman",
    "guard", "guards", "sg", "sekyu", "security", "front desk", "guard_front_desk_staff",
    # Neighbors & local residents
    "neighbor", "neighbors", "kapitbahay", "kapit-bahay", "kapit bahay", "neighborhood", "resident", "residents", "bystander", "bystanders",
    # Barangay officials
    "barangay official", "brgy official", "barangay tanod", "tanod", "kagawad", "barangay kagawad",
    "brgy staff", "barangay staff", "chairperson", "kapitan", "brgy_staff_officer", "barangay hall",
    "purok leader", "purok",
    # Colleagues & workplace
    "colleague", "colleagues", "officemate", "officemates", "coworker", "coworkers", "co-worker", "katrabaho", "workmate", "office staff",
    # Domestic staff & property contacts
    "helper", "helpers", "katulong", "kasambahay", "maid", "housemaid", "driver",
    "caretaker", "caretakers", "landlord", "landlady", "may-ari ng bahay", "property owner", "house owner",
    "tenant", "tenants", "renter", "renters", "staff", "store staff", "hoa", "homeowner", "homeowners",
    "informant", "informants", "third party"
]


def clean_preamble(text: str) -> str:
    """Safely strip agency prefixes without eating substantive remark text."""
    rem_body = re.sub(
        r"^ECA[\s_]+AUTO_[A-Za-z0-9._\s]+?_\d{1,2}/\d{1,2}/\d{2,4}\s*[-:]?\s*",
        "",
        str(text).strip(),
        flags=re.IGNORECASE,
    ).strip()
    return rem_body if rem_body else str(text).strip()


def classify_contact(
    contact_person: str = "",
    contact_relation: str = "",
    statement: str = "",
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> ClassifiedRelation:
    """
    3-tier classifier run on remark text (and supporting metadata), strictly prioritized:
      1. Borrower = client themself was spoken to directly.
      2. Representative = spouse, parent, child, sibling, aunt (tita), uncle (tito),
         nephew/niece, or ANY in-law (blood relative or in-law).
      3. Informant = apartment/building guard, neighbor, barangay official, colleague, staff.
         Informants can NEVER be classified as Representative.
      4. If none detected -> Relation Class = 'none reached' (never 'Unknown').

    Special case: ECA-handled accounts determine who was spoken to from the rest of the remark
    and metadata rather than defaulting to 'no contact reached'.
    """
    c_person = str(contact_person).strip() if contact_person is not None else ""
    c_relation = str(contact_relation).strip() if contact_relation is not None else ""
    raw_stmt = str(statement).strip() if statement is not None else ""

    stmt_clean = clean_preamble(raw_stmt)
    stmt_lower = stmt_clean.lower()

    # Gather auxiliary metadata (e.g. TALK TO, 3RD PARTY LIST)
    meta_parts = [c_relation, c_person]
    if extra_fields:
        for k in ("TALK TO", "3RD PARTY LIST", "contact_relation", "contact_person"):
            val = str(extra_fields.get(k, "")).strip()
            if val and val.lower() not in {"nan", "nat", "none", "<na>"}:
                meta_parts.append(val)
    meta_text = " ".join(meta_parts).strip().lower()

    # ---------------------------------------------------------
    # TIER 1: Borrower (Client directly spoken to)
    # ---------------------------------------------------------
    is_borrower = False
    matched_borrower_kw = None

    # Check direct patterns in remark statement
    for pat in LAB_BORROWER_REMARK_PATTERNS:
        m = re.search(pat, stmt_lower)
        if m:
            is_borrower = True
            matched_borrower_kw = m.group(0)
            break

    # Check metadata indicating direct client contact if remark doesn't say otherwise
    if not is_borrower:
        for kw in LAB_CARDHOLDER_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", meta_text):
                # Verify statement doesn't indicate speaking to a representative or informant instead
                has_rep_kw = any(re.search(r"\b" + re.escape(t) + r"\b", stmt_lower) for t in LAB_REPRESENTATIVE_KEYWORDS[:20])
                if not has_rep_kw:
                    is_borrower = True
                    matched_borrower_kw = kw
                    break

    if is_borrower:
        # Guardrail: check if remark actually said "per client's wife", "client's mother", etc.
        rep_match = re.search(r"\b(?:per\s+client['’]s|client['’]s)\s+([a-z\-]+)\b", stmt_lower)
        if rep_match:
            candidate = rep_match.group(1).lower()
            if any(candidate.startswith(t) or candidate == t for t in LAB_REPRESENTATIVE_KEYWORDS):
                return ClassifiedRelation(
                    relation=Relation.REPRESENTATIVE,
                    category_label="Representative",
                    normalized_role=candidate.title(),
                    matched_keyword=rep_match.group(0),
                    guardrail_applied=False,
                )
        return ClassifiedRelation(
            relation=Relation.UNKNOWN,
            category_label="Borrower",
            normalized_role="Borrower (Self)",
            matched_keyword=matched_borrower_kw,
            guardrail_applied=False,
        )

    # Informants can NEVER be classified as Representative.
    # 1. Metadata check for informant
    for t in LAB_INFORMANT_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", meta_text):
            return ClassifiedRelation(
                relation=Relation.INFORMANT,
                category_label="Informant",
                normalized_role=t.title(),
                matched_keyword=f"meta:{t}",
                guardrail_applied=True,
            )

    # 2. Explicit informant speaker in statement (e.g. "Per neighbor, client's sister...")
    inf_speaker_pat = r"\b(?:per|according to|as per|spoke\s+(?:with|to)|talked\s+to|inquired\s+with)\s+(?:an?\s+|the\s+)?(?:informant|neighbor|kapitbahay|kapit-bahay|guard|security|sekyu|sg|caretaker|tenant|renter|staff|store\s+staff|barangay|tanod|kagawad|colleague|landlord|landlady|hoa)\b"
    inf_match = re.search(inf_speaker_pat, stmt_lower)
    if inf_match:
        return ClassifiedRelation(
            relation=Relation.INFORMANT,
            category_label="Informant",
            normalized_role="Informant",
            matched_keyword=f"speaker:{inf_match.group(0)}",
            guardrail_applied=True,
        )

    # ---------------------------------------------------------
    # TIER 2: Representative (Blood relative OR ANY in-law)
    # ---------------------------------------------------------
    for t in LAB_REPRESENTATIVE_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", stmt_lower):
            return ClassifiedRelation(
                relation=Relation.REPRESENTATIVE,
                category_label="Representative",
                normalized_role=t.title(),
                matched_keyword=f"remark:{t}",
                guardrail_applied=False,
            )

    for t in LAB_REPRESENTATIVE_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", meta_text):
            return ClassifiedRelation(
                relation=Relation.REPRESENTATIVE,
                category_label="Representative",
                normalized_role=t.title(),
                matched_keyword=f"meta:{t}",
                guardrail_applied=False,
            )

    # ---------------------------------------------------------
    # TIER 3: Informant (Guard, neighbor, barangay official, colleague, staff)
    # Informants can NEVER be classified as Representative
    # ---------------------------------------------------------
    for t in LAB_INFORMANT_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", stmt_lower):
            return ClassifiedRelation(
                relation=Relation.INFORMANT,
                category_label="Informant",
                normalized_role=t.title(),
                matched_keyword=f"remark:{t}",
                guardrail_applied=True,
            )

    for t in LAB_INFORMANT_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", meta_text):
            return ClassifiedRelation(
                relation=Relation.INFORMANT,
                category_label="Informant",
                normalized_role=t.title(),
                matched_keyword=f"meta:{t}",
                guardrail_applied=True,
            )

    # Core domain classifier fallback
    core_res = classify_relation_result(c_relation)
    if core_res.relation == Relation.REPRESENTATIVE:
        return ClassifiedRelation(
            relation=Relation.REPRESENTATIVE,
            category_label="Representative",
            normalized_role="Representative",
            matched_keyword=core_res.matched_keyword,
            guardrail_applied=False,
        )
    elif core_res.relation == Relation.INFORMANT:
        return ClassifiedRelation(
            relation=Relation.INFORMANT,
            category_label="Informant",
            normalized_role="Informant",
            matched_keyword=core_res.matched_keyword,
            guardrail_applied=True,
        )

    # ---------------------------------------------------------
    # TIER 4: none reached (Never "Unknown")
    # ---------------------------------------------------------
    return ClassifiedRelation(
        relation=Relation.UNKNOWN,
        category_label="none reached",
        normalized_role="none reached",
        matched_keyword=None,
        guardrail_applied=False,
    )
