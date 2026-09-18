# Implementation Plan: RCBC Initial Demo (On-Demand Vertical Slice)

## Overview

This plan converts the approved `design.md` into incremental coding steps for the on-demand
RCBC Auto Loan demo. Each step builds on the previous one and ends by wiring components into the
lazy per-run pipeline and the local FastAPI portal, so no code is left orphaned.

The plan preserves the `src/mc03` layout (`domain`, `persistence`, `services`, plus
`settings.py` and `storage.py`), targets Python 3.12 managed by `uv`, pins dependencies with
`uv add --exact`, and keeps tests under `tests/` using pytest with `tmp_path` fixtures and
Hypothesis. Verification commands per workspace rules: `uv run pytest`, `uv run ruff check .`,
`uv run mypy src`.

The task numbering maps to the design's execution sprint:
- Task 1 → Sept 15 (upload routes, date-window filtering, Master File account resolution)
- Task 2 → Sept 16 (noise exclusion, sanitizer, pipe extraction, RCBC ranking, 200-char trim)
- Task 3 → Sept 17 (exception queue state, openpyxl injection, pyzipper AES-256 packaging)
- Task 4 → Sept 18 (end-to-end dry run and deliverable verification)

## Tasks

- [x] 1. Sprint Day Sept 15 — Scaffolding, configuration, ingestion, and account resolution
  - [x] 1.1 Scaffold the project and `src/mc03` package skeleton
    - Create `pyproject.toml` configured for `uv` with `requires-python = ">=3.12,<4.0"`, Ruff rules (`E`, `F`, `I`, `B`, `UP`), 100-char line limit, and strict mypy.
    - Add runtime dependencies with `uv add --exact`: `fastapi`, `uvicorn`, `python-multipart`, `jinja2`, `pandas`, `openpyxl`, `pydantic`, `pydantic-settings`, `pyzipper`.
    - Add dev extra with `uv add --exact --optional dev`: `pytest`, `hypothesis`, `ruff`, `mypy`; run `uv lock` and `uv sync --extra dev`.
    - Create the package tree: `src/mc03/{__init__.py, settings.py, storage.py}`, `src/mc03/domain/`, `src/mc03/persistence/`, `src/mc03/services/` (each with `__init__.py`), and an empty `tests/` package.
    - Add `.env.example` documenting `MC03_`-prefixed settings; keep real `.env` out of source control.
    - _Requirements: 17.1_

  - [x] 1.2 Implement typed `RuntimeSettings`
    - Implement `RuntimeSettings` in `settings.py` using pydantic-settings with the `MC03_` env prefix and `__` nested delimiter, rejecting unknown settings.
    - Model explicit path boundaries (database, raw-source, artifact, template, lock) and the `127.0.0.1:8000` bind config.
    - _Requirements: 17.1, 17.2_

  - [ ]* 1.3 Write unit tests for `RuntimeSettings` validation
    - Cover successful load, unknown-setting rejection within the startup budget, and `MC03_`/`__` parsing.
    - _Requirements: 17.1, 17.2_

  - [x] 1.4 Implement protected storage guards in `storage.py`
    - Implement `build_protected_storage_paths` and `prepare_protected_storage` enforcing a single Protected_Storage_Root, rejecting paths resolving outside the root, and rejecting UNC / known network-mounted paths before any directory is created.
    - Create validated directories with owner-only permissions.
    - _Requirements: 17.3, 17.4, 17.5, 17.6_

  - [ ]* 1.5 Write unit tests for storage-path guards
    - Use `tmp_path` fixtures; cover valid roots, outside-root rejection, UNC/network rejection, and owner-only permission creation.
    - _Requirements: 17.3, 17.4, 17.5, 17.6_

  - [x] 1.6 Implement domain models and enums
    - In `domain/models.py`, implement `Disposition`, `ExceptionKind` (including `AMBIGUOUS_ACCOUNT`), `Relation`, `RemarkFields`, `ProcessedRow`, and `RunResult` per the design's dataclasses.
    - _Requirements: 12.1, 5.4, 5.5_

  - [x] 1.7 Implement Volare DRR ingestion adapter
    - In `services/ingestion.py`, implement `load_volare_drr(path, date_from, date_to)` with pandas confined to the adapter: inclusive date-window filter, excluded-status drop (case-insensitive, trimmed), Viber/Email remark substitutions, purge-pattern removal, and REF-sheet numeric rank population (1–999999).
    - Route unparseable/missing call dates and missing rank lookups to Review_Only exceptions; halt with a column-name error when a required column is absent.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [ ]* 1.8 Write unit tests for Volare DRR ingestion
    - Cover date-window inclusivity, status exclusion, substitutions, purge patterns, rank population, and the review/halt exception paths.
    - _Requirements: 3.1, 3.2, 3.4, 3.6, 3.7, 3.8_

  - [x] 1.9 Implement Field Result ingestion adapter
    - Implement `load_field_result(path, date_from, date_to)`: keep `bank == 'RCBC Auto Loan'` (trimmed, case-insensitive), drop `status == 'Cancelled'`, inclusive `visit_date` window, preserve the `CH code`..`OB` contiguous column range; halt with a missing-column error and drop unparseable visit dates.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 1.10 Write unit tests for Field Result ingestion
    - Cover bank/status/window filters, column-range preservation, missing-column halt, and unparseable-date drop.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 1.11 Implement Master File account resolution
    - In `services/resolution.py`, implement `resolve_accounts(field_df, master_df)`: case-sensitive left-merge on trimmed `CH code`, valid statuses `RETAIN`/`RESOLVED`/`PULLOUT` with non-empty `Account Number`, single-match assignment, `UNMAPPED_ACCOUNT` and `AMBIGUOUS_ACCOUNT` routing to Review_Only, and row-count conservation.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 1.12 Write property test for account resolution row conservation
    - **Property 2: Account resolution never drops rows**
    - **Validates: Requirements 5.3, 5.4, 5.6**

  - [x] 1.13 Implement FastAPI app factory, Screen 1, and `/process` route
    - In `services/web.py`, create the FastAPI app factory bound to `127.0.0.1:8000` with no auth/sessions, run attribution defaulting to `"DA"`, exactly the three screen routes, no Viber webhook, 404 for other paths, and loopback-only refusal for non-loopback sources.
    - Implement `GET /` (date inputs + 3 file inputs + Process control via Jinja2) and `POST /process` accepting `report_date_from`, `report_date_to`, `volare_drr`, `field_result`, `master_file`, with field-specific validation (missing/non-`.xlsx`/unreadable, date order, 50 MB cap, missing/invalid dates) and no Run creation on failure.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.2, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 1.14 Write integration tests for upload intake and validation
    - Use `tmp_path` sample `.xlsx`; cover successful `POST /process` and each Screen 1 validation/error path (no Run created).
    - _Requirements: 2.2, 2.4, 2.5, 2.6, 2.7, 1.4, 1.6, 1.7_

