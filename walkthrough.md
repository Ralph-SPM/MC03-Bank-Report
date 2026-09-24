# Walkthrough: Parallel 2-Tier Language-Routed AI Engine & Remarks Intelligence Lab

Successfully implemented and verified the **Parallel 2-Tier Language-Routed Remark Summarizer & Classifier** with native Rust Lingua language detection, full AI CSU/RFD structured classification, and fixed CSV/date filtering in the Remarks Intelligence Lab.

---

## 1. Key Changes & Architecture

### A. 2-Tier Parallel Language Router ([`engine/router.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/engine/router.py))
- **Singleton Lingua Engine**: Initialized with high precision restricted exclusively to `Language.ENGLISH` and `Language.TAGALOG`.
- **Rust-Native Multi-Threaded Batch Detection**: Uses `LanguageDetector.detect_languages_in_parallel()` to detect batch languages in milliseconds.
- **2-Tier Model Routing**:
  - **Tagalog / Taglish**: Routed to `minimax-m2.5` (multilingual reasoning model with 1,500 token budget for deep Tagalog analysis).
  - **English**: Routed to `qwen3-32b` (high-accuracy 32B model adept at nuanced Philippine banking narratives).

### B. Full AI CSU & RFD Classification ([`engine/summarizer.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/engine/summarizer.py))
- **Independent CSU Inference**: CSU is inferred directly and independently from the remark narrative (analyzing Client Status, Unit Status, and Contact Status) rather than being tied to or derived from Detailed RFD.
- **Standardized Taxonomy Integration**: Directly embeds the RCBC 8 Collection Status Update (CSU) codes and 16 Reason for Default (RFD) codes in the system prompt enum schema.
- **Strict Structured JSON Schema**:
  ```json
  {
    "csu": "<Exact RCBC CSU Status>",
    "rfd": "<Exact RCBC RFD Code>",
    "detailed_rfd": "<RFD Clause>; <Contact Clause>; <Core Statement>",
    "summary": "<Bank-compliant summary <= 200 chars>"
  }
  ```
- **Context Scope**: Feeds isolated remark narratives (from final remarks or the message column in test mode, cleanly stripped of ECA prefixes) directly into the LLM prompt.
- **Asynchronous Parallel Dispatch**: Dispatches both Tagalog and English batches in parallel using `asyncio.gather()` over LiteLLM with automatic retry and rate-limit backoff.

### C. CSV Import & Date Filter Bug Fix ([`src/mc03/services/remarks_lab/parser.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/parser.py))
- **Word-Boundary Alias Matching**: Replaced loose substring checks with `(?:\b|_){alias}(?:\b|_)` to prevent `"COLLECTION STATUS UPDATE"` from accidentally matching the alias `"date"` inside `"update"`.
- **Embedded Date Extraction**: Added regex scanning to extract embedded dates directly from ECA headers (e.g. `_09/02/2026`) when explicit date columns are absent.
- **Graceful Date Handling**: Workbooks or test CSVs without date columns now parse and display all rows rather than dropping them to 0.

### D. Dual-Engine Evaluation Toggle in UI ([`src/mc03/services/templates/remarks_lab.html`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/templates/remarks_lab.html) & [`src/mc03/services/web.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/web.py))
- **Engine Toggle Switch**: Added an intuitive switch in the upload form:
  - **Full 2-Tier AI (Default)**: Leverages `minimax-m2.5` and `qwen3-32b` in parallel for complete AI classification of CSU, RFD, Detailed RFD, and Summaries.
  - **Rule-Based Engine (Instant)**: Evaluates rows deterministically in milliseconds using dictionary and keyword heuristics.
- **Classification Source Badges**: Table Column 5 dynamically badges each row as `2-Tier AI Classified` (green) or `Rule Ranked` (slate) along with the detected language (`TAGALOG` vs `ENGLISH`).

---

## 2. Rule Fallback Explanation

