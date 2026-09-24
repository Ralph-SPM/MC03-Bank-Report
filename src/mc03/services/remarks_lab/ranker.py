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
    csu_reasoning: str = ""
    rfd_reasoning: str = ""


# Standard RCBC CSU Status Options (from Bank Data Validation Dropdown)
CSU_CP_UP_WITHOUT_CONTACT_CLIENT = "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)"
CSU_CP_UP_WITHOUT_CONTACT_UNIT   = "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Unit)"
CSU_CP_UP_WITHOUT_CONTACT_BOTH   = "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)"
CSU_CP_UP_WITH_CONTACT_BOTH      = "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)"
CSU_CP_UN_WITHOUT_CONTACT        = "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
CSU_CN_UP_WITHOUT_CONTACT        = "CLIENT NEGATIVE/UNIT POSITIVE (WITHOUT Actual Contact)"
CSU_CP_UN_WITH_CONTACT           = "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
CSU_CN_UP_WITH_CONTACT           = "CLIENT NEGATIVE/UNIT POSITIVE (WITH Actual Contact)"
CSU_NO_CONTACT                   = "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"

# Backwards-compatibility aliases
CSU_REPO = CSU_CP_UP_WITH_CONTACT_BOTH
CSU_PTP = CSU_CP_UP_WITH_CONTACT_BOTH
CSU_RESOLVED = CSU_CP_UP_WITHOUT_CONTACT_CLIENT
CSU_CLIENT_POSITIVE = CSU_CP_UN_WITH_CONTACT

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
            r"\b(?:third[- ]party(?:\s+user)?|3rd\s+party(?!\s+informant)|ibang\s+gumagamit|ipinagamit|pasalo|nagpasalo|assumed?\s+balance|assumer|sold\s+unit|binenta|buyer|unauthorized\s+user|unit\s+with\s+third\s+party)\b"
        ],
    ),
    (
        "Scammed",
        [
            r"\b(?:scam|scammed|carnap|carnapped)\b"
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
        "Business Closure",
        [
            r"\b(?:business\s+closure|closed\s+down|closure\s+of\s+business|nagsara\s+ang\s+negosyo|closed\s+business)\b"
        ],
    ),
    (
        "Death-Family Member",
        [
            r"\b(?:death\s+in\s+family|death[- ]family\s+member|namatayan\s+ng\s+(?:kapamilya|magulang|anak|asawa)|deceased\s+(?:relative|family|parent|spouse|child))\b"
        ],
    ),
    (
        "Delayed Pension",
        [
            r"\b(?:delayed?\s+pension|pending\s+pension|pension|sss\s+pension|gsis\s+pension)\b"
        ],
    ),
    (
        "Reduction of Salary",
        [
            r"\b(?:reduction\s+of\s+salary|salary\s+reduction|cut\s+salary|salary\s+cut|bawas\s+sahod|salary\s+deduction)\b"
        ],
    ),
    (
        "Unemployment",
        [
            r"\b(?:unemployed|lost\s+job|laid\s+off|resigned|no\s+job|walang\s+trabaho|jobless|retrenched)\b"
        ],
    ),
    (
        "Migration",
        [
            r"\b(?:migrated|migration|abroad|overseas|migrating|relocating\s+abroad)\b"
        ],
    ),
    (
        "Collateral/Dealer Issue (Auto)",
        [
            r"\b(?:dealer\s+issue|collateral\s+issue|lemon|casa|warranty\s+issue|defective\s+unit|engine\s+issue|unit\s+problem|reklamasyon\s+sa\s+casa)\b"
        ],
    ),
    (
        "Pending Insurance Claim",
        [
            r"\b(?:insurance\s+claim|pending\s+insurance|total\s+loss\s+claim|insurance\s+payout)\b"
        ],
    ),
    (
        "Bank Account On-Hold/Under Garnishment",
        [
            r"\b(?:garnish(?:ed|ment)?|bank\s+account\s+(?:on[- ]?hold|frozen|lock(?:ed)?)|frozen\s+account)\b"
        ],
    ),
    (
        "Family Problem",
        [
            r"\b(?:family\s+problem|broken\s+family|hiwalay\s+sa\s+asawa|marital\s+problem|family\s+dispute)\b"
        ],
    ),
    (
        "Pending Recon",
        [
            r"\b(?:pending\s+recon|already\s+settled|paid\s+in\s+full|cleared\s+account|payment\s+dispute)\b"
        ],
    ),
]


