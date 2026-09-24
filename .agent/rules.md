# RCBC Auto Loan — CSR Report Business Rules

This file is the authoritative source for how the DA classifies and reports
RCBC Auto Loan collections data. If anything in `reference/rcbc-transcript.md`
or any other example file conflicts with this file, THIS FILE WINS. The
transcript is illustrative only — a live walkthrough of one example, not a
complete or always-precise spec.

Apply these rules in the remarks_lab pipeline (classifier.py, ranker.py,
trimmer.py) and any future campaign work under src/mc03/services/.

---

## 1. Report structure & cadence

- Report name: CSR (Collection Status Report). Two file types: DRR (calls)
  and CSR (field result + calls combined).
- Daily CSR due 3:00 PM every day (includes ongoing overall data).
- Full Overall CSR due every Wednesday 9:00 AM.
- Core reporting principle: capture every field effort AND every call
  effort (DRR) per account, then report only the SINGLE highest/most-
  positive status between the two. Field and DRR are two separate effort
  streams for the same account — compare both, don't treat field as the
  sole source of truth.
  - Fully paid off → "Resolved"
  - Still ongoing → "Retained"
  - Account pulled out → "Deleted" (removed from report)
- Final output file must be password-protected: first three letters of
  the report month, all caps, + year, no separator (e.g. "AUG2026",
  "SEP2026").

---

## 2. Relation Class (who was contacted) — classify this FIRST

This is the single most important classification — CSU and RFD both
depend on it. Priority order:

1. **Borrower** — the client themself, spoken to directly. Detect via
   direct-contact language ("client ptp", "spoke with borrower",
   "client-0-0", etc.) in the remark text.
2. **Representative** — spouse, parent, child, sibling, aunt (tita),
   uncle (tito), nephew/niece, OR ANY in-law. Blood relative OR in-law
   both qualify equally — this is "part of the family" broadly, not
   blood-only. Detect via family-relation keywords in the remark text
   OR the workbook's `TALK TO` / `3RD PARTY LIST` metadata columns.
3. **Informant** — apartment/building guard, neighbor, barangay
   official, or colleague/housemaid. An Informant can NEVER be
   classified as Representative and can NEVER produce a "Representative
   Refused to Disclose" RFD.
4. **None reached** — no contact detected. Do not default to this
   category just because a remark doesn't cleanly match a pattern —
   check for ECA-reference special case below first (Section 4).

Triage order when processing a batch of rows: positive-contact rows
first, then "Unit 0" / negative-unit rows (this flag is unreliable —
fieldmen mark it inconsistently, so always verify against the remark
text rather than trusting the raw flag), then general-negative rows last.

---

## 3. Collection Status Update (CSU)

Hierarchy, highest to lowest:

1. Explicit language: REPO (repo/surrender/pullout), RESOLVED (paid
   off/cleared), or PTP (payment promise) — these override the
   contact-based tiers below when explicitly stated in the remark.
2. Client positive + unit positive (contact made OR residence verified
   per neighbor/observation AND unit seen parked)
3. Client positive + unit negative (residence verified, unit not seen)
4. Client negative (unlocated / unknown in area / moved out)

Notes:
- "With actual contact" (spoke to client directly) ranks above "without
  actual contact" (spoke to representative/neighbor/guard/informant).
- Client unknown → CSU left blank. Client moved out → CSU = "moved out".
- Commitment-to-pay / promise-to-surrender entries have ~1-week
  validity; expired ones become "Expired" (cross-check against the
  BOMA active-accounts file).
