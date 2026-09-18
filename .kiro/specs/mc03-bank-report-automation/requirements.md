# Requirements Document

## Introduction

**MC03 Bank Report Automation** is a greenfield, on-premises system that moves daily and weekly external-collections report preparation from manual reconciliation toward automated ingestion, extraction, deterministic validation, and Data Analyst (DA) quality assurance. A Report_Run can contain approximately 50,000 source rows. The initial Campaigns are TFS Auto, RCBC Auto Loan, and CBS Auto; the system must support additional Campaigns through configuration rather than Campaign-specific processing code.

This document defines the requirements phase only. The terms **shall**, **must**, and **required** are normative. Each acceptance criterion is written using one EARS pattern. Items identified as an assumption, review-only condition, open question, or Production_Gate are deliberately not silently resolved by this document.

## Scope and v1 boundaries

In scope are DA-triggered Report_Runs, a separately running Chat_Listener, pluggable source ingestion, configuration-driven Campaign rules, LLM-assisted extraction with strict data minimization, deterministic validation, DA review and approval, XLSX Artifact generation, Campaign-configurable encrypted archives, immutable audit evidence, and the conditional TFS/RCBC Phase 1 decision.

v1 limits TFS processing to main collections and excludes a report scheduler, direct DA editing of Excel workbooks, arbitrary Template upload, automatic bank delivery, TFS attachment capture, Viber message edit/delete tracking, TFS call-efforts/callouts processing, LLM identifier extraction, plate-repository automation, and a Viber fallback lookup chain. A future Campaign or deferred workflow may reuse the shared infrastructure only after its rules and gates are approved.

## Glossary

- **MC03_System**: The MC03 Bank Report Automation application and its required services.
- **Campaign**: A named report program, initially TFS Auto, RCBC Auto Loan, or CBS Auto.
- **Campaign_Configuration**: A versioned external YAML or JSON document that defines one Campaign’s vocabulary, rules, Templates, source settings, export policy, archive settings, validation settings, and supported configuration fields.
- **Config_Parser**: The MC03_System component that validates the syntax and JSON Schema of a Campaign_Configuration and converts the configuration into its validated internal representation and canonical serialized representation.
- **Config_Formatter**: The MC03_System component that emits a canonical, human-readable YAML or JSON representation of a validated Campaign_Configuration.
- **Config_Hash**: A cryptographic digest calculated from the exact immutable canonical serialized bytes of the schema-valid Campaign_Configuration version selected by a Report_Run.
- **Operational_Configuration**: A versioned, approved configuration that defines operational thresholds and intervals, including LLM provider retry and timeout behavior, lock lease and Heartbeat staleness criteria, Viber registration-validation deadline, listener Heartbeat staleness criteria, and repeated-failure thresholds.
- **Report_Processor**: The single shared Python processing pipeline used by all Campaigns.
- **Report_Run**: An immutable, versioned processing attempt initiated by a DA.
- **Report_Job**: The executable work associated with one Report_Run.
- **Report_Window**: The source-date and source-time interval selected for a Report_Run.
- **Data_Analyst (DA)**: A trusted internal user who initiates a run, reviews records, makes decisions, previews/downloads Artifacts, and manually sends approved outputs.
- **Reviewer_Name**: A nonblank name entered by a DA for audit attribution; it is not a verified identity while the DA_Portal has no application authentication.
- **DA_Portal**: The internal web user interface used for run initiation, review, approval, export confirmation, alerts, Artifact preview, and download.
- **Trusted_Internal_Network**: The approved local or private internal network segment that restricts DA_Portal access without relying on application authentication.
- **Offsite_UI_Access**: Any DA_Portal access path that is not confined to the Trusted_Internal_Network, including access through a public tunnel.
- **Access_Boundary**: The network and application routing controls that distinguish public webhook access from private DA_Portal access.
- **Public_Webhook_Route**: The exact public route `/webhooks/viber` used only for Viber callbacks.
- **Session_Authentication**: Authenticated user sessions with secure session cookies, CSRF protection, expiry, logout, and audited actor identity.
- **Source_Adapter**: A pluggable component that acquires and routes records from one source type, such as a Viber callback, a manual file upload, a Volare export, a DRR export, or a future internal source.
- **Chat_Listener**: The always-on service that receives and persists Viber callbacks independently of Report_Runs and Generation_Lock ownership.
- **Raw_Source_Record**: An immutable, durably persisted original source message or original uploaded-file row, retained with its source metadata and content before report transformation.
- **Accepted_Source_Row**: A source row that has been successfully accepted, durably persisted as a Raw_Source_Record, and placed into a Report_Run candidate set.
- **Source_Failure_Event**: A visible source acquisition or parsing failure that prevented a source input from becoming an Accepted_Source_Row.
- **Source_Hash**: A cryptographic digest of an immutable Raw_Source_Record or original uploaded file.
- **Source_Coverage_Assessment**: A DA-recorded assessment of whether a Report_Window has adequate source coverage after a known Source_Gap.
- **Source_Gap**: A time interval in which expected source capture is known to be incomplete, unavailable, or unverified.
- **Metadata_Attachment**: Associating trusted source metadata with a Raw_Source_Record before extraction and rules are evaluated.
- **Remark_Text**: Free-text agent or source commentary subject to extraction and deterministic validation.
- **Structured_Identifier**: An account number, phone number, source identifier, or other structured identifying value; free-text Remark_Text may incidentally contain personal information.
- **Extraction_Gateway**: The provider-agnostic component that creates permitted LLM input and validates structured JSON Schema extraction output.
- **LLM_Provider**: A selectable external or internal model provider used only through the Extraction_Gateway.
- **Redaction_Mode**: The approved LLM-input mode: `raw`, which sends only Remark_Text plus permitted non-identifying Campaign context, or `text-redacted`, which removes Structured_Identifiers from Remark_Text before sending the remaining text plus permitted non-identifying Campaign context.
- **Extraction_Result**: Structured values, field confidence, schema status, provider/model metadata, and Redaction_Mode metadata returned by the Extraction_Gateway.
- **Deterministic_Rule_Layer**: The configuration-driven validation and transformation layer that applies Campaign rules after Metadata_Attachment and Extraction_Result creation.
- **Validation_Finding**: A persisted result identifying a passed rule, failed rule, ambiguity, exclusion, duplicate, missing value, invalid value, configured limit, tie, or required review condition.
- **Review_Record**: A record-level or account-level item displayed to a DA with source context, derived values, findings, decisions, and history.
- **Review_Decision**: A DA action to edit a structured/report value, approve, explicitly accept, exclude, reprocess, or manually resolve an account, with a reason.
- **Approved_Record**: A record approved by a DA for export under the active Campaign_Configuration.
- **Explicitly_Accepted_Record**: A record specifically accepted by a DA for a Partial_Export under the active Campaign_Configuration.
- **Unresolved_Record**: A record with a missing, ambiguous, low-confidence, invalid, unconfirmed, or conflicting outcome that requires DA review.
- **Review_Only**: A disposition that prevents automatic finalization or export until a DA records a resolving Review_Decision.
- **Export_Category**: Exactly one visible category assigned to each Accepted_Source_Row for an export scope: included, excluded, unresolved, or failed.
- **Final_Status**: The Campaign-approved terminal report status.
- **RFD**: A Campaign-approved reason-for-disposition value.
- **Banned_Term**: A Campaign-configured token or pattern that creates a Validation_Finding and review requirement.
- **Generation_Lock**: The combined atomic database lock record and lifetime OS/process/file lock that permits one Report_Job to execute at a time.
- **Lock_Owner**: The Report_Job recorded as holding Generation_Lock after both lock components are acquired.
- **Heartbeat**: A timestamped liveness record emitted by a Lock_Owner or Chat_Listener at an interval defined in Operational_Configuration.
- **Stale_Lock_Evidence**: Evidence, evaluated using approved Operational_Configuration lease or Heartbeat criteria, that a recorded Lock_Owner is no longer live.
- **Export_Workflow**: The DA_Portal process that selects Full_Export or Partial_Export, displays dispositions, obtains explicit confirmation, and produces Artifacts.
- **Full_Export**: An export whose inclusion policy is defined by the selected Campaign_Configuration.
- **Partial_Export**: An export that contains only Approved_Records or Explicitly_Accepted_Records after an explicit DA confirmation.
- **Artifact**: A generated bank-facing XLSX workbook, internal/system-import XLSX workbook, or encrypted archive.
- **Artifact_Hash**: A cryptographic digest of a completed Artifact.
- **Template**: A fixed, internally supplied XLSX workbook approved for a Campaign and Artifact type; v1 does not accept a user-uploaded Template.
- **Output_Mapping_Region**: The approved Template cells to which Workbook_Renderer may write report output values.
- **Workbook_Renderer**: The MC03_System component that creates XLSX Artifacts from fixed Templates using openpyxl.
- **Archive_Service**: The MC03_System component that optionally packages output using the archive settings in Campaign_Configuration.
- **Encryption_Mode**: The configured archive mode `aes_256` or `zipcrypto`.
- **Recipient_Compatibility_State**: The recorded validation state showing whether the intended bank recipient supports a configured Encryption_Mode and approved password format.
- **Audit_Ledger**: Append-only evidence for source lineage, processing, configuration, reviews, runs, Artifacts, errors, and lock ownership.
- **Production_Gate**: A mandatory, recorded approval or evidence condition that must pass before the affected capability is enabled in production.
- **Viber_Eligibility_Gate**: The Production_Gate that determines whether TFS live Viber ingestion can be used in Phase 1.
- **Viber_Webhook_Callback**: A raw callback payload received from Viber on Public_Webhook_Route.
- **Viber_Registration_Validation_Callback**: The Viber callback sent after `set_webhook` to validate registration health at the configured stable Public_Webhook_Route.
- **Viber_Group_Message_Callback**: A Viber_Webhook_Callback arising from an actual TFS group text message.
- **Raw_Payload_Metadata**: The usable source token or message ID, timestamp, sender ID, sender display name, group/channel ID, and text body contained directly in a Viber_Group_Message_Callback.
- **Placeholder_Only_Value**: An empty, whitespace-only, or configuration-listed placeholder value that does not establish usable source metadata.
- **Endpoint_Drift**: A configured Viber registration URL or callback path that differs from the approved reserved hostname and Public_Webhook_Route.
- **Critical_Alert**: A persisted high-severity alert displayed in the DA_Portal and Audit_Ledger.
- **TFS_Account_Resolution**: A DA review process using direct TFS account metadata or a direct approved lookup; it excludes LLM identifier extraction, plate-repository automation, and fallback lookup chains.
- **TFS_L2_L3_Placement**: The unresolved business decision that determines whether an affected TFS record belongs in L2 or L3 placement.
- **Volare_Export**: A report extract supplied by the authorized Volare source.
- **DRR_Export**: A Daily Recovery Report extract supplied by the authorized source.
- **Master_File**: Approved account-resolution data used with an RCBC Volare_Export.
- **Latest_Existing_Monitoring_Row**: The approved latest prior CBS monitoring-sheet row used only after the CBS incremental-cutoff rule is confirmed.
- **Report_Orchestrator**: The MC03_System component that creates, queues, starts, and records Report_Runs.
- **Lock_Manager**: The MC03_System component that owns Generation_Lock acquisition, Heartbeat, release, and interrupted-run recovery.
- **Phase 1**: The first approved live Campaign delivery, which is TFS Auto only if Viber_Eligibility_Gate passes and otherwise RCBC Auto Loan.
- **JSON Schema**: The approved machine-readable structure used to validate Extraction_Result and Campaign_Configuration data.
- **SQLite**: The initial embedded relational persistence engine required for MC03_System.
- **SQLAlchemy**: The persistence interface required between MC03_System and SQLite.
- **Write-Ahead Logging (WAL)**: The required SQLite journal mode that supports the initial persistence concurrency model.
- **OS/Process/File Lock**: A lock held for the lifetime of the executing process in addition to the atomic database lock record.
- **XLSX**: The Excel workbook format used for bank-facing and internal/system-import Artifacts.
- **TLS Endpoint**: A public HTTPS endpoint with approved transport security for the Viber callback.
- **pyzipper**: The required archive writer for Encryption_Mode `aes_256`.
- **ZipCrypto Writer**: A separately vetted archive writer used only when Encryption_Mode is `zipcrypto`.

