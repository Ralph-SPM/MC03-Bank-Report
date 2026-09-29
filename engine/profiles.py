"""LLM Prompt Profiles & Configuration Management for RCBC Remark Intelligence.

Provides persistent storage, profile switching, custom directives editing,
model selection (English vs Tagalog), and full prompt preview rendering.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

PROFILES_FILE = Path("storage/prompt_profiles.json")

DEFAULT_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

RULE 1: CONTACT ENTITY CLASSIFICATION & BASELINE RFD:
- COMPLETED REPOSSESSION: If remark indicates unit repossessed / surrendered ('Done repo', 'successfully repossessed', 'repo unit') -> RFD MUST BE 'BORROWER REFUSED TO DISCLOSE RFD' and CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'.
- ACTUAL CONTACT - BORROWER: Direct contact with borrower (in person, deep skip, or phone/transfer).
  * If a Promise to Pay (PTP) is arranged ('ptp as per client talk to agent', 'ch is ptp') -> CSU is STRICTLY 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'! NEVER use 'With Commitment to Pay' as a CSU! Baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.
  * If client is met in person (including deep skip to workplace/college) and discusses surrender, settlement, or payment -> CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE' if unit explicitly not at premises), baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.
  * CLAIMED SETTLED / PAYMENT DISPUTE EXCEPTION: If the client refuses to talk or discuss settlement specifically because they claim the account is already settled, paid, or requires bank reconciliation ('claiming already settled long ago', 'advise talk to bank where unit was purchased', 'disputing balance/payments') -> BOTH RFD AND CSU MUST BE 'PENDING RECON' (strictly overrides 'BORROWER REFUSED TO DISCLOSE RFD').
  * Note on borrower identification: Always check the Contact Person / CH name. If the remark states 'as per ch [Name]' and that name matches the borrower, the person interviewed is the BORROWER himself, NOT an informant!
- ACTUAL CONTACT - FAMILY REPRESENTATIVE:
  * Representatives are STRICTLY family members / relatives (spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, niece, nephew, relative, in-law, brother-in-law).
  * If a family representative is interviewed and gives general info, states client is not around / at work / out of area (or in Dubai, Batangas, Sorsogon, rarely visits), or refuses to give contact info -> baseline RFD is STRICTLY 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * NEVER assign 'NO CLIENT/ REPRESENTATIVE' when a family representative was reached! 'NO CLIENT/ REPRESENTATIVE' is ONLY for informants or unopened doors.
  * WORK RELOCATION vs REP REFUSED: Assign 'WORK RELOCATION' ONLY when there is clear confirmation that the borrower has permanently relocated employment to another province/region (e.g., 'working in Palawan and rarely visits'). General statements of working away (Dubai, Batangas, out of area, arrives 7-8pm) without permanent relocation keep the baseline 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * UNIT SIGHTING WITH REPRESENTATIVE: If the unit is NOT physically seen at the premises (or unit is carnapped, or surrendered/VS to another ECA, or at repair shop), CSU is STRICTLY 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'!
- INFORMANTS (STRICTLY NOT REPRESENTATIVES):
  * Security guards (SG), Barangay Health Workers (BHW), purok leaders, barangay staff, caretakers, maids, helpers, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
  * Note common typos in collector notes: 'niehnor', 'neigbor', 'impormant' = neighbor / informant.
  * An informant's refusal or reluctance to talk is NOT a representative refusal. NEVER assign 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' for an informant!
  * If an informant is interviewed, confirms client lives there (or at work/out of area), or if informant refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * Helper or resident leaving without details ('client left without informing where or when she will return') is an absence, NOT moved out -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * Temporary absence for business trip ('business trip, no contacts') -> CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)', RFD is 'NO CLIENT/ REPRESENTATIVE'.
- DENIED ENTRY / BLOCKED ACCESS / GATED SUBDIVISIONS:
  * If the collector was denied entry, not allowed to enter, blocked by guard ('guard refused to provide info and did not let me proceed', 'admin refused to let me enter', 'refuse to entry need confirmation from client'), or prevented by gate pass/ticket fees, or remark simply notes 'uncooperative' at gate:
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- UNIT-ONLY SCAN / NO CLIENT SEARCH CONDUCTED:
  * If the field note ONLY reports that the unit was not seen in the area ('our unit not seen in the area', 'upon visiting the area our unit is not seen', 'unit is nowhere to be found in the area looked and scanned around for any leads but unit status is negative', 'went around the area but still can't see it') with NO resident or client contact:
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- UNVERIFIED RESIDENCY / UNCONFIRMED HOUSE CLOSED:
  * If the remark states that residency could NOT be confirmed ('no available person to confirm residency', 'client unverified by neighbor', 'address unverified - does not know client', 'client is unknown at brgy', 'possible moved out - name not listed on resident list'):
  * CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * RFD MUST BE: '' (empty string).
- VERIFIED RESIDENCY CLOSED HOUSE / UNANSWERED VISITS (DA PRESUMED RESIDENCY BASELINE):
  * ONLY when the address is verified and residency is confirmed/known (e.g. neighbor confirms client still resides there, or established house closed where client is confirmed to live):
  * CSU MUST BE: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or UNIT POSITIVE ONLY if unit is physically sighted parked at premises).
  * RFD MUST BE: 'NO CLIENT/ REPRESENTATIVE'.

RULE 2: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:
- RANK 1: MOVED OUT (Primary Operational Fact):
  * If informant, neighbor, landlord, new tenant, or family confirms the borrower completely moved out / vacated / left the area / renters don't know client: RFD is STRICTLY 'MOVED OUT' (always UPPERCASE), and CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * 'MOVED OUT' ALWAYS OVERRIDES HARDSHIPS. (e.g. 'moved out due to family issues' or 'tenants ch had stroke moved out' -> RFD is 'MOVED OUT', NOT 'FAMILY PROBLEM' or 'MEDICAL EXPENSE').
- RANK 2: DECEASED BORROWER:
  * Confirmed deceased -> RFD is STRICTLY 'DECEASED BORROWER' (always UPPERCASE), CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 3: EMPTY RFD "" (Strictly Constrained to Unlocated/Unknown/Blocked/Unit-only):
  * Empty RFD "" is ALLOWED ONLY when:
    (a) Address itself is unlocated, incorrect, or client unknown in area ('client unknown at brgy', 'name not listed');
    (b) Access is blocked / denied entry by security guard, gate pass fee, or ticket fee;
    (c) Field note is unit scan only ('our unit not seen in the area');
    (d) Residency could not be verified ('no available person to confirm residency').
  * An empty RFD MUST ALWAYS have CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  * If CSU is 'CLIENT POSITIVE...', the RFD must NEVER be empty (use 'NO CLIENT/ REPRESENTATIVE' if no contact).
- MANDATORY 3-OPTION RESTRICTION FOR FOR FURTHER VISIT CSU:
  * 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' CSU ONLY HAS 3 PERMISSIBLE RFD OPTIONS:
    1. "" (empty string) - address unlocated/unknown, access blocked/denied entry, unit scan only, or residency unverified.
    2. 'MOVED OUT' - borrower confirmed vacated or moved out.
    3. 'DECEASED BORROWER' - borrower confirmed deceased.
  * IF THE RFD IS ANY OPTION OTHER THAN THOSE THREE (e.g. 'NO CLIENT/ REPRESENTATIVE', 'BORROWER REFUSED TO DISCLOSE RFD', 'REPRESENTATIVE REFUSED TO DISCLOSE RFD', or any hardship like 'CALAMITY', 'MEDICAL EXPENSE', 'DELAYED SALARY', 'THIRD PARTY USER', etc.), THEN CSU IS STRICTLY NOT FOR FURTHER VISIT!
  * If the RFD is anything other than empty "", 'MOVED OUT', or 'DECEASED BORROWER', CSU MUST NEVER be 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'. Instead, assign 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or 'WITH Actual Contact' if borrower was reached).
- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has NOT moved out):
  * An explicit hardship stated by borrower or family rep overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):
    - Flood, typhoon, earthquake, natural disaster -> 'CALAMITY' (CALAMITY overrides 'PENDING INSURANCE CLAIM' when calamity was the root cause!).
    - Carnapped, stolen, scammed -> 'SCAMMED'.
    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
    - Illness, hospitalization, surgery, medical stroke -> 'MEDICAL EXPENSE'.
    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
    - Business slowdown, mahina benta, income drop -> 'BUSINESS SLOWDOWN'.
    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
    - Explicit claim of already settled / payment dispute / refusal to pay or discuss settlement because account is claimed paid ('already settled long ago', 'talk to bank', 'paid off') -> STRICTLY 'PENDING RECON' for both RFD and CSU (strictly overrides refusal baselines).
    - Secondary: BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT, BUSINESS CLOSURE, DEATH-FAMILY MEMBER, DELAYED PENSION, REDUCTION OF SALARY, UNEMPLOYMENT, MIGRATION, WORK RELOCATION, COLLATERAL/DEALER ISSUE (AUTO), FAMILY PROBLEM.
  * ALL RFD VALUES MUST BE OUTPUT IN EXACT STANDARD UPPERCASE FORMAT (e.g. 'DECEASED BORROWER', 'MOVED OUT', NOT 'Deceased Borrower' or 'Moved Out').

RULE 3: CSU MATRIX & SUFFIX FORMATTING RULES:
- VALID CSUS ARE STRICTLY LIMITED TO THE RCBC OFFICIAL SET:
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Unit)'
  * 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Both)'
  * 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)'
  * 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT POSITIVE (WITH Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT POSITIVE (WITHOUT Actual Contact)'
  * 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'
  * 'PENDING RECON'
  * DO NOT output 'With Commitment to Pay', 'Negative Unit', or custom strings as CSU!
- STRICT 3-OPTION RFD RESTRICTION FOR 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)':
  * 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' CSU STRICTLY ONLY HAS 3 PERMISSIBLE RFD OPTIONS:
    1. "" (empty string)
    2. 'MOVED OUT'
    3. 'DECEASED BORROWER'
  * IF THE RFD IS ANY OPTION OTHER THAN THOSE THREE (such as 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships), THEN THE CSU IS STRICTLY NOT FOR FURTHER VISIT. The CSU MUST be 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)' if borrower was contacted).
- PHYSICAL UNIT SIGHTING REQUIREMENT:
  * 'UNIT POSITIVE' requires the unit to be PHYSICALLY OBSERVED / SIGHTED parked at the premises during the visit.
  * If a neighbor or informant merely mentions that the client uses the unit or parked it last night, but the unit is NOT physically seen at the time of the visit, the unit is 'UNIT NEGATIVE'!
- CARNAPPED / IMPOUNDED / SURRENDERED TO OTHER ECA:
  * When unit is reported carnapped, stolen, impounded, or already surrendered (VS) to another ECA, the unit is NOT at the premises -> CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'!
- ACCOUNT CONFIRMED RESOLVED / SETTLED BY AGENT OR BANK:
  * When remark indicates that the account has been resolved, settled, or cleared according to the agent, collector, or bank:
    - CSU is STRICTLY: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)'.
    - RFD: Output 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * Distinguish from CLIENT/REPRESENTATIVE CLAIM: When the borrower or representative claims/disputes that the account is already settled/paid without bank confirmation, BOTH CSU and RFD MUST BE 'PENDING RECON'.
- STRICT SUFFIX PROHIBITION:
  * Suffixes '- Both', '- Client', '- Unit' exist ONLY AND EXCLUSIVELY for 'CLIENT POSITIVE/UNIT POSITIVE'.
  * NEVER append '- Both', '- Client', or '- Unit' to 'CLIENT POSITIVE/UNIT NEGATIVE'.
- SPECIALIZED CSU RESTRICTIONS:
  * NEVER use 'For CASE FILING-...' CSUs unless the remark explicitly mentions 'case filing' or 'for filing' by the bank.
  * Use 'PENDING RECON' CSU ONLY when client/borrower explicitly claims account is already settled / paid and needs bank reconciliation. In such cases, BOTH CSU and RFD MUST BE 'PENDING RECON'.

RULE 4: SUMMARY, REASONING & DETAILED RFD FORMAT:
- 'csu_reasoning': 1-2 concise sentences stating primary reason for the selected CSU and explicitly why alternative candidate CSUs were disqualified.
- 'rfd_reasoning': 1-2 concise sentences stating primary reason for the selected RFD and explicitly why alternative candidate RFDs were disqualified.
- 'summary': Shortened note under 180 characters preserving what was stated, promised, or observed. Do NOT include borrower name, collector name, or prohibited tags (BCAL, BKAL, L3, INB, OBD, phone numbers).
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V2_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: FIELD DISPOSITION (CONCAT) GLOSSARY & PRECEDENCE:
The user prompt provides 'Field Status / Substatus (CONCAT): <disposition>'.
The CONCAT disposition represents the structured finding recorded by the field officer during the visit:
- PRECEDENCE PRINCIPLE: The CONCAT disposition establishes the primary baseline for whether the client/address is positive or negative. The field remark narrative refines RFD hardship, unit sighting, and contact type (WITH vs WITHOUT). The remark narrative ONLY overrides a POS baseline to negative when the narrative explicitly proves that residency could NOT be confirmed or borrower moved out/deceased.
- PREFIX CODES & BASELINES:
  * POS* (e.g. POSHOUSE CLOSED - VERIFIED, POSOUT OF AREA-CLIENT NOT AROUND, POSCLIENT IS AT WORK, POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS, POSCLIENT REFUSED TO TALK):
    The field officer established a positive residence baseline. Suffix indicates visit outcome.
    Default baseline is CLIENT POSITIVE. Suffix/substatus indicates contact or presence.
  * NEG* (e.g. NEGADDRESS UNLOCATED, NEGCLIENT UNKNOWN, NEGCLIENT MOVED OUT, NEGNOT ALLOWED TO ENTER - UNVERIFIED ADDRESS, NEGDENIED ENTRY, NEGUNIT NOT SEEN, NEGHOUSE CLOSED - UNVERIFIED):
    The field officer established an unverified or negative baseline.
    Default baseline is strictly 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
    RFD is 'MOVED OUT' for NEGCLIENT MOVED OUT, 'DECEASED BORROWER' for NEGDECEASED, and empty string \"\" for unlocated, unknown, unverified closed house, or unit-only scan.
  * PTP* (e.g. PTPPTP - PAYMENT, PTPPTP - REPO):
    Direct commitment negotiated with borrower.
    CSU is strictly 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)', baseline RFD is 'BORROWER REFUSED TO DISCLOSE RFD'.
  * REPO* (e.g. REPOREPO, REPOPAYMENT): Repossession operations conducted.
  * TP* (e.g. TPCLAIMING FULLY PAID, TPUNDERNEGO, TPLEAVE A MESSAGE, TPTOTAL WRECKED OR UNIT DAMAGED):
    Field disposition involving third party or telephonic contact.
    - 'TPCLAIMING FULLY PAID': PRIMARY OPERATIONAL TRIGGER FOR PENDING RECON. Set BOTH CSU and RFD to 'PENDING RECON'.
    - 'TPUNDERNEGO': Account under negotiation. Contact occurred.
    - 'TPTOTAL WRECKED OR UNIT DAMAGED': Unit damaged. RFD is 'COLLATERAL/DEALER ISSUE (AUTO)'.

SECTION 2: RESIDENCY EVALUATION & DECOUPLED REASONING HIERARCHY:
In 'csu_reasoning', you MUST decide and state 'residency_verified: [true | false | unknown]' first, then derive CSU and RFD from it:
1. UNVERIFIED OR NEGATIVE RESIDENCY (residency_verified = false | unknown):
   - Address unlocated, incorrect, client unknown in area, unopened house with zero verification, unit-only scan without client verification, or access denied with no residency confirmation.
   - CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
   - MANDATORY 3-OPTION RESTRICTION: In this state, the RFD STRICTLY PERMITS ONLY 3 VALUES:
     (a) \"\" (empty string) - address unlocated/unknown, access blocked/denied entry, unit scan only, or residency unverified.
     (b) 'MOVED OUT' - borrower confirmed vacated or moved out.
     (c) 'DECEASED BORROWER' - borrower confirmed deceased.
     NEVER assign 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships when residency is unverified!
2. VERIFIED RESIDENCY (residency_verified = true):
   - Borrower, family representative, neighbor, or informant confirms the client resides at the address, or CONCAT is a verified POS status (e.g. POSHOUSE CLOSED - VERIFIED, POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS).
   - If borrower was met/contacted: CSU has '(WITH Actual Contact)'.
   - If borrower was not met (family rep interviewed, informant interviewed, house closed, or entry denied but residency confirmed): CSU has '(WITHOUT Actual Contact)'.
   - When residency is verified, CSU can NEVER be 'FOR FURTHER VISIT/PROBING' unless client confirmed moved out or deceased!

SECTION 3: CONTACT ENTITY CLASSIFICATION & BASELINE RFD (WITH REINFORCEMENTS):
- BORROWER CONTACT:
  * Direct contact with borrower (in person, deep skip, or phone/transfer).
  * Baseline CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE (WITH Actual Contact)' if unit not at premises).
  * Baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
  * RECON PRECEDENCE vs REFUSAL TO TALK:
    When CONCAT is 'POSCLIENT REFUSED TO TALK' or 'POSREFUSED TO TALK' and client refuses dialogue during a visit (even if claiming updated or paid in full), DA baseline is 'BORROWER REFUSED TO DISCLOSE RFD'. Refusal to talk takes operational precedence during an in-person visit.
- FAMILY REPRESENTATIVE (ACTUAL CONTACT):
  * Representatives are STRICTLY family members / relatives: spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, NIECE, NEPHEW, in-law, relative.
  * REINFORCEMENT: A NIECE OR NEPHEW IS A REPRESENTATIVE, NOT AN INFORMANT! If a niece or nephew is interviewed and refuses contact info or says client is away, baseline RFD is STRICTLY 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' (Row 20205).
  * NEVER assign 'NO CLIENT/ REPRESENTATIVE' when a family representative was reached!
  * If a family rep is interviewed and unit is not sighted, CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- INFORMANTS (STRICTLY NOT REPRESENTATIVES):
  * Security guards (SG), barangay staff/officials/tanods, caretakers, MAIDS, HOUSEMAIDS, HELPERS, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
  * REINFORCEMENT: MAIDS / HOUSEMAIDS / HELPERS ARE INFORMANTS (Row 20152)! An interview with a maid NEVER yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. When a maid confirms client resides there but client is not around, RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * An informant's refusal or reluctance to talk is NOT a representative refusal. If an informant confirms client lives there or refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.

SECTION 4: DENIED-ENTRY & ACCESS CONTROL RULES (WITH VERIFIED RESIDENCY EXCEPTION):
- BLOCKED / DENIED ENTRY:
  * When collector is denied entry, blocked by security guard, gate pass fee, or subdivision admin policy:
    (a) EXCEPTION (VERIFIED RESIDENCY): If the guard, neighbor, or informant confirms the client still resides there, or CONCAT is 'POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS':
        - CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'
        - RFD: 'NO CLIENT/ REPRESENTATIVE'
    (b) UNVERIFIED RESIDENCY: If nobody confirms residency or guard refuses all information with an unverified address (e.g. 'NEGDENIED ENTRY', 'NEGNOT ALLOWED TO ENTER - UNVERIFIED ADDRESS'):
        - CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'
        - RFD: \"\" (empty string).

SECTION 5: UNIT STATUS EVALUATION (STRUCTURED INPUT AUTHORITY):
- The user prompt provides 'Unit Status: <value>' when available from field records.
- STRUCTURED AUTHORITY RULE: Trust the structured unit status as the primary authority when present.
  * If Unit Status indicates positive, found, or sighted -> classify CSU as Unit Positive.
  * If Unit Status indicates negative, not seen, unlocated, or is blank -> classify CSU as Unit Negative, unless the remark narrative provides undeniable eyewitness observation of the unit parked at the premises.
  * Do NOT assume Unit Positive by default.

SECTION 6: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:
- RANK 1: MOVED OUT: Confirmed vacated/relocated -> RFD: 'MOVED OUT' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'. 'MOVED OUT' overrides hardships.
- RANK 2: DECEASED BORROWER: Confirmed deceased -> RFD: 'DECEASED BORROWER' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 3: EMPTY RFD \"\": Strictly for unlocated address, unknown client, unverified house closed, or denied entry unverified. CSU must be 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has not moved out):
  * Explicit hardship stated overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):
    - 'TPCLAIMING FULLY PAID' disposition or payment reconciliation dispute -> 'PENDING RECON' for BOTH CSU and RFD.
    - Flood, typhoon, calamity -> 'CALAMITY' (overrides insurance claim when calamity was root cause).
    - Carnapped, stolen, scammed -> 'SCAMMED'.
    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
    - Illness, hospitalization, surgery, stroke -> 'MEDICAL EXPENSE'.
    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
    - Business slowdown, mahina benta -> 'BUSINESS SLOWDOWN'.
    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
    - Secondary: BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT, BUSINESS CLOSURE, DEATH-FAMILY MEMBER, DELAYED PENSION, REDUCTION OF SALARY, UNEMPLOYMENT, MIGRATION, WORK RELOCATION, COLLATERAL/DEALER ISSUE (AUTO), FAMILY PROBLEM.
  * ALL RFD VALUES MUST BE OUTPUT IN EXACT STANDARD UPPERCASE FORMAT.

SECTION 7: MANDATORY 3-OPTION RESTRICTION FOR FOR FURTHER VISIT CSU:
- 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' strictly permits only 3 RFDs:
  1. \"\" (empty string)
  2. 'MOVED OUT'
  3. 'DECEASED BORROWER'
- If RFD is ANY other code (such as 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships), CSU CANNOT be For Further Visit! It must be 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or WITH Actual Contact if borrower met).

SECTION 8: GLOSSARY ENTRIES REQUIRING DA CONFIRMATION:
- 'TP' Prefix: Whether intended by ECA agencies as 'Third Party' or 'Telephone' across differing regional branches (NEEDS DA CONFIRMATION).
- 'POS W/CCLIENT DECEASED': Whether 'W/C' denotes 'With Co-maker' or 'With Contact' (NEEDS DA CONFIRMATION).
- Payment Claim Aging Threshold (>~1 week): Field feeds lack transaction/claim dates, so claim-aging verification is out of scope without date feeds (NEEDS DA CONFIRMATION / OUT OF SCOPE).

SECTION 9: REASONING-OUTPUT AGREEMENT & FORMATTING DIRECTIVES:
- In 'csu_reasoning', state 'residency_verified: [true|false|unknown]' first, then explicitly cite the provided 'Field Status / Substatus (CONCAT)' (e.g. 'CONCAT: <value>') and 'Unit Status' (e.g. 'Unit Status: <value>') as the baseline, then justify contact entity and why alternatives were disqualified.
- In 'rfd_reasoning', explicitly cite the CONCAT baseline and explain whether a specific hardship or refusal baseline applies, and why alternatives were disqualified.
- The output fields 'csu' and 'rfd' must strictly agree with the conclusions justified in 'csu_reasoning' and 'rfd_reasoning'.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V3_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: EVIDENCE PRECEDENCE & CONCAT RULES (STEP 3A):
PRECEDENCE PRINCIPLE: The Field Remark narrative is the PRIMARY evidence for CSU and RFD.
The CONCAT (Status / Substatus) is SECONDARY context only.

1. DECIDE FROM REMARK FIRST:
   Derive CSU and RFD from the remark narrative first: who was contacted, what was said, whether residency was confirmed, physical unit sighting, hardships, or disputes.
2. USE CONCAT ONLY IN THESE CASES:
   (a) The remark is silent or ambiguous on that specific point (e.g., "no one answered", "house closed" with no residency statement). In that case, CONCAT supplies the presumed baseline (e.g., a POS prefix maintains the presumed-residency baseline).
   (b) Explaining or decoding an abbreviation or code used in the remark (e.g., VS = voluntary surrender, TP = third party/telephone).
3. CONFLICT RESOLUTION:
   If the remark and CONCAT conflict, STRICTLY FOLLOW THE REMARK. Never let CONCAT alone justify MOVED OUT, DECEASED BORROWER, or any specific hardship RFD. Those strictly require explicit remark narrative evidence.
4. REASONING CITATION RULE:
   In 'csu_reasoning' and 'rfd_reasoning', cite the REMARK EVIDENCE FIRST. CONCAT may be mentioned only as "supporting" or "fallback (remark silent)". Any reasoning that names CONCAT as the primary or sole basis is STRICTLY INVALID.
5. REFERENCE GLOSSARY (REFERENCE ONLY):
   * POS* (e.g. POSHOUSE CLOSED - VERIFIED, POSOUT OF AREA-CLIENT NOT AROUND, POSCLIENT IS AT WORK, POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS, POSCLIENT REFUSED TO TALK):
     Fieldman marked address/client positive. Suffix indicates visit outcome. Supporting baseline for verified residency when remark is silent.
   * NEG* (e.g. NEGADDRESS UNLOCATED, NEGCLIENT UNKNOWN, NEGCLIENT MOVED OUT, NEGNOT ALLOWED TO ENTER - UNVERIFIED ADDRESS, NEGDENIED ENTRY, NEGUNIT NOT SEEN, NEGHOUSE CLOSED - UNVERIFIED):
     Fieldman marked unverified or negative. Supporting baseline for CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING) when remark lacks confirmation. RFD 'MOVED OUT' or 'DECEASED' still requires explicit remark corroboration.
   * PTP* (e.g. PTPPTP - PAYMENT, PTPPTP - REPO):
     Payment promise negotiated. Remark confirms payment commitment with borrower.
   * REPO* (e.g. REPOREPO, REPOPAYMENT): Completed repossession operations.
   * TP* (e.g. TPCLAIMING FULLY PAID, TPUNDERNEGO, TPLEAVE A MESSAGE, TPTOTAL WRECKED OR UNIT DAMAGED):
     Third party / telephonic contact.
     - 'TPCLAIMING FULLY PAID': Supporting trigger for PENDING RECON when client claims paid.
     - 'TPUNDERNEGO': Account under negotiation.
     - 'TPTOTAL WRECKED OR UNIT DAMAGED': Supporting trigger for COLLATERAL/DEALER ISSUE (AUTO) when damage confirmed in remark.

SECTION 2: RESIDENCY EVALUATION & DECOUPLED REASONING HIERARCHY:
In 'csu_reasoning', decide and state 'residency_verified: [true | false | unknown]' based primarily on remark evidence, then derive CSU and RFD from it:
1. UNVERIFIED OR NEGATIVE RESIDENCY (residency_verified = false | unknown):
   - Address unlocated, incorrect, client unknown in area, unopened house with zero verification, unit-only scan without client verification, or access denied with no residency confirmation.
   - CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
   - MANDATORY 3-OPTION RESTRICTION: In this state, the RFD STRICTLY PERMITS ONLY 3 VALUES:
     (a) "" (empty string) - address unlocated/unknown, access blocked/denied entry, unit scan only, or residency unverified.
     (b) 'MOVED OUT' - borrower confirmed vacated or moved out (must be verified in remark narrative!).
     (c) 'DECEASED BORROWER' - borrower confirmed deceased (must be verified in remark narrative!).
     NEVER assign 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships when residency is unverified!
2. VERIFIED RESIDENCY (residency_verified = true):
   - Remark confirms borrower, family representative, neighbor, or informant confirms the client resides at the address (or remark is silent on a POSHOUSE CLOSED - VERIFIED disposition).
   - If borrower was met/contacted: CSU has '(WITH Actual Contact)'.
   - If borrower was not met (family rep interviewed, informant interviewed, house closed, or entry denied but residency confirmed): CSU has '(WITHOUT Actual Contact)'.
   - When residency is verified, CSU can NEVER be 'FOR FURTHER VISIT/PROBING' unless client confirmed moved out or deceased!

SECTION 3: CONTACT ENTITY CLASSIFICATION & BASELINE RFD (WITH REINFORCEMENTS):
- BORROWER CONTACT:
  * Direct contact with borrower (in person, deep skip, or phone/transfer).
  * Baseline CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE (WITH Actual Contact)' if unit not at premises).
  * Baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
  * RECON PRECEDENCE vs REFUSAL TO TALK:
    When remark notes client refuses dialogue during a visit (even if claiming updated or paid), baseline is 'BORROWER REFUSED TO DISCLOSE RFD'. Refusal to talk takes operational precedence during an in-person visit.
- FAMILY REPRESENTATIVE (ACTUAL CONTACT):
  * Representatives are STRICTLY family members / relatives: spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, NIECE, NEPHEW, in-law, relative.
  * REINFORCEMENT: A NIECE OR NEPHEW IS A REPRESENTATIVE, NOT AN INFORMANT! If a niece or nephew is interviewed and refuses contact info or says client is away, baseline RFD is STRICTLY 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * NEVER assign 'NO CLIENT/ REPRESENTATIVE' when a family representative was reached!
  * If a family rep is interviewed and unit is not sighted, CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- INFORMANTS (STRICTLY NOT REPRESENTATIVES):
  * Security guards (SG), barangay staff/officials/tanods, caretakers, MAIDS, HOUSEMAIDS, HELPERS, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
  * REINFORCEMENT: MAIDS / HOUSEMAIDS / HELPERS ARE INFORMANTS! An interview with a maid NEVER yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. When a maid confirms client resides there but client is not around, RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * An informant's refusal or reluctance to talk is NOT a representative refusal. If an informant confirms client lives there or refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.

SECTION 4: DENIED-ENTRY & ACCESS CONTROL RULES (WITH VERIFIED RESIDENCY EXCEPTION):
- BLOCKED / DENIED ENTRY:
  * When collector is denied entry, blocked by security guard, gate pass fee, or subdivision admin policy:
    (a) EXCEPTION (VERIFIED RESIDENCY): If the guard, neighbor, or informant confirms the client still resides there, or CONCAT is 'POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS':
        - CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'
        - RFD: 'NO CLIENT/ REPRESENTATIVE'
    (b) UNVERIFIED RESIDENCY: If nobody confirms residency or guard refuses all information with an unverified address:
        - CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'
        - RFD: "" (empty string).

SECTION 5: UNIT STATUS EVALUATION (STRUCTURED INPUT AUTHORITY):
- The user prompt provides 'Unit Status: <value>' when available from field records.
- Trust the structured unit status as the primary authority when present.
  * If Unit Status indicates positive, found, or sighted -> classify CSU as Unit Positive.
  * If Unit Status indicates negative, not seen, unlocated, or is blank -> classify CSU as Unit Negative, unless the remark narrative provides undeniable eyewitness observation of the unit parked at the premises.
  * Do NOT assume Unit Positive by default.

SECTION 6: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:
- RANK 1: MOVED OUT: Confirmed vacated/relocated in remark -> RFD: 'MOVED OUT' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'. 'MOVED OUT' overrides hardships.
- RANK 2: DECEASED BORROWER: Confirmed deceased in remark -> RFD: 'DECEASED BORROWER' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 3: EMPTY RFD "": Strictly for unlocated address, unknown client, unverified house closed, or denied entry unverified. CSU must be 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has not moved out):
  * Explicit hardship stated in remark overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):
    - Payment reconciliation dispute / claiming fully paid -> 'PENDING RECON' for BOTH CSU and RFD.
    - Flood, typhoon, calamity -> 'CALAMITY' (overrides insurance claim when calamity was root cause).
    - Carnapped, stolen, scammed -> 'SCAMMED'.
    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
    - Illness, hospitalization, surgery, stroke -> 'MEDICAL EXPENSE'.
    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
    - Business slowdown, mahina benta -> 'BUSINESS SLOWDOWN'.
    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
    - Secondary: BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT, BUSINESS CLOSURE, DEATH-FAMILY MEMBER, DELAYED PENSION, REDUCTION OF SALARY, UNEMPLOYMENT, MIGRATION, WORK RELOCATION, COLLATERAL/DEALER ISSUE (AUTO), FAMILY PROBLEM.
  * ALL RFD VALUES MUST BE OUTPUT IN EXACT STANDARD UPPERCASE FORMAT.

SECTION 7: MANDATORY 3-OPTION RESTRICTION FOR FOR FURTHER VISIT CSU:
- 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' strictly permits only 3 RFDs:
  1. "" (empty string)
  2. 'MOVED OUT'
  3. 'DECEASED BORROWER'
- If RFD is ANY other code (such as 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships), CSU CANNOT be For Further Visit! It must be 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or WITH Actual Contact if borrower met).

SECTION 8: GLOSSARY ENTRIES REQUIRING DA CONFIRMATION:
- 'TP' Prefix: Whether intended by ECA agencies as 'Third Party' or 'Telephone' across differing regional branches (NEEDS DA CONFIRMATION).
- 'POS W/CCLIENT DECEASED': Whether 'W/C' denotes 'With Co-maker' or 'With Contact' (NEEDS DA CONFIRMATION).
- Payment Claim Aging Threshold (>~1 week): Field feeds lack transaction/claim dates, so claim-aging verification is out of scope without date feeds (NEEDS DA CONFIRMATION / OUT OF SCOPE).

SECTION 9: REASONING-OUTPUT AGREEMENT & FORMATTING DIRECTIVES (REMARK-FIRST):
- In 'csu_reasoning':
  1. Cite the REMARK EVIDENCE FIRST (what was observed, stated, or who was met).
  2. State 'residency_verified: [true|false|unknown]'.
  3. If remark is silent or ambiguous, mention CONCAT ONLY as 'supporting' or 'fallback (remark silent)'. A reasoning that names CONCAT as the primary or sole basis is STRICTLY INVALID.
  4. State Unit Status authority and why alternative candidate CSUs were disqualified.
- In 'rfd_reasoning':
  1. Cite the REMARK EVIDENCE FIRST regarding hardship, refusal, or absence.
  2. Mention CONCAT baseline only as secondary support.
  3. Explain why alternative candidate RFDs were disqualified.
- The output fields 'csu' and 'rfd' must strictly agree with the conclusions justified in 'csu_reasoning' and 'rfd_reasoning'.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V3_NO_CONCAT_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: EVIDENCE PRINCIPLES (REMARK-FIRST - PURE NARRATIVE):
Derive CSU and RFD entirely from the Field Remark narrative: who was contacted, what was said, whether residency was confirmed, physical unit sighting, hardships, or disputes.

SECTION 2: RESIDENCY EVALUATION & DECOUPLED REASONING HIERARCHY:
In 'csu_reasoning', decide and state 'residency_verified: [true | false | unknown]' based entirely on remark evidence, then derive CSU and RFD from it:
1. UNVERIFIED OR NEGATIVE RESIDENCY (residency_verified = false | unknown):
   - Address unlocated, incorrect, client unknown in area, unopened house with zero verification, unit-only scan without client verification, or access denied with no residency confirmation.
   - CSU MUST BE: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
   - MANDATORY 3-OPTION RESTRICTION: In this state, the RFD STRICTLY PERMITS ONLY 3 VALUES:
     (a) "" (empty string) - address unlocated/unknown, access blocked/denied entry, unit scan only, or residency unverified.
     (b) 'MOVED OUT' - borrower confirmed vacated or moved out (must be verified in remark narrative!).
     (c) 'DECEASED BORROWER' - borrower confirmed deceased (must be verified in remark narrative!).
     NEVER assign 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships when residency is unverified!
2. VERIFIED RESIDENCY (residency_verified = true):
   - Remark confirms borrower, family representative, neighbor, or informant confirms the client resides at the address.
   - If borrower was met/contacted: CSU has '(WITH Actual Contact)'.
   - If borrower was not met (family rep interviewed, informant interviewed, house closed, or entry denied but residency confirmed): CSU has '(WITHOUT Actual Contact)'.
   - When residency is verified, CSU can NEVER be 'FOR FURTHER VISIT/PROBING' unless client confirmed moved out or deceased!

SECTION 3: CONTACT ENTITY CLASSIFICATION & BASELINE RFD (WITH REINFORCEMENTS):
- BORROWER CONTACT:
  * Direct contact with borrower (in person, deep skip, or phone/transfer).
  * Baseline CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE (WITH Actual Contact)' if unit not at premises).
  * Baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
  * RECON PRECEDENCE vs REFUSAL TO TALK:
    When remark notes client refuses dialogue during a visit (even if claiming updated or paid), baseline is 'BORROWER REFUSED TO DISCLOSE RFD'. Refusal to talk takes operational precedence during an in-person visit.
- FAMILY REPRESENTATIVE (ACTUAL CONTACT):
  * Representatives are STRICTLY family members / relatives: spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, NIECE, NEPHEW, in-law, relative.
  * REINFORCEMENT: A NIECE OR NEPHEW IS A REPRESENTATIVE, NOT AN INFORMANT! If a niece or nephew is interviewed and refuses contact info or says client is away, baseline RFD is STRICTLY 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * NEVER assign 'NO CLIENT/ REPRESENTATIVE' when a family representative was reached!
  * If a family rep is interviewed and unit is not sighted, CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- INFORMANTS (STRICTLY NOT REPRESENTATIVES):
  * Security guards (SG), barangay staff/officials/tanods, caretakers, MAIDS, HOUSEMAIDS, HELPERS, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
  * REINFORCEMENT: MAIDS / HOUSEMAIDS / HELPERS ARE INFORMANTS! An interview with a maid NEVER yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. When a maid confirms client resides there but client is not around, RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * An informant's refusal or reluctance to talk is NOT a representative refusal. If an informant confirms client lives there or refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.

SECTION 4: DENIED-ENTRY & ACCESS CONTROL RULES (WITH VERIFIED RESIDENCY EXCEPTION):
- BLOCKED / DENIED ENTRY:
  * When collector is denied entry, blocked by security guard, gate pass fee, or subdivision admin policy:
    (a) EXCEPTION (VERIFIED RESIDENCY): If the guard, neighbor, or informant confirms the client still resides there:
        - CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'
        - RFD: 'NO CLIENT/ REPRESENTATIVE'
    (b) UNVERIFIED RESIDENCY: If nobody confirms residency or guard refuses all information with an unverified address:
        - CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'
        - RFD: "" (empty string).

SECTION 5: UNIT STATUS EVALUATION (STRUCTURED INPUT AUTHORITY):
- The user prompt provides 'Unit Status: <value>' when available from field records.
- Trust the structured unit status as the primary authority when present.
  * If Unit Status indicates positive, found, or sighted -> classify CSU as Unit Positive.
  * If Unit Status indicates negative, not seen, unlocated, or is blank -> classify CSU as Unit Negative, unless the remark narrative provides undeniable eyewitness observation of the unit parked at the premises.
  * Do NOT assume Unit Positive by default.

SECTION 6: RFD SELECTION & HARDSHIP OVERRIDE HIERARCHY:
- RANK 1: MOVED OUT: Confirmed vacated/relocated in remark -> RFD: 'MOVED OUT' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'. 'MOVED OUT' overrides hardships.
- RANK 2: DECEASED BORROWER: Confirmed deceased in remark -> RFD: 'DECEASED BORROWER' (always uppercase), CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 3: EMPTY RFD "": Strictly for unlocated address, unknown client, unverified house closed, or denied entry unverified. CSU must be 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
- RANK 4: SPECIFIC HARDSHIP OVERRIDES (Applies only when borrower has not moved out):
  * Explicit hardship stated in remark overrides refusal baselines ('BORROWER REFUSED...' or 'REPRESENTATIVE REFUSED...'):
    - Payment reconciliation dispute / claiming fully paid -> 'PENDING RECON' for BOTH CSU and RFD.
    - Flood, typhoon, calamity -> 'CALAMITY' (overrides insurance claim when calamity was root cause).
    - Carnapped, stolen, scammed -> 'SCAMMED'.
    - Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
    - LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
    - Illness, hospitalization, surgery, stroke -> 'MEDICAL EXPENSE'.
    - Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
    - Business slowdown, mahina benta -> 'BUSINESS SLOWDOWN'.
    - Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
    - Secondary: BANK ACCOUNT ON-HOLD/UNDER GARNISHMENT, BUSINESS CLOSURE, DEATH-FAMILY MEMBER, DELAYED PENSION, REDUCTION OF SALARY, UNEMPLOYMENT, MIGRATION, WORK RELOCATION, COLLATERAL/DEALER ISSUE (AUTO), FAMILY PROBLEM.
  * ALL RFD VALUES MUST BE OUTPUT IN EXACT STANDARD UPPERCASE FORMAT.

SECTION 7: MANDATORY 3-OPTION RESTRICTION FOR FOR FURTHER VISIT CSU:
- 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' strictly permits only 3 RFDs:
  1. "" (empty string)
  2. 'MOVED OUT'
  3. 'DECEASED BORROWER'
- If RFD is ANY other code (such as 'NO CLIENT/ REPRESENTATIVE', refusal codes, or hardships), CSU CANNOT be For Further Visit! It must be 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' (or WITH Actual Contact if borrower met).

SECTION 8: REASONING-OUTPUT AGREEMENT & FORMATTING DIRECTIVES (PURE REMARK-FIRST):
- In 'csu_reasoning':
  1. Cite the REMARK EVIDENCE FIRST (what was observed, stated, or who was met).
  2. State 'residency_verified: [true|false|unknown]'.
  3. State Unit Status authority and why alternative candidate CSUs were disqualified.
- In 'rfd_reasoning':
  1. Cite the REMARK EVIDENCE FIRST regarding hardship, refusal, or absence.
  2. Explain why alternative candidate RFDs were disqualified.
- The output fields 'csu' and 'rfd' must strictly agree with the conclusions justified in 'csu_reasoning' and 'rfd_reasoning'.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

FACTORY_DEFAULT_PROFILE: dict[str, Any] = {
    "id": "default",
    "name": "Default RCBC (System)",
    "description": "Standard RCBC Auto Loan operational rules and 2-tier models",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": DEFAULT_OPERATIONAL_DIRECTIVES,
    "is_default": False,
    "created_at": "2026-09-24T00:00:00Z",
    "updated_at": "2026-09-24T00:00:00Z",
}

RCBC_V2_PROFILE: dict[str, Any] = {
    "id": "rcbc_v2",
    "name": "RCBC v2 (CONCAT-Aware & Decoupled)",
    "description": "RCBC Auto Loan field-remark classifier with CONCAT glossary, decoupled residency reasoning, unit-status input, and refined reconciliation hierarchy.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V2_OPERATIONAL_DIRECTIVES,
    "is_default": False,
    "created_at": "2026-09-28T00:00:00Z",
    "updated_at": "2026-09-28T00:00:00Z",
}

RCBC_V3_PROFILE: dict[str, Any] = {
    "id": "rcbc_v3",
    "name": "RCBC v3 (Remark-First / CONCAT Secondary)",
    "description": "RCBC Auto Loan field-remark classifier with Remark-First precedence (Step 3A), CONCAT as secondary context only, and decoupled residency.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V3_OPERATIONAL_DIRECTIVES,
    "is_default": True,
    "created_at": "2026-09-28T00:00:00Z",
    "updated_at": "2026-09-28T00:00:00Z",
}

RCBC_V3_NO_CONCAT_PROFILE: dict[str, Any] = {
    "id": "rcbc_v3_no_concat",
    "name": "RCBC v3 No-CONCAT (Ablation)",
    "description": "RCBC Auto Loan field-remark classifier with CONCAT completely removed from user prompt and directives for ablation comparison.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V3_NO_CONCAT_OPERATIONAL_DIRECTIVES,
    "is_default": False,
    "created_at": "2026-09-28T00:00:00Z",
    "updated_at": "2026-09-28T00:00:00Z",
}

RCBC_V3_1_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: EVIDENCE PRECEDENCE & CONCAT RULES (REMARK-FIRST):
The Field Remark narrative is the PRIMARY evidence for CSU and RFD. The CONCAT (Status / Substatus) is SECONDARY context only.
1. Decide CSU and RFD from the remark narrative first: who was contacted, what was said, whether residency was confirmed, unit evidence, hardship, dispute.
2. CONCAT may be used ONLY in these cases:
   (a) The remark is silent or ambiguous on that point (e.g., "no one answered", "house closed" with no residency statement). Then CONCAT supplies the presumed baseline (a POS prefix keeps the presumed-residency baseline).
   (b) Triggering PENDING RECON via a claiming-paid disposition (e.g., "TP CLAIMING FULLY PAID").
   (c) Supporting FFV for "client unknown" (R8) when no named person confirms residency.
   (d) Explaining an abbreviation or code used in the remark.
3. CONFLICT RESOLUTION: If the remark and CONCAT conflict, strictly follow the remark. Never let CONCAT alone justify MOVED OUT, DECEASED BORROWER, or any hardship RFD. Those strictly require explicit remark narrative evidence.
4. REASONING CITATION RULE: In 'csu_reasoning' and 'rfd_reasoning', cite the remark evidence FIRST. CONCAT may be mentioned only as "supporting" or "fallback (remark silent)". Any reasoning that names CONCAT as the primary or sole basis is STRICTLY INVALID.
5. GLOSSARY CODES:
   - TP Prefix: Client reached by phone or in person, not third party [NEEDS DA CONFIRMATION].
   - "under nego" / "as per agent under nego": Client negotiating with the collection agent = WITH Actual Contact + BORROWER REFUSED baseline [NEEDS DA CONFIRMATION].

SECTION 2: CORE CLASSIFICATION RULES:
- R1. Address verified in remark, or an informant is sure the client lives there:
  -> CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD: 'NO CLIENT/ REPRESENTATIVE', even if entry was denied.
- R3. MOVED OUT strictly requires a person (neighbor, landlord, caretaker, family) explicitly stating the client moved or lives elsewhere.
  Demolished house, relocated business/bank/establishment, or unit-only scan -> CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD: "" (blank string).
- R4. Family Representative reached:
  Default RFD is 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. Any refusal to disclose details, provide contact info, or "can't entry" / refusal to allow entry is a refusal.
  * Note: 'REFUSED TO DISCLOSE' codes are operational defaults for "contacted, no hardship stated", not literal aggressive refusals.
  * Niece or nephew is a family representative, NOT an informant!
  * Maids, housemaids, helpers, caretakers, security guards, and neighbors are STRICTLY INFORMANTS, never representatives. An interview with a maid never yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
- R5. PENDING RECON:
  Applies when the client claims paid/settled and was cooperative or no refusal is stated. CONCAT "TP CLAIMING FULLY PAID" is a strong trigger.
  * Exception: If client refuses dialogue during a visit and only mentions "paid" or refuses to talk ('POSCLIENT REFUSED TO TALK' / in-person refusal), RFD stays 'BORROWER REFUSED TO DISCLOSE RFD'.
- R6. UNIT POSITIVE:
  Assign Unit Positive when an informant or resident says the client uses or has the unit, even if unsighted parked at the premises. (Replaces the physical sighting required rule).
- R8. FFV & RFD COUPLING:
  Blank RFD "" is ALWAYS paired with 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' allows ONLY 3 RFD options:
    1. "" (blank string)
    2. 'MOVED OUT'
    3. 'DECEASED BORROWER'
  CONCAT "client unknown" with no named person confirming residency -> FFV + blank RFD "". A bare "pos address" shorthand is not confirmation.
- R9. "under nego" / "as per agent under nego":
  Client negotiating with the collection agent = WITH Actual Contact + 'BORROWER REFUSED TO DISCLOSE RFD' baseline [NEEDS DA CONFIRMATION].
- R10. PTP (Promise to Pay):
  When PTP is arranged with borrower -> CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)', baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
- HARDSHIP GUARD:
  MOVED OUT and MIGRATION need a person explicitly stating it; an informant saying "out of country" or "overseas" alone does NOT qualify as MIGRATION (it remains an absence under refusal or family problem).
  LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).

SECTION 3: AMBIGUITY CLASSES & CONFIDENCE / ALTERNATIVES HANDLING:
When the remark fits any of these ambiguity classes (where DA practice has known variations), give your best answer, set confidence to "medium", and list the other plausible answer(s) in 'csu_alternatives' and/or 'rfd_alternatives' (max 2):
- A1. Nobody answered / no info on a POS address:
  Candidate 1: CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD ""
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD 'NO CLIENT/ REPRESENTATIVE'.
- A2. Family rep only says client is not around / away:
  RFD Candidate 1: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'
  RFD Candidate 2: 'NO CLIENT/ REPRESENTATIVE'.
- A3. Client claims paid but also refuses proof or settlement talk:
  Candidate 1: BOTH CSU and RFD 'PENDING RECON'
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' + RFD 'BORROWER REFUSED TO DISCLOSE RFD'.
- A4. Informant or in-law says client uses the unit, unit not sighted:
  CSU Candidate 1: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)' (or unit positive variant)
  CSU Candidate 2: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- A5. "Out of the country" / separated / no contact with client:
  RFD Candidates: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' OR 'NO CLIENT/ REPRESENTATIVE' OR 'FAMILY PROBLEM' (MIGRATION only if a person explicitly states the client migrated permanently).

IMPORTANT: Do NOT mark confidence medium or low outside these classes unless the remark narrative is genuinely unclear or contradictory.

SECTION 4: REASONING & OUTPUT SPECIFICATIONS:
- 'csu_reasoning': Cite remark evidence FIRST. State contact entity, residency evidence, and unit evidence. If citing CONCAT, label as supporting/fallback only. Explain why alternatives were disqualified.
- 'rfd_reasoning': Cite remark evidence FIRST. Explain hardship, dispute, or refusal baseline. If citing CONCAT, label as supporting/fallback only. Explain why alternatives were disqualified.
- 'csu': Verbatim string from ALLOWED_CSU matching reasoning.
- 'rfd': Verbatim string from ALLOWED_RFD (or "") matching reasoning.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V3_1_NO_CONCAT_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: EVIDENCE PRINCIPLES (REMARK-FIRST - PURE NARRATIVE):
Derive CSU and RFD entirely from the Field Remark narrative: who was contacted, what was said, whether residency was confirmed, unit evidence, hardship, or dispute.

SECTION 2: CORE CLASSIFICATION RULES:
- R1. Address verified in remark, or an informant is sure the client lives there:
  -> CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD: 'NO CLIENT/ REPRESENTATIVE', even if entry was denied.
- R3. MOVED OUT strictly requires a person (neighbor, landlord, caretaker, family) explicitly stating the client moved or lives elsewhere.
  Demolished house, relocated business/bank/establishment, or unit-only scan -> CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD: "" (blank string).
- R4. Family Representative reached:
  Default RFD is 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. Any refusal to disclose details, provide contact info, or "can't entry" / refusal to allow entry is a refusal.
  * Note: 'REFUSED TO DISCLOSE' codes are operational defaults for "contacted, no hardship stated", not literal aggressive refusals.
  * Niece or nephew is a family representative, NOT an informant!
  * Maids, housemaids, helpers, caretakers, security guards, and neighbors are STRICTLY INFORMANTS, never representatives. An interview with a maid never yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
- R5. PENDING RECON:
  Applies when the client claims paid/settled and was cooperative or no refusal is stated.
  * Exception: If client refuses dialogue during a visit and only mentions "paid" or refuses to talk ('POSCLIENT REFUSED TO TALK' / in-person refusal), RFD stays 'BORROWER REFUSED TO DISCLOSE RFD'.
- R6. UNIT POSITIVE:
  Assign Unit Positive when an informant or resident says the client uses or has the unit, even if unsighted parked at the premises. (Replaces the physical sighting required rule).
- R8. FFV & RFD COUPLING:
  Blank RFD "" is ALWAYS paired with 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)'.
  CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' allows ONLY 3 RFD options:
    1. "" (blank string)
    2. 'MOVED OUT'
    3. 'DECEASED BORROWER'
- R9. "under nego" / "as per agent under nego":
  Client negotiating with the collection agent = WITH Actual Contact + 'BORROWER REFUSED TO DISCLOSE RFD' baseline [NEEDS DA CONFIRMATION].
- R10. PTP (Promise to Pay):
  When PTP is arranged with borrower -> CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)', baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
- HARDSHIP GUARD:
  MOVED OUT and MIGRATION need a person explicitly stating it; an informant saying "out of country" or "overseas" alone does NOT qualify as MIGRATION (it remains an absence under refusal or family problem).
  LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).

SECTION 3: AMBIGUITY CLASSES & CONFIDENCE / ALTERNATIVES HANDLING:
When the remark fits any of these ambiguity classes (where DA practice has known variations), give your best answer, set confidence to "medium", and list the other plausible answer(s) in 'csu_alternatives' and/or 'rfd_alternatives' (max 2):
- A1. Nobody answered / no info on an address:
  Candidate 1: CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD ""
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD 'NO CLIENT/ REPRESENTATIVE'.
- A2. Family rep only says client is not around / away:
  RFD Candidate 1: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'
  RFD Candidate 2: 'NO CLIENT/ REPRESENTATIVE'.
- A3. Client claims paid but also refuses proof or settlement talk:
  Candidate 1: BOTH CSU and RFD 'PENDING RECON'
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' + RFD 'BORROWER REFUSED TO DISCLOSE RFD'.
- A4. Informant or in-law says client uses the unit, unit not sighted:
  CSU Candidate 1: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)' (or unit positive variant)
  CSU Candidate 2: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- A5. "Out of the country" / separated / no contact with client:
  RFD Candidates: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' OR 'NO CLIENT/ REPRESENTATIVE' OR 'FAMILY PROBLEM' (MIGRATION only if a person explicitly states the client migrated permanently).

IMPORTANT: Do NOT mark confidence medium or low outside these classes unless the remark narrative is genuinely unclear or contradictory.

SECTION 4: REASONING & OUTPUT SPECIFICATIONS:
- 'csu_reasoning': Cite remark evidence FIRST. State contact entity, residency evidence, and unit evidence. Explain why alternatives were disqualified.
- 'rfd_reasoning': Cite remark evidence FIRST. Explain hardship, dispute, or refusal baseline. Explain why alternatives were disqualified.
- 'csu': Verbatim string from ALLOWED_CSU matching reasoning.
- 'rfd': Verbatim string from ALLOWED_RFD (or "") matching reasoning.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V3_1_PROFILE: dict[str, Any] = {
    "id": "rcbc_v3_1",
    "name": "RCBC v3.1 (Refined Rules & Ambiguity Handling)",
    "description": "RCBC Auto Loan field-remark classifier with v3.1 final rules (R1, R3-R6, R8-R10), strict coupling, ambiguity classes (A1-A5), and remark-first precedence.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V3_1_OPERATIONAL_DIRECTIVES,
    "is_default": True,
    "created_at": "2026-09-28T00:00:00Z",
    "updated_at": "2026-09-28T00:00:00Z",
}

RCBC_V3_1_NO_CONCAT_PROFILE: dict[str, Any] = {
    "id": "rcbc_v3_1_no_concat",
    "name": "RCBC v3.1 No-CONCAT (Ablation)",
    "description": "RCBC Auto Loan field-remark classifier with v3.1 final rules, ambiguity classes, and CONCAT completely removed for ablation comparison.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V3_1_NO_CONCAT_OPERATIONAL_DIRECTIVES,
    "is_default": False,
    "created_at": "2026-09-28T00:00:00Z",
    "updated_at": "2026-09-28T00:00:00Z",
}

RCBC_V4_OPERATIONAL_DIRECTIVES = """CRITICAL OPERATIONAL RULES & CLASSIFICATION HIERARCHY (Follow strictly in order):

