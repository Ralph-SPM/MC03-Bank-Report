# Implementation Plan: MC03 Bank Report Automation

## Overview

Implement the approved design as a Python 3.12+ on-premises application with immutable evidence, a configuration-driven shared processor, and separate public-listener/private-portal service boundaries. The plan intentionally front-loads Viber feasibility and the Phase-1 RCBC pivot. It implements gate evaluation and safe `Review_Only`/blocked behavior only; it does **not** assume that Viber, LLM, template, campaign-rule, archive, or operational approvals exist.

## Tasks

- [x] 1. Establish the Python service, persistence, and immutable-evidence foundation
  - [x] 1.1 Create the Python project structure and protected runtime configuration boundary
    - Create the pinned Python package manifest, `src/mc03` package layout, application settings models, local protected-storage paths, and test bootstrap.
    - Keep public-listener, private-portal, runner, local database, raw-source, artifact, template, and fixed file-lock paths explicit; reject unsupported network-mounted persistence paths at startup.
    - _Requirements: 7.1–7.2, 8.1, 8.2, 8.13, 20.6_

  - [x] 1.2 Implement SQLAlchemy/Alembic persistence initialization and append-only storage primitives
    - Enable SQLite foreign keys and WAL during initialization; add migrations and repositories for immutable facts, current-state projections, and transactionally appended audit events.
    - Provide database constraints and guarded repository operations that reject updates/deletes to raw sources, selected snapshots, completed evidence, and completed artifact records.
    - _Requirements: 8.1, 12.1, 12.5–12.6, 13.1, 13.4_

  - [x] 1.3 Define shared domain contracts, error codes, and safe audit payload types
    - Add typed identifiers, event/state models, source/run/outcome/artifact value objects, repository protocols, stable DA-visible error contracts, and secret-redaction-safe audit payload builders.
    - Ensure all later services can link values to source, run, configuration hash, actor/service, correlation ID, and time without storing passwords, session values, or prohibited LLM input.
    - _Requirements: 6.5–6.7, 11.5–11.6, 12.1–12.4, 13.1–13.2_

  - [ ]* 1.4 Add persistence bootstrap and immutability repository tests
    - Verify migrations, SQLite WAL/foreign-key initialization, local-volume startup rejection, audit append behavior, and rejected immutable mutations using isolated temporary storage.
    - _Requirements: 8.1, 12.6, 13.1_

- [ ] 2. Implement configuration evidence, production gates, and the Viber-first Phase-1 decision
  - [x] 2.1 Implement the versioned Campaign_Configuration parser, formatter, canonicalizer, and hash store
    - Build Pydantic/JSON Schema validation and safe YAML/JSON parsing that rejects duplicate mappings, unsafe tags, unknown/unsupported fields, syntax errors, invalid cross-field combinations, and unrepresentable behaviors.
    - Create deterministic canonical UTF-8 JSON bytes, SHA-256 Config_Hash values, semantic formatting round trips, immutable configuration-version storage, and generic supported-field discovery without Campaign-specific pipeline branches.
    - _Requirements: 1.1–1.7, 21.1–21.2_

  - [x] 2.2 Implement Operational_Configuration snapshots and capability-scoped GateEvaluator services
    - Model approved/pending/rejected gate evidence, approved operational thresholds, and immutable selection snapshots; make configuration unable to self-approve its gates.
    - Return explicit blocked, deferred, or `Review_Only` decisions for missing LLM compliance/provider/redaction policy, templates/mappings, campaign rules, archive compatibility, source authorization, access, or operational evidence.
    - _Requirements: 4.7–4.9, 7.6, 9.4, 11.8, 15.5–15.8, 20.1–20.7, 21.1–21.2_

  - [x] 2.3 Implement Viber eligibility evaluation and deterministic Phase-1 RCBC pivot planning
    - Evaluate persisted Viber provisioning/commercial/verification, group permission, actual-group delivery, TLS, persistent endpoint, registration-validation, and metadata-completeness evidence separately.
    - When any required Viber evidence is absent, pending, rejected, or failed, append TFS-deferred evidence and select RCBC Auto Loan with Volare_Export plus Master_File design; choose a configured authorized automated Volare adapter or recorded manual-upload fallback, and prohibit RCBC chat-bot processing.
    - Do not treat registration validation as actual-group delivery proof and do not manufacture missing Viber/commercial evidence.
    - _Requirements: 14.1–14.4, 15.1–15.4, 17.1–17.5_

  - [ ]* 2.4 Write property test for canonical configuration and immutable selected snapshots
    - **Property 1: Canonical configuration and immutable selected snapshot**
    - Use Hypothesis with at least 100 examples to verify parse/canonicalize/format/parse equivalence, exact-byte hashing, immutable per-run snapshots, and audited mutation rejection.
    - **Validates: Requirements 1.3, 1.5, 21.1, 21.2**

  - [ ]* 2.5 Write property test for Viber gate failure and the RCBC Phase-1 pivot
    - **Property 15: Phase-1 Viber gate and RCBC pivot behavior**
    - Use generated evidence combinations and RCBC source-availability states to prove every required Viber failure defers TFS, selects the safe RCBC source path, records fallback evidence when required, and never starts RCBC chat processing.
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4, 15.1, 15.2, 15.3, 17.1, 17.2, 17.3, 17.4, 17.5**

  - [ ]* 2.6 Add configuration, gate, and Phase-1 decision integration tests
    - Exercise invalid/unsupported configuration blocking, pending-gate behavior, immutable snapshot selection, and Viber registration-versus-group-delivery evidence separation using in-memory repositories and fakes.
    - _Requirements: 1.4, 1.7, 14.1–14.4, 15.3–15.4, 17.1–17.5, 20.7_