## Assumptions and review-only conditions

1. **A-01 — Reporting cadence:** “Daily” and “weekly” identify report business periods, not automatic execution. A DA explicitly initiates every v1 Report_Run.
2. **A-02 — Capacity baseline:** The initial acceptance corpus will include at least one Report_Run containing 50,000 source rows. A production hardware, elapsed-time, and memory budget is not yet approved and is a pre-production operational artifact.
3. **A-03 — Internal access:** No application authentication is permitted only while the DA_Portal is confined to the Trusted_Internal_Network. Reviewer_Name provides attribution only in that state.
4. **A-04 — LLM privacy:** Remark_Text may incidentally contain personal information typed in free text. Compliance must approve the permitted Redaction_Mode and LLM_Provider handling before production use.
5. **A-05 — Full export policy:** Each Campaign_Configuration must define exactly which dispositions are eligible for a Full_Export. Until a Campaign owner approves that policy, Full_Export is review-only for that Campaign.
6. **A-06 — TFS vocabulary:** The reported approximately 55 TFS RFD values and their authoritative source artifact are not yet supplied. Affected TFS RFD results remain Review_Only until the artifact is approved.
7. **A-07 — TFS placement:** TFS_L2_L3_Placement is unresolved. Affected records remain Review_Only and receive no inferred or default placement.
8. **A-08 — RCBC values:** The initial RCBC remark cap is expected to be near 200 characters, and the proposed archive password convention is the first three uppercase letters of the report month plus a year representation. The exact integer cap and year representation require Campaign-owner approval.
9. **A-09 — CBS logic:** CBS status, RFD, contact logic, exact cutoff semantics, timezone, and system-wide suitability remain unconfirmed. CBS must not inherit TFS or RCBC rules.
10. **A-10 — Operational artifacts:** Retention duration, backup schedule, recovery objectives, actual Templates, representative sanitized sample remarks, Volare authorization, Master_File ownership, archive recipient compatibility, approved password format, trusted network ranges, approved persistent Viber domain, and Operational_Configuration values are unresolved pre-production artifacts.

## Requirements

### Requirement 1: Configuration-driven shared processing

**User Story:** As a product owner, I want every Campaign to use a single configurable processing capability, so that new Campaign rules do not require Campaign-specific pipeline branches.

#### Acceptance Criteria

1. THE Report_Processor SHALL execute one shared Python processing pipeline for every Campaign selected for a Report_Run.
2. WHEN a schema-valid Campaign_Configuration for a selected Campaign is selected for a Report_Run, THE Report_Processor SHALL obtain Campaign-specific statuses, RFD values, precedence, exclusions, formats, Templates, archive settings, export policy, validation settings, and source settings from that external YAML or JSON configuration.
3. WHEN Config_Parser receives a syntactically valid and JSON Schema-valid Campaign_Configuration, THE Config_Parser SHALL create a validated internal representation, an immutable canonical serialized representation, and a Config_Hash calculated from the exact canonical serialized bytes.
4. IF Config_Parser receives a missing, syntactically invalid, or JSON Schema-invalid Campaign_Configuration, THEN THE MC03_System SHALL block the affected Report_Run before processing, persist a Validation_Finding, and display the configuration error to the DA.
5. WHEN Config_Formatter serializes a validated Campaign_Configuration and Config_Parser parses that serialized representation, THE Config_Parser SHALL return a semantically equivalent Campaign_Configuration with an equivalent semantic value for every Campaign_Configuration field.
6. WHEN a new schema-valid Campaign_Configuration defines a new Campaign using supported configuration fields, THE Report_Processor SHALL make the Campaign selectable for a Report_Run without a Campaign-specific processing branch.
7. IF a requested Campaign behavior cannot be represented by the approved Campaign_Configuration schema, THEN THE MC03_System SHALL classify the behavior as a design review item and prevent unapproved rule execution.

