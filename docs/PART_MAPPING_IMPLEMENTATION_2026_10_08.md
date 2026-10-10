# Part Mapping implementation — 8 October 2026

## Behavior

Part Mapping now groups active rows by **source sheet plus the configured mapping label**. Material Cut List (`5. Material Cut List Price`) and Tool Shop Items use `Machine Piece Description`; CNC Cut List uses `Part Category`. CNC `Plate Part to Cut` remains line-level evidence and is shown in the source detail table. A blank mapping label remains its own row. Missing or ambiguous label headers are diagnosed and block assignment saves until the workbook header is resolved.

The v2 group identity does not carry an old assignment forward. Existing `PartMappingReview` rows stay immutable and appear as previous-assignment evidence when their sheet/row coordinates still match. This is especially important when CNC moves from plate description to category, or a former cross-sheet description group splits. Each current row resolves its own latest previous evidence; the user must explicitly select and save a new assignment. Records without matching source coordinates cannot be safely associated with a current group and remain history only.

The review displays source values from the saved workbook, including exact source header and cell coordinates. Material Cut List shows Material to Cut, Dimension to Cut (mm), Quantity Nos, Item Group 1 and In Use by default; expanded details add Remarks and CR Log. Toolshop and CNC show their parsed fields, with CNC plate detail and dimensions retained. Missing and blank source values remain distinguishable; values are not recalculated or converted to zero. Each group has a bounded, scrollable details block.

Source filters (All, MS List, Toolshop, CNC), review-state filters and source-text search only change visibility. Draft choices, reasons and browse context remain local until an explicit Save. Single-group Save writes only that group. Batch Save previews every pending group, including filtered-out groups. Explicit clears are saved as audited rows; prior decisions remain in history. A successful save refreshes Grist state and retains other drafts only when workbook, association and group evidence are unchanged. Failed attempts preserve the exact idempotency key and payload for retry. Changed evidence holds local choices aside for explicit review rather than silently reapplying them.

The Part combobox uses bounded server search over permanent number, current name, description, variant, stable ID and supported aliases. It excludes retired, unpublished and ambiguous-name records. Scope warnings are advisory and selection does not edit intended sharing. `View Part` opens the selected Part. `Browse / create Part` carries the source group/workbook context into Parts and returns a chosen Part as an unsaved mapping draft; it never saves the mapping automatically.

## Persistence and compatibility

Confirmed assignments are append-only `PartMappingReview` rows in the Safari Manufacturing Grist document. Rows reference `ProductPart` and stable Part identity where available, and freeze the workbook key/hash, association key/version, mapping group, source sheet/row/description, Part number/name/revision/metadata used, reviewer, reason, time, request key/fingerprint and batch version. Partial batches are supported: unresolved groups have no new review rows. This work required no Grist schema migration. The local Parts journal serializes the supported writer; it is not business-data storage.

After a Grist save, the application reads back the review rows before showing `Saved`. Reloading the same workbook restores the current sheet-scoped assignments from Grist. Older description-only groups remain review evidence, not a hidden migration. An audited clear records a null Part decision without deleting the previous assignment history.

## Verification

- Full Python suite: 207 tests run, 206 passed, 1 platform-specific skip.
- UI suite: 36 tests passed across 9 files. TypeScript check and production UI build passed.
- A read-only validated Safari source document was copied with Grist's `asTemplate` option into a disposable document. A synthetic canonical Part and one mapping row were written only in that copy. A fresh Grist client/registry recovered the saved Part Mapping row; an exact retry returned idempotently without a duplicate; the same label in a second sheet stayed unresolved. The copy was moved to Grist Trash. No production Part Mapping assignment was created.
- An isolated local browser/mock session exercised the single-group save and visible saved state. Part Mapping was inspected at default desktop width and 840 px; Files and Parts were also inspected after the shared masthead change. Automated UI tests cover failure/retry, draft preservation, filters, stale evidence, keyboard selection and Parts return flow.

The existing production mapping table remains unchanged by this verification. As of the latest read captured before this work it contained no mapping assignments, so the owner’s live Chassis was not modified. Production write behavior is not inferred from local SQLite or the browser mock: the persistence evidence above comes from the separate real Grist test copy.

## Remaining boundaries

- Older mapping rows without source sheet/row evidence cannot be matched safely and require a manual review from the immutable history.
- This change does not create manufacturing composition, Model Code configuration or costing snapshots from a mapping. Those remain separate governed actions.
- Existing project gates remain: automatic Summary import/authority integration and the approved Change Request flow are not implemented by Part Mapping.