- [ ] 3. Build pluggable source ingestion and strict public/private access isolation
  - [ ] 3.1 Implement SourceAdapter contracts and immutable raw-source/manual-upload acceptance
    - Add streaming Viber, manual file upload, Volare, DRR, internal-source, and Master_File adapter interfaces with source authorization hooks.
    - Persist immutable raw content/reference, metadata, acquisition time, source identity, and Source_Hash before candidate membership, extraction, transformation, or rules; preserve upload file identity/hash/original row reference and create separate Source_Failure_Event evidence for rejected inputs.
    - _Requirements: 3.1–3.5, 12.1, 13.1, 17.2–17.4, 19.2_

  - [ ] 3.2 Implement the independently supervised public Chat_Listener and allow-listed public route
    - Create a minimal public ASGI application that permits only `POST /webhooks/viber`, persists bounded callbacks durably before acknowledgement, emits listener heartbeats, and never acquires Generation_Lock.
    - Reject every other path and every non-POST request to the webhook route before handler invocation, without returning portal, artifact, API-doc, admin, or health-detail content.
    - _Requirements: 3.6, 7.1–7.2, 8.13, 15.1–15.3, 21.3_

  - [ ] 3.3 Implement direct Viber callback classification, health, and source-gap evidence
    - Persist registration-validation callbacks separately from group-message callbacks; derive required TFS Raw_Payload_Metadata exclusively from original callback fields.
    - Route missing, ambiguous, placeholder-only, attachment, edit, and delete events to the required `Review_Only` or out-of-scope evidence path; detect configured endpoint drift, registration deadlines, repeated failures, stale listener heartbeats, and Source_Gaps using Operational_Configuration values only.
    - _Requirements: 13.1–13.3, 14.5–14.8, 15.3–15.8_

  - [ ] 3.4 Implement private-network and offsite-access policy guards
    - Add a route/service policy that keeps portal APIs, review queues, artifacts, documentation, and administrative routes off the public listener; block unauthenticated portal startup when trusted-network restrictions cannot be demonstrated.
    - Expose only an authentication-required portal mode when an offsite/tunneled UI deployment mode is selected; leave session mechanics to task 11.4.
    - _Requirements: 7.1–7.6, 21.3_

  - [ ]* 3.5 Write property test for raw-source preservation, file-row lineage, and failure visibility
    - **Property 4: Raw-source preservation, file-row lineage, and failure visibility**
    - Use generated callbacks, uploads, rows, and parser failures to prove evidence persists before transformation, upload-row lineage remains intact, derived values retain ancestry, and failures never disappear into accepted-row counts.
    - **Validates: Requirements 3.3, 3.4, 3.5, 12.1**

  - [ ]* 3.6 Write property test for Viber callback provenance, health separation, and coverage safety
    - **Property 14: Viber callback provenance, health separation, and coverage safety**
    - Generate Viber callback variants and operational thresholds to prove direct-field provenance, correct unsupported-event treatment, registration/group-delivery separation, and Source_Gap export blocking until assessment and explicit DA decision.
    - **Validates: Requirements 14.5, 14.6, 14.7, 14.8, 15.4, 15.5, 15.6, 15.7, 15.8**

  - [ ]* 3.7 Add public-route, listener-during-run, and source-adapter integration tests
    - Verify all public path/method rejections, durable listener acceptance while a report lock is held, Viber callback classifications, manual-upload lineage, and source-failure visibility with sanitized fixtures.
    - _Requirements: 3.1–3.6, 7.1–7.2, 8.13, 14.5–14.8, 21.3_