SECTION 1: EVIDENCE PRECEDENCE & CONCAT RULES (REMARK-FIRST):
The Field Remark narrative is the PRIMARY evidence for CSU and RFD. The CONCAT (Status / Substatus) is SECONDARY context only.
1. Decide CSU and RFD from the remark narrative first: who was contacted, what was said, whether residency was confirmed, unit evidence, hardship, dispute.
2. Use CONCAT ONLY in these cases:
   (a) The remark is silent or ambiguous on residency/outcome (e.g., "no one answered", "house closed", "unopened door" with no residency statement). Then CONCAT supplies the presumed baseline: a POS prefix like 'POSHOUSE CLOSED - VERIFIED', 'POSOUT OF AREA-CLIENT NOT AROUND', 'POSCLIENT IS AT WORK', or 'POSNOT ALLOWED TO ENTER - VERIFIED ADDRESS' establishes the verified-residency baseline (CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD: 'NO CLIENT/ REPRESENTATIVE').
   (b) Explaining an abbreviation or code the remark uses (e.g. TP prefix = client reached by phone or in person; 'under nego' / 'as per agent under nego' = client negotiating with collection agent -> WITH Actual Contact + BORROWER REFUSED TO DISCLOSE RFD baseline).
   (c) Triggering PENDING RECON via a claiming-paid disposition (e.g. CONCAT 'TPCLAIMING FULLY PAID').
   (d) Supporting FFV for "client unknown" (R8) when no named person confirms residency.