## Complete-prompt follow-up checkpoint — 8 October 2026

The complete owner prompt adds the detailed Parts-detail, defect-matrix, visual-check and completion-report requirements that were cut off in the earlier pasted copy. The current implementation includes the mapping-policy fixes and the explicit Rev A baseline/reconciliation flow below. This is a checkpoint report, not a declaration that every requested verification is complete.

### Mapping behavior and UI

- Initial Part assignment is reason-free. Replacing or clearing a previously saved assignment requires a reason, based on persisted mapping history. An unchanged assignment confirmed against refreshed evidence records a source-context update without asking for a replacement reason. The backend enforces this, records an explicit action type, and stores the initial action with the audit text `Initial Part assignment`.
- Batch save identifies the changed groups that need reasons; it permits initial assignments alongside changed assignments, keeps exact payload/idempotency evidence for retry, and preserves drafts when some groups save and others fail. A search query alone is not a selection; returning from Part browsing returns a draft selection.
- Search results carry their producing query, display loading separately from empty results, cancel/deduplicate requests where supported and reject stale or out-of-order responses for keyboard and pointer selection.
- Mapping groups default collapsed and retain expansion separately from source-detail expansion. Group state is keyed by workbook and sheet-scoped group identity and survives filtering, navigation, save and reload. Collapse preserves draft choices. Expand/collapse-all applies to displayed groups; hidden source-detail tables are not mounted while collapsed.
- The Part detail view includes baseline status, workbook identity/hash, applicability/completeness, normalized accepted requirements, incoming comparisons and decisions, pending proposals, and revision lifecycle. Baseline, comparison, requirement, decision and proposal panels are collapsible.

### Baselines and workbook comparisons

An explicit Part Mapping processing panel lets the user classify each source family as Applicable or Confirmed not applicable. A missing/unreadable sheet or unmapped/incomplete applicable family cannot be treated as not applicable. Establishment gathers all accepted groups mapped to the Part from one workbook, validates current workbook and association evidence, writes normalized requirement and source provenance rows, then confirms the Rev A baseline pointer. The request is resumable and idempotent; requirements are not published as an established baseline during partial writes. Existing manual, purchase or component content is displayed and preserved; append requires explicit confirmation. Establishing the baseline leaves Rev A draft until the separate finalization action.

Later workbooks save immutable observations and compare against the accepted revision. Quantity is exact, kilogram weight is compared to two decimals, and raw values/units remain provenance. Rates, costs and audit/descriptive-only fields remain evidence without defining an engineering difference. Missing family evidence blocks deletion inference. Unanchored material substitutions remain ambiguous until explicitly matched. Keep-baseline decisions retain discrepancies; CR proposals stay pending and do not mutate the accepted baseline, Part revision, configuration or historical snapshot; a different-Part decision returns a mapping draft. Decisions are tied to exact incoming evidence and baseline references.

### Grist deployment and recovery evidence

Schema `safari-part-baseline-reconciliation-grist-2026-10-08.v11` was applied additively to the validated Safari Manufacturing document. The pre-apply native `.grist` backup passed SQLite integrity validation and has SHA-256 `C9DE749F60003ECF130F91628F863E08936EF34AAD66190E28AB1385C502D7EF` at `%TEMP%/CostingFilesToGristRemap/safari-grist-backups/safari-manufacturing-bAPdkEDn7brbqTrfVsRmXZ-20261008T122759Z.grist`. The pre-apply plan had seven new tables, eight table extensions and zero type changes. The post-apply plan reported no pending schema changes.

Production readback after apply: `ProductPart` 4, `PartRevision` 4, `LineMaster` 313, `PartMappingReview` 0, `PartRegistryRequest` 10, `PartRegistryCoordinator` 1, and `CostingSnapshot` 1. The v10 intended-sharing tables contained 3 state rows, 30 normalized Model Code links and 30 events. Every v11 baseline/comparison table remained empty. No production Part mapping or baseline fixture was written.

A separate disposable Grist document verified a synthetic Part's baseline write and fresh-registry recovery after the Grist commit response was deliberately lost. Readback recovered the established Rev A pointer, one normalized revision line, one source observation, three family records and one detail requirement. A changed material was classified as ambiguous; an explicit row-to-baseline match persisted one decision while keeping Rev A unchanged, creating no proposal, and creating no `CostSnapshot`. The disposable document was moved to Grist Trash. This is real Grist API readback; local SQLite is used only for writer coordination/retry journaling.

### Verification and measured performance