- [ ] 4. Implement DA-triggered run orchestration and the two-component Generation_Lock
  - [ ] 4.1 Implement Report_Orchestrator request validation, immutable run creation, and state-event projections
    - Validate Campaign/configuration version/window/Reviewer_Name, create a unique immutable Report_Run, per-run exact configuration and operational snapshots, queued Report_Job, predecessor links, and DA-visible queued/rejected states.
    - Ensure no scheduler creates work in v1 and every accepted row ultimately receives exactly one terminal, failed, or `Review_Only` outcome projection.
    - _Requirements: 2.1–2.5, 8.8, 12.5, 21.1–21.2_

  - [ ] 4.2 Implement Lock_Manager database/file locking, fencing, heartbeat, release, and recovery
    - Acquire a conditional singleton database ownership row and nonblocking lifetime local OS/process/file lock before execution; compensate partial acquisition failures and audit every acquisition, denial, heartbeat, release, and attempted non-owner release.
    - Enforce matching owner/fencing-token checks, fail closed on release failure, and recover only after both approved stale-lock evidence and absent predecessor file lock, recording interruption before stale-record release and successor grant.
    - _Requirements: 8.2–8.5, 8.9–8.12, 13.1_

  - [ ] 4.3 Implement the single Report_Runner job-claim and execution boundary
    - Claim queued jobs, use Lock_Manager before invoking Report_Processor, update append-only run-state events, and prevent HTTP request threads or non-owner jobs from executing report work.
    - Expose an owner/fencing-token capability that Workbook_Renderer can recheck immediately before every write.
    - _Requirements: 2.4, 8.2–8.8, 8.13_

  - [ ]* 4.4 Write property test for immutable run creation and accepted-row coverage
    - **Property 3: Immutable run creation and accepted-row coverage**
    - Generate valid and invalid DA requests plus finite accepted-row sets to prove valid immutable run/job creation, invalid-request non-execution, and one visible outcome per accepted row.
    - **Validates: Requirements 2.2, 2.3, 2.5**

  - [ ]* 4.5 Write state-machine property test for Generation_Lock mutual exclusion and ordered recovery
    - **Property 9: Generation-lock mutual exclusion and ordered recovery**
    - Use a Hypothesis state machine with faked file locks and repositories to generate acquisition, heartbeat, render, release, crash, and recovery interleavings; assert no non-owner execution/write, proper compensation, and dual-evidence recovery order.
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.9, 8.10, 8.11, 8.12**

  - [ ]* 4.6 Add SQLite WAL, local file-lock, and listener-concurrency integration tests
    - Exercise real local SQLite WAL and file-lock behavior, rejected non-owner render/release attempts, stale recovery sequencing, and listener persistence while a runner holds Generation_Lock.
    - _Requirements: 3.6, 8.1–8.13_

