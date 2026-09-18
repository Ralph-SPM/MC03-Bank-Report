# Requirements Document

## Introduction

The RCBC Initial Demo is an on-demand, local-only vertical slice of the broader
`mc03-bank-report-automation` system, targeted at a live demo on September 18, 2026. It processes
RCBC Auto Loan collection data from three manually uploaded spreadsheets (Volare DRR, Field
Result, Master File) through a deterministic pipeline: date-window filtering, account resolution,
prohibited-marker sanitization, remark extraction, RCBC hierarchy ranking, and auto-approval of
clean records. A Data Analyst reviews only actionable exceptions, then generates two XLSX
workbooks and one AES-256 encrypted ZIP for manual download.

The slice runs as a single local FastAPI process bound to `127.0.0.1:8000` with no authentication,
no sessions, no durable multi-run persistence, and no automatic bank delivery. Run attribution
defaults internally to `"DA"`. These requirements are derived from and governed by the approved
design document (`design.md`); the design remains the authoritative behavioral source. This spec
does not modify the parent `mc03-bank-report-automation` spec files and does not authorize
production deployment or external transmission.

## Glossary

- **Portal**: The local FastAPI web application serving the three demo screens, bound to
  `127.0.0.1:8000`.
- **DA**: Data Analyst; the sole operator of the demo. Default run attribution value.
- **Run**: One on-demand processing execution identified by a `run_id`.
- **RunOrchestrator**: The component that wires and executes the lazy per-run pipeline.
- **RunStore**: The in-memory store mapping `run_id` to a `RunResult`.
- **Volare_Adapter**: The ingestion adapter that loads and filters the Volare DRR upload.
- **Field_Adapter**: The ingestion adapter that loads and filters the Field Result upload.
- **Account_Resolver**: The component that joins Field Result rows to Master File on `CH code`.
- **Remark_Sanitizer**: The component that strips prohibited bank markers from remark text.
- **Remark_Extractor**: The component that parses pipe-tag remarks and falls back to the LLM.
- **Extraction_Gateway**: The provider-neutral LLM boundary that receives PII-minimized input.
- **Hierarchy_Engine**: The component that ranks CSU, classifies relations, selects RFD, and
  trims remarks.
- **Workbook_Renderer**: The component that injects rows into the fixed RCBC template via openpyxl.
- **Archive_Service**: The component that packages workbooks into an AES-256 encrypted ZIP.
- **RuntimeSettings**: The typed startup configuration object using the `MC03_` environment prefix.
- **Report_Window**: The inclusive date range `[report_date_from, report_date_to]`.
- **Clean_Row**: A processed row that is compliant and auto-approved for export.
- **Review_Only**: A processed row that is an actionable exception requiring a DA decision.
- **Excluded_Row**: A row dropped as noise or by explicit DA exclusion.
- **Representative**: A blood relative or immediate family member of the borrower.
- **Informant**: A non-family contact such as neighbor, security guard, barangay official, or
  colleague.
- **CSU**: Collection Status Update, ranked by client/unit sentiment.
- **RFD**: Reason For Delinquency.
- **Output_Mapping_Regions**: The specific writable cell regions in the `FIELD RSULT` and `DRR`
  template sheets.
- **Protected_Storage_Root**: The single validated local storage root under which all persistence
  paths must reside.

## Requirements

### Requirement 1: Local On-Demand Portal

**User Story:** As a DA, I want a local-only web portal with no login, so that I can run the demo on my desktop without authentication or offsite exposure.

#### Acceptance Criteria

1. THE Portal SHALL bind exclusively to the loopback network address `127.0.0.1` on TCP port `8000` and SHALL NOT bind to any non-loopback interface.
2. THE Portal SHALL serve all requests without requiring authentication credentials and without creating or reading user session state.
3. WHEN a Run is created, THE Portal SHALL set the run attribution field to the exact string value `"DA"`.
4. THE Portal SHALL expose exactly three screens and no others: Run Setup and Uploads at path `/`, Exception Review Queue at path `/review/{run_id}`, and Export and Download Center at path `/export/{run_id}`.
5. WHERE the demo is running, THE Portal SHALL NOT mount or expose any public Viber webhook listener endpoint.
6. IF a request is received on any path other than the three defined screen paths, THEN THE Portal SHALL respond with a not-found error response and SHALL NOT alter any Run state.
7. IF a request originates from any non-loopback source address, THEN THE Portal SHALL refuse the connection and SHALL NOT process the request.