def classify_rfd(remark: str, category_label: str = "") -> str:
    """
    Determine Reason for Default (RFD) strictly by DA priority hierarchy:
      1. Explicitly stated hardship.
      2. Confirmed Moved Out (relocation confirms borrower no longer resides).
      3. Deceased Borrower.
      4. Direct Borrower contact -> Borrower refused to disclose.
      5. Direct Representative contact -> Representative refused to disclose.
      6. Unit-only search / Repo skip -> Empty string ("").
      7. Guard refused entry with no resident confirmation -> Empty string ("").
      8. House closed / gate padlocked / at work / out of area -> No client / representative reached.
      9. Blank / unlocated / unknown in area.
    """
    text = str(remark).lower()

    # 1. Repossession Completed (Done repo / successfully repossessed)
    if re.search(r"\b(?:repossessed?|repossession|done\s+repo|successfully\s+repossessed|repo\s+unit)\b", text):
        return RFD_BORROWER_REFUSED

    # 2. Explicitly stated RFD hardships
    for rfd_name, patterns in EXPLICIT_RFD_HIERARCHY:
        for pat in patterns:
            if re.search(pat, text):
                return rfd_name

    # Representative contact detection: someone actually spoke with / received info from a family member
    rep_contact_pat = (
        r"\b(?:talk(?:ed)?\s+(?:to\s+)?(?:client\s+|ch\s+|his\s+|her\s+)?|"
        r"spoke\s+(?:with\s+)?|"
        r"as\s*per\s+(?:client\s+|ch\s+|his\s+|her\s+)?|"
        r"according\s+to\s+(?:client\s+|ch\s+|his\s+|her\s+)?)"
        r"(?:wife|husband|spouse|mother|father|sister|brother|kapatid|niece|nephew|cousin|uncle|aunt|in-law|inlaw|daughter|son|children|sibling)\b"
    )
    is_rep_contact = category_label == "Representative" or bool(re.search(rep_contact_pat, text))
    # Exclude if primary interviewee was an informant
    if is_rep_contact and re.search(r"\b(?:talk\s+to\s+(?:neighbor|caretaker|care\s*taker)|as\s*per\s+(?:staff|guard|neighbor|nieghbor))\b", text):
        is_rep_contact = False

    # 3. Confirmed Moved Out (takes precedence when borrower household vacated residence, or when confirmed by representative/informant)
    moved_out_pat = r"\b(?:moved\s*out|move\s*out|lipat|lumipat|relocated|no\s+longer\s+(?:resides?|living|at\s+the\s+house)|hasn[\'’]t\s+lived|has\s+not\s+been\s+returning|left\s+(?:the\s+)?area|former\s+renter|former\s+tenant|renters?\s+who\s+don[\'’]t\s+know|separated\s+to\s+her\s+husband|staying\s+in\s+[a-z]+)\b"
    if re.search(moved_out_pat, text):
        return RFD_MOVED_OUT

    # 4. Deceased Borrower
    if re.search(r"\b(?:deceased|passed\s*away|died|namatay)\b", text):
        return "Deceased Borrower"

    # 5. Direct entity contact
    if category_label in ("Borrower", "Cardholder"):
        return RFD_BORROWER_REFUSED

    if is_rep_contact:
        if re.search(r"\b(?:work\s+relocat|working\s+in\s+[a-z]+|work(?:ing)?\s+abroad|in\s+dubai)\b", text):
            return "WORK RELOCATION"
        return RFD_REPRESENTATIVE_REFUSED

    # 6. Closed house / padlocked with NO interview / no interaction
    has_informant_interview = bool(re.search(r"\b(?:neighbor|nieghbor|guard|informant|barangay|brgy|tenant|caretaker|maid|staff|care\s*taker|sg)\b", text))
    is_client_away = bool(re.search(r"\b(?:client\s+(?:is\s+)?not\s+around|not\s+around|at\s+work|is\s+a\s+work|out\s+(?:for|to)\s+work|still\s+residing|resides\s+there|out\s+of\s+area|ootc|out\s+of\s+town)\b", text))
    if not has_informant_interview and not is_rep_contact and not is_client_away and category_label not in ("Borrower", "Cardholder"):
        if re.search(r"\b(?:house\s+close[d]?|gate\s+(?:closed|padlocked|locked)|nobody\s+home|no\s+one\s+(?:is\s+)?around|no\s+one\s+answering|padlocked|nakapadlock|naka-padlock)\b", text):
            return ""

    # 8. Unit-only search / Repo skip (no resident interviewed) -> RFD is blank ("")
    unit_only_pat = r"^(?:our\s+)?unit\s+not\s+seen|^(?:our\s+)?unut\s+negative|^negative\s+unit|^unit\s+is\s+nowhere|^negative\s+area|^upon\s+visit(?:ation)?\s+(?:our\s+)?unit\s+not\s+seen"
    if re.search(unit_only_pat, text.strip()):
        return ""

    # 9. Guard refused entry with no resident verification -> RFD is blank ("")
    if re.search(r"\b(?:not\s+allowed\s+to\s+enter|refuse\s+to\s+entry|refused\s+to\s+let\s+me\s+enter|guard\s+refused)\b", text) and not re.search(r"\b(?:confirm|residing)\b", text):
        return ""

    # 10. Zero information gathered / client unknown / informant doesn't know -> RFD left empty ("")
    unknown_client_pat = (
        r"\b(?:"
        r"client\s+(?:is\s+)?unknown|"
        r"ch\s+(?:is\s+)?unknown|"
        r"unknown\s+client|"
        r"unkwon|"
        r"unknown\s+in\s+(?:the\s+)?area|"
        r"not\s+known\s+in\s+(?:the\s+)?area|"
        r"not\s+known|"
        r"no\s+such\s+person|"
        r"don[\'’]t\s+know\s+(?:the\s+)?client|doesn[\'’]t\s+know\s+(?:the\s+)?client|does\s+not\s+know\s+(?:the\s+)?client|do\s+not\s+know\s+(?:the\s+)?client|"
        r"don[\'’]t\s+know\s+(?:the\s+)?borrower|doesn[\'’]t\s+know\s+(?:the\s+)?borrower|"
        r"don[\'’]t\s+know\s+location|doesn[\'’]t\s+know\s+location|"
        r"hindi\s+kilala|hindi\s+alam|"
        r"cannot\s+locate|incorrect\s+address|incomplete\s+ad[dr]|unlocated|not\s+familiar|"
        r"dont\s+know\s+or\s+recognize|no\s+one\s+recognized|"
        r"can[\'’]t\s+verify|"
        r"guard\s+can[\'’]t\s+verify"
        r")\b"
    )
    if re.search(unknown_client_pat, text):
        return ""

    # 11. Informant absence report (neighbor, guard, maid, caretaker confirmed client still resides / at work / out of area / house closed)
    resides_not_around_pat = r"\b(?:gate\s+(?:closed|padlocked|locked)|house\s+close[d]?|hc|padlocked|naka-padlock|nakapadlock|walang\s+tao|not\s+around|out\s+for\s+work|at\s+work|is\s+a\s+work|temporarily\s+absent|out\s+of\s+area|ootc|out\s+of\s+town|still\s+residing|confirm|agent|resolved|neighbor|nieghbor|brgy|barangay|guard|maid|caretaker|staff|tenant|as\s*per\s+informant)\b"
    if re.search(resides_not_around_pat, text):
        return RFD_NO_REACH

    # 12. Fallback text checks when category_label is not specified
    if re.search(
        r"\b(?:talk\s+to\s+client|spoke\s+with\s+borrower|speak\s+to\s+client|met\s+the\s+client|client\s+is\s+present|client\s+is\s+pos|ch\s+is\s+ptp|ptp\s+as\s+per\s+client|client\s+refused|borrower\s+refused|spoke\s+(?:with|to)\s+(?:client|borrower))\b",
        text,
    ):
        return RFD_BORROWER_REFUSED

    return ""


