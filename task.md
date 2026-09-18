# Task Progress: Remark Intelligence Lab & Benchmark View

- [x] 1. Engine Modules (`src/mc03/services/remarks_lab/`)
  - [x] `__init__.py`
  - [x] `parser.py` (XLSX parsing for `FIELD RSULT` sheet, fuzzy column matching)
  - [x] `cleaner.py` (Tag sanitizer, prohibited tag tracking, Viber/Email/Phone mask)
  - [x] `classifier.py` (Extended Filipino relation whitelists, hard guardrail)
  - [x] `ranker.py` (RFD ranker + CSU mapping)
  - [x] `trimmer.py` (200-char trimmer + preamble stripping)
  - [x] `pipeline.py` (Orchestration + benchmark comparison & metrics)
- [x] 2. Templates & Design
  - [x] Update `src/mc03/services/templates/base.html` (wrap nav in block)
  - [x] Create `src/mc03/services/templates/remarks_lab.html` (Upload form + Benchmark metrics + Filter pills + Diff table)
- [x] 3. Routes & Integration
  - [x] Add `GET /remarks-lab` and `POST /remarks-lab/analyze` in `src/mc03/services/web.py`
- [x] 4. Verification & Testing
  - [x] Run pytest suite to verify no regressions (107/107 passed)
  - [x] Write unit test for Remarks Lab pipeline (`tests/test_remarks_lab.py`)
  - [x] Live upload endpoint validation with synthetic Excel data
