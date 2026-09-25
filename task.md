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
- [x] 5. Parallel 2-Tier Language-Routed Summarizer with Lingua
  - [x] Engine Singleton & Parallel Router (`engine/router.py`) with Lingua Rust batch API (<25ms)
  - [x] Parallel 2-Tier LLM Dispatcher (`engine/summarizer.py`) via LiteLLM (`qwen3-32b` & `nova-2-lite`)
  - [x] UI & Benchmark Lab Integration (`remarks_lab.html` route badges, split card, filter pills)
  - [x] Full Verification & 147/147 test suite pass
- [x] 6. Pure Test Mode & Dedicated Remark Processor with React 19 Frontend
  - [x] Concat column resolution & extraction (`parser.py` & `pipeline.py`)
  - [x] LLM confidence & alternative candidates schema with quick-switch pills (`engine/summarizer.py`)
  - [x] Modern React 19 + Vite + TypeScript + Tailwind CSS Frontend (`frontend/`)
  - [x] 5-column production table (CH Code, Relationship, Concat, Remark Summary, Tool Answer)
  - [x] Inline editable textarea with real-time <=200 character counter and validation warning
  - [x] Interactive searchable dropdowns for full RCBC CSU & RFD taxonomies
  - [x] Pure test mode for Remark Lab (KPI benchmark cards, agreement %, DIFF tags, prompt fine-tuning)
  - [x] REST API endpoints (`/api/remarks/taxonomies`, `/api/remarks/process`, `/api/remarks/test`, `/api/remarks/export-processed`)
  - [x] Full Verification (166/166 tests passed, Vite production bundle generated)