### Requirement 2: Run Setup and Upload Intake

**User Story:** As a DA, I want to upload three spreadsheets and choose a date range, so that I can start a processing run.

#### Acceptance Criteria

1. WHEN a DA requests `GET /`, THE Portal SHALL display date inputs for `report_date_from` and `report_date_to`, three file inputs for `volare_drr`, `field_result`, and `master_file`, and a Process control.
2. WHEN a DA submits `POST /process` with `report_date_from`, `report_date_to`, and a readable `.xlsx` file for each of `volare_drr`, `field_result`, and `master_file`, THE RunOrchestrator SHALL execute the pipeline and produce one RunResult identified by a unique `run_id`.
3. WHEN a Run completes successfully, THE Portal SHALL redirect the DA to `/review/{run_id}` within 2 seconds of completion.
4. IF a required upload is missing, is not a `.xlsx` file, or cannot be read as a valid `.xlsx` file, THEN THE Portal SHALL display a field-specific error indicating the affected field and the reason on the Run Setup screen, SHALL retain any previously entered date values, and SHALL NOT create a Run.
5. IF `report_date_from` is later than `report_date_to`, THEN THE Portal SHALL display a date validation error indicating that `report_date_from` must not be later than `report_date_to` on the Run Setup screen, SHALL retain the submitted file selections where the browser permits, and SHALL NOT create a Run.
6. IF any uploaded file exceeds 50 MB, THEN THE Portal SHALL display a field-specific error indicating the size limit was exceeded on the Run Setup screen and SHALL NOT create a Run.
7. IF `report_date_from` or `report_date_to` is missing or is not a valid calendar date, THEN THE Portal SHALL display a field-specific date validation error on the Run Setup screen and SHALL NOT create a Run.
8. IF the RunOrchestrator fails to complete the pipeline after creating a Run, THEN THE Portal SHALL display an error message indicating that processing failed and SHALL NOT redirect the DA to `/review/{run_id}`.

### Requirement 3: Volare DRR Ingestion and Filtering

**User Story:** As a DA, I want the Volare DRR filtered and normalized, so that only relevant, clean collection rows enter the pipeline.

#### Acceptance Criteria

1. WHEN the Volare DRR .xlsx upload is loaded, THE Volare_Adapter SHALL retain only rows whose call date falls within the Report_Window inclusive of both report_date_from and report_date_to.
2. WHEN the Volare DRR .xlsx upload is loaded, THE Volare_Adapter SHALL exclude rows whose status equals one of `BP`, `New`, `Reactive`, `Abort`, `Lock`, `Failed`, or `Field`, using case-insensitive exact matching after trimming leading and trailing whitespace.
3. WHEN the Volare DRR .xlsx upload is loaded, THE Volare_Adapter SHALL substitute negative Viber-status remarks with `Sent notice via Viber` and negative Email-status remarks with `Sent email notice`.
4. WHEN the Volare DRR .xlsx upload is loaded, THE Volare_Adapter SHALL remove rows matching the purge patterns `New Assignment`, `Subspecial`, `System Auto`, and `Predictive`, using case-insensitive exact matching after trimming leading and trailing whitespace.
5. WHEN the Volare DRR .xlsx upload is loaded, THE Volare_Adapter SHALL populate a numeric rank column from the DRR template REF lookup sheet with an integer value in the range 1 to 999999.
6. IF a loaded row has a call date that is missing, empty, or cannot be parsed as a valid date, THEN THE Volare_Adapter SHALL exclude that row from the pipeline and record it as a Review_Only exception indicating an unparseable or missing call date, without terminating the load.
7. IF a loaded row cannot be matched to a rank value in the DRR template REF lookup sheet, THEN THE Volare_Adapter SHALL retain the row, leave the rank column empty, and record a Review_Only exception indicating the missing rank lookup, without terminating the load.
8. IF the Volare DRR .xlsx upload is missing a column required for date, status, remarks, or rank processing, THEN THE Volare_Adapter SHALL halt the load, retain all previously loaded pipeline data unchanged, and return an error indicating which required column is missing.