def classify_csu(remark: str, category_label: str = "") -> str:
    """
    CSU status classification choosing strictly between the standardized RCBC bank dropdown categories:
      - CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)
      - CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)
      - CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Unit)
      - CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)
      - CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)
      - CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)
      - CLIENT NEGATIVE/UNIT POSITIVE (WITH Actual Contact)
      - CLIENT NEGATIVE/UNIT POSITIVE (WITHOUT Actual Contact)
      - CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)
    """
    text = str(remark).lower()
    has_actual_contact = category_label in ("Borrower", "Cardholder") or bool(
        re.search(r"\b(?:talk\s+to\s+client|spoke\s+with\s+borrower|speak\s+to\s+client|met\s+the\s+client|client\s+is\s+present|ch\s+is\s+ptp|ptp\s+as\s+per\s+client)\b", text)
    )

    # 1. Repossession Completed
    if re.search(r"\b(?:repossessed?|repossession|done\s+repo|successfully\s+repossessed)\b", text):
        return CSU_CP_UP_WITH_CONTACT_BOTH

    # 2. Unit-only repo search (no resident interviewed) -> Neg/Neg
    unit_only_pat = r"^(?:our\s+)?unit\s+not\s+seen|^(?:our\s+)?unut\s+negative|^negative\s+unit|^unit\s+is\s+nowhere|^negative\s+area|^upon\s+visit(?:ation)?\s+(?:our\s+)?unit\s+not\s+seen"
    if re.search(unit_only_pat, text.strip()):
        return CSU_NO_CONTACT

    # 3. Guard refused entry with no confirmation -> Neg/Neg
    if re.search(r"\b(?:not\s+allowed\s+to\s+enter|refuse\s+to\s+entry|refused\s+to\s+let\s+me\s+enter|guard\s+refused)\b", text) and not re.search(r"\b(?:confirm|residing)\b", text):
        return CSU_NO_CONTACT

    # 4. Closed house with NO interaction / no other info gathered and no neighbor confirmation -> Neg/Neg
    uncontacted_closed_pat = (
        r"^(?:house\s+close[d]?\s+address\s+verified|"
        r"address\s+verified\s+house\s+close[d]?|"
        r"house\s+close[d]?\s*(?:only|verefied|verified)?\.?\s*(?:unit\s+not\s+seen)?\s*(?:and\s+)?no\s+(?:other\s+)?info(?:rmation)?\s*(?:was\s+)?gathered)"
    )
    if re.search(uncontacted_closed_pat, text.strip()) and not re.search(r"\b(?:neighbor|nieghbor|guard|informant|barangay|brgy|tenant|caretaker|maid|staff)\b", text):
        return CSU_NO_CONTACT

    if re.search(r"\b(?:no\s+other\s+info(?:rmation)?\s+(?:was\s+)?gather|nobody\s+home|no\s+one\s+is\s+around)\b", text) and not re.search(r"\b(?:still\s+residing|resides\s+there|at\s+work|out\s+of\s+area|ootc)\b", text):
        if not has_actual_contact and category_label != "Representative" and not re.search(r"\b(?:wife|husband|spouse|mother|father|sister|brother|kapatid|niece|nephew|cousin|uncle|aunt|in-law|inlaw|daughter|son|children|sibling)\b", text):
            return CSU_NO_CONTACT

    # Representative contact check
    rep_contact_pat = (
        r"\b(?:talk(?:ed)?\s+(?:to\s+)?(?:client\s+|ch\s+|his\s+|her\s+)?|"
        r"spoke\s+(?:with\s+)?|"
        r"as\s*per\s+(?:client\s+|ch\s+|his\s+|her\s+)?|"
        r"according\s+to\s+(?:client\s+|ch\s+|his\s+|her\s+)?)"
        r"(?:wife|husband|spouse|mother|father|sister|brother|kapatid|niece|nephew|cousin|uncle|aunt|in-law|inlaw|daughter|son|children|sibling)\b"
    )
    is_rep_contact = category_label == "Representative" or bool(re.search(rep_contact_pat, text))
    if is_rep_contact and re.search(r"\b(?:talk\s+to\s+(?:neighbor|caretaker|care\s*taker)|as\s*per\s+(?:staff|guard|neighbor|nieghbor))\b", text):
        is_rep_contact = False

    # 5. Negative address / moved out / unknown client / unlocated / deceased
    moved_out_pat = r"\b(?:moved\s*out|move\s*out|lipat|lumipat|relocated|no\s+longer\s+(?:resides?|living|at\s+the\s+house)|hasn[\'’]t\s+lived|has\s+not\s+been\s+returning|left\s+(?:the\s+)?area|former\s+renter|former\s+tenant|renters?\s+who\s+don[\'’]t\s+know|separated\s+to\s+her\s+husband|unknown\s+in\s+area|client\s+(?:is\s+)?unknown|ch\s+(?:is\s+)?unknown|unknown\s+client|unkwon|not\s+known|unlocated|cannot\s+locate|incomplete\s+ad[dr]|incorrect\s+address|deceased|passed\s*away|dont\s+know\s+the\s+client|no\s+one\s+recognized|unverified|can[\'’]t\s+verify|unable\s+to\s+verify)\b"
    if re.search(moved_out_pat, text):
        return CSU_NO_CONTACT

    # 5b. Account Confirmed Resolved / Settled per Agent or Bank
    resolved_pat = (
        r"\b(?:according\s+to\s+(?:the\s+)?agent\s+(?:the\s+)?account\s+(?:has\s+been|is|was)?\s*resolved|"
        r"account\s+(?:has\s+been|is|was|already)\s*resolved|"
        r"resolved\s+per\s+agent|"
        r"confirmed\s+resolved|"
        r"agent\s+(?:confirms?|stated?|says?)\s+(?:the\s+)?account\s+(?:is|was|has\s+been)\s*resolved)\b"
    )
    if re.search(resolved_pat, text):
        return CSU_RESOLVED

    if re.search(r"\b(?:pending\s+recon|already\s+settled)\b", text):
        return "PENDING RECON"

    # 6. Client Positive Determination (Borrower, representative, or verified residence / positive address)
    is_client_pos = False
    if has_actual_contact or category_label == "Representative" or is_rep_contact:
        is_client_pos = True
    elif re.search(
        r"\b(?:positive\s+address|pos\s+add|pos\.add|address\s+verified|verified\s+residing|still\s+residing|resides\s+there|client\s+is\s+at\s+work|at\s+work|is\s+a\s+work|out\s+for\s+work|temporarily\s+absent|out\s+of\s+area|ootc|out\s+of\s+town|returns\s+home|seen\s+in\s+area|confirmed\s+client['’]s\s+house|verified\s+per|client\s+on\s+business\s+trip|location\s+where\s+client\s+resides\s+was\s+verified|property\s+confirmed\s+owned|client\s+still\s+residing|pos(?:\.|\b)|confirm(?:ed|ing)?|resolved|paid\s+off|fully\s+paid|cleared)\b",
        text,
    ):
        is_client_pos = True

    # 7. Unit Determination (Strictly requires vehicle explicitly seen/parked/surrendered)
    unit_neg_pat = r"\b(?:unit\s+negative|unit\s+not\s+seen|our\s+unit\s+not\s+seen|flooded|impounded|assumer|not\s+in\s+premises|unit\s+in\s+[a-z]+|pasalo|unit\s+transferred)\b"
    is_unit_neg = bool(re.search(unit_neg_pat, text))

    unit_pos_pat = r"\b(?:unit\s+seen|unit\s+positive|parked|spotted\s+unit|unit\s+parked|repossessed?|repossession|surrender(?:ed)?|pullout|pulled\s+out|ptp|will\s+pay|promise\s+to\s+pay)\b"
    is_unit_pos = bool(re.search(unit_pos_pat, text)) and not is_unit_neg

    # 8. Standardized Category Selection from the Bank Dropdown
    if not is_client_pos and not is_unit_pos:
        return CSU_NO_CONTACT

    if is_client_pos and is_unit_pos:
        if has_actual_contact:
            return CSU_CP_UP_WITH_CONTACT_BOTH
        else:
            return CSU_CP_UP_WITHOUT_CONTACT_CLIENT

    if is_client_pos and not is_unit_pos:
        if has_actual_contact:
            return CSU_CP_UN_WITH_CONTACT
        else:
            return CSU_CP_UN_WITHOUT_CONTACT

    if not is_client_pos and is_unit_pos:
        if has_actual_contact:
            return CSU_CN_UP_WITH_CONTACT
        else:
            return CSU_CN_UP_WITHOUT_CONTACT

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
    if rfd_clause:
        c1 = rfd_clause.strip()
    elif category_label == "Informant" or not category_label or category_label.lower() in {"unknown", "none", "none reached"}:
        c1 = "NO CLIENT / REPRESENTATIVE REACHED"
    else:
        c1 = "Reason not specified"

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