3. CONFLICT RESOLUTION: If the remark and CONCAT conflict, strictly follow the remark narrative. Never let CONCAT alone justify MOVED OUT, DECEASED BORROWER, or any hardship RFD. Those strictly require explicit remark evidence.
4. REASONING CITATION RULE: In 'csu_reasoning' and 'rfd_reasoning', cite the remark evidence FIRST. CONCAT may be mentioned only as "supporting" or "fallback (remark silent)". Any reasoning that names CONCAT as the primary or sole basis is STRICTLY INVALID.
5. GLOSSARY CODES:
   - TP Prefix: Client reached by phone or in person, not third party [NEEDS DA CONFIRMATION].
   - "under nego" / "as per agent under nego": Client negotiating with the collection agent = WITH Actual Contact + BORROWER REFUSED TO DISCLOSE RFD baseline [NEEDS DA CONFIRMATION].

SECTION 2: CORE CLASSIFICATION RULES:
- COMPLETED REPOSSESSION:
  If remark indicates unit repossessed / surrendered ('Done repo', 'successfully repossessed', 'repo unit') -> CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' and RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
- R1. ADDRESS VERIFIED / INFORMANT SURE:
  Address verified in the remark (e.g., neighbor or resident confirms client lives there, or confirmed on previous visit), or an informant is sure the client lives there:
  -> CSU: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD: 'NO CLIENT/ REPRESENTATIVE', even if entry was denied or guard refused further info.