- Full Python suite: 226 tests, 225 passed and one platform-specific skip.
- UI suite at this checkpoint: 42 Vitest tests passed across 10 files, plus the 2 standalone mapped-files view-model tests. TypeScript check, Vite production build and `git diff --check` passed.
- An isolated current-code browser session opened the HF workbook and rendered 25 groups/100 active rows. Groups were collapsed by default; the baseline Part selector stayed unavailable until a saved mapping existed. Parts and Mapping were visually reviewed at 1366×900 and 823×900. The production Chassis detail and mapping page were read only. During one mapping view the adjacent workbook-preview indicator still showed `Reading workbook…` while the mapping endpoint had returned parsed rows; a prior browser check later showed the bounded preview loaded. The indicator’s timing remains an observed UI limitation, not a Grist persistence claim.
- Production API observations below are single local-environment samples, not timing guarantees. “Prior loop” is a replay of the known pre-fix access pattern against the current document, not a recorded historical benchmark.

| Operation | Observed result |
| --- | --- |
| Parts register, optimized | 147.7 ms; 4 Grist reads (one each for ProductPart, PartRevision, PartMetadataVersion, PartNameAlias) |
| Previous repeated register lookup pattern, replayed at N=3 | 424.7 ms; 13 reads |
| Part details with request-scoped reuse | 3917.9 ms; 31 reads across 31 tables |
| Same detail route with reuse bypassed | 5288.9 ms; 88 reads |
| Part name preview with request-scoped reuse / bypassed | 84.9 ms / 130.3 ms; 3 / 4 reads |
| Part search, cold / warm | 69.9 ms and 2 reads / 0.2 ms and 0 reads; warm result uses a bounded five-second document-keyed cache |
| Mapping load, extraction cache cold / warm | 7287.4 ms and 18 reads / 912.4 ms and 16 reads |

The mapping UI keeps views mounted across navigation, consumes confirmed targeted mapping responses and refreshes Part creation in the background after displaying the confirmed record. These are implementation observations, not separate timing measurements. The combined cold/warm mapping sample also includes local ODS and repository refresh/cache work, so it is not a controlled isolated workbook-parser benchmark.

#### Disposable-Grists writer request reduction

The first timed operations exposed repeated schema-column discovery: each Parts write asked Grist to enumerate columns for every table, even though the writer only needed the table names before it knew which table schema to validate. The registry now requests the table list without eager columns, and fetches columns only for the specific table being validated. The schema/data behavior is unchanged. Measurements below compare one operation per run against disposable copies of the validated document; they are local samples and not a timing guarantee.

| Operation | Before fast schema listing | After fast schema listing | Change |
| --- | --- | --- | --- |
| Part-name preview | 179.3 ms; 3 HTTP requests | 112.6 ms; 3 requests | One sample each; request count unchanged |
| Part creation and Grist publication | 4,772.1 ms; 95 requests, including 66 table-column requests | 1,261.6 ms; 31 requests, including 2 table-list requests | 73.6% lower elapsed time; 67.4% fewer HTTP requests |
| Intended-sharing publication | 4,648.6 ms; 98 requests, including 66 table-column requests | 1,324.2 ms; 34 requests, including 2 table-list requests | 71.5% lower elapsed time; 65.3% fewer HTTP requests |
| One mapping save with Grist read-back | 4,104.4 ms; 77 requests, including 66 table-column requests | 1,406.4 ms; 13 requests, including 2 table-list requests | 65.7% lower elapsed time; 83.1% fewer HTTP requests |

The Part Mapping page’s post-save endpoint projection was 100.3 ms and three HTTP reads after the optimized save. The current UI displays the confirmed targeted save response rather than reloading the entire mapping page after every save. Part creation similarly displays the confirmed created Part while its register refresh runs in the background. The sample above times Part creation/publication and intended-sharing publication as separate operations; it does not imply a rate guarantee.

#### Navigation and rendered-UI checks

In one isolated-browser pass against the current production-backed read-only API, switching from Mapping to the already-loaded Parts detail took 608 ms to the next accessibility snapshot and issued two GETs (scope targets and the Parts register). Returning to Part Mapping took 543 ms to the next snapshot and issued one GET for mapping evidence. The app immediately reused the existing 25-group/100-row view and showed a revalidation indicator while that request completed; it did not blank the page or discard the current review. These are one-pass interaction samples and include browser accessibility-tree collection, not repeatable UI timing guarantees.

