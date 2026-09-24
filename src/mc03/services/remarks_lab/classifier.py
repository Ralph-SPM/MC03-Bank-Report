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
    r"\b(?:client|borrower|cardholder|principal|ch)\s+(?:ptp|promised?|refused?|stated?|claims?|declared?|advised?|declined?|agreed?|requested?|coordinated?|present|took\s+transfer\s+call)\b",
    r"\bspoke\s+(?:with|to)\s+(?:client|borrower|cardholder|principal|ch)\b",
    r"\btalked\s+to\s+(?:client|borrower|cardholder|principal|ch)\b",
    r"\b(?:direct\s+contact\s+with|contact\s+made\s+with|actual\s+contact\s+with)\s+(?:client|borrower|cardholder|principal|ch)\b",
    r"\bper\s+(?:client|borrower|cardholder|principal|ch)(?!['’]s)\b",
    r"\b(?:client|borrower|cardholder|ch)\s+(?:himself|herself)\b",
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


NO_CONTACT_PATTERNS = [
    r"\b(?:house\s+(?:is\s+)?closed|gate\s+(?:is\s+)?locked|unoccupied|vacant(?:\s+lot)?|empty\s+lot|nobody\s+around|no\s+one\s+around|no\s+person\s+around|unlocated|uncontacted|not\s+around|did\s+not\s+answer|refused\s+to\s+open)\b"
]


def clean_preamble(text: str) -> str:
    """Safely strip agency prefixes without eating substantive remark text."""
    from mc03.services.remarks_lab.trimmer import ECA_HEADER_PATTERN
    rem_text = str(text).strip() if text is not None else ""
    m = ECA_HEADER_PATTERN.match(rem_text)
    if m:
        rem_text = rem_text[len(m.group(1)):].strip()
    return rem_text


def _detect_remark_relation(stmt_lower: str) -> Optional[tuple[str, str, str]]:
    """
    Extract relation signal from substantive remark text.
    Returns (category_label, normalized_role, keyword) or None.
    """
    if not stmt_lower or not stmt_lower.strip():
        return None

    # 1. Direct borrower contact
    for pat in LAB_BORROWER_REMARK_PATTERNS:
        m = re.search(pat, stmt_lower)
        if m:
            # Guardrail: check if remark actually said "per client's wife", "as per ch wife", "ch father", etc.
            rel_m = re.search(r"\b(?:per\s+|as\s+per\s+)?(?:client|borrower|cardholder|principal|ch)(?:['’]s|\s+)\s*([a-z\-]+)\b", stmt_lower)
            if rel_m:
                cand = rel_m.group(1).lower()
                for t in LAB_REPRESENTATIVE_KEYWORDS:
                    if cand == t or cand.startswith(t):
                        return ("Representative", t.title(), rel_m.group(0))
                for t in LAB_INFORMANT_KEYWORDS:
                    if cand == t or cand.startswith(t):
                        return ("Informant", t.title(), rel_m.group(0))
            return ("Borrower", "Borrower (Self)", m.group(0))

    # 2. Informant explicit speaker pattern (e.g. "Per neighbor, client's sister...")
    inf_speaker_pat = (
        r"\b(?:per|according to|as per|spoke\s+(?:with|to)|talked\s+to|inquired\s+with|interviewed|talk\s+to)\s+"
        r"(?:an?\s+|the\s+)?([a-z\s\-]+?)\b"
    )
    for m in re.finditer(inf_speaker_pat, stmt_lower):
        cand = m.group(1).strip().lower()
        for t in LAB_INFORMANT_KEYWORDS:
            if t == cand or cand.startswith(t) or cand.endswith(t):
                return ("Informant", t.title(), t)

    # 3. Informant keywords in remark
    for t in LAB_INFORMANT_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", stmt_lower):
            return ("Informant", t.title(), t)

    # 4. Representative keywords in remark
    for t in LAB_REPRESENTATIVE_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", stmt_lower):
            return ("Representative", t.title(), t)

    # 5. Explicit no-contact / unreached cues in remark
    for pat in NO_CONTACT_PATTERNS:
        if re.search(pat, stmt_lower):
            return ("none reached", "none reached", "no contact reached")

    return None