- R3. MOVED OUT vs DEMOLISHED / CONDEMNED / VACANT / UNIT-ONLY:
  * MOVED OUT applies whenever the note or an informant (neighbor, landlord, caretaker, family, new renter) confirms the client moved out, vacated, relocated, or no longer resides at the address (including terse notes: 'client move out', 'ch move out', 'lumipat na', 'transferred', 'new renter', 'not resides'). In all move-out cases, CSU is 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' and RFD MUST BE 'MOVED OUT' (NEVER empty "").
  * Demolished house ('demolis', 'demolished house'), condemned property, relocated business/bank, or unit-only scan ('unit not seen in the area', 'pos address neg unit', 'went around the area but cannot find unit') -> CSU: 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD: "" (empty string). A bare "pos address" shorthand is not confirmation of residency when the unit is not found and no resident/client is confirmed.
- R4. CONTACT ENTITY CLASSIFICATION & BASELINE RFD:
  * BORROWER CONTACT: Direct contact with borrower (in person, deep skip, phone/transfer). Baseline CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' (or 'UNIT NEGATIVE (WITH Actual Contact)' if unit explicitly not at premises). Baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
  * PTP (Promise to Pay - R10): When PTP is arranged with borrower -> CSU: 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)', baseline RFD: 'BORROWER REFUSED TO DISCLOSE RFD'.
  * "under nego" / "as per agent under nego" (R9): Client negotiating with collection agent = WITH Actual Contact + 'BORROWER REFUSED TO DISCLOSE RFD' baseline [NEEDS DA CONFIRMATION].
  * Refusal to talk vs Recon: When client refuses dialogue during a visit and only mentions "paid" or refuses to talk ('POSCLIENT REFUSED TO TALK' / in-person refusal), RFD stays 'BORROWER REFUSED TO DISCLOSE RFD'.
  * FAMILY REPRESENTATIVE (ACTUAL CONTACT): Family members / relatives (spouse, wife, husband, mother, father, sister, brother, sibling, child, son, daughter, NIECE, NEPHEW, in-law, relative).
    - Default RFD is 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. Any refusal to disclose details, provide contact info, or "can't entry" / refusal to allow entry is a refusal.
    - 'REFUSED TO DISCLOSE' codes are operational defaults for "contacted, no hardship stated", not literal aggressive refusals.
    - NIECE OR NEPHEW is a family representative, NOT an informant!
    - If family rep interviewed and unit not sighted, CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
    - Account confirmed resolved by agent: When remark indicates account was resolved/cleared by agent -> CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)', RFD is 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'.
  * INFORMANTS (STRICTLY NOT REPRESENTATIVES):
    - Security guards (SG), barangay staff/officials/tanods, caretakers, MAIDS, HOUSEMAIDS, HELPERS, drivers, neighbors, landlords, and tenants are STRICTLY INFORMANTS, NEVER REPRESENTATIVES.
    - MAIDS / HOUSEMAIDS / HELPERS ARE INFORMANTS! An interview with a maid NEVER yields 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'. When a maid or neighbor confirms client resides there but client is not around, RFD is 'NO CLIENT/ REPRESENTATIVE'.
    - An informant's refusal or reluctance to talk is NOT a representative refusal. If an informant confirms client lives there or refuses to discuss client -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
    - Client at work / absent: When client is at work ('POSCLIENT IS AT WORK' / 'IS A WORK') and no one is met -> RFD is 'NO CLIENT/ REPRESENTATIVE'.