def explain_csu(csu_status: str, category_label: str = "", text: str = "") -> str:
    """Generates concise decision logic explanation for a CSU status."""
    csu_upper = (csu_status or "").upper()
    text_lower = (text or "").lower()

    if "resolved" in text_lower and ("agent" in text_lower or "bank" in text_lower):
        return "Agent confirmed account has been resolved/settled; positive unit and client status without direct borrower contact."
    if "PENDING RECON" in csu_upper:
        return "Remark mentions claimed payment, settlement dispute, or pending reconciliation."
    if "WITH ACTUAL CONTACT - BOTH" in csu_upper:
        return "Direct borrower contact verified and unit sighted or payment promised."
    if "WITHOUT ACTUAL CONTACT - CLIENT" in csu_upper:
        return f"Contact made with {category_label or 'representative'} (not borrower); unit confirmed."
    if "WITHOUT ACTUAL CONTACT - UNIT" in csu_upper:
        return "Unit located in premises, but borrower was not reached."
    if "WITHOUT ACTUAL CONTACT - BOTH" in csu_upper:
        return "Informant confirmed residence and unit sighted parked in premises."
    if "CLIENT POSITIVE/UNIT NEGATIVE (WITH ACTUAL CONTACT)" in csu_upper:
        return "Direct contact with borrower, but vehicle was not seen / impounded."
    if "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT ACTUAL CONTACT)" in csu_upper:
        return f"Residence verified via {category_label or 'representative/informant'} (or house closed / at work), but unit was not seen."
    if "CLIENT NEGATIVE/UNIT POSITIVE" in csu_upper:
        return "Borrower uncontacted or relocated, but unit was located on premises."
    if "CLIENT NEGATIVE/UNIT NEGATIVE" in csu_upper:
        return "Client moved out, unknown in area, unlocated address, or unit-only scan with no contact."
    if "PAID-OFF/CLOSED" in csu_upper:
        return "Account confirmed fully paid, settled, or closed."
    return f"Selected based on contact entity ({category_label or 'None'}) and unit presence indicators."