### Requirement 4: Field Result Ingestion and Filtering

**User Story:** As a DA, I want the Field Result filtered to RCBC Auto Loan within the window, so that only in-scope field visits are considered.

#### Acceptance Criteria

1. WHEN the Field Result is loaded, THE Field_Adapter SHALL retain only rows where the bank column value, after trimming leading and trailing whitespace and applying case-insensitive comparison, equals `RCBC Auto Loan`.
2. WHEN the Field Result is loaded, THE Field_Adapter SHALL exclude rows whose status column value, after trimming leading and trailing whitespace and applying case-insensitive comparison, equals `Cancelled`.
3. WHEN the Field Result is loaded, THE Field_Adapter SHALL retain only rows whose visit date is greater than or equal to the Report_Window start date and less than or equal to the Report_Window end date, with both boundary dates treated as inclusive.
4. WHEN the Field Result is loaded, THE Field_Adapter SHALL preserve the contiguous ordered column range from `CH code` through `OB` inclusive, keeping the original left-to-right column order.
5. IF a required filter column (bank, status, or visit date) is absent from the loaded Field Result, THEN THE Field_Adapter SHALL halt the filtering operation, produce no output file, and return an error indicating the missing column name.
6. IF a row's visit date value is empty or cannot be parsed as a calendar date, THEN THE Field_Adapter SHALL exclude that row from the retained results.

### Requirement 5: Master File Account Resolution

**User Story:** As a DA, I want field rows resolved to account numbers via the Master File, so that export records carry valid accounts and unresolved rows are never lost.

#### Acceptance Criteria

1. WHEN account resolution runs, THE Account_Resolver SHALL left-merge Field Result rows to the Master File on `CH code`, matching case-sensitively on the exact trimmed `CH code` value, to assign an `Account Number`.
2. THE Account_Resolver SHALL treat a Master File row as a valid match only when its status equals exactly one of `RETAIN`, `RESOLVED`, or `PULLOUT` and it carries a non-empty `Account Number`.
3. IF exactly one valid Master File match exists for a Field Result row, THEN THE Account_Resolver SHALL assign that match's `Account Number` to the row.
4. IF a Field Result row has no valid Master File match, THEN THE Account_Resolver SHALL flag the row with reason `UNMAPPED_ACCOUNT` and route it to Review_Only, preserving all original field values unchanged.
5. IF a Field Result row matches more than one valid Master File row on `CH code`, THEN THE Account_Resolver SHALL flag the row with reason `AMBIGUOUS_ACCOUNT` and route it to Review_Only without assigning an `Account Number`.
6. THE Account_Resolver SHALL ensure the count of resolved rows plus the count of rows routed to Review_Only equals the input Field Result row count, dropping no rows.

### Requirement 6: Prohibited-Marker Sanitization

**User Story:** As a compliance owner, I want internal bank markers stripped from remarks, so that exported text contains no prohibited identifiers.

#### Acceptance Criteria

1. WHEN a remark is sanitized, THE Remark_Sanitizer SHALL remove all occurrences of the following prohibited markers: BCAL-prefixed CH codes, L3 labels, INB call-direction markers, OBD call-direction markers, PH codes, `.com` web links, and SRC tags.
2. WHEN a remark is sanitized, THE Remark_Sanitizer SHALL return output in which zero occurrences of any prohibited marker pattern remain.
3. WHEN sanitization is applied to text that has already been sanitized one or more times, THE Remark_Sanitizer SHALL return output character-for-character identical to the output of a single sanitization pass on the original remark.
4. IF the input remark contains no prohibited marker, THEN THE Remark_Sanitizer SHALL return the input remark unchanged, character-for-character.
5. IF the input remark is empty or contains only prohibited markers, THEN THE Remark_Sanitizer SHALL return an empty string.