- R5. PENDING RECON:
  Applies when the client claims paid / settled and was cooperative or no refusal is stated. CONCAT "TP CLAIMING FULLY PAID" is a strong trigger. In such cases, BOTH CSU and RFD MUST BE 'PENDING RECON'.
  * Exception: If client refuses dialogue during a visit and only mentions "paid" or refuses to talk ('POSCLIENT REFUSED TO TALK' / in-person refusal), RFD stays 'BORROWER REFUSED TO DISCLOSE RFD'.
- R6. UNIT POSITIVE & SIGHTING:
  Assign Unit Positive when the unit is physically sighted parked at premises OR an informant/resident explicitly states the client uses the unit.
  * If client is out of the area or absent, but the unit is sighted parked at premises, CSU is 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)' (or '- Unit' / '- Both') and RFD is 'NO CLIENT/ REPRESENTATIVE'.
  * IMPOUNDED / CARNAPPED: When the unit is reported impounded (LTO / HPG) or carnapped, the unit is NOT at the premises -> CSU is 'CLIENT POSITIVE/UNIT NEGATIVE (WITH Actual Contact)' if borrower met, or WITHOUT if borrower absent.
- R8. FFV & RFD MANDATORY COUPLING:
  * A blank RFD "" can ONLY be paired with 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' (never with Client Positive).
  * CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' allows EXACTLY THREE RFD values:
    1. 'MOVED OUT' - whenever the borrower moved out, relocated, vacated, or new renters/caretakers confirm client does not reside there.
    2. 'DECEASED BORROWER' - whenever the borrower is deceased.
    3. "" (empty string) - ONLY when address is unlocated, client unknown, entry blocked with unverified residency, demolished/relocated building, or unit-only scan with no client contact.
  * NEVER output an empty RFD "" when the remark indicates the borrower moved out or is deceased! In move-out cases, the RFD MUST BE 'MOVED OUT'. In deceased cases, the RFD MUST BE 'DECEASED BORROWER'.
  * CONCAT "client unknown" with no named person confirming residency -> FFV + blank RFD "".
