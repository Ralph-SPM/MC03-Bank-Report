"""Unit tests for Remarks Intelligence Lab & Benchmark View."""

from __future__ import annotations

import io
import pandas as pd
import pytest
from starlette.testclient import TestClient

from mc03.services.remarks_lab.parser import parse_field_result_sheet, RawRemarkRow
from mc03.services.remarks_lab.cleaner import clean_remark
from mc03.services.remarks_lab.classifier import classify_contact
from mc03.services.remarks_lab.ranker import rank_rfd_and_csu
from mc03.services.remarks_lab.trimmer import trim_and_format
from mc03.services.remarks_lab.pipeline import RemarksLabPipeline
from mc03.services.web import create_demo_app


def test_cleaner_strips_prohibited_tags_and_phones():
    text = "Spoke to neighbor BCAL L3 OBD 09171234567 visit result ch moved to prov"
    res = clean_remark(text)
    assert res.is_modified
    assert "BCAL" in res.detected_prohibited_tags
    assert "L3" in res.detected_prohibited_tags
    assert "OBD" in res.detected_prohibited_tags
    assert "PHONE" in res.detected_prohibited_tags
    assert "09171234567" not in res.cleaned
    assert "BCAL" not in res.cleaned


def test_classifier_extended_filipino_and_guardrail():
    # 1. Representative Tagalog
    rep1 = classify_contact("Maria Santos", "kapatid")
    assert rep1.category_label == "Representative"
    assert rep1.normalized_role.lower() == "kapatid"

    rep2 = classify_contact("Juan Dela Cruz", "nanay")
    assert rep2.category_label == "Representative"

    # 2. Informant Tagalog
    inf1 = classify_contact("Kuya Guard", "sekyu")
    assert inf1.category_label == "Informant"
    assert inf1.guardrail_applied

    inf2 = classify_contact("Pedro", "kapitbahay")
    assert inf2.category_label == "Informant"
    assert inf2.guardrail_applied

    # 3. Cardholder / Borrower
    ch = classify_contact("Self", "Borrower")
    assert ch.category_label in ("Borrower", "Cardholder")


def test_ranker_guardrail_informant_never_representative_refused():
    rank_rep = rank_rfd_and_csu("borrower refused to talk", "borrower refused to talk", "Representative")
    assert rank_rep.rfd_code == "REPRESENTATIVE REFUSED TO DISCLOSE RFD"

    rank_inf = rank_rfd_and_csu("neighbor said ch moved out", "neighbor said ch moved out", "Informant")
    assert rank_inf.rfd_code != "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
    assert rank_inf.rfd_code == "Moved Out"


def test_trimmer_preamble_and_length():
    long_statement = "FIELD VISIT RESULT: " + ("The borrower promises to settle balance next week upon release of harvest funds. " * 5)
    trimmed = trim_and_format(long_statement)
    assert trimmed.preamble_stripped
    assert trimmed.trimmed_length <= 200
    assert trimmed.fits_within_200


def test_remarks_lab_pipeline_synthetic_excel():
    # Create synthetic DataFrame
    data = {
        "Account Number": ["1001", "1002", "1003"],
        "CH Code": ["CH01", "CH02", "CH03"],
        "Contact Person": ["Juana Santos", "Brgy Tanod Pedro", "Cardholder"],
        "Contact Relation": ["Asawa", "Barangay Tanod", "Self"],
        "Remarks": [
            "BCAL PTP on Friday 09181234567",
            "OBD CH moved to Cavite per tanod",
            "CH refused to pay"
        ],
        "CSU": ["Representative PTP", "Moved Out / Relocated", "Borrower Refused"],
        "RFD": ["PTP", "Moved", "Refused to pay"]
    }
    df = pd.DataFrame(data)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    buffer.seek(0)

    pipeline = RemarksLabPipeline()
    report = pipeline.process_file(buffer)

    assert report.total_rows == 3
    assert report.sanitized_tag_count >= 2
    assert report.representative_count == 1
    assert report.informant_count == 1
    assert report.cardholder_count == 1
    assert len(report.rows) == 3