def explain_rfd(rfd_code: str, category_label: str = "", text: str = "") -> str:
    """Generates concise decision logic explanation for an RFD code."""
    rfd_clean = (rfd_code or "").strip()
    rfd_upper = rfd_clean.upper()

    if not rfd_clean:
        return "Zero information gathered or borrower unknown in area (RFD left empty per guidelines)."
    if "BORROWER REFUSED" in rfd_upper:
        return "Direct contact with borrower who did not disclose an explicit hardship."
    if "REPRESENTATIVE REFUSED" in rfd_upper:
        return f"Contact with {category_label or 'representative'} who did not disclose an explicit hardship."
    if "NO CLIENT" in rfd_upper:
        return "Borrower resides at address but was temporarily away / house was closed during visit."
    if rfd_upper == "MOVED OUT":
        return "Explicit confirmation from informant or representative that borrower relocated."
    if "LTO APPREHENSION" in rfd_upper:
        return "Vehicle impounded, apprehended by LTO/HPG, or lacking OR/CR documents."
    return f"Explicit hardship '{rfd_code}' identified in remark narrative."


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

    csu_reason = explain_csu(csu_status, cat_label, text_to_analyze)
    rfd_reason = explain_rfd(rfd_code, cat_label, text_to_analyze)

    return RankedResult(
        rfd_type=rfd_code,
        rfd_code=rfd_code,
        detailed_rfd=detailed_rfd,
        csu_status=csu_status,
        rule_source=rule_source,
        csu_reasoning=csu_reason,
        rfd_reasoning=rfd_reason,
    )