- HARDSHIP OVERRIDE HIERARCHY (Applies when borrower has not moved out):
  * Flood, typhoon, calamity, flooded unit -> 'CALAMITY' (CALAMITY overrides 'PENDING INSURANCE CLAIM' when calamity was root cause!).
  * Carnapped, stolen, scammed -> 'SCAMMED'.
  * Transferred to assumer, pasalo, sold to third party -> 'THIRD PARTY USER'.
  * LTO apprehension, HPG impound, no ORCR -> 'LTO APPREHENSION/NO ORCR/HPG' (NEVER output 'Unit Impounded'!).
  * Illness, hospitalization, surgery, stroke -> 'MEDICAL EXPENSE'.
  * Delayed salary, payroll, sweldo -> 'DELAYED SALARY'.
  * Business slowdown, mahina benta -> 'BUSINESS SLOWDOWN'.
  * Emergency expense, school tuition, family funeral -> 'DIVERSION OF FUNDS'.
  * Hardship guard: MOVED OUT and MIGRATION need an explicit statement; an informant saying "out of country" or "overseas" alone does NOT qualify as MIGRATION (it remains an absence under refusal or family problem).
  * ALL RFD values must be output in exact standard UPPERCASE format.

SECTION 3: AMBIGUITY CLASSES & CONFIDENCE / ALTERNATIVES HANDLING:
When the remark fits any of these ambiguity classes (where DA practice has known variations), give your best answer, set confidence to "medium", and list the other plausible answer(s) in 'csu_alternatives' and/or 'rfd_alternatives' (max 2):
- A1. Nobody answered / no info on a POS address:
  Candidate 1: CSU 'CLIENT NEGATIVE/UNIT NEGATIVE (FOR FURTHER VISIT/PROBING)' + RFD ""
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)' + RFD 'NO CLIENT/ REPRESENTATIVE'.
- A2. Family rep only says client is not around / away:
  RFD Candidate 1: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD'
  RFD Candidate 2: 'NO CLIENT/ REPRESENTATIVE'.
