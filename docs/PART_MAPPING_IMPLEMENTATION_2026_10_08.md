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

- Full Python suite: 218 tests, 217 passed and one platform-specific skip.
- UI suite: 41 Vitest tests passed across 9 files, plus the 2 standalone mapped-files view-model tests. TypeScript check, Vite production build and `git diff --check` passed.
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

### Remaining work before calling the full prompt complete

- Execute any remaining cases from the full defect matrix not covered by the automated suite or disposable Grist readbacks; tests alone do not replace live Grist readback or visual verification.
- The approved CR workflow, automatic Summary import/costing-authority integration, and reviewed legacy Part identity/revision classification remain unimplemented project gates. No Part may be advanced beyond Rev A until approved CR control exists.

No pull request has been merged or deployed. Continue this checkpoint on `codex/part-identity-foundation` and update the existing PR only with the remaining limits stated explicitly.