- Full descriptive CSU labels used in the actual template (select from
  these, don't invent new phrasing): "CLIENT POSITIVE/UNIT POSITIVE
  (WITH Actual Contact - Client)", "CLIENT POSITIVE/UNIT NEGATIVE
  (WITHOUT Actual Contact)", "CLIENT NEGATIVE/UNIT NEGATIVE (FOR
  FURTHER VISIT/PROBING)", "FRESH ENDORSEMENT/FOR REVIEW".
- CSU and RFD are independent fields. Don't let an RFD/ECA mention drag
  CSU down to RESOLVED unless resolution is explicitly stated elsewhere
  in the remark.

---

## 4. Reason for Default (RFD)

Priority, highest to lowest:

1. **Explicitly stated RFD.** Secondary priority among explicit RFDs:
   medical expense > diversion of funds > delayed salary > delayed
   collection > business slowdown > third-party user. If RFDs tie,
   the latest-dated one wins.
2. **Borrower refused to disclose** (client themself refused, no
   reason given)
3. **Representative refused to disclose** (blood relative/in-law
   refused, no reason given)
4. **No client/representative reached**
5. **Moved out** (lowest priority)

Fuller RFD vocabulary observed in practice (~55 standardized values
exist total; these are the ones confirmed so far): uncooperative,
unemployment, delayed collection, business slowdown, medical expense,
diversion of funds, delayed remittance (from abroad), delayed salary,
scammed, third-party user/loan accommodation, migration (client moved
abroad), arrangement with other ECA/will avail care program.

**Special case — ECA reference:** if a remark states the account is
already being handled by another external collection agency (ECA) with
no other reason given, do NOT default to "no contact reached." This is
a refusal-to-disclose case:
- If the borrower themself was reached → RFD = Borrower Refused to
  Disclose
- If a Representative was reached → RFD = Representative Refused to
  Disclose
- Determine which by applying Section 2's classification to the rest
  of the remark — the ECA mention alone does not tell you who was
  contacted.

**Special case — Pending recon:** client claims they already paid. If
roughly more than one week has passed since that claim with no payment
posted on the bank's side, update/replace the RFD to the actual current
status. (This ~1-week threshold is DA judgment, not a hard system rule
— use it as a reasonable default, not an exact cutoff.)

**Special case — unknown/unlocated:** if client is unknown/unlocated
with no leads obtained, RFD is left blank, and CSU = "client negative,
unit negative, for further visit(ing)".

**Before final RFD assignment:** certain raw client-negative and
representative-contact entries should pass through an intermediate
"under nego" normalization step (reset associated fields like "leave
message to third party") before the final hierarchy comparison is
applied — don't assign a final RFD straight off the raw flag.

**Ongoing maintenance:** the bank periodically adds new call
dispositions. When a raw status value isn't yet in the hierarchy/RFD
reference table, it will show as NA/unmapped — this needs a manual
config update, not a guess. Flag unmapped values rather than silently
defaulting them.

---

## 5. Detailed RFD

Always assemble as a fixed 3-clause string, separated by semicolons, in
this exact order:

```
[RFD clause]; [Contact clause]; [Core statement/promise]
```

Example: `REPRESENTATIVE REFUSED TO DISCLOSE RFD; Contact made with
representative (Mother); Account already resolved per collector/agent`

Never emit a single free-text label instead of the 3-clause structure.
If one clause has nothing specific to report, use the closest
applicable standardized phrase (e.g. contact clause = "No contact made"
if Relation Class is none-reached) — never leave a clause empty or "N/A".

---

## 6. Remark trimming (≤200 characters)

Goal: shorten the remark to keep ONLY who was contacted and what was
discussed. This is a judgment/summarization task, not a truncation
task — never cut on a fixed character index without regard to content.

Process:
1. Detect and preserve any ECA header prefix (e.g. "ECA AUTO_S.P.
   MADRID_HOME_09/02/2026") separately from the body — never let it get
   cut off or included in a hard truncation.
2. Strip internal collector preambles (e.g. "FIELD VISIT RESULT:",
   "NOTE:").
3. Deduplicate repeated phrases/sentences in the body.
4. If combined (header + body) already fits within 200 characters, stop
   — no further trimming needed.
5. If it still exceeds 200 characters, summarize the body via LLM:
   instruction = "shorten to keep who was contacted and what was
   discussed"; target ≤200 total chars, 181 preferred. If the LLM
   output is still too long, retry once with an explicit "shorter"
   instruction.
6. If no LLM is available, fall back to clause-boundary truncation
   (cut at the last complete `; , . -` boundary before the limit) —
   never mid-word, never mid-phrase. This fallback should still leave a
   recognizable remark, not a fragment.
7. Character checker target: ≤200 chars hard limit, ~181 preferred
   (this 181 figure was specifically cited for the DRR/call side —
   apply the same standard to field-result remarks too).

Also strip, from every remark before it reaches the bank-facing file:
BCAL / BKAL (fieldman internal codes), L3 (fieldman follow-up timing
tag), INB / OBD (call-direction tags), CH codes, ".com"/URL links, SRC
tags, phone numbers, email addresses.

---

## 7. Field result & DRR ingestion (upstream of classification)

- Filter source to bank = RCBC Auto Loan, unreported dates only
  (yesterday, or Fri–Sun range if today is Monday), cancelled accounts
  removed.
- Account numbers resolved via CH code against a master/clerk file —
  this file must be current; an out-of-date master file is a known
  recurring failure point, not a code bug.
- DRR: excluded statuses = New, Abort, Reactive, BP, Failed, Lock,
  Field (only real call outcomes kept). Remark replacements: "Email
  neg" → "Sent email notice"; "Viber neg" → "Sent notice via Viber".
  Sort by time (latest first) then status hierarchy (largest to
  smallest) via lookup against the REF sheet's numeric HIERARCHY rank
  — this is a literal table lookup, not a computed/inferred value.
- Master-file cross-check: if an account disappears from the master
  file, it means locked-in or resolved — flag for verification if
  unclear, never assume it's just missing data.
- Raw DRR remarks often arrive semi-structured with pipe-delimited
  labeled segments (e.g. "TYPE OF RFD : ... | RFD : ... | DETAILED RFD
  : ... | REMARKS : ..." with a trailing UNIT:POS/NEG marker and a
  channel tag like "[OBD]"). Parse these directly (deterministic tag
  parsing) rather than routing them through free-text classification —
  a meaningful share of RFD/reason data already arrives pre-labeled.

---

## 8. Final CSR cleanup (after field + DRR merged)

Apply in this order:
1. Remove rows that are N/A or zero (first pass).
2. Any rows STILL showing N/A status after step 1 → set to "Retained"
   (not deleted). This is a two-step sequence: remove-then-default, not
   a contradiction.
3. Remove all date columns except the current report date; center-align.
4. Remove call-type tags (INB/OBD) from remarks.
5. Search and remove any leftover CH code, ".com" link, or SRC tag.
6. Cross-check against the BOMA active-accounts file; remove unmatched
   (N/A) entries from the working sheet.
7. Copy as values (lock formulas).
8. Run the ≤200-char (≤181 preferred) length check on both call and
   field remarks.
9. When merging Daily CSR into Overall CSR: rows present in Daily but
   marked N/A relative to Overall (not yet in Overall) get copied in as
   part of reconciliation — never dropped.

---

## 9. UI/output labeling — keep it DA-legible

The DA reviewing this tool's output is non-technical. Internal/
engineering labels must be translated to plain language before being
shown to her:
- "Predicted vs Manual Truth" → she doesn't produce "manual truth" to
  be predicted against; she just does the work directly. Label this
  comparison as something like "Tool's Answer vs. Your Original Entry."
- "Source: RCBC_Taxonomy" / "Source: PipeTags" → these are legitimate
  internal labels for which rule path a row took (standard deterministic
  engine vs. legacy pre-tagged remark), NOT external citations. Relabel
  for her as e.g. "Matched via: standard rules" / "Matched via:
  pre-tagged remark".
- "kw: meta:mother" / "kw: remark:mother" → legitimate evidence-source
  tags (found in workbook metadata column vs. found in remark text
  itself). Relabel as e.g. "Found in: contact list field" / "Found in:
  remark text".
- CSU and RFD are SELECTED from a fixed standardized vocabulary by the
  DA, not freely typed (except Detailed RFD, which is composed from the
  3-clause structure above). Validate/score tool output as a
  classification-into-fixed-vocabulary problem — an exact standardized
  string match — not free-text similarity.

---

## 10. Known open items / do not silently resolve

- If a raw status or RFD value isn't in the current hierarchy/vocabulary
  config, flag it for manual review — do not guess or silently drop it.
- If `llm_provider` isn't available/configured for trimming, verify the
  non-LLM fallback still produces a readable partial remark (not a
  near-empty fragment) before shipping any output that used the fallback.
- CBS Auto and TFS campaigns have their own distinct rule sets — do NOT
  apply RCBC-specific rules (this file) to those campaigns. Each
  campaign's rules belong in its own config/rules file.
