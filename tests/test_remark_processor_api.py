"""Tests for Remark Processor and Remark Lab REST API endpoints."""

import io

import pandas as pd
from starlette.testclient import TestClient

from mc03.services.web import create_demo_app


def test_taxonomies_endpoint():
    app = create_demo_app()
    client = TestClient(app)

    res = client.get("/api/remarks/taxonomies")
    assert res.status_code == 200
    data = res.json()
    assert "csu_options" in data
    assert "rfd_options" in data
    assert "primary_csu" in data
    assert "primary_rfd" in data
    assert len(data["csu_options"]) > 0
    assert len(data["rfd_options"]) > 0


def test_api_remarks_process_with_concat():
    app = create_demo_app()
    client = TestClient(app)

    # Create synthetic excel with Concat column
    data = {
        "Row Index": [1, 2],
        "Account Number": ["1001", "1002"],
        "CH Code": ["CH-A", "CH-B"],
        "Contact Person": ["Maria Santos", "Pedro Cruz"],
        "Contact Relation": ["Asawa", "Kapitbahay"],
        "Concat": ["KEY_1001_CHA", "KEY_1002_CHB"],
        "Final Remarks": [
            "ECA AUTO_S.P. MADRID_HOME_09/02/2026 Spoke with asawa, promised PTP next Friday",
            "ECA AUTO_S.P. MADRID_HOME_09/02/2026 Informant stated borrower relocated to Bicol",
        ],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    buf.seek(0)

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    res = client.post(
        "/api/remarks/process",
        files={"workbook": ("test_field.xlsx", buf.getvalue(), xlsx_mime)},
        data={"run_ai": "false"},
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["success"] is True
    assert res_data["total_rows"] == 2
    assert len(res_data["rows"]) == 2

    row0 = res_data["rows"][0]
    assert row0["ch_code"] == "CH-A"
    assert row0["concat_val"] == "KEY_1001_CHA"
    assert row0["category_label"] == "Representative"
    assert "trimmed_statement" in row0
    assert "predicted_csu" in row0
    assert "predicted_rfd" in row0
    assert "csu_confidence" in row0

    row1 = res_data["rows"][1]
    assert row1["concat_val"] == "KEY_1002_CHB"
    assert row1["category_label"] == "Informant"


def test_api_remarks_test_mode_agreement():
    app = create_demo_app()
    client = TestClient(app)

    data = {
        "Account Number": ["2001"],
        "CH Code": ["CH-TEST"],
        "Contact Person": ["Self"],
        "Contact Relation": ["Borrower"],
        "Remarks": ["Client refused to talk about auto loan"],
        "Original CSU": ["CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)"],
        "Original RFD": ["BORROWER REFUSED TO DISCLOSE RFD"],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    buf.seek(0)

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    res = client.post(
        "/api/remarks/test",
        files={"workbook": ("test_mode.xlsx", buf.getvalue(), xlsx_mime)},
        data={"run_ai": "false"},
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["success"] is True
    assert res_data["total_rows"] == 1
    assert "csu_accuracy_pct" in res_data
    assert "rfd_accuracy_pct" in res_data
    assert res_data["rows_with_ground_truth"] == 1


def test_api_export_processed_rows():
    app = create_demo_app()
    client = TestClient(app)

    rows = [
        {
            "row_index": 1,
            "account_number": "1001",
            "ch_code": "CH-01",
            "contact_person": "Juana Dela Cruz",
            "contact_relation": "Asawa",
            "category_label": "Representative",
            "concat_val": "CONCAT_1001",
            "predicted_csu": "CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)",
            "predicted_rfd": "DELAYED SALARY",
            "detailed_rfd": "DELAYED SALARY; SPOKE TO ASAWA; PTP NEXT WEEK",
            "trimmed_statement": "ECA AUTO_S.P. MADRID_HOME_09/02/2026 Spoke with spouse.",
        }
    ]

    res = client.post("/api/remarks/export-processed", json={"rows": rows})
    assert res.status_code == 200
    assert "spreadsheetml.sheet" in res.headers["content-type"]

    # Verify generated workbook content
    out_df = pd.read_excel(io.BytesIO(res.content), sheet_name="FIELD RESULT (PROCESSED)")
    assert len(out_df) == 1
    assert out_df.iloc[0]["CH Code"] == "CH-01"
    assert out_df.iloc[0]["CONCAT"] == "CONCAT_1001"
    assert "Both" in out_df.iloc[0]["COLLECTION STATUS UPDATE"]
    assert out_df.iloc[0]["RFD"] == "DELAYED SALARY"
    assert out_df.iloc[0]["char checker"] != "PLS REVISE"


def test_profiles_and_models_endpoints(monkeypatch, tmp_path):
    test_file = tmp_path / "prompt_profiles.json"
    import engine.profiles
    monkeypatch.setattr(engine.profiles, "PROFILES_FILE", test_file)

    app = create_demo_app()
    client = TestClient(app)

    # 1. Models list
    res = client.get("/api/remarks/models")
    assert res.status_code == 200
    models_data = res.json()
    assert "models" in models_data
    assert isinstance(models_data["models"], list)
    assert len(models_data["models"]) > 0

    # 2. Get profiles
    res = client.get("/api/remarks/profiles")
    assert res.status_code == 200
    prof_data = res.json()
    assert "active_profile_id" in prof_data
    assert "profiles" in prof_data
    assert len(prof_data["profiles"]) >= 1

    # 3. Create/save custom profile
    new_profile = {
        "id": "test_profile_custom",
        "name": "Custom Test Profile",
        "description": "Profile for unit testing separate models and directives",
        "model_english": "nova-2-lite",
        "model_tagalog": "qwen3-32b",
        "system_instructions": "CUSTOM DIRECTIVE: Always favor PENDING RECON if disputed.",
    }
    res = client.post("/api/remarks/profiles", json={"profile": new_profile, "set_active": True})
    assert res.status_code == 200
    save_data = res.json()
    assert save_data["status"] == "success"
    assert save_data["saved_profile"]["id"] == "test_profile_custom"
    assert save_data["profiles_data"]["active_profile_id"] == "test_profile_custom"

    # 4. Prompt preview
    res = client.post(
        "/api/remarks/prompt-preview",
        json={"system_instructions": "CUSTOM DIRECTIVE: Always favor PENDING RECON if disputed."},
    )
    assert res.status_code == 200
    preview_data = res.json()
    assert "rendered_prompt" in preview_data
    assert "CUSTOM DIRECTIVE" in preview_data["rendered_prompt"]
    assert "ALLOWED_CSU" in preview_data["rendered_prompt"]

    # 5. Reset to defaults
    res = client.post("/api/remarks/profiles/reset")
    assert res.status_code == 200
    reset_data = res.json()
    assert reset_data["status"] == "success"
    assert reset_data["profiles_data"]["active_profile_id"] == "default"


def test_api_remarks_process_stream():
    app = create_demo_app()
    client = TestClient(app)

    data = {
        "Account Number": ["3001"],
        "CH Code": ["CH-STREAM"],
        "Contact Person": ["Maria"],
        "Contact Relation": ["Asawa"],
        "Concat": ["CONCAT_3001"],
        "Final Remarks": ["ECA AUTO_S.P. MADRID_HOME_09/02/2026 Spoke with spouse PTP next week."],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="FIELD RSULT", index=False)
    buf.seek(0)

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    res = client.post(
        "/api/remarks/process-stream",
        files={"workbook": ("stream_test.xlsx", buf.getvalue(), xlsx_mime)},
        data={"run_ai": "false"},
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    text = res.text
    assert "data: " in text
    assert '"type": "complete"' in text


def test_api_remarks_export_processed_test_mode():
    app = create_demo_app()
    client = TestClient(app)

    test_rows = [
        {
            "row_index": 1,
            "account_number": "8888",
            "ch_code": "CH-TEST",
            "contact_person": "Pedro",
            "contact_relation": "Self",
            "category_label": "Borrower",
            "concat_val": "KEY_8888",
            "raw_remarks": "Borrower promises to pay next Monday",
            "manual_csu": "CLIENT POSITIVE/UNIT POSITIVE",
            "manual_rfd": "PTP",
            "predicted_csu": "CLIENT POSITIVE/UNIT POSITIVE",
            "predicted_rfd": "PTP",
            "csu_reasoning": "Direct borrower contact with PTP",
            "rfd_reasoning": "Explicit commitment to settle",
            "csu_match": True,
            "rfd_match": True,
            "discrepancy_flag": False,
            "trimmed_statement": "Borrower promises to pay next Monday.",
        }
    ]

    res = client.post(
        "/api/remarks/export-processed",
        json={"rows": test_rows, "test_mode": True},
    )
    assert res.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in res.headers["content-type"]
    assert "RCBC_FIELD_EVALUATED_" in res.headers["content-disposition"]

    # Verify Excel contents
    out_buf = io.BytesIO(res.content)
    df = pd.read_excel(out_buf, sheet_name="FIELD RESULT (EVALUATED)")
    assert "Raw Remark" in df.columns
    assert "Original CSU" in df.columns
    assert "CSU Why" in df.columns
    assert "CSU Match" in df.columns
    assert df["CSU Match"].iloc[0] == "YES"