- [ ] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement provider-neutral LLM extraction with a hard privacy boundary
  - [ ] 6.1 Implement the Extraction_Gateway provider contract and approval checks
    - Define provider adapters, approved provider/model selection, extraction-schema lookup, confidence thresholds, TFS forbidden identifier fields, and explicit gate failures for unapproved provider handling or redaction modes.
    - Keep provider SDKs and responses outside the deterministic rule layer and prevent provider configuration from deciding business outcomes.
    - _Requirements: 4.1, 4.5, 4.8–4.10, 16.2, 20.1–20.2_

  - [ ] 6.2 Implement allow-listed LLM request construction, redaction, and a second outbound privacy guard
    - Build requests from only Remark_Text and approved non-identifying context; support `raw` and `text-redacted` semantics exactly, remove/reject structured identifiers where required, and never serialize raw callback objects, account IDs, phone numbers, file names, reviewer identity, or artifact metadata.
    - Persist only safe manifests/fingerprints and `LLM_INPUT_PRIVACY_BLOCKED` findings; prohibit sending an unsafe request.
    - _Requirements: 4.2–4.4, 4.8–4.9, 16.2_

  - [ ] 6.3 Implement bounded provider transport, response validation, and uncertainty routing
    - Apply only approved Operational_Configuration timeout/retry policy, record provider/model/mode/policy outcome safely, validate structured JSON results, null malformed/missing/invalid/low-confidence fields, and route affected records to `Review_Only` without repair or replacement values.
    - Preserve deterministic values/findings from provider override and append linked retry/failure evidence.
    - _Requirements: 4.5–4.7, 4.10–4.11, 12.2, 13.1, 13.4_

  - [ ]* 6.4 Write property test for LLM request minimization and redaction safety
    - **Property 5: LLM request minimization and redaction safety**
    - Generate Remark_Text, metadata, identifiers, contexts, modes, providers, and TFS schemas to prove prohibited identifiers never reach a provider request and unapproved selections are blocked.
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.8, 4.9, 16.2**

  - [ ]* 6.5 Write property test for extraction uncertainty, audit policy, and deterministic authority
    - **Property 6: Extraction uncertainty, audit policy, and deterministic authority**
    - Generate malformed/schema-invalid/missing/low-confidence/provider-failure sequences and approved policies to prove null-plus-`Review_Only` behavior, safe audit evidence, and inability to override deterministic results.
    - **Validates: Requirements 4.5, 4.6, 4.7, 4.10, 4.11, 12.2**

  - [ ]* 6.6 Add fake-provider contract and retry-policy integration tests
    - Verify no real provider is called, configured timeout/retry behavior is honored, unsafe payloads are blocked, safe audit fields are emitted, and provider failures remain visible and linked.
    - _Requirements: 4.1–4.11, 12.2, 13.1, 13.4_