### Requirement 2: DA-triggered runs and capacity accounting

**User Story:** As a DA, I want to explicitly start a report run and see its outcome, so that daily and weekly reporting stays under DA control.

#### Acceptance Criteria

1. THE DA_Portal SHALL provide a DA action that requests a Report_Run for a selected Campaign, selected schema-valid Campaign_Configuration version, valid Report_Window, and nonblank Reviewer_Name.
2. WHEN a DA submits a valid run request, THE Report_Orchestrator SHALL create a new immutable Report_Run with a unique run identifier, selected Campaign, selected Campaign_Configuration identity and version, Config_Hash, requested Report_Window, Reviewer_Name, and request time.
3. IF a run request has an absent or invalid Campaign selection, an absent, unselected, or invalid Campaign_Configuration or Campaign_Configuration version, an absent or invalid Report_Window, or a blank Reviewer_Name, THEN THE DA_Portal SHALL prevent execution and display the required correction for each absent or invalid selection.
4. WHEN no DA has submitted a run request, THE Report_Orchestrator SHALL leave report execution unstarted in v1.
5. WHEN a Report_Run receives up to 50,000 Accepted_Source_Rows, THE MC03_System SHALL persist one visible terminal, Review_Only, or failed outcome for every Accepted_Source_Row.

### Requirement 3: Pluggable ingestion and immutable source preservation

**User Story:** As a DA, I want source records to enter through approved adapters and remain traceable, so that report values can be audited back to their origin.

#### Acceptance Criteria

1. THE MC03_System SHALL support Source_Adapters that route Viber_Webhook_Callback ingestion and manual file upload in v1.
2. WHEN an authorized future source is approved, THE MC03_System SHALL support a configured Source_Adapter for Volare_Export, DRR_Export, or an internal source without changing the shared Report_Processor.
3. WHEN a Source_Adapter accepts a source message or uploaded file row, THE MC03_System SHALL durably persist an immutable Raw_Source_Record with source type, acquisition time, source metadata, source content, and Source_Hash before Metadata_Attachment, extraction, deterministic validation, or report transformation.
4. WHEN a manual file upload is accepted, THE MC03_System SHALL preserve the original uploaded-file identity and original uploaded-file Source_Hash and associate each Accepted_Source_Row with its original uploaded-file identity, original row reference, and Source_Hash.
5. IF a source acquisition or parser error prevents a Raw_Source_Record from being accepted, THEN THE MC03_System SHALL create a visible Source_Failure_Event and Audit_Ledger entry that identify the affected source input and error without silently omitting the source input.
6. WHILE a Report_Job is executing, THE Chat_Listener SHALL continue to persist incoming Viber_Webhook_Callback records independently of that Report_Job and without requiring Generation_Lock.

### Requirement 4: LLM extraction privacy, structure, and uncertainty handling

**User Story:** As a compliance-conscious DA, I want LLM assistance limited to structured remark extraction, so that automated extraction does not expose unnecessary identifiers or make business judgments.

#### Acceptance Criteria

1. THE Extraction_Gateway SHALL expose one provider-agnostic interface for every configured LLM_Provider.
2. WHEN the approved Redaction_Mode is `raw`, THE Extraction_Gateway SHALL send only Remark_Text plus permitted non-identifying Campaign vocabulary or context required by the approved extraction schema and SHALL not send Structured_Identifiers outside permitted raw Remark_Text.
3. WHEN the approved Redaction_Mode is `text-redacted`, THE Extraction_Gateway SHALL remove Structured_Identifiers from Remark_Text before sending the remaining Remark_Text plus permitted non-identifying Campaign vocabulary or context and SHALL not send a Structured_Identifier in redacted Remark_Text or in any other request field.
4. IF an LLM request candidate contains a Structured_Identifier outside permitted raw Remark_Text in `raw` mode or contains a Structured_Identifier in redacted Remark_Text or any other request field in `text-redacted` mode, THEN THE Extraction_Gateway SHALL reject or remove the prohibited field, persist a Validation_Finding, and prevent the prohibited value from being sent to the LLM_Provider.
5. WHEN an LLM_Provider returns an Extraction_Result, THE Extraction_Gateway SHALL validate the result against the approved structured JSON Schema before the result reaches Deterministic_Rule_Layer.
6. IF an Extraction_Result is malformed, schema-invalid, lacks a required field, or has confidence below the active Campaign_Configuration threshold, THEN THE Extraction_Gateway SHALL produce null for each affected extracted field and route the Raw_Source_Record to Review_Only without guessing a replacement.
7. WHEN Extraction_Gateway invokes an LLM_Provider, THE Extraction_Gateway SHALL apply the provider timeout and retry policy from approved Operational_Configuration and record the approved policy, selected LLM_Provider, model identifier, Redaction_Mode, and timeout or retry outcome in the Audit_Ledger.
8. IF a deployment selects an unapproved Redaction_Mode or unapproved LLM_Provider handling, THEN THE Extraction_Gateway SHALL reject the selection, persist a Validation_Finding, and block affected LLM-assisted extraction.
9. WHEN an approved deployment replaces its selected Redaction_Mode, THE Extraction_Gateway SHALL apply only the newly approved Redaction_Mode without changing the provider-agnostic interface or Deterministic_Rule_Layer behavior.
10. WHEN Deterministic_Rule_Layer produces a configured value or Validation_Finding, THE Extraction_Gateway SHALL preserve that deterministic outcome without an LLM_Provider override.
11. IF an LLM_Provider is unavailable, exhausts the approved retry behavior, or returns an unparseable response, THEN THE MC03_System SHALL create a visible extraction failure, route affected records to Review_Only, and produce no replacement extracted or business value.

### Requirement 5: Deterministic Campaign rules and review routing

**User Story:** As a DA, I want consistent rule-based outcomes after extraction, so that ambiguous records are reviewed instead of inconsistently reconciled.

#### Acceptance Criteria

1. WHEN Metadata_Attachment and Extraction_Result are available for a Raw_Source_Record, THE Deterministic_Rule_Layer SHALL apply every rule configured by the selected Campaign_Configuration after those inputs are attached and persist an associated Validation_Finding for each applied rule.
2. WHEN Campaign_Configuration defines status or RFD precedence, tie-breaks, exclusions, character limits, phone formatting, duplicate checks, Report_Window checks, or Banned_Terms, THE Deterministic_Rule_Layer SHALL apply the configured rule and persist its Validation_Finding.
3. IF the selected Campaign_Configuration, a required Campaign rule, vocabulary value, configured limit, or tie-break is missing, unknown, conflicting, or unconfirmed, THEN THE Deterministic_Rule_Layer SHALL set the affected record to Review_Only and record the unresolved condition.
4. IF deterministic validation detects a conflict among candidate extracted values or source metadata, THEN THE Deterministic_Rule_Layer SHALL retain the candidates and findings for DA review and SHALL not use an LLM_Provider to resolve the conflict or make a final judgment.
5. WHEN the same Raw_Source_Record is reprocessed under the same Campaign_Configuration and equivalent approved inputs, THE Deterministic_Rule_Layer SHALL produce the same deterministic values and Validation_Findings.
6. IF duplicate or Report_Window validation identifies a candidate that cannot be resolved by the active Campaign_Configuration, THEN THE MC03_System SHALL prevent silent inclusion and route the candidate to Review_Only.
7. WHILE any CBS Campaign-specific status, RFD, contact, selection, cutoff, or timezone rule is unapproved, THE Deterministic_Rule_Layer SHALL not apply or inherit TFS or RCBC rules in CBS processing and SHALL route affected outcomes to Review_Only.

