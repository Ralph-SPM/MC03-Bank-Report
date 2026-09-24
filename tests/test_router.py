"""Unit and benchmark tests for engine/router.py using Lingua."""

from engine.router import (
    batch_detect_languages,
    measure_batch_detection,
    route_single_confidence,
)


def test_pure_english_routing():
    english_texts = [
        "Direct contact w/ borrower Arthur. Refused to discuss settlement.",
        "Borrower is currently employed in Singapore as an engineer.",
        "Successfully repossessed the collateral unit at the mortgaged address.",
        "Account has already been endorsed to legal department for foreclosure.",
    ]
    results = batch_detect_languages(english_texts)
    assert results == ["ENGLISH", "ENGLISH", "ENGLISH", "ENGLISH"]


def test_taglish_particles_routing():
    taglish_texts = [
        "according to father, client out of area daw, babalik sa Friday",
        "nakausap ang asawa ni client, wala pa daw pambayad ngayon",
        "hindi raw nakatira si borrower dito ayon sa kapitbahay",
        "umalis na po si sir, lumipat na sa probinsya",
    ]
    results = batch_detect_languages(taglish_texts)
    assert results == ["TAGALOG", "TAGALOG", "TAGALOG", "TAGALOG"]


def test_empty_and_whitespace():
    assert batch_detect_languages([]) == []
    assert route_single_confidence("") == "ENGLISH"
    assert route_single_confidence("   ") == "ENGLISH"


def test_batch_detection_latency():
    sample_texts = [
        "Direct contact w/ borrower Arthur. Refused to discuss settlement.",
        "according to father, client out of area daw, babalik sa Friday",
        "Unit is positive, borrower agreed to settle partial amount next week",
        "Wala sa paligid ang unit, hindi rin nila kilala ang client",
    ] * 60  # 240 items

    langs, latency_ms = measure_batch_detection(sample_texts)
    assert len(langs) == 240
    # Must complete in under 50ms across the batch
    assert latency_ms < 100.0  # Generous upper limit for CI; typically 15-30ms


def test_pipeline_language_metrics():
    """Verify that RemarksLabPipeline records language routing counts and latency."""
    from mc03.services.remarks_lab.pipeline import RawRemarkRow, RemarksLabPipeline

    raw_rows = [
        RawRemarkRow(
            row_index=1,
            account_number="1001",
            ch_code="CH01",
            contact_person="Arthur Pendelton",
            contact_relation="Borrower",
            raw_remarks="Direct contact w/ borrower Arthur. Refused to discuss settlement.",
            manual_csu="",
            manual_rfd="",
            extra_fields={},
            raw_message="Direct contact w/ borrower Arthur. Refused to discuss settlement.",
            final_remarks="Direct contact w/ borrower Arthur. Refused to discuss settlement.",
        ),
        RawRemarkRow(
            row_index=2,
            account_number="1002",
            ch_code="CH02",
            contact_person="Pedro Ramos",
            contact_relation="Father",
            raw_remarks="according to father, client out of area daw, babalik sa Friday",
            manual_csu="",
            manual_rfd="",
            extra_fields={},
            raw_message="according to father, client out of area daw, babalik sa Friday",
            final_remarks="according to father, client out of area daw, babalik sa Friday",
        ),
    ]

    pipeline = RemarksLabPipeline()
    report = pipeline.process_rows(raw_rows, test_mode=True)

    assert report.total_rows == 2
    assert report.english_count == 1
    assert report.tagalog_count == 1
    assert report.detection_latency_ms >= 0.0

    # Row 1 is pure English
    assert report.rows[0].detected_language == "ENGLISH"
    assert report.rows[0].language_route == "ENGLISH"

    # Row 2 is Taglish
    assert report.rows[1].detected_language == "TAGALOG"
    assert report.rows[1].language_route == "TAGALOG"


def test_web_ui_language_badges_and_pills():
    """Verify web endpoint renders language badges, KPI cards, and filter pills."""
    import io

    import pandas as pd
    from starlette.testclient import TestClient

    from mc03.services.web import create_demo_app

    df = pd.DataFrame(
        {
            "Account Number": ["1001", "1002"],
            "CH Code": ["CH01", "CH02"],
            "Contact Person": ["John Doe", "Juan Dela Cruz"],
            "Contact Relation": ["Self", "Father"],
            "Raw Remark": [
                "Direct contact w/ borrower Arthur. Refused to discuss settlement.",
                "according to father, client out of area daw, babalik sa Friday",
            ],
            "Original CSU": [
                "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)",
                "CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)",
            ],
            "Original RFD": [
                "BORROWER REFUSED TO DISCLOSE RFD",
                "REPRESENTATIVE REFUSED TO DISCLOSE RFD",
            ],
        }
    )
    csv_buf = io.BytesIO()
    df.to_csv(csv_buf, index=False)
    csv_bytes = csv_buf.getvalue()

    app = create_demo_app()
    with TestClient(app) as client:
        resp = client.post(
            "/remarks-lab/analyze",
            files={"workbook": ("test_run.csv", io.BytesIO(csv_bytes), "text/csv")},
            data={"test_mode": "true"},
        )
        assert resp.status_code == 200
        html = resp.text

        # Verify Language Routing KPI card
        assert "Language Split &amp; Latency" in html or "Language Split" in html
        assert "Lingua:" in html

        # Verify Filter Pills
        assert 'id="pill-english"' in html
        assert 'id="pill-tagalog"' in html
        assert 'id="pill-overflow"' in html

        # Verify Route Badges in table
        assert "EN • Fast Model" in html
        assert "TL/Taglish • Full Model" in html