- [ ] 7. Implement generic deterministic rules and Campaign-safe behavior
  - [ ] 7.1 Implement the generic Deterministic_Rule_Layer and reusable validation primitives
    - Evaluate selected configuration snapshots against immutable raw source, metadata, extraction, and window context; persist a finding for every applied rule and deterministic derived values/dispositions.
    - Add vocabulary/rank/tie-break/exclusion/limit/format/duplicate/window/banned-term primitives that retain conflicting candidates and route missing, unknown, conflicting, or unresolvable inputs to `Review_Only` without LLM resolution.
    - _Requirements: 1.1–1.2, 5.1–5.6, 13.1_

  - [ ] 7.2 Implement TFS direct-only account resolution and rule gates
    - Restrict automated account resolution to direct callback metadata or approved direct lookup; implement pending vocabulary/RFD/placement review gates and social-media Banned_Term handling.
    - Support future approved TFS call-efforts/callouts only through the shared configuration/processing/review/audit interfaces, never a Campaign-specific pipeline branch.
    - _Requirements: 5.3–5.4, 16.1, 16.3–16.10_

  - [ ] 7.3 Implement RCBC ranking, relationship, sanitization, and cap primitives
    - Apply only configured Resolved/Retained/Deleted status, ordered primary and explicit-secondary RFD ranking, latest-source-date tie breaks, approved representative relations, idempotent marker removal, and configured cap/trim behavior.
    - Route records to `Review_Only` when required contacted-person and statement fields cannot both be retained; preserve applied transformation evidence and defer unapproved source/cap/export/password decisions.
    - _Requirements: 5.2–5.6, 18.1–18.9_

  - [ ] 7.4 Implement CBS isolation, approval gates, and configured exclusions
    - Route all affected CBS selection/status/RFD/contact results to `Review_Only` while any required approval is pending; after approval, support only approved Today/Yesterday DRR plus incremental-cutoff selection and configured status/remark exclusions.
    - Explicitly prevent TFS or RCBC rule inheritance for CBS.
    - _Requirements: 5.7, 19.1–19.5_

  - [ ]* 7.5 Write property test for deterministic replay and safe ambiguity routing
    - **Property 7: Deterministic rule replay and safe ambiguity routing**
    - Generate equivalent immutable inputs, conflicts, duplicate/window cases, and incomplete rules to prove stable values/findings, per-rule findings, retained candidates, and safe `Review_Only` routing.
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

  - [ ]* 7.6 Write property test for TFS safe rule gating and direct-only resolution
    - **Property 16: TFS safe rule gating and direct-only resolution**
    - Generate resolution sources, vocabulary/gate states, placement states, and remarks to prove no fallback/identifier automation/defaults and correct Banned_Term handling.
    - **Validates: Requirements 16.1, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10**

  - [ ]* 7.7 Write property test for RCBC ranking, relationship, sanitization, and cap safety
    - **Property 17: RCBC ranking, relationship, sanitization, and cap safety**
    - Generate candidate permutations, relation values, markers, required fields, and caps to prove invariant ranking, relation allow-list behavior, idempotent sanitization, and safe overflow review routing.
    - **Validates: Requirements 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9**

  - [ ]* 7.8 Write property test for CBS isolation, exclusion, and approved selection behavior
    - **Property 18: CBS isolation, exclusion, and approved selection behavior**
    - Generate CBS records and approval states to prove pending approvals remain `Review_Only`, exclusions are monotonic, approved selection follows only approved inputs, and no cross-Campaign rules are inherited.
    - **Validates: Requirements 5.7, 19.1, 19.2, 19.3, 19.4, 19.5**