### Requirement 6: DA review, account context, and decision history

**User Story:** As a DA, I want to review records and account context in the web UI, so that I can resolve exceptions without directly editing Excel.

#### Acceptance Criteria

1. THE DA_Portal SHALL display Review_Record details with record-level context, account-level context, immutable Raw_Source_Record content, derived values, Validation_Findings, and prior Review_Decisions.
2. WHEN a DA submits a structured or report value edit with a nonblank reason and Reviewer_Name or authenticated actor identity, THE DA_Portal SHALL accept the proposed value for recording only when the proposed value meets the active Campaign_Configuration.
3. IF a proposed edited value does not meet the active Campaign_Configuration, THEN THE DA_Portal SHALL reject the proposed value, preserve the prior state, and display the failed validation rules.
4. IF a proposed edit omits a nonblank reason or Reviewer_Name or authenticated actor identity, THEN THE DA_Portal SHALL reject the proposed edit and preserve the prior state.
5. WHEN a validated DA edit is recorded, THE DA_Portal SHALL preserve the immutable Raw_Source_Record and append the prior value, new value, Reviewer_Name or authenticated actor identity, nonblank reason, and decision time to the Audit_Ledger.
6. WHEN a DA submits an approval, explicit acceptance, exclusion, reprocess, or manual resolution for a Review_Record with a nonblank reason and Reviewer_Name or authenticated actor identity, THE DA_Portal SHALL append the Review_Decision to the decision history.
7. IF a submitted Review_Decision omits a nonblank reason or Reviewer_Name or authenticated actor identity, THEN THE DA_Portal SHALL reject the Review_Decision and preserve the prior Review_Record state.
8. WHILE a Review_Record is Review_Only without a resolving Review_Decision, THE Export_Workflow SHALL prevent automatic finalization of that Review_Record.
9. WHEN a DA requests a reprocess action, THE Report_Processor SHALL create only a new processing outcome linked to the prior Review_Record without overwriting the prior outcome or decision history.
10. WHEN a DA uses the reporting workflow, THE DA_Portal SHALL provide review and decision controls for structured/report values rather than a direct XLSX-editing workflow.

### Requirement 7: Private UI and public webhook exposure

**User Story:** As a system owner, I want public exposure limited to the Viber callback, so that internal reports and Artifacts remain private.

#### Acceptance Criteria

1. WHEN a Viber tunnel is enabled, THE Access_Boundary SHALL expose only Public_Webhook_Route on the public tunnel.
2. WHEN a public request targets a route other than Public_Webhook_Route, THE Access_Boundary SHALL block access to the DA_Portal, review queue, Artifacts, API documentation, and administrative routes.
3. WHILE DA_Portal access is confined to the Trusted_Internal_Network and no Offsite_UI_Access exists, THE DA_Portal SHALL record Reviewer_Name as audit attribution without treating Reviewer_Name as verified application identity.
4. WHEN Offsite_UI_Access or tunneled UI access is enabled in any phase, THE Access_Boundary SHALL require Session_Authentication before displaying the DA_Portal or accepting DA actions.
5. WHEN Session_Authentication is required, THE DA_Portal SHALL use secure session cookies, CSRF protection, session expiry, logout, and the authenticated actor identity in Audit_Ledger entries.
6. IF Trusted_Internal_Network restrictions cannot be demonstrated for an unauthenticated DA_Portal deployment, THEN THE MC03_System SHALL block the unauthenticated deployment until Session_Authentication is enabled.

### Requirement 8: Single report-generation lock and crash recovery

**User Story:** As a DA, I want report runs serialized and recoverable, so that concurrent processing cannot corrupt workbooks or audit records.

#### Acceptance Criteria

1. THE MC03_System SHALL use SQLite persistence through SQLAlchemy with Write-Ahead Logging enabled for initial deployment.
2. WHEN a Report_Job transitions into execution, THE Lock_Manager SHALL acquire both an atomic database lock record and a lifetime OS/Process/File Lock before any Report_Job execution, report processing, or workbook generation begins.
3. IF acquisition of either Generation_Lock component fails after the other component was acquired, THEN THE Lock_Manager SHALL release the partially acquired component, record the acquisition failure, and prevent the Report_Job from entering execution.
4. WHEN Lock_Manager grants Generation_Lock ownership after both lock components are acquired, THE Lock_Manager SHALL persist the Lock_Owner, acquisition time, Heartbeat, and Audit_Ledger event.
5. WHILE a Lock_Owner holds Generation_Lock, THE Lock_Manager SHALL permit only the Lock_Owner’s Report_Job to enter execution.
6. WHILE a Lock_Owner holds Generation_Lock, THE Workbook_Renderer SHALL accept workbook-write operations only from the Lock_Owner’s Report_Job.
7. IF a Report_Job that is not the Lock_Owner requests a workbook-write operation, THEN THE Workbook_Renderer SHALL reject the operation and preserve the workbook unchanged.
8. WHEN Lock_Manager queues or rejects a subsequent run request, THE DA_Portal SHALL display the queued or rejected state to the requesting DA.
9. WHEN a Lock_Owner requests release of Generation_Lock after a terminal outcome, THE Lock_Manager SHALL release the database lock record and the OS/Process/File Lock and record the release in the Audit_Ledger.
10. IF Lock_Manager cannot release either Generation_Lock component after a terminal outcome, THEN THE Lock_Manager SHALL record the release failure in the Audit_Ledger and prevent a later Report_Job from entering execution.
11. IF a non-owner requests Generation_Lock release, THEN THE Lock_Manager SHALL reject the request and record the attempted release.
12. WHEN a later Report_Job detects both Stale_Lock_Evidence under approved Operational_Configuration and absence of the prior OS/Process/File Lock, THE Lock_Manager SHALL first mark the prior Report_Run interrupted in the Audit_Ledger, then release the stale database lock record, and only then grant Generation_Lock to the later Report_Job.
13. WHILE a Report_Job holds Generation_Lock, THE Chat_Listener SHALL retain independent source persistence and SHALL not require Generation_Lock to receive Viber_Webhook_Callback records.

### Requirement 9: Explicit export scope and omission visibility

**User Story:** As a DA, I want to choose and confirm export scope with visible counts, so that no record is silently omitted.

#### Acceptance Criteria

1. THE Export_Workflow SHALL require a DA to select exactly one of Full_Export or Partial_Export and record explicit DA confirmation before creating an Artifact.
2. WHEN a DA selects an export scope, THE Export_Workflow SHALL assign exactly one Export_Category to every Accepted_Source_Row and display included, excluded, unresolved, and failed counts for every Accepted_Source_Row together with a separate Source_Failure_Event count before confirmation.
3. WHEN a DA confirms Full_Export, THE Export_Workflow SHALL apply the approved inclusion policy defined by the active Campaign_Configuration.
4. IF a DA selects Full_Export for a Campaign without an approved Full_Export inclusion policy, THEN THE Export_Workflow SHALL prevent Artifact generation and display the unresolved policy gate.
5. WHEN a DA selects Partial_Export, THE Export_Workflow SHALL display the Export_Category counts and separate Source_Failure_Event count before requesting explicit confirmation.
6. WHEN a DA confirms Partial_Export, THE Export_Workflow SHALL include only Approved_Records or Explicitly_Accepted_Records.
7. WHEN a DA confirms Full_Export or Partial_Export, THE Export_Workflow SHALL record a nonblank confirmation reason, Reviewer_Name or authenticated actor identity, and confirmation time.
8. WHEN an export scope includes an excluded, unresolved, failed, or policy-omitted candidate, THE Export_Workflow SHALL display the candidate’s Export_Category, applicable recorded reason and time, and category count before confirmation rather than silently omitting the candidate.