def _detect_meta_relation(meta_text: str, c_relation: str) -> Optional[tuple[str, str, str]]:
    """
    Extract relation signal from workbook metadata (contact relation, contact person, TALK TO, 3RD PARTY).
    Returns (category_label, normalized_role, keyword) or None.
    """
    if not meta_text or not meta_text.strip():
        return None

    # Check informant in metadata
    for t in LAB_INFORMANT_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", meta_text):
            return ("Informant", t.title(), t)

    # Check representative in metadata
    for t in LAB_REPRESENTATIVE_KEYWORDS:
        if re.search(r"\b" + re.escape(t) + r"\b", meta_text):
            return ("Representative", t.title(), t)

    # Check cardholder / borrower in metadata
    for kw in LAB_CARDHOLDER_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", meta_text):
            return ("Borrower", "Borrower (Self)", kw)

    # Domain classifier fallback on c_relation
    if c_relation:
        core_res = classify_relation_result(c_relation)
        if core_res.relation == Relation.REPRESENTATIVE:
            return ("Representative", "Representative", core_res.matched_keyword or c_relation)
        elif core_res.relation == Relation.INFORMANT:
            return ("Informant", "Informant", core_res.matched_keyword or c_relation)

    return None


def classify_contact(
    contact_person: str = "",
    contact_relation: str = "",
    statement: Optional[str] = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> ClassifiedRelation:
    """
    Classifies relation based entirely on the final remark text.
    The remark text is the SOLE source of truth for who was contacted:
      - If remark text contains a person/relation cue: classify and cross-check with metadata.
      - If remark text has NO person cue or is unreached: returns category 'none', role 'none'.
      - Metadata is only used as a fallback if statement was explicitly omitted (statement is None).
    """
    c_person = str(contact_person).strip() if contact_person is not None else ""
    c_relation = str(contact_relation).strip() if contact_relation is not None else ""

    # Gather auxiliary metadata (e.g. TALK TO, 3RD PARTY LIST)
    meta_parts = [c_relation, c_person]
    if extra_fields:
        for k in ("TALK TO", "3RD PARTY LIST", "contact_relation", "contact_person"):
            val = str(extra_fields.get(k, "")).strip()
            if val and val.lower() not in {"nan", "nat", "none", "<na>"}:
                meta_parts.append(val)
    meta_text = " ".join(meta_parts).strip().lower()

    # If statement argument was explicitly provided, remark is the sole source of truth
    if statement is not None:
        raw_stmt = str(statement).strip()
        stmt_clean = clean_preamble(raw_stmt)
        stmt_lower = stmt_clean.lower()
        remark_sig = _detect_remark_relation(stmt_lower)
        meta_sig = _detect_meta_relation(meta_text, c_relation)

        if remark_sig is not None:
            rem_cat, rem_role, rem_kw = remark_sig
            if rem_cat == "none reached":
                category_label = "none"
                normalized_role = "none"
                matched_keyword = None
            elif meta_sig is not None:
                meta_cat, meta_role, meta_kw = meta_sig
                # Check agreement
                is_agree = (
                    rem_cat == meta_cat
                    and (
                        rem_cat == "Borrower"
                        or rem_role.lower() == meta_role.lower()
                        or rem_kw.lower() in meta_kw.lower()
                        or meta_kw.lower() in rem_kw.lower()
                    )
                )
                if is_agree:
                    category_label = rem_cat
                    normalized_role = rem_role
                    matched_keyword = f"remark:{rem_kw}; also listed in contact field"
                else:
                    # Conflict: Remark text is the primary source of truth and wins
                    category_label = rem_cat
                    normalized_role = rem_role
                    matched_keyword = f"remark:{rem_kw}; contact field listed ({meta_kw})"
            else:
                category_label = rem_cat
                normalized_role = rem_role
                matched_keyword = f"remark:{rem_kw}"
        else:
            # Remark doesn't mention anyone -> do not infer from metadata, just say 'none'
            category_label = "none"
            normalized_role = "none"
            matched_keyword = None
    else:
        # statement was omitted (None): standalone metadata evaluation fallback
        meta_sig = _detect_meta_relation(meta_text, c_relation)
        if meta_sig is not None:
            meta_cat, meta_role, meta_kw = meta_sig
            category_label = meta_cat
            normalized_role = meta_role
            matched_keyword = f"meta:{meta_kw}"
        else:
            category_label = "none"
            normalized_role = "none"
            matched_keyword = None

    if category_label == "Representative":
        rel = Relation.REPRESENTATIVE
        guardrail = False
    elif category_label == "Informant":
        rel = Relation.INFORMANT
        guardrail = True
    else:
        rel = Relation.UNKNOWN
        guardrail = False

    return ClassifiedRelation(
        relation=rel,
        category_label=category_label,
        normalized_role=normalized_role,
        matched_keyword=matched_keyword,
        guardrail_applied=guardrail,
    )