- [ ] 8. Compose the shared processing pipeline, review history, and complete lineage
  - [ ] 8.1 Implement Report_Processor as the one streaming, configuration-driven pipeline
    - Process accepted rows in bounded pages/batches through metadata attachment, approved extraction, deterministic rules, and outcome persistence; never branch the pipeline by Campaign identity.
    - Use selected immutable configuration/operational snapshots, preserve every row outcome, route blocked capabilities to `Review_Only`/failed states, and keep the browser out of 50,000-row in-memory processing.
    - _Requirements: 1.1–1.2, 1.6, 2.5, 3.3, 4.1–4.11, 5.1–5.7, 13.1_

  - [ ] 8.2 Implement Review_Record, decision, edit, reprocess, and account-context services
    - Expose immutable raw context, derived values, findings, prior decisions, and account context; validate nonblank reason plus attribution and active configuration before appending edits, approvals, explicit acceptances, exclusions, manual resolutions, or reprocess links.
    - Reject invalid edits/decisions without changing prior state, prevent unresolved automatic finalization, and never add a direct XLSX-editing workflow.
    - _Requirements: 6.1–6.10, 12.3, 12.5–12.6_

  - [ ] 8.3 Complete cross-entity lineage, immutable audit events, and retry links
    - Connect raw source, upload row, configuration/run snapshots, extraction attempts/results, rule findings, decisions, export classifications, artifacts, lock events, alerts, and retry/successor outcomes through append-only Audit_Ledger events.
    - Implement attempted-alteration rejection/auditing and ensure completed runs/artifacts and raw data cannot be overwritten.
    - _Requirements: 3.3–3.5, 4.7, 6.5, 8.4, 8.9–8.12, 10.6–10.8, 11.6, 12.1–12.6, 13.4, 21.2_

  - [ ]* 8.4 Write property test for configuration-driven processing and safe unsupported behavior
    - **Property 2: Configuration-driven processing and safe unsupported behavior**
    - Generate supported Campaign configurations and equivalent source contexts to prove the shared pipeline remains generic, governed configuration changes are isolated, new supported Campaigns remain selectable, and invalid/unsupported behavior cannot execute.
    - **Validates: Requirements 1.1, 1.2, 1.4, 1.6, 1.7**

  - [ ]* 8.5 Write property test for review-decision validity and append-only reprocessing
    - **Property 8: Review-decision validity and append-only reprocessing**
    - Generate proposed edits, decisions, records, and configurations to prove acceptance criteria, full history append behavior, unresolved-finalization prevention, and linked successor outcomes without mutation.
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 12.3, 12.5, 12.6**

  - [ ]* 8.6 Add bounded-processing, repository, and reprocess integration tests
    - Use sanitized fixtures including a 50,000-row run to verify paged persistence, outcome coverage, immutable rerun lineage, source-failure separation, and no direct dependence on real LLM or external sources.
    - _Requirements: 2.5, 3.3–3.5, 6.9, 12.1, 12.5, 13.4_

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Implement confirmed export, controlled XLSX rendering, and optional encrypted archives
  - [ ] 10.1 Implement Export_Workflow classification, coverage gates, and attributed confirmation
    - Require exactly one Full_Export or Partial_Export selection; build immutable classifications assigning one category to every accepted row, separate Source_Failure_Event counts, visible reasons/times, and nonblank attributable confirmation.
    - Enforce approved Full_Export policy, Partial_Export membership restrictions, and TFS Source_Gap assessment/explicit decision gates before creating an export snapshot.
    - _Requirements: 6.8, 9.1–9.8, 13.3, 15.7_

  - [ ] 10.2 Implement TemplateRepository and owner-checked Workbook_Renderer
    - Resolve only fixed approved template identity/version/mapping regions, reject upload requests, recheck lock owner/fencing token before writes, and use openpyxl to produce separate bank-facing and internal/system-import workbooks.
    - Preserve formulas, formatting, hidden sheets/cells, and all content outside approved mappings; independently validate/hash/audit each normal or substitute XLSX before it becomes available.
    - _Requirements: 8.6–8.7, 10.1–10.8, 12.4, 20.3_

  - [ ] 10.3 Implement Archive_Service with a secret-safe, gate-aware archive boundary
    - Support only disabled, `aes_256` via pyzipper, or a separately injected vetted ZipCrypto adapter; record ZipCrypto as compatibility-only and block all other modes.
    - Derive passwords behind a non-projecting secret interface, gate production-default use on recipient/password-format compatibility, preserve completed XLSX evidence on archive failure, and independently hash/audit each archive or substitute archive before download eligibility.
    - _Requirements: 11.1–11.11, 12.4, 18.10, 20.5_

  - [ ]* 10.4 Write property test for export-category conservation and confirmation safety
    - **Property 10: Export-category conservation and confirmation safety**
    - Generate accepted-row outcomes, source failures, policies, scopes, and confirmations to prove exactly one scope, complete/disjoint category accounting, policy gates, constrained partial membership, and attributed visible omissions.
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 9.8, 13.3**

  - [ ]* 10.5 Write property test for controlled-template preservation and separate XLSX evidence
    - **Property 11: Controlled-template preservation and separate XLSX evidence**
    - Generate approved mappings and output values to prove only mapped cells change, protected template characteristics remain equivalent, separate artifact records/hashes exist, and substitute workbooks stay unavailable until independently validated and audited.
    - **Validates: Requirements 10.1, 10.3, 10.4, 10.6, 10.7, 10.8, 12.4**

  - [ ]* 10.6 Write property test for archive allow-list, secrecy, and workbook preservation
    - **Property 12: Archive allow-list, secrecy, and workbook preservation**
    - Generate archive configurations, gate states, secret values, completed XLSX sets, and failures to prove mode allow-listing, no secret projection, compatibility gating, completed-workbook preservation, and complete substitute evidence requirements.
    - **Validates: Requirements 11.1, 11.5, 11.6, 11.8, 11.9, 11.10, 11.11, 18.10**

  - [ ]* 10.7 Add template, artifact, and archive integration tests with sanitized fixtures
    - Verify openpyxl mapping/formula/format/hidden-state preservation, renderer owner rejection, normal/substitute artifact hashes and audit links, AES-256 archive round trips with fake secrets, archive-failure preservation, and no automatic delivery.
    - _Requirements: 8.6–8.7, 10.1–10.8, 11.1–11.11, 12.4_