- [~] 2. Checkpoint — Sept 15 deliverables
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Sprint Day Sept 16 — Sanitization, extraction, and the RCBC hierarchy engine
  - [x] 3.1 Implement prohibited-marker sanitizer
    - In `domain/sanitizer.py`, implement `sanitize_remark(text)` removing BCAL CH codes, L3 labels, INB/OBD markers, PH codes, `.com` links, and SRC tags; collapse whitespace; guarantee idempotence, unchanged passthrough when clean, and empty-string output for empty/all-marker input.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 3.2 Write property test for sanitizer idempotence and completeness
    - **Property 1: Sanitizer idempotence and completeness**
    - **Validates: Requirements 6.1, 6.2, 6.3**

  - [x] 3.3 Implement the provider-neutral extraction gateway
    - In `services/extraction_gateway.py`, implement `ExtractionGateway` with `from_settings(settings)`, a PII-minimized request contract, schema validation, and a 0.70 confidence threshold; make no business decisions in the gateway.
    - _Requirements: 7.4, 7.5_

  - [x] 3.4 Implement the remark extraction pipeline
    - In `domain/remarks.py`, implement `extract_remark(text, gateway)`: fast pipe-tag parse (TYPE OF RFD | RFD | DETAILED RFD | REMARKS) with null-filling for absent segments and no gateway call on match; gateway fallback for free text; null fields plus Review_Only routing on schema failure or low confidence.
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [ ]* 3.5 Write property test for LLM input minimization
    - **Property 7: LLM input minimization**
    - **Validates: Requirements 7.3**

  - [ ]* 3.6 Write unit tests for the pipe-tag parser
    - Cover full match, partial/empty segments (null-filling), and non-match gateway fallback using a fake gateway.
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 3.7 Implement relation classification
    - In `domain/hierarchy.py`, implement `classify_relation(contact_relation)` mapping to REPRESENTATIVE/INFORMANT/UNKNOWN, enforcing that informants are never eligible for `Representative Refused`, and routing ambiguous-informant UNKNOWNs to Review_Only.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 3.8 Write property test for representative exclusivity
    - **Property 4: Representative exclusivity**
    - **Validates: Requirements 8.2, 8.3**

  - [x] 3.9 Implement CSU ranking
    - Implement `csu_rank(client_sentiment, unit_sentiment)` producing the strict total order CP+UP > CP+UN > CN, equal ranks for equal sentiments, and an error indication for missing/unrecognized sentiment.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 3.10 Write unit tests for CSU ranking
    - Cover strict ordering, tie equality, and invalid-sentiment error handling.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 3.11 Implement RFD selection hierarchy
    - Implement `select_rfd(candidates)` applying the primary hierarchy, the Explicit secondary tie-break, latest-timestamp tie-break, and a deterministic identifier tie-break; return no RFD for an empty candidate set without error.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 3.12 Write property test for RFD selection permutation invariance
    - **Property 3: RFD selection permutation invariance**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

  - [x] 3.13 Implement 200-char remark trimming
    - Implement `trim_to_200(contact_person, statement)` returning a `<= 200` char remark preserving both fields, or reporting non-fit so the caller routes to Review_Only without discarding/truncating; route empty/missing-content rows to Review_Only.
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ]* 3.14 Write property test for the 200-char trim
    - **Property 5: 200-char trim preserves required fields or defers**
    - **Validates: Requirements 11.1, 11.2, 11.3**

  - [x] 3.15 Implement noise/DA exclusion classification
    - Implement the exclusion rules that assign Excluded_Row only for defined noise or explicit DA exclusion, never for unmapped accounts, remark overflow, or ambiguous informants.
    - _Requirements: 12.3_