def test_remarks_lab_endpoints():
    app = create_demo_app()
    client = TestClient(app)

    # GET /remarks-lab
    get_res = client.get("/remarks-lab")
    assert get_res.status_code == 200
    assert "Remark Intelligence Lab" in get_res.text
    assert "Upload FIELD RSULT Workbook" in get_res.text

    # POST /remarks-lab/analyze with synthetic workbook
    df = pd.DataFrame({
        "Account Number": ["9999"],
        "Contact Person": ["Maria"],
        "Contact Relation": ["Ate"],
        "Remarks": ["L3 PTP next Tuesday"]
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    data = buf.getvalue()

    post_res = client.post(
        "/remarks-lab/analyze",
        files={"workbook": ("test.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    )
    assert post_res.status_code == 200
    assert "Rows Analyzed" in post_res.text
    assert "9999" in post_res.text
    assert "Representative" in post_res.text


def test_remarks_lab_template_shaped_workbook_parsing():
    """Verify parsing workbooks with metadata headers like RCBC AL_NEW-CSR_TEMPLATE."""
    buf = io.BytesIO()
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "FIELD RSULT"
    ws["A1"] = "Output_Mapping_Regions=A2:H10"
    headers = ["CH code", "Account Number", "Relation", "CSU", "RFD", "Remark", "Length", "Disposition"]
    for col, val in enumerate(headers, start=1):
        ws.cell(2, col, val)

    ws.append(["CH-999", "ACC-999", "parent", "3", "Medical", "This is a long remark over 200 chars. " * 8, 280, "Clean"])
    wb.save(buf)
    data = buf.getvalue()

    pipeline = RemarksLabPipeline()
    report = pipeline.process_file(io.BytesIO(data))
    assert report.total_rows == 1
    assert report.char_overflow_prevented_count == 1
    assert report.rows[0].account_number == "ACC-999"
    assert report.rows[0].truncated is True


def test_remarks_lab_date_filtering():
    from datetime import date
    df = pd.DataFrame({
        "Visit Date": ["2026-08-15", "2026-09-02", "2026-09-03", "2026-09-10"],
        "Account Number": ["ACC-1", "ACC-2", "ACC-3", "ACC-4"],
        "Contact Person": ["Maria", "Pedro", "Juan", "Ana"],
        "Contact Relation": ["Ate", "Kuya", "Tatay", "Nanay"],
        "Remarks": [
            "PTP on Aug 20",
            "PTP on Sep 5",
            "Left notice with neighbor",
            "CH not at residence"
        ]
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    data = buf.getvalue()

    # 1. Pipeline direct test
    pipeline = RemarksLabPipeline()
    report = pipeline.process_file(
        io.BytesIO(data),
        date_from=date(2026, 9, 2),
        date_to=date(2026, 9, 3)
    )
    assert report.total_rows == 2
    assert report.total_unfiltered_rows == 4
    assert [r.account_number for r in report.rows] == ["ACC-2", "ACC-3"]
    assert report.rows[0].row_date == "2026-09-02"
    assert report.rows[1].row_date == "2026-09-03"

    # 2. HTTP POST endpoint test with date_from and date_to
    app = create_demo_app()
    client = TestClient(app)
    post_res = client.post(
        "/remarks-lab/analyze",
        data={"date_from": "2026-09-02", "date_to": "2026-09-03"},
        files={"workbook": ("csr_template.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    )
    assert post_res.status_code == 200
    assert "Date Window Filter" in post_res.text
    assert "2026-09-02" in post_res.text
    assert "2026-09-03" in post_res.text
    assert "isolated from" in post_res.text
    assert "ACC-2" in post_res.text
    assert "ACC-3" in post_res.text
    assert "ACC-1" not in post_res.text
    assert "ACC-4" not in post_res.text


def test_rcbc_csu_taxonomy_and_rfd_hierarchy():
    from engine.classifier import classify_csu, classify_rfd, rank_rfd_and_csu

    # 1. Repossession -> REPO
    repo_res = rank_rfd_and_csu("Unit successfully repossessed Sept 1, 2026", "Unit successfully repossessed Sept 1, 2026", "Unknown")
    assert repo_res.csu_status == "REPO"
    assert classify_csu("Voluntary surrender of mortgaged unit") == "REPO"
    assert classify_csu("Pullout scheduled by recovery officer") == "REPO"

    # 2. Payment Promise -> PTP CSU and BORROWER REFUSED TO DISCLOSE RFD
    ptp_res = rank_rfd_and_csu("Client PTP on Sept 4 for 15,000", "Client PTP on Sept 4 for 15,000", "Cardholder")
    assert ptp_res.csu_status == "PTP"
    assert ptp_res.rfd_code == "BORROWER REFUSED TO DISCLOSE RFD"

    # 3. Account Resolved -> RESOLVED
    res_res = rank_rfd_and_csu("Neg; per agent, account already resolved", "Neg; per agent, account already resolved", "Unknown")
    assert res_res.csu_status == "RESOLVED"
    assert classify_csu("Account fully paid per bank receipts") == "RESOLVED"

    # 4. No Contact / House Closed -> CLIENT NEGATIVE/UNIT NEGATIVE and NO CLIENT / REPRESENTATIVE REACHED
    nc_res = rank_rfd_and_csu("House closed gate padlocked nobody home", "House closed gate padlocked nobody home", "Unknown")
    assert nc_res.csu_status == "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
    assert nc_res.rfd_code == "NO CLIENT / REPRESENTATIVE REACHED"

    # 5. Explicit Hardships
    med_res = rank_rfd_and_csu("Borrower admitted in hospital due to medical surgery", "Borrower admitted in hospital due to medical surgery", "Cardholder")
    assert med_res.rfd_code == "Medical Expense"

    sal_res = rank_rfd_and_csu("Delayed salary from employer, promised to coordinate next week", "Delayed salary from employer, promised to coordinate next week", "Cardholder")
    assert sal_res.rfd_code == "Delayed Salary"

    bus_res = rank_rfd_and_csu("Business slowdown in store, low sales this quarter", "Business slowdown in store, low sales this quarter", "Cardholder")
    assert bus_res.rfd_code == "Business Slowdown"

    third_res = rank_rfd_and_csu("Car was transferred to third-party user under pasalo arrangement", "Car was transferred to third-party user under pasalo arrangement", "Representative")
    assert third_res.rfd_code == "Third-Party User"

    # 6. Representative contact with no hardship
    rep_res = rank_rfd_and_csu("Spoke with mother, refused to provide update", "Spoke with mother, refused to provide update", "Representative")
    assert rep_res.rfd_code == "REPRESENTATIVE REFUSED TO DISCLOSE RFD"

    # 7. Informant with moved out
    inf_res = rank_rfd_and_csu("Neighbor said cardholder moved out to Cavite", "Neighbor said cardholder moved out to Cavite", "Informant")
    assert inf_res.rfd_code == "Moved Out"

    # Never output FIELD VISIT, PTP, or blank as RFD
    for rfd in [repo_res.rfd_code, ptp_res.rfd_code, res_res.rfd_code, nc_res.rfd_code]:
        assert rfd not in ["FIELD VISIT", "PTP", ""]


def test_agency_preamble_removal_and_deduplication():
    from engine.trimmer import trim_and_format, strip_preamble, deduplicate_phrases

    # 1. Internal preamble removal
    sample_with_header = "FIELD VISIT RESULT: Unit successfully repossessed Sept 1, 2026"
    stripped, was_stripped = strip_preamble(sample_with_header)
    assert was_stripped is True
    assert "FIELD VISIT RESULT" not in stripped
    assert "Unit successfully repossessed" in stripped

    # 2. Phrase deduplication
    repeated_text = "Neg; per agent, account already resolved. Neg; per agent, account already resolved. Neg; per agent, account already resolved."
    deduped = deduplicate_phrases(repeated_text)
    assert deduped.count("Neg; per agent, account already resolved") == 1

    # 3. Combined trimmer test (ECA header preserved)
    combined = "ECA AUTO_S.P. MADRID_HOME_09/02/2026 - Neg; per agent, account already resolved. Neg; per agent, account already resolved."
    res = trim_and_format(combined)
    assert res.trimmed_length <= 200
    assert "ECA AUTO_S.P. MADRID_HOME_09/02/2026" in res.trimmed_text
    assert res.trimmed_text.count("account already resolved") == 1


def test_rcbc_226_rows_benchmark_accuracy():
    """Verify CSU match > 80% and accurate RFD mapping on representative 226 records."""
    rows_data = []
    # 50 PTP rows
    for i in range(50):
        rows_data.append({
            "Account Number": f"ACC-{i}",
            "Relation": "Borrower",
            "Remarks": f"ECA AUTO_S.P. MADRID_HOME_09/02/2026 - Spoke with client, PTP on Sept {i%20 + 1}",
            "CSU": "PTP",
            "RFD": "BORROWER REFUSED TO DISCLOSE RFD",
        })
    # 30 REPO rows
    for i in range(50, 80):
        rows_data.append({
            "Account Number": f"ACC-{i}",
            "Relation": "Self",
            "Remarks": "Unit successfully repossessed Sept 1, 2026 by recovery team",
            "CSU": "REPO",
            "RFD": "BORROWER REFUSED TO DISCLOSE RFD",
        })
    # 20 RESOLVED rows
    for i in range(80, 100):
        rows_data.append({
            "Account Number": f"ACC-{i}",
            "Relation": "",
            "Remarks": "Neg; per agent, account already resolved. Neg; per agent, account already resolved.",
            "CSU": "RESOLVED",
            "RFD": "NO CLIENT / REPRESENTATIVE REACHED",
        })
    # 100 House closed / No contact rows
    for i in range(100, 200):
        rows_data.append({
            "Account Number": f"ACC-{i}",
            "Relation": "Neighbor",
            "Remarks": "House closed gate locked, neighbor said not around",
            "CSU": "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)",
            "RFD": "NO CLIENT / REPRESENTATIVE REACHED",
        })
    # 26 Explicit hardship rows
    hardships = [
        ("medical surgery in hospital", "Medical Expense"),
        ("delayed salary from employer", "Delayed Salary"),
        ("business slowdown low revenue", "Business Slowdown"),
        ("diversion of funds emergency", "Diversion of Funds"),
        ("delayed collection from contractor", "Delayed Collection"),
        ("delayed remittance from OFW brother", "Delayed Remittance"),
        ("third-party user pasalo arrangement", "Third-Party User"),
    ]
    for i in range(200, 226):
        phrase, exp_rfd = hardships[i % len(hardships)]
        rows_data.append({
            "Account Number": f"ACC-{i}",
            "Relation": "Client",
            "Remarks": f"Borrower explained default due to {phrase}, PTP next month",
            "CSU": "PTP",
            "RFD": exp_rfd,
        })

    df = pd.DataFrame(rows_data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    buf.seek(0)

    pipeline = RemarksLabPipeline()
    report = pipeline.process_file(buf)

    assert report.total_rows == 226
    assert report.csu_accuracy_pct > 80.0
    assert report.rfd_accuracy_pct > 80.0
    # Specific row checks
    repo_row = next(r for r in report.rows if "repossessed" in r.raw_remarks)
    assert repo_row.predicted_csu == "REPO"
    assert repo_row.csu_match is True

    ptp_row = next(r for r in report.rows if "PTP on Sept" in r.raw_remarks)
    assert ptp_row.predicted_csu == "PTP"
    assert ptp_row.predicted_rfd == "BORROWER REFUSED TO DISCLOSE RFD"
    assert ptp_row.csu_match is True
    assert ptp_row.rfd_match is True


def test_bug1_3tier_relation_classifier_and_eca():
    # 1. Borrower (Self / direct conversation)
    b1 = classify_contact("", "", "Client PTP to settle account on Friday")
    assert b1.category_label == "Borrower"
    assert b1.normalized_role == "Borrower (Self)"

    b2 = classify_contact("", "", "Spoke with borrower directly at residence")
    assert b2.category_label == "Borrower"

    # 2. Representative (In-law, blood relative)
    r1 = classify_contact("", "", "Spoke with client's sister-in-law regarding car loan")
    assert r1.category_label == "Representative"

    r2 = classify_contact("", "", "Per tita, client is out of the country")
    assert r2.category_label == "Representative"

    # 3. Informant (Guard, neighbor, tanod, colleague)
    i1 = classify_contact("", "", "Per building guard, client moved out last year")
    assert i1.category_label == "Informant"
    assert i1.guardrail_applied is True

    i2 = classify_contact("Kuya Guard", "sekyu", "Unit parked at garage")
    assert i2.category_label == "Informant"
    assert i2.category_label != "Representative"

    # 4. None reached (Never "Unknown")
    n1 = classify_contact("", "", "Gate closed, nobody answered intercom")
    assert n1.category_label == "none reached"
    assert n1.normalized_role == "none reached"

    # 5. ECA Special Case: determine spoken party from rest of remark & metadata
    eca_rep = classify_contact(
        "", "",
        "ECA AUTO_S.P. MADRID_HOME_09/02/2026 Per agent, account already resolved.",
        extra_fields={"3RD PARTY LIST": "mother"}
    )
    assert eca_rep.category_label == "Representative"


def test_bug2_detailed_rfd_3clause_and_hierarchies():
    from engine.classifier import rank_rfd_and_csu, classify_rfd, classify_csu

    # 1. DETAILED RFD 3-Clause Structure: [RFD clause]; [Contact clause]; [Core statement/promise]
    res_b = rank_rfd_and_csu("Client promised to pay 10k on Oct 15", "Client promised to pay 10k on Oct 15", "Borrower", "Borrower (Self)")
    parts_b = [p.strip() for p in res_b.detailed_rfd.split(";")]
    assert len(parts_b) == 3
    assert parts_b[0] == "BORROWER REFUSED TO DISCLOSE RFD"
    assert parts_b[1] == "Contact made with borrower directly"
    assert "Promised to pay" in parts_b[2]

    res_rep = rank_rfd_and_csu("Spoke with wife, account already resolved", "Spoke with wife, account already resolved", "Representative", "Wife")
    parts_rep = [p.strip() for p in res_rep.detailed_rfd.split(";")]
    assert len(parts_rep) == 3
    assert parts_rep[0] == "REPRESENTATIVE REFUSED TO DISCLOSE RFD"
    assert parts_rep[1] == "Contact made with representative (Wife)"
    assert "Account already resolved" in parts_rep[2]

    res_none = rank_rfd_and_csu("Gate closed, unlocated", "Gate closed, unlocated", "none reached", "none reached")
    parts_none = [p.strip() for p in res_none.detailed_rfd.split(";")]
    assert len(parts_none) == 3
    assert parts_none[0] == "NO CLIENT / REPRESENTATIVE REACHED"
    assert parts_none[1] == "No contact made"

    # 2. RFD Value Priority: Explicit hardship > Borrower refused > Representative refused > No client > Moved out
    assert classify_rfd("Client hospital surgery due to medical condition", "Borrower") == "Medical Expense"
    # Secondary order: medical expense > diversion of funds
    assert classify_rfd("Client had medical expense and diversion of funds", "Borrower") == "Medical Expense"

    # 3. CSU Hierarchy: Repo / Resolved > Client Positive + Unit Positive > Client Positive + Unit Negative > Client Negative
    assert classify_csu("Client surrendered unit to repo agent", "Borrower") == "REPO"
    assert classify_csu("Address verified per neighbor, unit seen in garage", "Informant") == "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact)"
    assert classify_csu("Address verified per neighbor, unit not seen", "Informant") == "CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)"
    assert classify_csu("Client unknown in area, cannot locate", "none reached") == "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"


def test_bug3_llm_trimming_and_safety_fallback():
    from engine.trimmer import trim_and_format, fallback_clause_truncate

    # 1. Fallback clause truncation: preserves substantive findings without destructive cuts
    long_remark = (
        "Positive address verified by neighbor Mr. Santos; borrower is temporarily out of town for business trip until Monday; "
        "unit is parked inside covered garage with plate ABC-1234; neighbor advised agent to return Tuesday morning."
    )
    trimmed = fallback_clause_truncate(long_remark, max_chars=200, preferred_chars=181)
    assert len(trimmed) <= 181
    assert not trimmed.endswith(";")
    assert not trimmed.endswith(",")
    # Substantive content retained, NOT 7-41 chars
    assert len(trimmed) > 100
    assert "Positive address verified" in trimmed

    # 2. LLM Trimmer mock with retry
    call_count = 0

    def mock_llm_provider(text: str, is_retry: bool) -> str:
        nonlocal call_count
        call_count += 1
        if not is_retry:
            # First attempt returns > 200 chars
            return "Contacted borrower directly: discussed overdue payment terms and unit condition in extensive detail with multiple payment promises scheduled for next month across several installments exceeding two hundred characters."
        # Retry returns <= 181 chars
        return "Spoke with borrower directly; agreed to settle overdue payment next month."

    res = trim_and_format(long_remark, llm_provider=mock_llm_provider)
    assert call_count == 2  # Proves retry was invoked
    assert res.trimmed_length <= 181
    assert "Spoke with borrower directly" in res.trimmed_text


def test_export_to_excel_and_endpoint():
    pipeline = RemarksLabPipeline()
    df = pd.DataFrame({
        "Account Number": ["ACC-001", "ACC-002"],
        "CH Code": ["CH1", "CH2"],
        "Contact Person": ["Maria", "Pedro"],
        "Contact Relation": ["Asawa", "Neighbor"],
        "Remarks": [
            "BKAL Spoke with wife, PTP on Oct 5 09181234567",
            "Per neighbor, client moved out years ago"
        ],
        "COLLECTION STATUS UPDATE\nAGENT/ADMIN": [
            "CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)",
            "CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)"
        ],
        "RFD": [
            "REPRESENTATIVE REFUSED TO DISCLOSE RFD",
            "MOVED OUT"
        ]
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    data = buf.getvalue()

    report = pipeline.process_file(io.BytesIO(data))
    excel_out = pipeline.export_to_excel(report)
    assert excel_out is not None

    # Verify generated Excel content
    exported_df = pd.read_excel(excel_out, sheet_name="FIELD RESULT (EVALUATED)")
    assert len(exported_df) == 2
    assert "char checker" in exported_df.columns
    assert list(exported_df["char checker"]) == ["OKAY NA TO", "OKAY NA TO"]
    assert "COLLECTION STATUS UPDATE" in exported_df.columns
    assert "RFD" in exported_df.columns

    # Test HTTP POST /remarks-lab/export endpoint
    app = create_demo_app()
    client = TestClient(app)
    resp = client.post(
        "/remarks-lab/export",
        files={"workbook": ("test.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "RCBC_FIELD_EVALUATED" in resp.headers["content-disposition"]


def test_expanded_hardships_and_informant_guardrails():
    from engine.classifier import classify_contact, classify_rfd, classify_csu

    # 1. Calamity
    assert classify_rfd("Per client, residence was flooded due to typhoon") == "Calamity"

    # 2. Scammed
    assert classify_rfd("Client stated was scammed by coworker who took vehicle") == "Scammed"

    # 3. LTO Apprehension
    assert classify_rfd("Unit impounded by LTO; client coordinating for release") == "LTO Apprehension/No ORCR/HPG"

    # 4. Work Relocation
    assert classify_rfd("Per sibling, client no longer resides here, working in Palawan") == "Work Relocation"

    # 5. Pending Recon
    assert classify_rfd("Client claims already settled long ago, pending recon with bank") == "Pending Recon"

    # 6. Informant speaker guardrail (e.g. caretaker Bong Bong, store staff, HOA guard)
    c1 = classify_contact("", "", "Positive address, unit negative; per caretaker Bong Bong, property confirmed owned by client.")
    assert c1.category_label == "Informant"

    c2 = classify_contact("", "", "Address verified positive; only store staff present. Per staff, client out of town.")
    assert c2.category_label == "Informant"

    c3 = classify_contact("", "", "House closed; per HOA guard, client still resides in subdivision.")
    assert c3.category_label == "Informant"