- [ ] 11. Implement the private DA portal and authenticated offsite mode
  - [ ] 11.1 Implement private portal run, status, alert, and gate APIs/pages
    - Build private-only DA actions for configuration selection, valid run request, queued/rejected/current status, Critical_Alerts, gate status/outstanding evidence, and safe error display.
    - Ensure public applications cannot route to these endpoints and unauthenticated private mode remains conditional on demonstrable trusted-network restrictions.
    - _Requirements: 2.1–2.4, 7.1–7.6, 8.8, 13.2, 20.7_

  - [ ] 11.2 Implement review, account-context, decision, and template-upload-rejection portal controls
    - Render immutable source context, derived values, findings, history, and valid edit/decision/reprocess controls; show failed validation rules while preserving prior state.
    - Omit direct XLSX editing and reject all template-upload requests with the fixed-template v1 policy.
    - _Requirements: 6.1–6.10, 10.5, 12.3_

  - [ ] 11.3 Implement private export confirmation, artifact preview, and manual-download controls
    - Display category/source-failure counts, candidate reasons/times, coverage and policy gates, confirmation attribution, and completed artifact availability.
    - Allow preview/download only after all evidence checks pass and provide no automatic external transmission capability.
    - _Requirements: 9.1–9.8, 10.6–10.8, 11.7, 11.9–11.11, 13.3_

  - [ ] 11.4 Implement Session_Authentication for enabled offsite/tunneled portal mode
    - Add secure session cookies, CSRF protection, expiry, logout, authenticated actor audit attribution, and startup/configuration enforcement that prevents Reviewer_Name-only offsite access.
    - _Requirements: 7.3–7.6, 12.3_

  - [ ]* 11.5 Add portal API/browser tests for private access and DA workflows
    - Cover request validation, record details/history, rejected edits/decisions, queued state, alerts, export preconfirmation counts, template-upload rejection, manual download only, and authenticated offsite session/CSRF/logout behavior.
    - _Requirements: 2.1–2.4, 6.1–6.10, 7.1–7.6, 8.8, 9.1–9.8, 10.5, 11.7_

- [ ] 12. Harden failures, health projections, and production-gate enforcement
  - [ ] 12.1 Implement stage-specific failure, retry, alert, and incomplete-run projections
    - Normalize source, parsing, extraction, validation, render, archive, Viber registration, and lock failures into visible stage-specific outcomes, linked retry facts, incomplete-run counts, Critical_Alerts, and safe DA action guidance.
    - Preserve original failure evidence; never mark a run complete when required audit evidence cannot be appended.
    - _Requirements: 3.5, 4.11, 8.3, 8.10, 10.7–10.8, 11.9–11.11, 13.1–13.4_

  - [ ] 12.2 Implement startup and capability-entry gate enforcement
    - Invoke GateEvaluator at configuration approval, service startup, run request, source activation, LLM request construction, export confirmation, archive creation, and release-validation code paths.
    - Keep unresolved Viber/commercial, LLM compliance/provider/redaction, templates/mappings, campaign rules, archive compatibility/password format, source authorization, trusted-network/authentication, retention/recovery/capacity, and operational-threshold matters as explicit blocked or `Review_Only` outcomes only.
    - _Requirements: 4.8, 7.6, 9.4, 11.8, 15.8, 16.4–16.8, 18.7–18.10, 19.1–19.5, 20.1–20.7_

  - [ ]* 12.3 Write property test for failure and retry evidence preservation
    - **Property 13: Failure and retry evidence preservation**
    - Generate failures for every defined stage and retries to prove stage-identified visible failure/audit evidence and linked successor retries without mutation or deletion of original evidence.
    - **Validates: Requirements 13.1, 13.4**

  - [ ]* 12.4 Write property test for capability-specific production-gate denial
    - **Property 19: Capability-specific production-gate denial**
    - Generate requested capabilities and evidence maps to prove pending/incomplete/rejected gates block only their affected capability, surface safe outstanding evidence, and cannot be bypassed by schema validity, configuration defaults, or Reviewer_Name.
    - **Validates: Requirements 7.6, 11.8, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7**

  - [ ]* 12.5 Add automated security and startup-gate smoke tests
    - Test public allow-list/method isolation, absent public docs/portal/artifact routes, private-network/auth startup behavior, local-WAL assertion, unresolved production-gate blocks, and safe error/alert projection without secrets.
    - _Requirements: 7.1–7.6, 8.1, 11.5, 15.1–15.8, 20.1–20.7, 21.3_