The current Part detail uses native `<details>/<summary>` disclosures. Overview opens first; baseline, sharing, process lines, components, purchase details, drawings, actual-configuration use and history start collapsed. Keyboard-operable summaries expand individual sections, and the section shortcuts open the matching section before scrolling. Parts and Mapping were inspected in the isolated browser at 1366×900 and 823×900. The production Chassis detail and HF workbook mapping review were read only: no baseline, Part Mapping row, Part, or intended-sharing record was written.

An App-level integration test now exercises the owner’s four-choice navigation scenario against a stateful API mock: it selects three Parts, creates the fourth Part from the mapping handoff and returns it to the initiating group, saves only the first group, collapses the remaining drafts, navigates through Parts and back, confirms those three selections and collapse states remain, then batch-saves only those groups against the incremented review version. The test confirms the UI request payloads and state transitions; its mock is not Grist persistence evidence. The separate disposable-Grist write/read-back evidence above remains the basis for the persistence claim.

The baseline verification matrix was expanded in `tests/test_part_baseline.py`: isolated Grist-test-double cases cover Toolshop-only, CNC-only and combined-family publication; two concurrent writer registries cannot establish two initial baselines for one Part; complete comparisons distinguish reordered/duplicate matches from additions and deletions; rate/remark-only changes remain matches; keep-baseline records an explicit old-data decision without changing accepted Rev A requirements; and Use a different Part returns the exact workbook/group/row/hash provenance. This uncovered and fixed a guard-order issue where an already-established Part could produce an append-confirmation error before the existing-baseline rejection. These tests use `MemoryGrist`; they do not add a second production or disposable-Grist concurrency claim.

## Part baseline/comparison defect follow-up — 9 October 2026

This follow-up resolves the five findings in `PART_BASELINE_IMPLEMENTATION_REVIEW_2026_10_09.md` while keeping prior Grist evidence and accepted Rev A unchanged.

| Finding | Fix and regression coverage |
| --- | --- |
| Manual correspondence generated null or misleading field differences | Parse frozen `IncomingValues` JSON directly; validate the complete normalized physical object before writing; compare against the selected baseline line using normalized physical fields. Tests cover MS Plate → Aluminum with unchanged quantity, dimensions, units and weight, multi-field changes, malformed/missing evidence, no false proposal, proposal references and response-loss recovery. |
| Failed comparison retry stayed disabled | Retain and retry only the same Part/compare operation with the exact request key and payload. UI tests cover a failed attempt, reload restoration, lockout of competing actions and exact request reuse. |
| Invalid baseline confirmation locked the form | Validate family evidence, applicability, completeness and append confirmation in the client; the backend remains authoritative and returns structured retry dispositions. Definite pre-write rejection unlocks correction; uncertain/partial publication retains its exact attempt. UI tests cover validation explanation, correction and exact retry across reload. |
| Decisions used stale workbook/mapping context | Revalidate path/file identity, hash, association/version, mapping version/policy, assigned group fingerprints, family completeness, active selected Part and accepted baseline before all four decision actions. Baseline-only deletions use current hash/mapping/family evidence. Backend matrix tests alter hash, association or mapping for every action and verify no new decision/proposal. |
| Decision retries minted new request IDs | Persist exact comparison/action/differences/body/key across reload and lock competing edits. The Grist request fingerprint includes the complete request body as well as semantic decision fields. Response-loss tests cover keep, match, proposal and replacement, enforce one decision/proposal/matched row, and reject the same key with a changed body. |

The application schema advances to `safari-part-baseline-reconciliation-grist-2026-10-09.v12`. Its additive change adds numeric `PartWorkbookComparison.MappingVersion`, captured by comparisons and checked before decisions. The last documented applied schema is v11; this follow-up did not apply v12 to production or claim a real-Grist write. Apply and read back the additive v12 field before deploying this code. See [API/schema contract](API_SCHEMA.md).

Latest verification for this follow-up: full `.venv` Python suite 232 passed / 1 skipped; focused baseline suite 23 passed; retry-disposition web tests 3 passed; full Vitest suite 53 passed across 12 files; standalone mapped-files checks 2 passed; TypeScript validation, production Vite build and `git diff --check` passed. The backend write/retry tests use `MemoryGrist`; no disposable or production Grist records were written for these corrections.

The recovery guard also distinguishes two pending-publication cases. A browser holding the original request key can retry that exact body; if the workbook, association, mapping or pre-write validation has changed, the server returns `PART_BASELINE_RECOVERY_REQUIRED` and preserves the retry identity. A different request is blocked with `PART_BASELINE_RECOVERY_BLOCKED`. If a browser opens a Part already marked recovery-required but has no stored original request, the UI disables a second publication and explains that the original request must be recovered from its originating browser.