When the rule engine is active (or if an AI call fails or is bypassed), remarks are formatted using **Deterministic Clause-Boundary Truncation**:
- Instead of simple arbitrary character truncation (which could cut in the middle of a word, phone number, or date), the trimmer identifies safe grammatical clause boundaries (semicolons, periods, or commas) within the 181–200 character target window.
- The ECA Header prefix (`ECA AUTO_...`) is strictly preserved at the start, ensuring compliance with RCBC bank upload requirements.

---

## 3. Verification & Benchmark Results

### Automated Pytest Suite
Ran the full test suite across the entire project:
```powershell
.venv\Scripts\pytest -q
```
**Results:**
- **148 passed, 0 failed in 4.50s**
- `tests/test_remarks_lab.py`: **33 passed in 2.97s**
- `tests/test_router.py`: **Passed (Lingua multi-threaded benchmark & language accuracy)**

### Real-Data CSV Verification (`media_1789971317609.csv`)
1. **Rule Engine Mode**:
   - Total rows: **226 rows** parsed and analyzed instantly (0.01s).
   - CSU accuracy: **70.4%** against manual entries.
   - RFD accuracy: **88.5%** against manual entries.
2. **2-Tier AI Mode**:
   - Small batch tested via Web endpoint `/remarks-lab/analyze` with `run_ai=True`.
---

## 4. RCBC CSU/RFD Taxonomy Mapping & Human Feedback Loop

### A. Official Taxonomies & Tiering
- **CSU (42 Options)**: 9 Core Primary Matrix options (8 contact/unit combinations + `PENDING RECON`) prioritized, with 33 secondary specialized/legal statuses.
- **RFD (27 Options)**: 13 Primary Operational RFDs prioritized, with 14 secondary specific hardships.
- **Unit Impounded Prohibition**: Strictly prohibited as an RFD code; mapped to `LTO APPREHENSION/NO ORCR/HPG`.
- **Empty RFD (`""`)**: Supported when zero information is gathered or client is unknown in the area.
- **Moved Out vs No Client Reached**: `MOVED OUT` requires confirmation that borrower previously lived there and relocated; `NO CLIENT / REPRESENTATIVE REACHED` applies when client still resides there but was not around.

### B. Dynamic Human Feedback Loop to Active Models
- **Rule Store**: Saved in `storage/custom_prompt_rules.json`.
- **Endpoint `POST /remarks-lab/apply-rules`**: Persists reviewer-tuned rules.
- **Dynamic Prompt Injection**: Injects `### ACTIVE HUMAN REVIEWER TUNED DIRECTIVES (HIGHEST PRIORITY)` into `TwoTierRemarksSummarizer` for both Qwen and Nova models immediately.
- **UI "Apply to Active Models" Button**: Integrated into the Finetune Modal in Remarks Lab for instant one-click deployment.

### C. Full Test Verification
- All **162 pytest tests** pass cleanly across the codebase.

---

## 5. Account Confirmed Resolved / Settled Tuning (Row 20129)

- **Prompt Directive Updated ([`engine/summarizer.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/engine/summarizer.py))**:
  - Differentiated between:
    1. Borrower/Client claiming they paid / payment dispute without agent verification -> `PENDING RECON`.
    2. Agent / Collector / Bank confirming the account is resolved, settled, or fully paid -> `CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)`.
- **Canonicalizer Updated ([`engine/summarizer.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/engine/summarizer.py))**:
  - Automatically maps agent-resolved remarks to `CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)` and `REPRESENTATIVE REFUSED TO DISCLOSE RFD` (or `NO CLIENT / REPRESENTATIVE REACHED`).
- **Rule Engine & Decision Logic ([`src/mc03/services/remarks_lab/ranker.py`](file:///c:/Users/SPM/Desktop/work-files/automation-project/src/mc03/services/remarks_lab/ranker.py))**:
  - `classify_csu` now detects agent-resolved remarks and outputs `CSU_RESOLVED` (`CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)`).
  - `explain_csu` provides explicit decision rationale: *"Agent confirmed account has been resolved/settled; positive unit and client status without direct borrower contact."*