- [ ] 13. Wire the services together and verify the integrated implementation
  - [ ] 13.1 Compose public listener, private portal, runner, migrations, and local service entry points
    - Wire the final application factories and dependency injection so the public listener owns only the exact webhook endpoint, the private portal owns DA workflows, the runner owns report execution, and both use shared immutable repositories safely.
    - Connect configuration/gate snapshots, Phase-1 decision, ingestion, lock, processing, review, export, render, archive, audit, alerts, and manual-download availability into one end-to-end code path without adding a scheduler, direct XLSX editing, template upload, or automatic delivery.
    - _Requirements: 1.1, 2.4, 3.1–3.6, 7.1–7.6, 8.1–8.13, 9.1–9.8, 10.1–10.8, 11.1–11.11, 12.1–12.6, 13.1–13.4, 17.1–17.5, 20.1–20.7, 21.1–21.3_

  - [ ]* 13.2 Add automated cross-service integration coverage for the final wiring
    - Use fakes and sanitized fixtures to execute the RCBC-pivot path, gated TFS callback path, private DA review/export path, lock-aware rendering path, archive-failure preservation path, and full audit-lineage assertions without contacting real Viber, LLM, banks, or production storage.
    - _Requirements: 2.5, 3.6, 7.1–7.6, 8.2–8.13, 9.1–9.8, 10.1–10.8, 11.1–11.11, 12.1–12.6, 13.1–13.4, 14.1–14.8, 15.1–15.8, 17.1–17.5_

- [ ] 14. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks. Core implementation tasks are not optional.
- Each numbered correctness property from the design has exactly one dedicated Hypothesis task. Use `@settings(max_examples=100)` or a higher approved value, one property/comment tag per test, and fakes or in-memory adapters rather than real LLM, Viber, recipient, or production services.
- Gate-related work records/evaluates evidence and implements fail-closed behavior; it does not fabricate or approve Viber/commercial, LLM compliance, template/mapping, campaign-rule, archive-compatibility, or operational evidence.
- The tasks intentionally end by wiring components into private/public service boundaries. They do not authorize deployment, bank delivery, external evidence gathering, or implementation outside this task plan.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "2.1", "3.1", "4.2"] },
    { "id": 3, "tasks": ["2.2", "3.2", "3.5", "4.5"] },
    { "id": 4, "tasks": ["2.3", "3.3", "3.4", "4.1"] },
    { "id": 5, "tasks": ["2.4", "2.5", "2.6", "4.3", "4.4", "6.1"] },
    { "id": 6, "tasks": ["3.7", "4.6", "6.2", "7.1"] },
    { "id": 7, "tasks": ["6.3", "6.4", "7.2", "7.3", "7.4", "7.5"] },
    { "id": 8, "tasks": ["6.5", "6.6", "7.6", "7.7", "7.8"] },
    { "id": 9, "tasks": ["8.1"] },
    { "id": 10, "tasks": ["8.2", "8.3", "8.6"] },
    { "id": 11, "tasks": ["8.4", "8.5", "10.1"] },
    { "id": 12, "tasks": ["3.6", "10.2", "11.1", "12.1"] },
    { "id": 13, "tasks": ["10.3", "10.4", "10.5", "11.2", "11.4", "12.2", "12.3"] },
    { "id": 14, "tasks": ["10.6", "10.7", "11.3", "12.4", "12.5"] },
    { "id": 15, "tasks": ["11.5", "13.1"] },
    { "id": 16, "tasks": ["13.2"] }
  ]
}
```
