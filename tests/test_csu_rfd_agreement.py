"""Regression and agreement tests for CSU and RFD classification.

Ensures LLM decision rules and canonicalization strictly adhere to Data Analyst ground truth:
1. Closed house / padlocked / unanswered visits baseline to CLIENT POSITIVE / NO CLIENT/ REPRESENTATIVE.
2. Informants (guards, BHW, neighbors) are strictly not representatives.
3. Suffixes (- Both, - Client, - Unit) are prohibited on UNIT NEGATIVE.
4. MOVED OUT takes absolute priority over hardships.
5. CALAMITY overrides PENDING INSURANCE CLAIM when calamity was root cause.
6. WORK RELOCATION reverts to REP REFUSED when relative is unaware / refuses to discuss loan.
7. For CASE FILING CSUs are strictly prohibited without explicit mention of case filing.
"""

from engine.summarizer import (
    TwoTierRemarksSummarizer,
    canonicalize_csu_rfd,
    OFFICIAL_RCBC_CSU_OPTIONS,
    OFFICIAL_RCBC_RFD_OPTIONS,
)
from mc03.services.remarks_lab.pipeline import _is_csu_match, _is_rfd_match


def test_canonicalize_strips_illegal_suffixes_from_unit_negative():
    """Verify that - Both, - Client, - Unit suffixes are cleanly stripped from UNIT NEGATIVE CSUs."""
    # Without Contact with - Both
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact - Both)",
        "REPRESENTATIVE REFUSED TO DISCLOSE RFD",
    )
    assert csu == "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
    assert csu in OFFICIAL_RCBC_CSU_OPTIONS

    # With Contact with - Client
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact - Client)",
        "BORROWER REFUSED TO DISCLOSE RFD",
    )
    assert csu == "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
    assert csu in OFFICIAL_RCBC_CSU_OPTIONS

    # With Contact with - Both
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact - Both)",
        "PENDING RECON",
    )
    assert csu == "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"
    assert csu in OFFICIAL_RCBC_CSU_OPTIONS


def test_canonicalize_case_filing_guard():
    """Verify that CASE FILING CSU is prevented unless remark explicitly mentions case filing."""
    # Remark without case filing
    csu, rfd = canonicalize_csu_rfd(
        "For CASE FILING-CLIENT NEGATIVE (VS)",
        "REPRESENTATIVE REFUSED TO DISCLOSE RFD",
        remark="upon visit fs talk to client father and according to him unit is for vs and tp to father undernego",
    )
    assert not csu.startswith("For CASE FILING")
    assert csu == "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"

    # Remark with explicit case filing
    csu_explicit, _ = canonicalize_csu_rfd(
        "For CASE FILING-CLIENT NEGATIVE (BREACHED PTP)",
        "THIRD PARTY USER",
        remark="bank endorsed account for case filing due to breached ptp",
    )
    assert csu_explicit == "For CASE FILING-CLIENT NEGATIVE (BREACHED PTP)"


def test_canonicalize_presumed_residency_on_closed_house():
    """Verify closed houses baseline to CLIENT POSITIVE / NO CLIENT/ REPRESENTATIVE."""
    remark = "HC UPON VISIT FS CALLED SEVERAL TIMES BUT NO ONE ANSWERING. NO UNIT SEEN IN THE AREA."
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)",
        "",
        remark=remark,
    )
    assert csu == "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
    assert rfd == "NO CLIENT/ REPRESENTATIVE"
    assert _is_csu_match(csu, "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)")
    assert _is_rfd_match(rfd, "NO CLIENT/ REPRESENTATIVE")


def test_canonicalize_client_positive_requires_rfd():
    """Verify that CLIENT POSITIVE CSU never has an empty RFD."""
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",
        "",
        remark="neighbor says resident is at work",
    )
    assert rfd == "NO CLIENT/ REPRESENTATIVE"