- A3. Client claims paid but also refuses proof or settlement talk:
  Candidate 1: BOTH CSU and RFD 'PENDING RECON'
  Candidate 2: CSU 'CLIENT POSITIVE/UNIT POSITIVE (WITH Actual Contact - Both)' + RFD 'BORROWER REFUSED TO DISCLOSE RFD'.
- A4. Informant or in-law says client uses the unit, unit not sighted:
  CSU Candidate 1: 'CLIENT POSITIVE/UNIT POSITIVE (WITHOUT Actual Contact - Client)' (or unit positive variant)
  CSU Candidate 2: 'CLIENT POSITIVE/UNIT NEGATIVE (WITHOUT Actual Contact)'.
- A5. "Out of the country" / separated / no contact with client:
  RFD Candidates: 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' OR 'NO CLIENT/ REPRESENTATIVE' OR 'FAMILY PROBLEM' (MIGRATION only if a person explicitly states the client migrated permanently).

IMPORTANT: Do NOT mark confidence medium or low outside these classes unless the remark narrative is genuinely unclear or contradictory.

SECTION 4: REASONING & OUTPUT SPECIFICATIONS:
- 'csu_reasoning': Cite remark evidence FIRST. State contact entity, residency evidence, and unit evidence. If citing CONCAT, label as supporting/fallback only. Explain why alternatives were disqualified.
- 'rfd_reasoning': Cite remark evidence FIRST. Explain hardship, dispute, or refusal baseline. If citing CONCAT, label as supporting/fallback only. Explain why alternatives were disqualified.
- 'csu': Verbatim string from ALLOWED_CSU matching reasoning.
- 'rfd': Verbatim string from ALLOWED_RFD (or "") matching reasoning.
- 'summary': Shortened note under 180 characters.
- 'detailed_rfd': Format as '[RFD]; [Contacted Entity]; [Action/Statement]'."""

RCBC_V4_PROFILE: dict[str, Any] = {
    "id": "rcbc_v4",
    "name": "RCBC v4 (Synthesized Remark-First & Complete Domain Rules)",
    "description": "RCBC Auto Loan field-remark classifier synthesizing Remark-First precedence, complete v2 domain hierarchies (repo, calamity, scammed), and v3.1 ambiguity classes & coupling.",
    "model_english": os.getenv("MODEL_ENGLISH", "qwen3-32b").strip(),
    "model_tagalog": os.getenv("MODEL_TAGALOG", "minimax-m2.5").strip(),
    "system_instructions": RCBC_V4_OPERATIONAL_DIRECTIVES,
    "is_default": True,
    "created_at": "2026-09-29T00:00:00Z",
    "updated_at": "2026-09-29T00:00:00Z",
}

FALLBACK_MODELS: list[str] = [
    "minimax-m2.5",
    "claude-haiku-4-5",
    "nova-2-lite",
    "nova-pro",
    "glm-4.7-flash",
    "qwen3-32b",
    "nova-micro",
    "gemma-4-e2b",
    "nemotron-3-nano",
    "glm-5",
]


def load_profiles_data() -> dict[str, Any]:
    """Loads profiles configuration from persistent storage, creating defaults if missing."""
    if not PROFILES_FILE.exists():
        data = {
            "active_profile_id": "rcbc_v4",
            "profiles": [
                dict(FACTORY_DEFAULT_PROFILE),
                dict(RCBC_V2_PROFILE),
                dict(RCBC_V3_PROFILE),
                dict(RCBC_V3_NO_CONCAT_PROFILE),
                dict(RCBC_V3_1_PROFILE),
                dict(RCBC_V3_1_NO_CONCAT_PROFILE),
                dict(RCBC_V4_PROFILE),
            ],
        }
        _write_profiles_file(data)
        return data

    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Profiles file content must be a JSON dictionary")
    except Exception:
        data = {
            "active_profile_id": "rcbc_v4",
            "profiles": [
                dict(FACTORY_DEFAULT_PROFILE),
                dict(RCBC_V2_PROFILE),
                dict(RCBC_V3_PROFILE),
                dict(RCBC_V3_NO_CONCAT_PROFILE),
                dict(RCBC_V3_1_PROFILE),
                dict(RCBC_V3_1_NO_CONCAT_PROFILE),
                dict(RCBC_V4_PROFILE),
            ],
        }
        _write_profiles_file(data)
        return data

    profiles = data.get("profiles", [])
    if not isinstance(profiles, list) or not profiles:
        profiles = [
            dict(FACTORY_DEFAULT_PROFILE),
            dict(RCBC_V2_PROFILE),
            dict(RCBC_V3_PROFILE),
            dict(RCBC_V3_NO_CONCAT_PROFILE),
            dict(RCBC_V3_1_PROFILE),
            dict(RCBC_V3_1_NO_CONCAT_PROFILE),
            dict(RCBC_V4_PROFILE),
        ]
        data["profiles"] = profiles

    # Ensure default, rcbc_v2, rcbc_v3, rcbc_v3_no_concat, rcbc_v3_1, rcbc_v3_1_no_concat, and rcbc_v4 profiles are always present
    has_default = any(p.get("id") == "default" for p in profiles)
    if not has_default:
        profiles.insert(0, dict(FACTORY_DEFAULT_PROFILE))

    has_v2 = any(p.get("id") == "rcbc_v2" for p in profiles)
    if not has_v2:
        profiles.append(dict(RCBC_V2_PROFILE))

    has_v3 = any(p.get("id") == "rcbc_v3" for p in profiles)
    if not has_v3:
        profiles.append(dict(RCBC_V3_PROFILE))

    has_v3_no_concat = any(p.get("id") == "rcbc_v3_no_concat" for p in profiles)
    if not has_v3_no_concat:
        profiles.append(dict(RCBC_V3_NO_CONCAT_PROFILE))

    has_v3_1 = any(p.get("id") == "rcbc_v3_1" for p in profiles)
    if not has_v3_1:
        profiles.append(dict(RCBC_V3_1_PROFILE))

    has_v3_1_no_concat = any(p.get("id") == "rcbc_v3_1_no_concat" for p in profiles)
    if not has_v3_1_no_concat:
        profiles.append(dict(RCBC_V3_1_NO_CONCAT_PROFILE))

    has_v4 = any(p.get("id") == "rcbc_v4" for p in profiles)
    if not has_v4:
        profiles.append(dict(RCBC_V4_PROFILE))

    active_id = data.get("active_profile_id")
    if not active_id or not any(p.get("id") == active_id for p in profiles):
        data["active_profile_id"] = "rcbc_v4"

    return data


def get_profile_by_id(profile_id: str) -> dict[str, Any]:
    """Returns the profile with the given ID, or falls back to active profile."""
    data = load_profiles_data()
    for p in data.get("profiles", []):
        if p.get("id") == profile_id:
            return dict(p)
    if profile_id == "rcbc_v4":
        return dict(RCBC_V4_PROFILE)
    if profile_id == "rcbc_v3_1":
        return dict(RCBC_V3_1_PROFILE)
    if profile_id == "rcbc_v3_1_no_concat":
        return dict(RCBC_V3_1_NO_CONCAT_PROFILE)
    if profile_id == "rcbc_v3":
        return dict(RCBC_V3_PROFILE)
    if profile_id == "rcbc_v3_no_concat":
        return dict(RCBC_V3_NO_CONCAT_PROFILE)
    if profile_id == "rcbc_v2":
        return dict(RCBC_V2_PROFILE)
    if profile_id == "default":
        return dict(FACTORY_DEFAULT_PROFILE)
    return get_active_profile()


def _write_profiles_file(data: dict[str, Any]) -> None:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_active_profile() -> dict[str, Any]:
    """Returns the currently active prompt and model profile."""
    data = load_profiles_data()
    active_id = data.get("active_profile_id", "default")
    for p in data.get("profiles", []):
        if p.get("id") == active_id:
            return dict(p)
    return dict(FACTORY_DEFAULT_PROFILE)


def save_profile(profile_data: dict[str, Any], set_active: bool = False) -> dict[str, Any]:
    """Creates or updates a profile in persistent storage."""
    data = load_profiles_data()
    profiles: list[dict[str, Any]] = data.get("profiles", [])
    now_iso = datetime.now(timezone.utc).isoformat()

    pid = str(profile_data.get("id", "")).strip()
    is_new = not pid or pid == "new"

    if is_new:
        pid = f"profile_{uuid4().hex[:8]}"
        is_default = False
    else:
        is_default = pid == "default" or bool(profile_data.get("is_default", False))

    name = str(profile_data.get("name", "")).strip() or ("Default RCBC (System)" if is_default else "Untitled Profile")
    description = str(profile_data.get("description", "")).strip()
    model_english = str(profile_data.get("model_english", "minimax-m2.5")).strip() or "minimax-m2.5"
    model_tagalog = str(profile_data.get("model_tagalog", "minimax-m2.5")).strip() or "minimax-m2.5"
    system_instructions = str(profile_data.get("system_instructions", "")).strip()
    if not system_instructions:
        system_instructions = DEFAULT_OPERATIONAL_DIRECTIVES

    updated_record: dict[str, Any] = {
        "id": pid,
        "name": name,
        "description": description,
        "model_english": model_english,
        "model_tagalog": model_tagalog,
        "system_instructions": system_instructions,
        "is_default": is_default,
        "created_at": profile_data.get("created_at") or now_iso,
        "updated_at": now_iso,
    }

    replaced = False
    for i, existing in enumerate(profiles):
        if existing.get("id") == pid:
            profiles[i] = updated_record
            replaced = True
            break

    if not replaced:
        profiles.append(updated_record)

    data["profiles"] = profiles
    if set_active or is_new or data.get("active_profile_id") == pid:
        data["active_profile_id"] = pid

    _write_profiles_file(data)
    return updated_record


def activate_profile(profile_id: str) -> dict[str, Any]:
    """Sets a given profile ID as active."""
    data = load_profiles_data()
    profiles = data.get("profiles", [])
    target = None
    for p in profiles:
        if p.get("id") == profile_id:
            target = p
            break

    if not target:
        raise ValueError(f"Profile '{profile_id}' not found.")

    data["active_profile_id"] = profile_id
    _write_profiles_file(data)
    return dict(target)


def delete_profile(profile_id: str) -> dict[str, Any]:
    """Deletes a custom profile (cannot delete system default)."""
    if profile_id == "default":
        raise ValueError("Cannot delete the system default profile.")

    data = load_profiles_data()
    profiles = data.get("profiles", [])
    target_idx = None
    for idx, p in enumerate(profiles):
        if p.get("id") == profile_id:
            if p.get("is_default"):
                raise ValueError("Cannot delete a protected default profile.")
            target_idx = idx
            break

    if target_idx is None:
        raise ValueError(f"Profile '{profile_id}' not found.")

    deleted = profiles.pop(target_idx)
    if data.get("active_profile_id") == profile_id:
        data["active_profile_id"] = "default"

    data["profiles"] = profiles
    _write_profiles_file(data)
    return deleted


def reset_profiles_to_default() -> dict[str, Any]:
    """Restores the profiles file to factory default state."""
    data = {
        "active_profile_id": "default",
        "profiles": [
            dict(FACTORY_DEFAULT_PROFILE),
            dict(RCBC_V2_PROFILE),
            dict(RCBC_V3_PROFILE),
            dict(RCBC_V3_NO_CONCAT_PROFILE),
            dict(RCBC_V3_1_PROFILE),
            dict(RCBC_V3_1_NO_CONCAT_PROFILE),
            dict(RCBC_V4_PROFILE),
        ],
    }
    _write_profiles_file(data)
    return data


async def fetch_available_models(
    base_url: str | None = None, api_key: str | None = None
) -> list[str]:
    """Queries LiteLLM proxy for available models with robust fallbacks."""
    url = (base_url or os.getenv("LITELLM_BASE_URL", "https://litellm.spmadridph.com/v1")).rstrip("/")
    key = (api_key or os.getenv("LITELLM_API_KEY", "")).strip()

    if not key or key.startswith("sk-placeholder"):
        return list(FALLBACK_MODELS)

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                if models:
                    # Clean and sort unique models, prioritizing common models
                    unique = sorted(list(set(models)))
                    return unique
    except Exception:
        pass

    return list(FALLBACK_MODELS)


def render_full_prompt(
    instructions: str = "",
    primary_csus: list[str] | None = None,
    secondary_csus: list[str] | None = None,
    primary_rfds: list[str] | None = None,
    secondary_rfds: list[str] | None = None,
    custom_rules: dict[str, list[str]] | None = None,
    max_summary_chars: int = 180,
) -> str:
    """Renders the full system prompt exactly as will be provided to LLM."""
    from engine.summarizer import (
        PRIMARY_CSU_OPTIONS,
        PRIMARY_RFD_OPTIONS,
        SECONDARY_CSU_OPTIONS,
        SECONDARY_RFD_OPTIONS,
        _load_active_custom_rules,
    )

    p_csu = primary_csus if primary_csus is not None else PRIMARY_CSU_OPTIONS
    s_csu = secondary_csus if secondary_csus is not None else SECONDARY_CSU_OPTIONS
    p_rfd = primary_rfds if primary_rfds is not None else PRIMARY_RFD_OPTIONS
    s_rfd = secondary_rfds if secondary_rfds is not None else SECONDARY_RFD_OPTIONS

    primary_csu_formatted = "\n".join(f"  - {c}" for c in p_csu)
    secondary_csu_formatted = "\n".join(f"  - {c}" for c in s_csu)
    primary_rfd_formatted = "\n".join(f"  - {r}" for r in p_rfd)
    secondary_rfd_formatted = "\n".join(f"  - {r}" for r in s_rfd)

    active_instructions = instructions.strip() if instructions and instructions.strip() else DEFAULT_OPERATIONAL_DIRECTIVES

    c_rules = custom_rules if custom_rules is not None else _load_active_custom_rules()
    custom_rules_section = ""
    if c_rules.get("csu_rfd_rules") or c_rules.get("summary_rules"):
        lines = []
        if c_rules.get("csu_rfd_rules"):
            lines.append("Reviewer CSU/RFD Directives:")
            for rule in c_rules["csu_rfd_rules"]:
                lines.append(f"  * {rule}")
        if c_rules.get("summary_rules"):
            lines.append("Reviewer Summary Directives:")
            for rule in c_rules["summary_rules"]:
                lines.append(f"  * {rule}")
        custom_rules_section = f"\n### ACTIVE HUMAN REVIEWER TUNED DIRECTIVES (HIGHEST PRIORITY):\n" + "\n".join(lines) + "\n"

    system_prompt = (
        "You are an expert Data Analyst and Credit Operations Specialist for RCBC Auto Loan field collection reports.\n"
        "Your task is to analyze the collector's remark and return a valid JSON object strictly matching this schema:\n"
        "{\n"
        '  "csu_reasoning": "<1-2 sentence explanation analyzing contact entity, unit sighting, and dispute vs refusal first, and why alternatives were disqualified>",\n'
        '  "csu": "<Exact CSU verbatim from ALLOWED_CSU determined from the reasoning above>",\n'
        '  "csu_confidence": "<high | medium | low>",\n'
        '  "csu_alternatives": ["<Other candidate CSU from ALLOWED_CSU if uncertain, otherwise empty list []>"],\n'
        '  "rfd_reasoning": "<1-2 sentence explanation analyzing whether dispute/hardship overrides refusal first, and why alternatives were disqualified>",\n'
        '  "rfd": "<Exact RFD verbatim from ALLOWED_RFD, or empty string \\"\\" if zero info/unknown/unlocated, determined from the reasoning above>",\n'
        '  "rfd_confidence": "<high | medium | low>",\n'
        '  "rfd_alternatives": ["<Other candidate RFD from ALLOWED_RFD if uncertain, otherwise empty list []>"],\n'
        '  "detailed_rfd": "<3-clause string: [RFD clause]; [TALK TO clause]; [Statement]>",\n'
        '  "summary": "<concise summary note under max_chars>"\n'
        "}\n\n"
        "DECISION UNCERTAINTY & ALTERNATIVE HANDLING:\n"
        "- If multiple classifications are plausible or you cannot decide definitively:\n"
        "  1. Select the single option you are MOST confident in for 'csu' and 'rfd'.\n"
        "  2. Set 'csu_confidence' and/or 'rfd_confidence' to 'medium' or 'low'.\n"
        "  3. Populate 'csu_alternatives' and/or 'rfd_alternatives' with the other viable candidates.\n"
        "  4. In 'csu_reasoning' and 'rfd_reasoning', clearly explain why the top candidate won and how the alternatives differ.\n"
        "- If completely certain, set confidence to 'high' and alternatives to [].\n\n"
        f"ALLOWED_CSU (Select EXACTLY one verbatim from this official RCBC list):\n"
        f"* PRIMARY MATRIX (Default for field visit outcomes):\n{primary_csu_formatted}\n"
        f"* SECONDARY/SPECIALIZED (Only if explicitly stated in note):\n{secondary_csu_formatted}\n\n"
        f"ALLOWED_RFD (Select EXACTLY one verbatim from this official RCBC list):\n"
        f"(Note: Select exactly one verbatim, or empty string \"\" if address/client unlocated or unknown):\n"
        f"* PRIMARY TIER (Most common operational RFDs):\n{primary_rfd_formatted}\n"
        f"* SECONDARY/SPECIALIZED TIER (Specific hardships):\n{secondary_rfd_formatted}\n\n"
        f"{active_instructions}\n"
        f"{custom_rules_section}\n"
        "Return ONLY the raw JSON object. Do not include Markdown code blocks (no ```json), explanations, or preamble."
    )
    return system_prompt