### Requirement 7: Remark Extraction with PII-Minimized LLM Fallback

**User Story:** As a compliance owner, I want structured remarks parsed deterministically and free text extracted without exposing identifiers, so that borrower data never reaches the model.

#### Acceptance Criteria

1. WHEN a sanitized remark matches the pipe-tag pattern containing the ordered segments "TYPE OF RFD", "RFD", "DETAILED RFD", and "REMARKS" delimited by the pipe character, THE Remark_Extractor SHALL populate the corresponding remark fields directly from the parsed pipe-tag segments without invoking the Extraction_Gateway.
2. WHEN a sanitized remark matches the pipe-tag pattern but one or more of the four expected segments are absent or empty, THE Remark_Extractor SHALL set each absent or empty field to null and populate the remaining fields from the present segments without invoking the Extraction_Gateway.
3. WHEN a sanitized remark does not match the pipe-tag pattern, THE Remark_Extractor SHALL invoke the Extraction_Gateway to extract the four remark fields.
4. WHEN the Extraction_Gateway is invoked, THE Remark_Extractor SHALL exclude account numbers, CH codes, and borrower names from the request payload.
5. IF the Extraction_Gateway returns output that fails schema validation or reports a confidence score below 0.70 on a 0.00 to 1.00 scale, THEN THE Remark_Extractor SHALL set the affected remark fields to null, retain the original sanitized remark text unchanged, and route the row to Review_Only with an indication that extraction failed.

### Requirement 8: RCBC Relation Classification

**User Story:** As a DA, I want contacts classified as representative or informant, so that refusal reasons follow the RCBC blood-relative rule.

#### Acceptance Criteria

1. WHEN a contact relation matches a defined blood-relative or immediate-family term (parent, sibling, spouse, child, aunt/tita, uncle/tito, nephew, niece, or kapamilya), THE Hierarchy_Engine SHALL classify the contact as Representative.
2. WHEN a contact relation matches a defined informant term (neighbor, security guard, barangay official, or colleague), THE Hierarchy_Engine SHALL classify the contact as Informant.
3. WHERE a contact is classified as Informant, THE Hierarchy_Engine SHALL NOT assign the `Representative Refused` classification.
4. IF a contact relation matches no defined blood-relative, immediate-family, or informant term, THEN THE Hierarchy_Engine SHALL classify the contact as UNKNOWN.
5. WHILE a contact is classified as UNKNOWN and meets the ambiguous-informant exception condition, THE Hierarchy_Engine SHALL route the contact to Review_Only.

### Requirement 9: CSU Ranking

**User Story:** As a DA, I want Collection Status Updates ranked consistently, so that the strongest positive sentiment is selected.

#### Acceptance Criteria

1. THE Hierarchy_Engine SHALL rank CSU sentiment into a strict total order such that a client-positive with unit-positive CSU ranks strictly higher than a client-positive with unit-negative CSU, which ranks strictly higher than any client-negative CSU.
2. WHEN CSU values are ranked, THE Hierarchy_Engine SHALL return an integer rank value in which a numerically higher value denotes stronger positive sentiment.
3. WHEN two CSU values share the same client sentiment and the same unit sentiment, THE Hierarchy_Engine SHALL return an equal integer rank value for both.
4. IF a CSU has a client sentiment or unit sentiment that is missing or not one of the recognized values (positive, negative), THEN THE Hierarchy_Engine SHALL reject the CSU and return an error indication identifying the invalid sentiment input without producing a rank value.

### Requirement 10: RFD Selection Hierarchy

**User Story:** As a DA, I want a deterministic RFD winner selected across candidates, so that the export reflects the correct reason regardless of input order.

#### Acceptance Criteria