### Requirement 10: Fixed-template XLSX Artifacts

**User Story:** As a DA, I want bank-facing and internal XLSX workbooks generated from controlled Templates, so that reporting format is consistent and auditable.

#### Acceptance Criteria

1. WHEN Export_Workflow has a valid confirmed export scope, THE Workbook_Renderer SHALL generate both a bank-facing XLSX Artifact and an internal/system-import XLSX Artifact from the fixed Templates selected by Campaign_Configuration.
2. THE Workbook_Renderer SHALL use openpyxl to write the XLSX Artifacts.
3. WHEN Workbook_Renderer populates a Template, THE Workbook_Renderer SHALL write only within approved Output_Mapping_Regions and preserve formulas, formatting, hidden sheets, hidden cells, and all Template content outside those regions.
4. WHEN an Artifact contains Template formulas, THE Workbook_Renderer SHALL preserve the formula expressions for recalculation when the Artifact is opened in Excel and SHALL not require a server-side formula calculation engine for Report_Job completion.
5. WHEN a user submits a Template-upload request, THE DA_Portal SHALL reject the request and display that fixed internally supplied Templates are the only v1 Template source.
6. WHEN each bank-facing or internal/system-import XLSX Artifact is completed, THE MC03_System SHALL calculate a distinct Artifact_Hash and record the Artifact type, Template identity, Template version, Config_Hash, and Artifact_Hash in separate Audit_Ledger entries.
7. IF Workbook_Renderer cannot preserve an approved Template or complete an XLSX Artifact, THEN THE MC03_System SHALL fail the affected Artifact visibly and prevent an unrecorded substitute Artifact.
8. WHEN MC03_System creates a substitute XLSX Artifact after an Artifact failure, THE MC03_System SHALL separately validate the substitute against the approved Template, calculate its Artifact_Hash, and record the substitute Artifact type, Template identity, Template version, Config_Hash, Artifact_Hash, and Audit_Ledger entry before the substitute is available for delivery.

### Requirement 11: Encrypted archives and manual delivery

**User Story:** As a DA, I want compatible encrypted output that I can verify and send manually, so that banks receive protected reports without unapproved automatic delivery.

#### Acceptance Criteria

1. WHERE Campaign_Configuration enables archive creation, THE Archive_Service SHALL accept only `aes_256` or `zipcrypto` as Encryption_Mode and SHALL reject any other mode as a configuration error.
2. WHERE Campaign_Configuration does not enable archive creation, THE MC03_System SHALL retain completed XLSX Artifacts and SHALL not require an encrypted archive for that Campaign’s export workflow.
3. WHEN Encryption_Mode is `aes_256`, THE Archive_Service SHALL create the archive using pyzipper with AES-256 encryption.
4. WHEN Encryption_Mode is `zipcrypto`, THE Archive_Service SHALL use a separately vetted ZipCrypto Writer and record that ZipCrypto is a weaker compatibility-only mode.
5. WHEN Campaign_Configuration defines an archive password format, THE Archive_Service SHALL derive the archive password according to that approved format without displaying the password or any derived archive secret in logs, the DA_Portal, Audit_Ledger entries, or file names.
6. WHEN an archive Artifact is completed, THE MC03_System SHALL calculate an Artifact_Hash and record its Encryption_Mode, Recipient_Compatibility_State, and Artifact_Hash separately from each contained XLSX Artifact.
7. WHEN an Artifact is ready, THE DA_Portal SHALL provide DA preview and download actions, SHALL not automatically transmit the Artifact externally, and SHALL wait for manual external transmission by the DA.
8. IF Recipient_Compatibility_State or the archive password format is unapproved for a production-default external delivery, THEN THE Archive_Service SHALL block that production-default archive use and display the compatibility or approval gate.
9. IF Archive_Service fails after completed XLSX Artifacts exist, THEN THE MC03_System SHALL preserve the completed XLSX Artifacts and their Audit_Ledger evidence, create a visible archive failure, and prevent unrecorded substitute archive delivery.
10. WHEN MC03_System creates a substitute archive after an archive failure, THE MC03_System SHALL separately record the substitute archive Encryption_Mode, Recipient_Compatibility_State, Artifact_Hash, and Audit_Ledger evidence before the substitute archive is available for manual download or delivery.
11. IF a substitute archive lacks separate Encryption_Mode, Recipient_Compatibility_State, Artifact_Hash, or Audit_Ledger evidence, THEN THE MC03_System SHALL prevent the substitute archive from being available for download or delivery.

### Requirement 12: Immutable lineage and versioned audit evidence

**User Story:** As an auditor, I want every output value and Artifact to be traceable, so that report decisions can be reconstructed without overwritten history.

#### Acceptance Criteria

1. THE Audit_Ledger SHALL link every structured value and report value to its immutable Raw_Source_Record, source reference, Source_Hash, source Report_Run, Campaign_Configuration version, and Config_Hash.
2. WHEN an LLM_Provider contributes an Extraction_Result, THE Audit_Ledger SHALL record the linked Raw_Source_Record, associated Extraction_Result, LLM_Provider, model identifier, Redaction_Mode, schema-validation result, extraction confidence, approved provider timeout and retry policy outcome, and associated Validation_Findings.
3. WHEN a DA records a Review_Decision, THE Audit_Ledger SHALL link the decision, reason, Reviewer_Name or authenticated actor identity, decision time, prior value, new value when applicable, affected Review_Record, immutable Raw_Source_Record, and source Report_Run.
4. WHEN an Artifact is completed, THE Audit_Ledger SHALL link the Artifact_Hash, Artifact type, Template identity and version when applicable, Config_Hash, selected export scope, Export_Category counts, Source_Failure_Event count, and source Report_Run.
5. WHEN a DA reruns equivalent or changed input, THE MC03_System SHALL create a new immutable Report_Run, linked predecessor and successor processing outcomes, a distinct Artifact record when an Artifact is completed, and linked Audit_Ledger entries without overwriting prior run records or Artifacts.
6. IF an attempted update would alter immutable raw source, completed run evidence, completed Artifact evidence, or retained configuration snapshot evidence, THEN THE MC03_System SHALL reject the update, preserve the original evidence, and record the attempted alteration in the Audit_Ledger.

### Requirement 13: Failure visibility and operational alerts

**User Story:** As a DA, I want failures to be explicit and actionable, so that missing data or invalid output cannot masquerade as a complete report.

#### Acceptance Criteria

1. IF source acquisition, source parsing, LLM extraction, deterministic validation, Workbook_Renderer, Archive_Service, Viber registration, or lock acquisition fails for a Report_Run, THEN THE MC03_System SHALL create a visible failed outcome and an Audit_Ledger entry that identify the affected Report_Run and failed stage.
2. WHEN MC03_System detects a Critical_Alert condition, THE DA_Portal SHALL display the Critical_Alert with affected Campaign, affected service, detection time, and required DA action or Production_Gate.
3. IF a Report_Run contains a source, extraction, validation, rendering, archive, registration, or lock failure, THEN THE Export_Workflow SHALL mark the Report_Run incomplete and display the failed Accepted_Source_Row count and separate Source_Failure_Event count with recorded failure dispositions.
4. WHEN a failure is retried, THE MC03_System SHALL create a linked retry outcome that references the original failure evidence without deleting or overwriting that evidence.

### Requirement 14: TFS Viber eligibility and raw callback evidence

**User Story:** As a Phase 1 owner, I want TFS live chat ingestion enabled only after proven Viber feasibility, so that the first live Campaign is based on reliable and commercial-ready source delivery.

#### Acceptance Criteria

