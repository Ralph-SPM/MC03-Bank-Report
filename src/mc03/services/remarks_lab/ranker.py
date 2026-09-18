"""RFD/CSU Hierarchy ranker and mapping engine complying with RCBC taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Tuple
import re
from mc03.domain.remarks import _parse_pipe_tags


@dataclass
class RankedResult:
    rfd_type: str
    rfd_code: str
    detailed_rfd: str
    csu_status: str
    rule_source: str     # "PipeTags", "RCBC_Taxonomy", "RemarkKeywords"


# Standard RCBC CSU Status Categories
CSU_REPO = "REPO"
CSU_PTP = "PTP"
CSU_RESOLVED = "RESOLVED"
CSU_NO_CONTACT = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
CSU_CLIENT_POSITIVE = "CLIENT POSITIVE/UNIT [POS/NEG] (WITH Actual Contact - Client)"

# Standard RCBC RFD Categories
RFD_BORROWER_REFUSED = "BORROWER REFUSED TO DISCLOSE RFD"
RFD_REPRESENTATIVE_REFUSED = "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
RFD_NO_REACH = "NO CLIENT / REPRESENTATIVE REACHED"
RFD_MOVED_OUT = "Moved Out"

# Explicitly stated hardships in priority order:
EXPLICIT_RFD_HIERARCHY: List[Tuple[str, List[str]]] = [
    (
        "Medical Expense",
        [
            r"\b(?:medical|hospital|hospitalized|hospitalization|medication|medicine|gamot|pagpapagamot|sakit|karamdaman|surgery|operation|confined|confinement|dialysis|chemo|chemotherapy|treatment|stroke|illness|dengue|covid)\b"
        ],
    ),
    (
        "Diversion of Funds",
        [
            r"\b(?:diversion(?:\s+of\s+funds)?|diverted(?:\s+funds)?|ginamit\s+sa\s+iba|emergency\s+expense|school\s+fees|tuition(?:\s+fee)?|renovation|family\s+emergency|funeral|burial|death\s+in\s+family|namatayan)\b"
        ],
    ),
    (
        "Delayed Salary",
        [
            r"\b(?:delayed?\s+salary|delay\s+salary|late\s+salary|pending\s+salary|delayed?\s+sweldo|delay\s+sweldo|sweldo|antala\s+sweldo|delayed?\s+payroll|payroll\s+delay|no\s+salary\s+yet|waiting\s+for\s+salary|sahod)\b"
        ],
    ),
    (
        "Delayed Remittance",
        [
            r"\b(?:delayed?\s+remittance|pending\s+remittance|remittance|padala|ofw(?:\s+remittance|\s+funds)?|remit)\b"
        ],
    ),
    (
        "Delayed Collection",
        [
            r"\b(?:delayed?\s+collection|delay\s+collection|pending\s+collection|singilin|delayed?\s+receivables?|receivables?|delayed?\s+payment\s+from\s+client|client\s+collection|waiting\s+for\s+collection|collectibles?)\b"
        ],
    ),
    (
        "Business Slowdown",
        [
            r"\b(?:business\s+slowdown|slowdown|mahina\s+ang\s+negosyo|hina\s+ng\s+kita|mahina\s+kita|business\s+loss|lugi|bankruptcy|closure\s+of\s+business|closed\s+business|slump|low\s+sales|hina\s+ng\s+benta|pandemic\s+loss)\b"
        ],
    ),
    (
        "Calamity",
        [
            r"\b(?:calamity|flooded|flood|baha|bagyo|typhoon|earthquake|lindol)\b"
        ],
    ),
    (
        "Third-Party User",
        [
            r"\b(?:third[- ]party(?:\s+user)?|3rd\s+party|ibang\s+gumagamit|ipinagamit|pasalo|nagpasalo|assumed?\s+balance|sold\s+unit|binenta|buyer|unauthorized\s+user|unit\s+with\s+third\s+party)\b"
        ],
    ),
    (
        "Scammed",
        [
            r"\b(?:scam|scammed|carnapped)\b"
        ],
    ),
    (
        "Deceased Borrower",
        [
            r"\b(?:deceased|passed\s+away|died|namatay)\b"
        ],
    ),
    (
        "LTO Apprehension/No ORCR/HPG",
        [
            r"\b(?:impounded|lto(?:\s+apprehension)?|hpg|orcr|no\s+orcr|replevin)\b"
        ],
    ),
    (
        "Work Relocation",
        [
            r"\b(?:working\s+in|work\s+relocation|job\s+relocation|relocated\s+for\s+work)\b"
        ],
    ),
    (
        "Pending Recon",
        [
            r"\b(?:pending\s+recon|claiming\s+paid|claimed\s+paid|already\s+settled\s+long\s+ago)\b"
        ],
    ),
]


def classify_rfd(remark: str, category_label: str = "") -> str:
    """
    Determine Reason for Default (RFD) strictly by DA priority hierarchy:
      1. Explicitly stated hardship.
      2. Confirmed Moved Out (relocation confirms borrower no longer resides).
      3. Borrower refused to disclose (direct borrower contact).
      4. Representative refused to disclose (relative / in-law contact).
      5. No client / representative reached (informant / house closed with address verified).
      6. Blank / unlocated.
    """
    text = str(remark).lower()

    # 1. Explicitly stated RFD hardships
    for rfd_name, patterns in EXPLICIT_RFD_HIERARCHY:
        for pat in patterns:
            if re.search(pat, text):
                return rfd_name

    # 2. Confirmed Moved Out (takes precedence when borrower has vacated the residence)
    moved_out_pat = r"\b(?:moved\s+out|lipat|lumipat|relocated|no\s+longer\s+resides?|hasn[\'’]t\s+lived|has\s+not\s+been\s+returning|left\s+(?:the\s+)?area|former\s+renter|former\s+tenant|renters?\s+who\s+don[\'’]t\s+know|no\s+longer\s+at\s+the\s+house)\b"
    if re.search(moved_out_pat, text):
        return RFD_MOVED_OUT

    # 3. Borrower refused to disclose (if direct borrower contact)
    if category_label in ("Borrower", "Cardholder"):
        return RFD_BORROWER_REFUSED

    # 4. Representative refused to disclose (if family member / in-law spoke)
    # (Informants can NEVER produce Representative Refused to Disclose)
    if category_label == "Representative":
        return RFD_REPRESENTATIVE_REFUSED

    # Fallback checks on remark text when category_label is not specified / none reached
    if re.search(
        r"\b(?:client\s+refused|borrower\s+refused|spoke\s+(?:with|to)\s+(?:client|borrower))\b",
        text,
    ):
        return RFD_BORROWER_REFUSED

    if re.search(
        r"\b(?:representative\s+refused|spouse\s+refused|wife\s+refused|husband\s+refused)\b",
        text,
    ):
        return RFD_REPRESENTATIVE_REFUSED

    # 5. Informant or house closed
    return RFD_NO_REACH


def classify_csu(remark: str, category_label: str = "") -> str:
    """
    CSU status hierarchy conforming to RCBC standardized bank text:
      Explicit resolution/repo > client positive + unit positive > client positive + unit negative > client negative.
      Excludes internal 'Repo AI' references from false repo classifications.
    """
    text = str(remark).lower()

    # 1. Explicit resolution / repo language (excluding 'Repo AI' internal app references)
    if re.search(r"\b(?:repossessed?|repossession|surrender(?:ed)?|pullout|pull-out|pull\s+out|pulled\s+out)\b|\brepo\b(?![\s_-]*ai)", text):
        return CSU_REPO

    if re.search(
        r"\b(?:already\s+resolved|account\s+resolved|resolved|paid\s+off|fully\s+paid|settled\s+already|cleared\s+account|cleared)\b",
        text,
    ):
        return CSU_RESOLVED

    if re.search(
        r"\b(?:ptp|promise\s+to\s+pay|promised\s+to\s+pay|will\s+pay|will\s+settle|settle\s+balance|settle\s+account)\b",
        text,
    ):
        return CSU_PTP

    if re.search(r"\b(?:pending\s+recon|claiming\s+paid|claimed\s+paid)\b", text):
        return "PENDING RECON"

    # 2. If client confirmed moved out -> negative residence
    moved_out_pat = r"\b(?:moved\s+out|lipat|lumipat|relocated|no\s+longer\s+resides?|hasn[\'’]t\s+lived|has\s+not\s+been\s+returning|left\s+(?:the\s+)?area|former\s+renter|former\s+tenant)\b"
    if re.search(moved_out_pat, text):
        return CSU_NO_CONTACT

    # 3. Client Positive Determination
    is_client_pos = False
    if category_label in ("Borrower", "Representative", "Cardholder"):
        is_client_pos = True
    elif re.search(
        r"\b(?:positive\s+address|address\s+verified|verified\s+residing|still\s+residing|resides\s+there|client\s+is\s+at\s+work|at\s+work|out\s+for\s+work|temporarily\s+absent|out\s+of\s+area|returns\s+home|seen\s+in\s+area|confirmed\s+client['’]s\s+house|verified\s+per|client\s+on\s+business\s+trip|location\s+where\s+client\s+resides\s+was\s+verified|property\s+confirmed\s+owned|client\s+still\s+residing|house\s+closed[;,]?\s*verified)\b",
        text,
    ):
        # Exclude confirmed negative residence
        if not re.search(
            r"\b(?:moved\s+out|unknown|not\s+known|cannot\s+locate|unlocated|no\s+longer\s+resides|hasn['’]t\s+lived)\b",
            text,
        ):
            is_client_pos = True

    # 4. Unit Positive Determination (verified against remark text)
    is_unit_pos = bool(
        re.search(
            r"\b(?:unit\s+seen|unit\s+positive|parked\s+at\s+given\s+address|seen\s+during\s+visitation|spotted\s+unit|unit\s+parked)\b",
            text,
        )
    )

    # 5. Standardized Bank Text Assignment
    if is_client_pos:
        has_actual_contact = category_label in ("Borrower", "Representative", "Cardholder")
        if is_unit_pos:
            if has_actual_contact:
                suffix = "WITH Actual Contact - Client" if category_label in ("Borrower", "Cardholder") else "WITH Actual Contact - Both"
                return f"CLIENT POSITIVE/UNIT POSITIVE ({suffix})"
            else:
                return "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact)"
        else:
            if has_actual_contact:
                return "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
            else:
                return "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
    else:
        return CSU_NO_CONTACT


def build_detailed_rfd(
    rfd_clause: str,
    category_label: str,
    normalized_role: str,
    remark_text: str,
) -> str:
    """
    Assembles DETAILED RFD as a fixed 3-clause string strictly in this order:
      [RFD clause]; [Contact clause]; [Core statement/promise]
    """
    # Clause 1: RFD clause
    c1 = rfd_clause.strip() if rfd_clause else "Reason not specified"

    # Clause 2: Contact clause
    if category_label in ("Borrower", "Cardholder"):
        c2 = "Contact made with borrower directly"
    elif category_label == "Representative":
        role_label = (
            f" ({normalized_role})"
            if normalized_role and normalized_role.lower() not in {"representative", "none reached"}
            else ""
        )
        c2 = f"Contact made with representative{role_label}"
    elif category_label == "Informant":
        role_label = (
            f" ({normalized_role})"
            if normalized_role and normalized_role.lower() not in {"informant", "none reached"}
            else ""
        )
        c2 = f"Inquired with informant{role_label}"
    else:
        c2 = "No contact made"

    # Clause 3: Core statement / promise
    text = str(remark_text).lower()
    ptp_match = re.search(r"\b(?:ptp|promised?\s+to\s+pay|will\s+pay|will\s+settle|promise)[^.;\n]*", text)
    if ptp_match:
        c3 = ptp_match.group(0).strip().capitalize()
    elif "already resolved" in text or "fully paid" in text or "account resolved" in text:
        c3 = "Account already resolved per collector/agent"
    elif "repossessed" in text or "surrender" in text or "pullout" in text:
        c3 = "Unit repossessed or surrendered"
    elif "moved out" in text or "lipat" in text or "relocated" in text:
        c3 = "Confirmed moved out to unknown location"
    elif "positive address" in text or "address verified" in text or "still residing" in text:
        c3 = "Address verified; client not around during visit"
    elif "unknown" in text or "cannot locate" in text or "unlocated" in text:
        c3 = "Client unknown in area / unable to locate"
    else:
        if category_label in ("Borrower", "Representative", "Cardholder"):
            c3 = "No specific payment terms disclosed"
        else:
            c3 = "No contact made at given address"

    return f"{c1}; {c2}; {c3}"


def map_csu_status(
    category_label: str,
    rfd_code: str,
    raw_statement: str = "",
) -> str:
    """Backward-compatible wrapper for CSU mapping."""
    combined = f"{rfd_code} {raw_statement}"
    return classify_csu(combined, category_label)


def rank_rfd_and_csu(
    raw_remarks: str,
    cleaned_remarks: str,
    category_label: str,
    normalized_role: str = "",
) -> RankedResult:
    """
    Deterministic RCBC classification for remarks:
    Derives standard CSU, RFD, and the 3-clause DETAILED RFD.
    """
    raw_str = str(raw_remarks) if raw_remarks is not None else ""
    cleaned_str = str(cleaned_remarks) if cleaned_remarks is not None else ""
    cat_label = str(category_label) if category_label is not None else ""
    role_str = str(normalized_role) if normalized_role is not None else ""

    text_to_analyze = f"{cleaned_str} {raw_str}".strip()

    # Check for pipe tags if present
    pipe_res = _parse_pipe_tags(raw_str)
    rule_source = "RCBC_Taxonomy"
    if pipe_res and pipe_res.rfd:
        rule_source = "PipeTags"

    csu_status = classify_csu(text_to_analyze, cat_label)
    rfd_code = classify_rfd(text_to_analyze, cat_label)
    detailed_rfd = build_detailed_rfd(rfd_code, cat_label, role_str, text_to_analyze)

    return RankedResult(
        rfd_type=rfd_code,
        rfd_code=rfd_code,
        detailed_rfd=detailed_rfd,
        csu_status=csu_status,
        rule_source=rule_source,
    )