1. WHEN selecting an RFD from a candidate set containing one or more candidates, THE Hierarchy_Engine SHALL rank candidates by the primary hierarchy in descending priority order Explicit (highest), Borrower Refused, Representative Refused, No Client or Representative Reached, then Moved Out (lowest), and SHALL select the candidate with the highest primary rank.
2. WHERE two or more candidates share the highest primary rank of Explicit, THE Hierarchy_Engine SHALL rank the tied candidates by the secondary tie-break in descending priority order Medical (highest), Diversion of Funds, Delayed Salary, Delayed Collection, Business Slowdown, then Third-Party User (lowest), and SHALL select the candidate with the highest secondary rank.
3. WHERE two or more candidates remain tied after applying the primary and secondary ordering, THE Hierarchy_Engine SHALL select the single candidate whose source timestamp is the most recent (latest chronological value).
4. WHERE two or more candidates remain tied after applying the primary ordering, secondary ordering, and latest source timestamp, THE Hierarchy_Engine SHALL select a single winner by applying a deterministic total-ordering tie-break over candidate identifiers so that exactly one RFD is returned.
5. FOR ALL permutations of a given candidate set, THE Hierarchy_Engine SHALL return an identical single selected RFD.
6. IF the candidate set contains zero candidates, THEN THE Hierarchy_Engine SHALL return no selected RFD and SHALL produce an indication that no RFD was selected, without raising an unhandled error.

### Requirement 11: Remark Trimming to 200 Characters

**User Story:** As a DA, I want remarks trimmed to the field limit without losing required content, so that overflowing rows are reviewed rather than silently truncated.

#### Acceptance Criteria

1. WHEN a remark's combined contact person and statement occupy 200 characters or fewer, THE Hierarchy_Engine SHALL return a remark of at most 200 characters containing both the complete contact person and the complete statement unmodified.
2. WHEN a remark's combined contact person and statement exceed 200 characters but both the complete contact person and the substantive statement can be represented within 200 characters after trimming non-substantive whitespace and separator characters, THE Hierarchy_Engine SHALL return a remark of at most 200 characters that retains both the complete contact person and the complete substantive statement.
3. IF the complete contact person and the substantive statement cannot both be represented within 200 characters, THEN THE Hierarchy_Engine SHALL route the row to Review_Only, retaining both the contact person and the statement fields without discarding, altering, or truncating either field.
4. IF a remark is empty or contains neither a contact person nor a statement, THEN THE Hierarchy_Engine SHALL route the row to Review_Only with an exception indication identifying the missing required content.

### Requirement 12: Disposition Assignment and Conservation

**User Story:** As a DA, I want every candidate row assigned one outcome, so that clean records are auto-approved and nothing is hidden.

#### Acceptance Criteria

1. WHEN the pipeline processes a Run, THE RunOrchestrator SHALL assign each candidate row to exactly one of Clean_Row, Review_Only, or Excluded_Row, such that no candidate row remains unassigned and no candidate row holds more than one disposition.
2. IF a candidate row has an unmapped account, remark content exceeding the configured remark capacity, or an ambiguous informant, THEN THE RunOrchestrator SHALL assign that row to Review_Only.
3. THE RunOrchestrator SHALL assign a candidate row to Excluded_Row only when the row matches a defined noise or DA exclusion rule, and SHALL NOT assign a row to Excluded_Row on the basis of an unmapped account, remark overflow, or ambiguous informant.
4. WHEN the pipeline completes a Run, THE RunOrchestrator SHALL reconcile the disposition counts such that the sum of Clean_Row, Review_Only, and Excluded_Row counts equals the total candidate row count, with a difference of exactly zero.
5. IF the sum of Clean_Row, Review_Only, and Excluded_Row counts does not equal the total candidate row count, THEN THE RunOrchestrator SHALL halt the Run and emit an error indicating a disposition reconciliation mismatch, retaining the candidate row set without modification.

### Requirement 13: Exception Review Queue

