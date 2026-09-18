# Walkthrough: Remark Intelligence Lab & Benchmark View

Successfully built and integrated the dedicated **Remark Intelligence Lab & Benchmark View** at `/remarks-lab` for the RCBC Auto Loan horizontal demo.

## Key Changes Made

### 1. Isolated Domain & Service Engine (`src/mc03/services/remarks_lab/`)
- [`parser.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/parser.py): Reads the `FIELD RSULT` sheet with case-insensitive column alias mapping (handles variants for `Account Number`, `CH Code`, `Contact Person`, `Contact Relation`, `Remarks`, `CSU`, `RFD`).
- [`cleaner.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/cleaner.py): Wraps domain sanitizer to strip `BCAL`, `L3`, `INB`, `OBD`, bare phone numbers (`09...`, `+63...`), emails, and messaging handles (`Viber`, `WhatsApp`), tracking each removed tag.
- [`classifier.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/classifier.py): Implements extended Filipino-language relationship vocabulary (`asawa`, `kapatid`, `kuya`, `ate`, `nanay`, `tatay`, `tita`, `tito`, `biyanan`, `kapitbahay`, `sekyu`, `tanod`, `kagawad`, `kasambahay`) with strict guardrails: an informant can never resolve to representative status.
- [`ranker.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/ranker.py): Handles pipe tag parsing (`TYPE | RFD | DETAILED | REMARKS`) and semantic keyword-to-RFD ranking (`Refused`, `PTP`, `Moved Out`) mapped directly to RCBC CSU statuses.
- [`trimmer.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/trimmer.py): Strips redundant collector preambles (`FIELD VISIT RESULT:`, `NOTE:`, etc.) and enforces strict `<= 200` character length at word boundaries for bank upload compliance.
- [`pipeline.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/pipeline.py): End-to-end orchestration computing summary statistics: Total Rows, Prohibited Tags Stripped, CSU Benchmark Accuracy %, RFD Benchmark Accuracy %, and 200-char Overflows Prevented.

### 2. UI & Design System (`src/mc03/services/templates/`)
- [`base.html`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/templates/base.html): Added `{% block nav_tabs %}` and a clean link to **Remark Lab & Benchmark** tab.
- [`remarks_lab.html`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/templates/remarks_lab.html):
  - **5 KPI Metric Cards**: Rows Analyzed, Prohibited Tags Stripped, CSU Benchmark Match %, RFD Benchmark Match %, 200-Char Overflow Prevented.
  - **Filter Pills**: Quick toggle for All, Discrepancies, Representatives, Informants, and Tags Stripped.
  - **Instant Search**: Real-time client-side search filtering across accounts, relations, remarks, and statuses.
  - **Inspection Table**: Side-by-side diff showing Raw Input vs Stripped Tags, Predicted vs Data Analyst manual ground truth, and the 200-char bank-ready copyable payload.

### 3. Application Routes (`src/mc03/services/web.py`)
- `GET /remarks-lab`: Serves the upload landing screen and workflow instructions.
- `POST /remarks-lab/analyze`: Ingests the uploaded `.xlsx` file, runs the pipeline on `FIELD RSULT`, and renders the interactive benchmark table.

---

## Verification Results

### Automated Tests
Run command:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -x -v --tb=short
```
- **107 passed in 3.35s** (101 existing regression tests + 6 new comprehensive unit tests in [`tests/test_remarks_lab.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/tests/test_remarks_lab.py)).
- Zero regressions against existing batch pipeline, review screens, or export packaging.

### Live Server Verification
Tested synthetic file upload through `POST /remarks-lab/analyze`:
- Correct HTTP 200 response with rendered KPI cards and table rows.
- Prohibited tag detection verified (Phone, L3, BCAL stripped).
- Filipino relationship classification verified.
- 200-char bank trimming verified.