- [~] 4. Checkpoint — Sept 16 deliverables
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Sprint Day Sept 17 — Orchestration, run store, exception queue, rendering, and archive
  - [x] 5.1 Implement the in-memory run store
    - In `persistence/run_store.py`, implement `RunStore` keyed by `run_id`: store/overwrite on completion, retrieve for review/export/download, not-found without mutation, and no durable persistence beyond process lifetime.
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

  - [ ]* 5.2 Write unit tests for the run store
    - Cover store, overwrite, retrieve, not-found, and process-lifetime-only semantics.
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

  - [x] 5.3 Implement the run orchestrator and disposition conservation
    - In `services/orchestrator.py`, implement `process_run(...)` wiring ingestion → resolution → sanitize → extract → hierarchy, assigning each candidate row to exactly one disposition, routing unmapped/overflow/ambiguous rows to Review_Only, defaulting attribution to `"DA"`, and halting on a reconciliation mismatch.
    - Store the resulting `RunResult` in the `RunStore`.
    - _Requirements: 12.1, 12.2, 12.4, 12.5, 1.3_

  - [ ]* 5.4 Write property test for disposition conservation
    - **Property 6: Disposition conservation**
    - **Validates: Requirements 12.1, 12.2, 12.4**

  - [x] 5.5 Wire `/process` to the orchestrator and redirect
    - Connect `POST /process` to `process_run`, redirect to `/review/{run_id}` within 2 seconds on success, and show a processing-failed error (no redirect) when the pipeline fails after Run creation.
    - _Requirements: 2.2, 2.3, 2.8_

  - [x] 5.6 Implement the exception review queue screens
    - Implement `GET /review/{run_id}` (Review_Only rows only, no Clean_Rows; run-not-found error) rendering CH/Account No., Contact & Relation badge, sanitized remark, length counter, editable CSU and RFD dropdowns, and inline approve/exclude controls; implement `POST /review/{run_id}/decision` applying approve/exclude/edit to exactly one valid Review_Only row and persisting CSU/RFD edits, rejecting invalid targets.
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 5.7 Write integration tests for the review queue
    - Use `tmp_path`; cover queue rendering, not-found, single-row decision isolation, invalid-target rejection, and CSU/RFD edit persistence.
    - _Requirements: 13.1, 13.2, 13.4, 13.5, 13.6_

  - [x] 5.8 Implement fixed-template workbook rendering
    - In `services/rendering.py`, implement `render_workbooks(result, template_path, out_dir)` with openpyxl: write only Output_Mapping_Regions in `FIELD RSULT` and `DRR`; preserve formulas, header formatting, and REF/REFERENCE/MASTERLIST sheets; never evaluate formulas server-side; abort with a render failure on missing template or region/dimension mismatch, leaving prior output unmodified; return `bank_csr` and `internal_csr` paths.
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_

  - [ ]* 5.9 Write rendering tests for template preservation
    - Verify mapped-region-only writes, formula/hidden-sheet preservation, and abort-on-failure behavior with a sample template under `tmp_path`.
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_

  - [x] 5.10 Implement the AES-256 archive service
    - In `services/archive.py`, implement `build_encrypted_archive(paths, out_zip, run_month)` with pyzipper AES-256, deriving the 7-char `MONYYYY` password, excluding it from logs/UI/file names, retaining workbooks and reporting a visible failure on archive error, and aborting on an unavailable/invalid run month/year.
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

  - [ ]* 5.11 Write property test for archive secrecy
    - **Property 8: Archive secrecy**
    - **Validates: Requirements 16.2, 16.3**

  - [ ]* 5.12 Write archive round-trip test
    - AES-256 encrypt/decrypt round trip with a fake password under `tmp_path`; assert password absence from any emitted output.
    - _Requirements: 16.1, 16.4, 16.5_

  - [x] 5.13 Implement the export and download center
    - Implement `GET /export/{run_id}` (reconciliation summary: Included, Excluded Noise, Unresolved Exceptions; generate/download controls; run-not-found error) with the generate control disabled while Unresolved Exceptions > 0; implement `POST /export/{run_id}/generate` rendering both workbooks and building the archive only at zero unresolved exceptions (reject otherwise); implement `GET /download/{run_id}/{artifact}` streaming completed artifacts and rejecting incomplete ones; expose exactly the three artifacts.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8_

  - [ ]* 5.14 Write integration tests for export and download
    - Cover summary display, generate gating on unresolved exceptions, successful generation, artifact streaming, incomplete-artifact rejection, and the three-artifact guarantee.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8_