**User Story:** As a DA, I want to review only actionable exceptions and decide on each, so that I do not re-review auto-approved records.

#### Acceptance Criteria

1. WHEN a DA requests `GET /review/{run_id}` for an existing Run, THE Portal SHALL display only the Review_Only rows for that Run and SHALL NOT display auto-approved Clean_Rows.
2. IF a DA requests `GET /review/{run_id}` for a run_id that does not exist, THEN THE Portal SHALL display an error message indicating the Run was not found and SHALL NOT display any review rows.
3. WHEN a DA requests `GET /review/{run_id}` for an existing Run with at least one Review_Only row, THE Portal SHALL display for each row the CH/Account No., the Contact & Relation badge, the sanitized remark, a length counter, an editable CSU dropdown, an editable RFD dropdown, and inline approve and exclude controls.
4. WHEN a DA submits `POST /review/{run_id}/decision` with an approve, exclude, or edit action targeting exactly one Review_Only row, THE Portal SHALL apply the selected action to that row only and SHALL NOT alter any other row.
5. IF a DA submits `POST /review/{run_id}/decision` with an action targeting a row that is not a Review_Only row of the given Run, THEN THE Portal SHALL reject the request, SHALL return an error message indicating the target row is invalid, and SHALL leave all rows unchanged.
6. WHERE the DA edits an exception, THE Portal SHALL accept inline changes to the CSU value and the RFD value from their respective dropdowns and SHALL persist the changed values on the targeted row.

### Requirement 14: Export and Download Center

**User Story:** As a DA, I want to generate deliverables and see a reconciliation summary, so that I can download the workbooks and encrypted archive.

#### Acceptance Criteria

1. WHEN a DA requests `GET /export/{run_id}` for an existing Run, THE Portal SHALL display a reconciliation summary showing Included Rows count, Excluded Noise count, and Unresolved Exceptions count, along with controls to generate and download artifacts.
2. IF a DA requests `GET /export/{run_id}` for a run_id that does not exist, THEN THE Portal SHALL reject the request and return an error indicating the Run was not found without displaying a summary.
3. WHILE the reconciliation summary for a Run shows an Unresolved Exceptions count greater than 0, THE Portal SHALL disable the generate control and prevent artifact generation for that Run.
4. WHEN a DA submits `POST /export/{run_id}/generate` for a Run whose Unresolved Exceptions count equals 0, THE Portal SHALL render the bank CSR workbook, render the internal CSR workbook, and build the encrypted archive for that Run.
5. IF a DA submits `POST /export/{run_id}/generate` for a Run whose Unresolved Exceptions count is greater than 0, THEN THE Portal SHALL reject the request and return an error indicating that unresolved exceptions must be zero before generation, without rendering any artifact.
6. WHEN a DA requests `GET /download/{run_id}/{artifact}` for an artifact that has completed generation, THE Portal SHALL stream that artifact for manual download.
7. IF a DA requests `GET /download/{run_id}/{artifact}` for an artifact that has not completed generation, THEN THE Portal SHALL reject the request and return an error indicating the artifact is not available.
8. THE Portal SHALL make exactly three artifacts available for a completed Run: a bank-facing daily CSR workbook, an internal master overall CSR workbook, and an encrypted bank package archive.

### Requirement 15: Fixed-Template Workbook Rendering

**User Story:** As a DA, I want rows injected into the fixed RCBC template, so that formulas and formatting are preserved for Excel to recalculate.

#### Acceptance Criteria