def test_canonicalize_empty_rfd_for_unlocated_address():
    """Verify empty RFD is preserved when address is unknown/unlocated with NEG/NEG CSU."""
    csu, rfd = canonicalize_csu_rfd(
        "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)",
        "",
        remark="client unknown at brgy and address not found in area",
    )
    assert csu == "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
    assert rfd == ""


def test_prompt_contains_critical_ground_truth_rules():
    """Verify that TwoTierRemarksSummarizer system prompt contains all key operational rules."""
    summarizer = TwoTierRemarksSummarizer(api_key="sk-test")
    messages = summarizer._build_prompt("test remark", contact_person="John Doe")
    content = messages[0]["content"]

    # Rule 1: Presumed residency on closed house
    assert "CLOSED HOUSE / UNANSWERED VISITS (DA PRESUMED RESIDENCY BASELINE)" in content
    assert "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)" in content
    assert "NO CLIENT/ REPRESENTATIVE" in content

    # Rule 2: Informants are strictly not representatives
    assert "INFORMANTS (STRICTLY NOT REPRESENTATIVES)" in content
    assert "Security guards (SG)" in content

    # Rule 3: Moved out priority over hardships
    assert "'MOVED OUT' ALWAYS OVERRIDES HARDSHIPS" in content

    # Rule 4: Calamity overrides insurance claim
    assert "CALAMITY overrides 'PENDING INSURANCE CLAIM'" in content

    # Rule 5: Work relocation nuance
    assert "WORK RELOCATION vs REP REFUSED" in content

    # Rule 6: Prohibited suffixes on UNIT NEGATIVE
    assert "STRICT SUFFIX PROHIBITION" in content

    # Rule 7: Case filing restriction
    assert "NEVER use 'For CASE FILING-...' CSUs unless the remark explicitly mentions 'case filing'" in content

    # Rule 8: Rich reasoning explaining alternatives
    assert "csu_reasoning" in content
    assert "why alternatives were disqualified" in content or "why alternatives were rejected" in content


def test_extract_json_payload_resilience():
    """Verify that _extract_json_payload parses markdown code blocks, dirty quotes, and trailing commas."""
    from engine.summarizer import _extract_json_payload

    # 1. Clean markdown code fence
    raw1 = '```json\n{\n  "summary": "House closed.",\n  "csu": "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",\n  "rfd": "NO CLIENT/ REPRESENTATIVE"\n}\n```'
    parsed1 = _extract_json_payload(raw1)
    assert parsed1 is not None
    assert parsed1["summary"] == "House closed."
    assert "CLIENT POSITIVE" in parsed1["csu"]

    # 2. Markdown fence with unescaped internal quotes
    raw2 = '```json\n{\n  "summary": "Talked to neighbor.",\n  "csu": "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",\n  "csu_reasoning": "Spoke with "neighbor" who confirmed residency",\n  "rfd": "NO CLIENT/ REPRESENTATIVE"\n}\n```'
    parsed2 = _extract_json_payload(raw2)
    assert parsed2 is not None
    assert parsed2["summary"] == "Talked to neighbor."

    # 3. Trailing comma in JSON
    raw3 = '```json\n{\n  "summary": "House closed.",\n  "csu": "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",\n  "rfd": "NO CLIENT/ REPRESENTATIVE",\n}\n```'
    parsed3 = _extract_json_payload(raw3)
    assert parsed3 is not None
    assert parsed3["summary"] == "House closed."

    # 4. Thinking tags before markdown block
    raw4 = '<think>Analyzing remark...</think>\n```json\n{\n  "summary": "House closed.",\n  "csu": "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",\n  "rfd": "NO CLIENT/ REPRESENTATIVE"\n}\n```'
    parsed4 = _extract_json_payload(raw4)
    assert parsed4 is not None
    assert parsed4["summary"] == "House closed."

    # 5. Unclosed markdown fence
    raw5 = '```json\n{\n  "summary": "House closed.",\n  "csu": "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)",\n  "rfd": "NO CLIENT/ REPRESENTATIVE"\n'
    parsed5 = _extract_json_payload(raw5)
    assert parsed5 is not None
    assert parsed5["summary"] == "House closed."