1. WHEN TFS live chat ingestion is proposed for Phase 1, THE Viber_Eligibility_Gate SHALL require recorded evidence of official bot provisioning before enabling TFS live chat ingestion.
2. WHEN TFS live chat ingestion is proposed for Phase 1, THE Viber_Eligibility_Gate SHALL require recorded evidence of every applicable paid commercial tier, commercial term, or business verification before enabling TFS live chat ingestion.
3. WHEN TFS live chat ingestion is proposed for Phase 1, THE Viber_Eligibility_Gate SHALL require recorded evidence of TFS group administrator permission before enabling TFS live chat ingestion.
4. WHEN TFS live chat ingestion is proposed for Phase 1, THE Viber_Eligibility_Gate SHALL require recorded evidence from an actual TFS Viber_Group_Message_Callback demonstrating representative text delivery, a trusted TLS Endpoint, and an approved reserved persistent endpoint before enabling TFS live chat ingestion.
5. WHEN Chat_Listener accepts a Viber_Group_Message_Callback for TFS, THE Chat_Listener SHALL persist Raw_Payload_Metadata derived directly from that callback with a substantive source token or message ID and substantive timestamp, sender ID, sender display name, group/channel ID, and text body.
6. IF Raw_Payload_Metadata has neither a substantive source token nor a substantive message ID, or has a missing, ambiguous, or Placeholder_Only_Value timestamp, sender ID, sender display name, group/channel ID, or text body, THEN THE Chat_Listener SHALL mark the callback ineligible for automated TFS reporting, create a Validation_Finding, and route associated records to Review_Only.
7. WHEN Chat_Listener captures required TFS Raw_Payload_Metadata, THE Chat_Listener SHALL derive the metadata only from the original Viber_Group_Message_Callback and SHALL not use post-hoc metadata enrichment, `get_account_info`, deprecated group/member lookups, or later metadata lookups to enrich the payload.
8. WHEN a Viber_Webhook_Callback includes an attachment or edit/delete event, THE Chat_Listener SHALL classify the event as unsupported TFS text capture in v1, SHALL not treat the event as text capture, and SHALL preserve the event as out-of-scope source evidence.

### Requirement 15: Persistent Viber endpoint, registration, and coverage gaps

**User Story:** As a DA, I want Viber delivery health to be visible and stable, so that a report does not rely on an unverified chat-capture period.

#### Acceptance Criteria

1. WHEN TFS Viber ingestion is enabled, THE Access_Boundary SHALL use an approved reserved persistent hostname or subdomain and the stable Public_Webhook_Route, such as `https://mc03-viber.<approved-domain>/webhooks/viber`.
2. WHEN Chat_Listener restarts, THE Access_Boundary SHALL retain the approved hostname and Public_Webhook_Route and SHALL not substitute a restart-specific callback URL.
3. WHEN a Viber webhook registration is completed or changed, THE Chat_Listener SHALL validate registration health through the received Viber_Registration_Validation_Callback sent after `set_webhook` at the stable Public_Webhook_Route and record the registration validation in the Audit_Ledger.
4. WHEN Viber_Eligibility_Gate evaluates actual-group delivery, THE Chat_Listener SHALL evaluate Raw_Payload_Metadata completeness and reliable representative delivery from Viber_Group_Message_Callback evidence separately from Viber registration-validation evidence.
5. WHEN Viber health is evaluated, THE MC03_System SHALL obtain registration-validation deadlines, repeated-failure thresholds, listener Heartbeat intervals, listener Heartbeat staleness criteria, and all other health thresholds from approved Operational_Configuration.
6. WHEN a tunnel outage, Endpoint_Drift, failed registration, missing Viber_Registration_Validation_Callback by the approved Operational_Configuration deadline, configured repeated-failure threshold, or stale Chat_Listener Heartbeat is detected, THE MC03_System SHALL create a Critical_Alert and display a visible DA_Portal warning.
7. WHILE a Source_Gap overlaps a requested TFS Report_Window, THE Export_Workflow SHALL require a Source_Coverage_Assessment and an explicit DA decision before creating an export.
8. IF a TFS Viber health condition remains unresolved before production activation, THEN THE Viber_Eligibility_Gate SHALL block TFS live chat production activation.

### Requirement 16: TFS rule constraints and unresolved placements

**User Story:** As a TFS DA, I want TFS-specific rules applied only when their source is confirmed, so that account and placement outcomes are not fabricated.

#### Acceptance Criteria

1. WHEN TFS_Account_Resolution is required, THE MC03_System SHALL restrict automated account resolution to direct TFS account metadata or a direct approved account lookup and SHALL not invoke LLM identifier extraction, plate-repository automation, or Viber fallback lookup chains.
2. WHEN TFS_Account_Resolution is required, THE Extraction_Gateway SHALL omit account identification from the permitted TFS extraction schema.
3. IF direct TFS account metadata and a direct approved account lookup do not resolve an account, THEN THE DA_Portal SHALL route the account to Review_Only and allow a DA to record a manual TFS_Account_Resolution with reason and history.
4. IF a TFS record requires Final_Status and the active TFS Campaign_Configuration or Final_Status vocabulary is unapproved, THEN THE Deterministic_Rule_Layer SHALL set the record to Review_Only without applying an inferred or default Final_Status.
5. WHEN a TFS record requires TFS_L2_L3_Placement before the business decision is approved, THE Deterministic_Rule_Layer SHALL set the record to Review_Only without applying an inferred or default placement.
6. WHILE the authoritative TFS RFD source artifact is unapproved, THE Deterministic_Rule_Layer SHALL route any TFS RFD-dependent record to Review_Only.
7. WHEN a TFS Campaign_Configuration and Final_Status vocabulary are approved, THE Deterministic_Rule_Layer SHALL restrict automated TFS Final_Status values to the approved positive/negative and PC, PU, NC, and NU-style vocabulary defined by that configuration.
8. WHEN the authoritative TFS RFD source artifact is approved, THE Deterministic_Rule_Layer SHALL enforce only the approved RFD vocabulary and preserve the artifact identity in Campaign_Configuration evidence.
9. WHEN a TFS skip-tracing remark contains a social-media reference other than the permitted `ST` or `skip tracing` terms, THE Deterministic_Rule_Layer SHALL create a Banned_Term Validation_Finding and route the record to Review_Only.
10. WHEN an approved future TFS call-efforts or callouts configuration is introduced, THE Report_Processor SHALL execute the workflow through the shared processing, ingestion, review, and audit infrastructure.

### Requirement 17: TFS gate failure and RCBC Phase 1 pivot

**User Story:** As a Phase 1 owner, I want an automatic Campaign pivot when live TFS chat is infeasible, so that Phase 1 can continue without unverified bot work.

#### Acceptance Criteria

1. IF Viber_Eligibility_Gate fails because commercial provisioning, group permission, Raw_Payload_Metadata completeness, reliable actual-group delivery, trusted TLS Endpoint, persistent endpoint, or Viber registration validation is absent or unsuccessful, THEN THE MC03_System SHALL select RCBC Auto Loan for Phase 1, mark TFS live chat ingestion deferred, and record the failed gate evidence.
2. WHEN RCBC Auto Loan is selected as the Phase 1 pivot, THE MC03_System SHALL use Volare_Export and approved Master_File account-resolution data as the RCBC source design.
3. WHEN authorized automated Volare_Export access is available, THE Source_Adapter SHALL acquire the RCBC source through the approved automated adapter and record source acquisition evidence.
4. IF authorized automated Volare_Export access is unavailable, THEN THE Source_Adapter SHALL accept the required RCBC Volare_Export through manual file upload and record the fallback condition.
5. WHILE RCBC Auto Loan is the Phase 1 pivot, THE MC03_System SHALL not initiate chat-bot source processing for RCBC Auto Loan.

### Requirement 18: RCBC Campaign rules

**User Story:** As an RCBC DA, I want known RCBC transformations and precedence applied consistently, so that the report follows the approved Campaign hierarchy.

#### Acceptance Criteria