1. WHEN a workbook is rendered, THE Workbook_Renderer SHALL write cell values only within the Output_Mapping_Regions of the `FIELD RSULT` and `DRR` sheets, and SHALL NOT write to any cell outside those regions.
2. WHEN a workbook is rendered, THE Workbook_Renderer SHALL preserve all existing formulas, header cell formatting, and the REF, REFERENCE, and MASTERLIST metadata sheets unchanged from the source template.
3. THE Workbook_Renderer SHALL NOT evaluate any formula server-side, leaving all formula cells with their unevaluated formula expressions for Excel to recalculate on open.
4. IF the fixed RCBC template file is missing or unreadable, THEN THE Workbook_Renderer SHALL abort the render, report a render failure identifying the missing or unreadable template, and SHALL NOT produce any output workbook.
5. IF an Output_Mapping_Region cannot be located in the `FIELD RSULT` or `DRR` sheet, or its cell range dimensions do not match the row data being injected, THEN THE Workbook_Renderer SHALL abort the render, report a render failure identifying the mismatched region, and SHALL NOT write a substitute or partial output.
6. WHEN a render failure is reported, THE Workbook_Renderer SHALL leave any existing output workbook from a prior successful render unmodified.

### Requirement 16: Encrypted Archive Packaging

**User Story:** As a compliance owner, I want deliverables packaged in an AES-256 archive with a predictable password, so that the archive is secure and the password is never exposed.

#### Acceptance Criteria

1. WHEN an archive build is triggered, THE Archive_Service SHALL produce a single ZIP file encrypted with AES-256 that contains all rendered workbooks for the run.
2. THE Archive_Service SHALL derive the archive password as the 3-letter uppercase English month abbreviation (JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC) of the run month immediately followed by the 4-digit calendar year of the run month, producing exactly 7 characters with no separators (for example, `SEP2026`).
3. THE Archive_Service SHALL exclude the archive password value from all log entries, Portal responses, and artifact file names.
4. IF archive creation fails, THEN THE Archive_Service SHALL retain all completed workbooks unmodified and SHALL report an archive failure indication that is visible in the Portal.
5. IF the run month or run year required to derive the password is unavailable or invalid, THEN THE Archive_Service SHALL abort archive creation without producing a ZIP file and SHALL report an archive failure indication that is visible in the Portal.

### Requirement 17: Configuration and Protected Storage

**User Story:** As an operator, I want typed configuration and protected local storage, so that the demo runs safely within validated boundaries.

#### Acceptance Criteria

1. WHEN the application starts, THE RuntimeSettings SHALL load configuration using the `MC03_` environment prefix and the `__` nested delimiter.
2. IF an unknown setting is provided, THEN THE RuntimeSettings SHALL reject startup within 5 seconds with a validation error indicating the unrecognized setting name and SHALL NOT create any persistence directory.
3. WHEN configuration loading completes, THE RuntimeSettings SHALL verify that every configured persistence path resolves to a location at or below the single Protected_Storage_Root.
4. IF any configured persistence path resolves to a location outside the single Protected_Storage_Root, THEN THE RuntimeSettings SHALL reject startup with a validation error identifying the offending path and SHALL NOT create any persistence directory.
5. IF a configured storage path is a UNC path or a known network-mounted path, THEN THE RuntimeSettings SHALL reject the path with a validation error identifying the offending path before any directory is created.
6. WHEN all configured persistence paths are validated as at or below the single Protected_Storage_Root and are neither UNC nor known network-mounted paths, THE RuntimeSettings SHALL create each persistence directory with owner-only read, write, and execute permissions and no access for group or other users.

### Requirement 18: In-Memory Run State

**User Story:** As a DA, I want run results held in memory keyed by run id, so that the demo stays simple without durable multi-run persistence.

#### Acceptance Criteria

1. WHEN a Run completes, THE RunStore SHALL store the RunResult in the in-memory map keyed by its `run_id`.
2. IF a Run completes with a `run_id` already present in the in-memory map, THEN THE RunStore SHALL overwrite the existing RunResult with the newly completed RunResult.
3. WHEN the Portal serves a review, export, or download request for a given `run_id`, THE RunStore SHALL retrieve and return the RunResult mapped to that `run_id`.
4. IF the Portal requests a `run_id` that is not present in the in-memory map, THEN THE RunStore SHALL return a not-found result and SHALL NOT modify any stored RunResult.
5. THE RunStore SHALL NOT persist RunResult state to durable storage, and SHALL retain stored RunResults only for the lifetime of the current process such that no RunResult is available after a process restart.