An isolated local-browser fixture with mocked API responses was prepared. The computer-use helper could not activate the available Chrome window on its first attempt or after refreshing the window list, so interactive browser verification was stopped. No user browser tab was navigated. Automated UI tests are the browser-verification boundary for this follow-up.

For possible historical manual-match errors, query `PartWorkbookDecision` where `Action="match_correspondence"`, follow its linked difference and `ResolvedFromDifference`, then compare `IncomingValues.physical` with the matched difference's `FieldDifferences`. Flag unchanged normalized fields represented as null/different; preserve those rows and request identities. Any correction should be separately reviewed and appended from verified workbook/mapping evidence. No historical Grist evidence was rewritten or queried during this follow-up.

### Remaining verification and project gates

- The five code findings in this review are implemented and covered by automated regressions. An isolated interactive browser check and a disposable-Grist readback for these exact corrections remain outstanding; the computer-use helper could not activate Chrome, and no Grist writes were made for this follow-up. Tests against `MemoryGrist` are not evidence of live persistence.
- The approved CR workflow, automatic Summary import/costing-authority integration, and reviewed legacy Part identity/revision classification remain unimplemented project gates. No Part may be advanced beyond Rev A until approved CR control exists.

No pull request has been merged or deployed. Apply and verify additive schema v12 before deploying this branch.

## Integration verification and v12 rollout preparation — 9 October 2026

This section supersedes the earlier statement that the five corrections lacked disposable-Grist and interactive-browser verification. The production document was only read. The explicit configured target (`bAPdkEDn7brbqTrfVsRmXZ`, workspace `3`, exact name `Safari Manufacturing`) still plans exactly one additive change: numeric `PartWorkbookComparison.MappingVersion`. No table creation or column type change is pending. `MappingVersion` freezes the saved mapping review version associated with each comparison, so later decisions can reject changed assignments rather than infer their historical context.

Schema v12 was applied only to a native Grist copy. The clean copy started with 64 tables and 2,493 records; a fresh post-apply plan was empty, the new column was Numeric, and all copied record counts and fingerprints were unchanged. A separate schema probe established that Grist changes the legacy blank-formula `LineDetail.EngineeringAttributes` placeholder from Any to Text on its first serialized write. The plan now treats that one Any/Text pair as compatible; a focused regression confirms other type mismatches remain visible. The final production plan was rerun after this adjustment and remained the single MappingVersion addition.

Real-Grist write/read-back on a disposable v12 document verified synthetic canonical Parts, initial mapping publication without a reason, complete Rev A baseline establishment, normalized requirements and workbook provenance, and Rev A preservation. Incomplete family evidence was rejected before publication. Material substitution comparison produced only the actual material change; unchanged quantity, dimension, units and weight were preserved. Malformed correspondence evidence created no decision/proposal. Keep-baseline, correspondence match, pending Part-change proposal and replacement-Part mapping draft were exercised; accepted baseline content remained unchanged, proposals stayed pending CR, and replacement selection did not save a mapping automatically. Hash, association-version and saved-mapping changes, including a baseline-only deletion, were rejected as stale before new decision/proposal rows. An old comparison without MappingVersion was rejected as stale and required recomparison; no historical version was inferred.

Committed-response loss was injected through a local response proxy after disposable Grist returned success. Fresh Grist reads confirmed exact-key retries recovered baseline, comparison, decision and mapping outcomes without duplicate rows. Same-key payload changes conflicted. The browser preserved decision identity across reload and recovered the original decision. Comparison response loss exposed a browser issue: a stalled write could keep the retry disabled. Baseline writes now abort after 30 seconds if no response arrives, and incomplete response bodies are reported as uncertain immediately; both preserve the attempt and expose its exact-retry path. Focused API/UI regressions cover both cases and request reuse. In the browser, a real comparison response was dropped after Grist committed, Retry comparison became enabled, and the exact retry recovered the same Grist row. The disposable browser also verified mapping save/replacement reasons, unsaved selection retention through collapse and navigation, baseline completeness guidance, and the current comparison/Part summary at desktop and narrow widths.

The production rollout is prepared but not performed. Follow [the v12 rollout runbook](PART_BASELINE_V12_PRODUCTION_ROLLOUT_RUNBOOK.md) for the exact-target check, fresh native backup and checksum, re-planning, additive apply, post-apply verification, compatible application rollout, read-only smoke checks and recovery. No production schema or business row was written; this PR remains open and has not been merged or deployed.