1. WHEN an RCBC record reaches Final_Status selection, THE Deterministic_Rule_Layer SHALL restrict automated Final_Status output to Resolved, Retained, or Deleted as defined by the active RCBC Campaign_Configuration.
2. WHEN multiple RCBC primary RFD candidates are present, THE Deterministic_Rule_Layer SHALL select the highest configured primary rank using this ordered sequence: explicit, borrower refused, representative refused, no contact, then moved out.
3. WHEN multiple explicit RCBC RFD candidates are present, THE Deterministic_Rule_Layer SHALL select the highest configured secondary rank using this ordered sequence: medical expense, diversion of funds, delayed salary, delayed collection, business slowdown, then third-party user.
4. WHEN RCBC candidates remain tied after the ordered primary and explicit-secondary RFD ranking, THE Deterministic_Rule_Layer SHALL select the candidate with the latest source date.
5. WHEN RCBC representative-refused logic is evaluated, THE Deterministic_Rule_Layer SHALL classify only a parent, sibling, spouse, child, aunt, uncle, nephew, or niece as a representative.
6. WHEN an RCBC contact is a neighbor, guard, informant, no-contact outcome, unknown relation, or a person outside the approved blood-relative list, THE Deterministic_Rule_Layer SHALL exclude that contact from representative-refused classification.
7. WHEN RCBC remark output exceeds a character cap and trim policy defined by the active RCBC Campaign_Configuration, THE Deterministic_Rule_Layer SHALL apply the active configured trim policy while preserving the contacted-person and statement fields when both full fields fit within the cap.
8. IF the full contacted-person and statement fields cannot both fit within the active RCBC character cap, THEN THE Deterministic_Rule_Layer SHALL route the record to Review_Only rather than silently discarding either required field.
9. WHEN an RCBC remark contains configured bank-internal marker patterns including BCAL-prefixed CH codes, L3, INB, OBD, PH codes, `.com` links, or SRC, THE Deterministic_Rule_Layer SHALL remove the matched marker content according to the active RCBC Campaign_Configuration and persist an auditable record of the applied transformation.
10. WHEN RCBC archive password generation is enabled, THE Archive_Service SHALL use the approved RCBC password format from Campaign_Configuration and SHALL not expose the password in logs, UI, audit evidence, or file names.

### Requirement 19: CBS provisional configuration

**User Story:** As a CBS DA, I want known CBS selection and exclusions captured without inventing unconfirmed rules, so that the Campaign can be reviewed safely before broader automation.

#### Acceptance Criteria

1. WHILE any CBS selection input, selection walkthrough, incremental cutoff semantic, timezone, system-wide applicability, status, RFD, or contact approval is pending, THE Deterministic_Rule_Layer SHALL treat CBS processing as Review_Only for affected selection, status, RFD, and contact outcomes.
2. WHEN all CBS selection inputs, the selection walkthrough, incremental cutoff semantics, timezone, and system-wide applicability are approved, THE Source_Adapter SHALL select CBS source candidates only from the approved Volare DRR Today and Volare DRR Yesterday inputs plus the approved incremental timestamp cutoff based on Latest_Existing_Monitoring_Row.
3. WHEN a CBS source record has a status of BP, New, Reactive, Aborted, Locked, Unlocked, or SMS Failed that is configured as a CBS status exclusion, THE Deterministic_Rule_Layer SHALL apply the configured CBS status exclusion and record the exclusion finding.
4. WHEN a CBS source record has a remark matching New Assignment, Updates when case, System Auto PD, or New Contact Details that is configured as a CBS remark exclusion, THE Deterministic_Rule_Layer SHALL apply the configured CBS remark exclusion and record the exclusion finding.
5. WHILE any CBS Campaign-specific rule or approval is unapproved, THE Deterministic_Rule_Layer SHALL not copy, apply, or inherit TFS or RCBC rules in CBS processing and SHALL route affected outcomes to Review_Only.

### Requirement 20: Production approval gates and operational artifacts

**User Story:** As a system owner, I want unresolved dependencies recorded as release gates, so that production behavior is approved rather than assumed.

#### Acceptance Criteria

1. WHEN LLM-assisted entry is proposed for production, THE Production_Gate SHALL require documented compliance approval and disclosure for LLM-assisted report entry and external-provider handling of Remark_Text.
2. WHEN LLM-assisted entry is proposed for production, THE Production_Gate SHALL require an approved policy selecting exactly one Redaction_Mode of `raw` or `text-redacted` for the deployed capability, identifying the permitted LLM_Provider handling, and approving the applicable Operational_Configuration provider retry and timeout behavior.
3. WHEN a Campaign is proposed for production export, THE Production_Gate SHALL require approved actual Templates and Output_Mapping_Regions, representative sanitized sample remarks, final Campaign vocabulary, exact Report_Window cutoff and timezone, and an approved Full_Export inclusion policy.
4. WHEN a Campaign depends on Volare_Export, DRR_Export, or Master_File data, THE Production_Gate SHALL require documented source authorization, access method, data owner, and source-coverage expectation for each required source dependency.
5. WHEN an encrypted archive is proposed as a production default, THE Production_Gate SHALL require bank-recipient validation of the supported Encryption_Mode and approved password-format compatibility.
6. WHEN MC03_System is proposed for production, THE Production_Gate SHALL require approved retention duration, backup process, recovery procedure, recovery objectives, capacity baseline evidence, Trusted_Internal_Network boundaries, alert thresholds, lock and listener Heartbeat criteria, registration-validation deadline, and repeated-failure thresholds.
7. IF a required Production_Gate is missing, incomplete, or rejected, THEN THE MC03_System SHALL block the affected production capability and display the gate identifier, gate status, and outstanding or rejected evidence in the DA_Portal.

### Requirement 21: Immutable configuration snapshots and public webhook method isolation

**User Story:** As a system owner, I want selected configuration evidence and the public webhook interface constrained explicitly, so that a completed Report_Run cannot be reinterpreted and the public tunnel cannot expose portal content through an unsupported method.

#### Acceptance Criteria

1. WHEN a Report_Run selects a schema-valid Campaign_Configuration, THE MC03_System SHALL retain an immutable per-Report_Run snapshot of the exact canonical serialized representation and Config_Hash for the selected Campaign_Configuration version.
2. IF a requested update would alter the retained canonical serialized representation or Config_Hash of a Campaign_Configuration version selected by a Report_Run, THEN THE MC03_System SHALL reject the update, append the attempted alteration to Audit_Ledger, and display the rejection to the requesting DA.
3. WHEN a public request targets Public_Webhook_Route using an HTTP method other than POST, THE Access_Boundary SHALL return a rejection response without invoking Chat_Listener webhook processing and without containing DA_Portal, Artifact, API documentation, or administrative content.

## Correctness properties for later property-based tests

The following are test design properties, not implementation choices. Property tests should use in-memory or mocked adapters, LLM providers, lock facilities, archive writers, and Viber callbacks where external-service cost or behavior would otherwise dominate the test.