- [~] 6. Checkpoint — Sept 17 deliverables
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Sprint Day Sept 18 — End-to-end dry run and deliverable verification
  - [~] 7.1 Implement an end-to-end pipeline test with historical sample data
    - Add an automated integration test driving upload → process → review decisions → export generation → download using a sanitized historical sample (`Copy_of_DRR_TEMPLATE_UPDATED_FINAL_DRR_September_02-03_.xlsx`) via FastAPI test client and `tmp_path`, asserting the three deliverables and disposition reconciliation.
    - _Requirements: 2.2, 12.4, 14.4, 14.8_

  - [ ]* 7.2 Write deliverable verification assertions
    - Assert clean-row length `<= 200`, Review_Only disposition invariants, template formula preservation, and AES-256 archive integrity in the end-to-end fixture.
    - _Requirements: 11.1, 12.1, 15.3, 16.1_

  - [~] 7.3 Final quality gate
    - Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src`; fix any failures so all three pass cleanly.
    - _Requirements: 17.1_

- [~] 8. Final checkpoint — Sept 18 deliverables
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation sub-tasks are never optional.
- Each task references specific requirement sub-clauses for traceability.
- The 8 correctness properties from `design.md` are implemented as Hypothesis property tests placed close to the code they validate.
- Checkpoints align with the Sept 15–18 sprint boundaries for incremental validation.
- Verification uses `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` per workspace rules.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "1.6"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.7", "1.9", "3.1", "3.3"] },
    { "id": 3, "tasks": ["1.8", "1.10", "1.11", "3.2", "3.4", "3.7", "3.9", "3.11", "3.13", "3.15", "5.1"] },
    { "id": 4, "tasks": ["1.12", "3.5", "3.6", "3.8", "3.10", "3.12", "3.14", "5.2", "5.3"] },
    { "id": 5, "tasks": ["1.13", "5.4", "5.6", "5.8", "5.10"] },
    { "id": 6, "tasks": ["1.14", "5.5", "5.7", "5.9", "5.11", "5.12", "5.13"] },
    { "id": 7, "tasks": ["5.14", "7.1"] },
    { "id": 8, "tasks": ["7.2", "7.3"] }
  ]
}
```