1. **Configuration round trip:** For every valid generated Campaign_Configuration, `parse(format(configuration))` is semantically equivalent to the original configuration for every configuration field, and the Config_Hash represents the exact immutable canonical serialized bytes selected for the Report_Run.
2. **Configuration isolation:** For equivalent source records in Campaigns selected for Report_Runs, changing a Campaign-specific precedence, exclusion, character cap, archive setting, or export policy changes only outcomes governed by that setting, and a new schema-valid Campaign remains selectable without a Campaign-specific pipeline branch.
3. **LLM request minimization and redaction:** For arbitrary Remark_Text, metadata, and generated Structured_Identifiers, a `raw` request contains only permitted raw Remark_Text plus approved non-identifying Campaign vocabulary/context and no Structured_Identifier outside raw Remark_Text; a `text-redacted` request contains no Structured_Identifier in redacted Remark_Text or elsewhere; and an unapproved Redaction_Mode is rejected.
4. **Extraction uncertainty:** For arbitrary malformed JSON, schema-invalid JSON, omitted required values, confidence values below the active threshold, or provider failures, each affected extracted field is null and the record is Review_Only; the system does not synthesize a replacement extracted or business value.
5. **Deterministic replay:** Given the same immutable raw source, Metadata_Attachment, valid Extraction_Result, Campaign_Configuration, and Report_Window context, repeated deterministic processing produces the same derived values and the same Validation_Findings.
6. **Raw-source immutability and lineage:** Any sequence of edits, approvals, exclusions, or reprocess operations preserves the original Raw_Source_Record and Source_Hash; every uploaded-file row retains its original file identity, file hash, and row reference; and every resulting structured/report value retains a lineage path to that source, Report_Run, and Config_Hash.
7. **Run immutability and rerun linkage:** Reprocessing arbitrary identical or changed input produces a new unique Report_Run with its selected Campaign, configuration version/hash, window, reviewer, and request time, linked successor evidence, and one visible outcome for every accepted row while leaving all prior run, Artifact, and decision records unchanged.
8. **Lock mutual exclusion and recovery:** Across arbitrary interleavings of acquire, Heartbeat, release, crash, and retry events, no Report_Job executes or renders unless it owns both matching Generation_Lock components; at most one Report_Job owns Generation_Lock; partial acquisition is released; non-owner execution, rendering, and release are rejected without changing a workbook; a release failure prevents later execution; and recovery records an interrupted prior run then releases the stale database lock only after both Stale_Lock_Evidence and absence of the prior OS/Process/File Lock.
9. **Export-category conservation:** For arbitrary Accepted_Source_Row dispositions and requested export scopes, exactly one Full_Export or Partial_Export scope is selected, each row has exactly one included, excluded, unresolved, or failed Export_Category, category counts reconcile to the Accepted_Source_Row set, Source_Failure_Events without Accepted_Source_Rows are separately accounted, Full_Export requires an approved policy, and every Partial_Export member is an Approved_Record or Explicitly_Accepted_Record.
10. **Template preservation:** For arbitrary approved output values written to Output_Mapping_Regions, all formula expressions, formatting, hidden-sheet/cell states, and cell contents outside approved mapping regions remain equivalent to the source Template; any substitute Artifact is separately validated, hashed, and audited before delivery.
11. **RCBC precedence permutation invariance:** For arbitrary permutations of the same RCBC candidate set, the ordered primary ranks of explicit, borrower refused, representative refused, no contact, and moved out, followed by the ordered explicit-secondary ranks and then latest-source-date tie-break, produce the same selected RFD; an unlisted, no-contact, or unknown-relation contact never becomes a representative solely through ordering.
12. **RCBC sanitization idempotence:** Applying the configured RCBC internal-marker transformation twice yields the same remark as applying it once; the active configured character cap and trim policy preserve both full required contacted-person and statement fields when possible, and records that cannot preserve both fields remain Review_Only.
13. **TFS callback completeness:** For arbitrary Viber_Group_Message_Callback payloads, a callback is eligible for automated TFS reporting only when a substantive source token or message ID and every other required substantive Raw_Payload_Metadata field originate directly from that callback; missing, ambiguous, Placeholder_Only_Value, or post-hoc-enriched fields produce Review_Only.
14. **Viber validation separation:** For arbitrary mocked Viber callbacks, a valid Viber_Registration_Validation_Callback can establish registration health without an actual-group message, while Raw_Payload_Metadata completeness and reliable actual-group delivery are evaluated only from Viber_Group_Message_Callback evidence and health deadlines or thresholds are read from Operational_Configuration.
15. **CBS exclusion monotonicity:** For arbitrary CBS records, any pending CBS approval keeps affected selection, status, RFD, and contact outcomes Review_Only; after all required approvals, adding a configured CBS excluded status or excluded remark pattern to an otherwise eligible source record cannot increase the set of CBS records eligible for automated inclusion, and no TFS or RCBC rule is inherited.
16. **Artifact audit separation and archive-failure preservation:** For arbitrary completed export runs, each bank-facing XLSX, internal/system-import XLSX, and archive Artifact has its own Artifact_Hash and audit record, no audit or file-name field contains an archive password or derived archive secret, a substitute archive has separately recorded Encryption_Mode, Recipient_Compatibility_State, Artifact_Hash, and audit evidence, and an archive failure preserves completed workbook Artifacts and their prior audit evidence.

## Open questions and required pre-production artifacts

| ID | Required decision or artifact | Owner / approval type | Production effect if unresolved |
| --- | --- | --- | --- |
| OQ-01 | Official Viber bot provisioning, commercial terms or tier, business verification, actual TFS group administrator permission, representative actual-group text-delivery proof, trusted TLS Endpoint, and callback payload sample | Business owner, Viber owner, security | TFS live chat ingestion is blocked; Phase 1 pivots to RCBC Auto Loan. |
| OQ-02 | Reserved persistent approved Viber hostname, stable `/webhooks/viber` registration, post-`set_webhook` registration-validation callback proof, approved registration-validation deadline, listener Heartbeat interval and staleness criteria, repeated-failure threshold, and outage response | Infrastructure and security | TFS live chat ingestion is blocked or affected runs remain Review_Only. |
| OQ-03 | Compliance approval/disclosure for LLM-assisted entry, external-provider handling, provider/model approval, approved provider retry/timeout behavior, and raw-versus-text-redacted policy | Compliance and data privacy | LLM-assisted production extraction is blocked. |
| OQ-04 | Authoritative TFS RFD source artifact, final approximately 55-value vocabulary, precedence, TFS_L2_L3_Placement decision, and final skip-tracing policy wording | TFS business owner | Affected TFS records remain Review_Only. |
| OQ-05 | RCBC exact character cap, Full_Export inclusion policy, final password year representation, Volare access, Master_File owner, and representative sample records | RCBC business owner and data owner | RCBC automation is limited to approved rules and source paths. |
| OQ-06 | CBS walkthrough, status/RFD/contact logic, exact incremental cutoff and timezone, monitoring-sheet interpretation, and confirmation of system-wide applicability | CBS business owner | CBS status/RFD/contact outcomes remain Review_Only. |
| OQ-07 | Approved bank-facing and internal/system-import Templates, Output_Mapping_Regions, versioning, and representative sanitized samples | Report owners | Artifact generation for the affected Campaign is blocked. |
| OQ-08 | Recipient validation for AES-256 or compatibility-only ZipCrypto, approved archive password format, and separately vetted ZipCrypto Writer if needed | Bank recipient, security, compliance | Production archive default is blocked. |
| OQ-09 | Retention duration, backups, restore/recovery procedure, recovery objectives, hardware capacity benchmark for 50,000 rows, lock recovery criteria, and monitoring ownership | Operations and security | Production release is blocked. |
| OQ-10 | Trusted internal network ranges and confirmation that all non-webhook routes remain private; Session_Authentication design if offsite UI is requested | Infrastructure and security | Unauthenticated UI release is blocked; offsite access requires Session_Authentication. |

## Implementation constraints and recommendations

The following records the agreed delivery constraints without turning optional technology recommendations into unreviewed behavior. The Python shared pipeline, external YAML/JSON Campaign configurations, SQLAlchemy with SQLite WAL, the cross-process Generation_Lock, openpyxl workbook rendering, and pyzipper for AES-256 are mandated by the requirements above. Operational intervals, thresholds, and deadlines must be supplied through approved Operational_Configuration rather than unapproved fixed implementation constants. A small trusted on-premises deployment with only Public_Webhook_Route exposed through the Viber tunnel is the required v1 topology.

FastAPI, Pydantic with JSON Schema validation, and pandas are recommended implementation choices because they align with the required web, schema, and tabular-processing capabilities. They do not supersede the observable requirements in this document. No design, implementation plan, or task breakdown is created in this requirements phase.
