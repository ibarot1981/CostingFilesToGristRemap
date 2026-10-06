# Safari Manufacturing — Implementation Roadmap

Updated: 1 October 2026

Planning rule: every delivery milestone ends with a demonstrable artifact, reconciliation evidence, tests, and updated documentation/status.

## Numbering and current position

This roadmap previously used *milestone* for two different sequences. The
decimal labels `0.1`–`0.7` and `1.1`–`1.4` identify work packages within a
phase. The whole-number labels **Delivery Milestone 1, 2, 3** identify
implementation efforts and their handoffs. Historical document names and
references retain their original labels; the crosswalk and evidence are in
`MILESTONE_NUMBERING_AUDIT.md`.

| Delivery effort | Phase work covered | Current position |
|---|---|---|
| Milestone 1 / PR 1 | Phase 0 packages 0.2–0.4, with governance and some 0.5 work | Merged; conditional pass, not a Phase 0 exit |
| Milestone 2 | Phase 0 package 0.5 governance and an accelerated Phase 1 S1KHF semantic-parity pilot | Committed as `476ad3a` and synced to `origin/main`; Phase 0 gates remain open |
| Milestone 3 — in progress | Phase 1 package 1.1, with the needed Phase 0 package 0.6 CLI mapping extraction and 0.7 pilot evidence carried into its entry work | Safari v5 schema and 2,043 normalized records applied; 313 lines are queryable and repeat import is a no-op; 10 mappings and Phase 0 owner gates remain open |

We are working in **Phase 1**, following the Milestone 2 pilot. Phase 0 is
not formally closed. Milestone 3 must track its outstanding gates separately
and must not claim Phase 0 completion merely because normalized ingestion
passes. The 45-entity target design is broader than Milestone 3; see
`data-model.html` and `MILESTONE_3_IMPLEMENTATION_PROMPT.md` for the subset.
The current implementation evidence and exact source drift are in
`MILESTONE_3_PILOT_RECONCILIATION.md`. The selected workbook changed after the
Milestone 2 archived report, so its local projection must not be called an
accepted Safari normalization until the validated snapshot is compared.

## Operating rules for every milestone

1. Inspect `git status` before work and preserve unrelated user changes.
2. Update the requirement ID, decision record, schema/API contract, tests, and `requirements-status.html` in the same change.
3. Default all imports and synchronization to dry-run.
4. Never write to a source ODS file or `Costing-New` unless that capability has passed its later approval gate.
5. Store source provenance and produce a reconciliation report for every import.
6. Demonstrate the milestone against a named fixture and the selected real pilot; do not rely only on mocks.
7. Commit in small, reviewable units. Do not include secrets, local `.env` values, downloaded business files, or generated caches.

## Phase 0 — Foundation and controlled mapping

### Work package 0.1 — Governance and source boundaries

**Outcome:** everyone can tell which system is authoritative for each field and what the software may change.

Deliverables:

- revised requirements baseline and decision log;
- living HTML requirements/status register;
- source-authority matrix and transition rules;
- environment-variable split between legacy Grist and Safari Manufacturing;
- safety guard that rejects the legacy document ID for new-schema writes;
- Architecture Decision Record template and change discipline.

Verification:

- documentation links render locally;
- no secrets in tracked files;
- automated configuration test proves the legacy document cannot be an apply target.

Exit gate: owner accepts the source-boundary matrix. The next milestone creates or validates the new document before any schema apply.

### Work package 0.2 — Safari Manufacturing document creation and schema bootstrap

**Outcome:** exactly one correctly targeted Safari Manufacturing Grist document exists, and its design is reproducible and reviewable before business data is written.

Deliverables:

- Grist organization/workspace discovery using the authenticated API;
- explicit workspace selection when more than one writable workspace exists;
- idempotent create-or-validate command for a document named `Safari Manufacturing`;
- duplicate-name detection that stops for review instead of creating or selecting silently;
- returned document ID written only to local/deployment configuration and never committed;
- document identity check and hard rejection of the legacy `Costing-New` document;
- versioned logical and physical schema manifest;
- tables for identity, file registry, associations, import batches, issues, and audit events;
- repository interfaces independent of Grist;
- Grist adapter plus in-memory test adapter;
- schema diff/plan command with dry-run default;
- explicit bootstrap command requiring the new document ID and typed confirmation.

Verification:

- repeated create-or-validate runs do not create duplicate documents;
- insufficient permission, multiple matches, and wrong-workspace cases fail safely;
- create and update plans are deterministic;
- schema creation is idempotent in an isolated test document;
- references and uniqueness checks match the domain rules;
- the code contains no direct UI-to-Grist dependency.

Exit gate: the document is created/validated, its ID is recorded safely, and the reviewed schema plan is applied only to Safari Manufacturing.

**Live bootstrap evidence (19 September 2026):** the owner-created document was
reused only after exact name, document ID, and selected workspace validation.
Schema `safari-foundation-2026-09-19.v2` was applied to the validated document:
six missing idempotency columns were added across FileModelAssociation,
ImportBatch, and AuditEvent. The extra default `Table1` was deleted only after
confirming it had zero records. Final remote inspection found exactly the 11
manifest tables, matching columns and types, and the follow-up CLI plan was a
no-op. The validated document settings are held in ignored local configuration;
no document ID is recorded here. The catalog was imported after explicit owner
approval; no costing-file association records were written.

### Work package 0.3 — Canonical identity import

**Outcome:** Product, Model Number, and Model Code identities exist with provenance and visible exceptions.

Deliverables:

- importer for `Product-ProductModelNo-ModelCode.ods`;
- exact source text preservation plus normalized comparison keys;
- Product, Model, Code, and Alias upsert services;
- duplicate GC source items deterministically map to the approved `GCMC-7.5`, `GCMC-10`, `GCMC18-7.5`, and `GCMC18-10` codes; blank descriptions remain reconciliation issues;
- bush-type records classified legacy-spares-only;
- import batch summary and row-level provenance.

Verification:

- expected source counts are checked, not silently assumed;
- re-running the same file changes nothing;
- changed source rows create a proposed change set;
- duplicate codes cannot become active until disambiguated.

Exit gate: canonical catalog and exception report approved.

### Work package 0.4 — Costing Explorer and associations (delivered in Milestone 1)

**Outcome:** the user can browse the Product Costing tree, inspect a workbook, and explicitly map it to a model and eligible codes.

Deliverables:

- recursive explorer API with safe root confinement;
- tree UI with search, type/status filters, health badges, and metadata;
- ODS sheet preview and external-link indicators;
- Product/Model/Code selectors sourced through repository APIs;
- proposed-change preview and association save;
- rules: one file → one model; one model → many files; one code → one active file;
- audit and processing-queue record after a successful association;
- conflict messages that identify the current owning file.

Verification:

- path traversal, symlink/reparse escape, invalid extension, and unreadable/encrypted cases;
- duplicate-code and cross-model association tests;
- moved/changed file recognition by path plus hash;
- keyboard navigation and basic accessibility checks;
- no ODS write occurs.

Exit gate: satisfied for the several-file pilot. Three owner-provided S1KHF
files are mapped to Safari 1000 HF, with the exact code sets and actor/reason
audited. Broader Phase 0 acceptance remains open for UI acceptance and conflict
or supersede review.

#### Implementation evidence — 21 September 2026

- `app/filesystem_catalog.py` implements root-confined lazy directory children,
  traversal/absolute-path/reparse-point checks, candidate classification, and
  on-demand ODS inspection/hash calculation. It inspects lexical path components
  with `lstat()` before canonical resolution so a Windows junction's reparse
  marker cannot be hidden by following its target; a regression test exercises
  the Windows reparse-attribute path.
- `app/web.py` exposes explorer/tree, inspect, bounded preview, Product/Model/
  Code categories (`active`, `available`, `legacy`, and owner-bearing `conflicts`),
  validation, save, history, and mapped-files contracts with stable error
  codes. Workbook reads use `pyexcel-ods3` and direct XML formula metadata; no
  source workbook is saved.
- A synthetic ODS preview contract test verifies sheet names and dimensions,
  bounded rows/columns, formula text alongside its cached value, external-link
  count/warning, the read-only marker, unsupported-extension rejection, and
  typed corrupt/encrypted errors. The preview UI renders the same metadata.
- `app/domain.py` and `app/repository.py` provide typed storage-neutral records,
  cardinality validation, optimistic versions, idempotency keys, supersede
  history, audit events, and queued import-batch records.
- `app/catalog_grist.py` compares canonical rows against the validated Grist
  identity tables and performs reviewed Product/Model/Code/Alias/Issue/Batch
  upserts; identical source hashes are no-ops, changed rows are explicit plan
  updates, API retries match existing canonical identities, and DateTime writes
  use Grist epoch-second encoding. Grist `Any` list cells use the typed `L`
  representation. The live document/schema gate is complete.
- Catalog importer `catalog-0.2` marks repeated canonical Model Codes inactive
  and emits an error-level reconciliation issue unless the distinct source
  value is an approved GC alias. Association validation rejects inactive codes.
  Synthetic tests cover the in-memory validator and Grist upsert; re-reading the
  actual 175-row catalog produced 24 Products, 42 Models, 173 Codes, 6 aliases,
  68 blank-description issues, and zero inactive codes.
- `app/grist_repository.py` persists request keys/fingerprints with association,
  audit, and queue rows; retries recover partial multi-table writes and reject
  idempotency-key collisions. `app/grist_types.py` centralizes Grist Ref/DateTime
  conversions. Save attribution comes from trusted proxy identity headers, not
  a caller-provided body field; proxy configuration is a deployment requirement.
- The initial `catalog-0.1` import populated 24 Products, 42 source Models,
  173 Codes, 6 aliases, 68 reconciliation issues, and one import batch. The
  reviewed `catalog-0.2` plan was applied to the validated Safari document in
  Work: it updated 17 existing ProductModelCode rows (`Description` and
  `SourceValues`) and recorded a second ImportBatch. The live inventory now has
  24 Products, 44 ProductModel rows, 173 Codes, 6 aliases, 68 issues, and 2
  batches. Two active ProductModel rows do not match a current source identity;
  neither owns an active code, and both were retained for owner reconciliation.
  A follow-up plan is idempotent. The source ODS hash is unchanged.
- `ui/src/App.tsx` and `ui/src/styles.css` provide the lazy Explorer tree,
  cascading association form, validation-gated Save, bounded preview, and
  Mapped Files review view. Explorer search now uses the recursive metadata-only
  ODS query, reconstructs folder ancestry for nested matches, preserves lazy
  navigation when filters are active, and indicates result truncation.
- 62 Python `unittest` cases run (61 pass; the symlink-creation case is skipped
  where the platform does not permit it). This Windows host successfully
  created and removed a temporary directory junction; the escape was rejected,
  and the simulated reparse-attribute regression passed. Coverage includes root safety,
  catalog duplicates/GC/Bush rules, code ownership, interrupted Grist-write
  recovery, idempotent-key collision, supersede history, schema target
  revalidation, proxy actor attribution, and Grist duplicate-name refusal. The
  UI initially had 2 dependency-free Node view-model tests and 5 Vitest component/workflow
  tests; typecheck and production build pass.
- A local browser smoke used the real Product Costing root: Explorer loaded 503
  files; Mapped Files loaded 504 ODS entries and its unmapped filter, search,
  and history drawer worked. The read-only JKM150 preview showed 16 sheets,
  2,593 × 23 cells on the selected sheet, and 61,108 external references. A
  `JK Mini Crane → JK Mini 150 → JK150M` proposal for
  `JKM/JKM150/Normal/JKM150.ods` validated. In a subsequent UI follow-up it
  was saved only to the in-memory adapter: the mapped row and queued-success
  state appeared, then disappeared when the test server shut down. No live
  Grist association or audit/queue row was created, and the ODS remained
  read-only. The Validate button remains unavailable until workbook preview
  completes, and validation submits the inspected file hash. An initial local
  page wait expired while the ODS scan was still running; the read-only preview
  then completed normally.
- A live read-only API smoke against the Safari Grist adapter returned the 24
  unique canonical Products and all four approved GC codes. The adapter no
  longer seeds duplicate identities from the local ODS catalog. Association
  validation/save refreshes remote code ownership; a retry after a pre-save
  failure is revalidated instead of relying on a stale in-process snapshot.
  The local-file association was also retried under its request key; the
  association and eight code links were not duplicated.
- Three owner-provided S1KHF files are associated with the same Safari 1000 HF
  model in the validated Safari Manufacturing document. The standard Local file
  has eight supplied Bearing Type codes; the Export file has the three
  requested Bearing Type codes; and the HF-MS Drum Local file has the eight
  supplied `-MS` codes. Export source spellings resolve exactly,
  case-insensitively, to catalog values `S1KHFExEP`, `S1KHFExEK`, and
  `S1KHFExM`, with catalog capitalization preserved. Fresh reads confirm 19
  unique active code owners, three FileModelAssociation rows, three association
  audit events, and three queued association ImportBatch rows (five batches total,
  including the two catalog imports). All three hashes matched at association
  verification time. A 23 September post-merge audit found that the HF-MS Drum
  source has since changed; the other two still match. Neither the application
  nor this audit modified a source workbook or `Costing-New`.
- Explorer inspection now recognizes an ODS named with “Export” as a costing
  candidate when it contains both `Cost Log` and `Total Summary`; a regression
  test covers this narrow case. Other generated-output classifications remain
  unchanged.
- A read-only browser smoke opened the Explorer with the explicit
  `grist-safari` adapter. It loaded 503 root nodes and the Product choices;
  live mapped-files reads returned the two owner-provided S1KHF associations.
  The Local and Export files show external-link warnings (61,130 and 61,119
  references respectively). The Export preview remained readable with 16
  sheets. No browser Save was attempted, and both local servers were stopped
  after the smoke.
- A follow-up read-only live browser smoke verified the Mapped Files projection
  after the repository began rehydrating aliases, issues, audit events, and
  import batches. The Local and Export rows showed the owner-supplied code sets
  and `queued` status; each details drawer showed actor/reason, association
  codes, and its persisted audit event. No browser Save was attempted against
  these already-persisted mappings. Source ODS hashes and the legacy document
  remained unchanged.
- The owner then supplied the HF-MS Drum workbook and exact eight-code set.
  Read-only preview of `Total Summary` confirms these codes occupy rows 11–18
  under `Bearing Type`. The UI reported 61,125 external references as a
  warning; inspection was readable and its SHA256
  `442f0140ff9ba81fc92145cf5d027e521fae8c072ce4fe68f030d2ed5c767a60`
  matched the current source. Validation found all eight active codes
  unowned. Browser Save through the guarded Grist adapter created CostingFile
  id 3, FileModelAssociation id 3, eight active code links (ProductModelCode
  ids 69–76), an audit event, and a queued read-only batch. The trusted local
  development actor is recorded as `local-development`; no proxy identity was
  fabricated. A live Mapped Files/history read verified the new row and its
  external-link review flag.
- On 22 September 2026, the live document was re-read without mutation:
  Safari Manufacturing remains in the Work workspace with all 11 foundation
  tables; the three active file-model associations and 19 active code links
  remained unique; all three source hashes matched in that dated check; and the
  five import batches comprise two applied catalog batches plus three queued
  read-only association batches. At that check, the 62-test Python suite, seven
  UI tests, and production build passed. The 23 September read found the HF-MS
  Drum source had since changed; the other two mapped source hashes still
  match.
- Live Grist discovery returned four writable workspaces; the owner explicitly
  selected Work. Setup safely reused the exact Safari Manufacturing document,
  applied and verified the complete foundation schema, and removed only the
  empty default table. The approved catalog is populated and its repeat plan is
  a no-op. Three user-supplied S1KHF mappings now persist with audit and queue
  records, satisfying the several-file pilot gate. The owner explicitly
  supplied the HF-MS Drum `-MS` codes; no variants were inferred. The source
  catalog contained no Bush variants, so live persistence for that category
  was not exercised (classification remains covered by tests).

### Work package 0.5 — Mapped Files and reconciliation

**Outcome:** mappings can be reviewed and corrected without hidden overwrites.

**Post-merge audit (23 September 2026):** Milestone 1 received a conditional
pass. A fresh read found the mapped HF-MS Drum workbook no longer matches its
stored observation, and the API correctly derives `changed_file`. The catalog
comparison also identified two active, unreferenced Safari SS ProductModel rows
containing the Unicode replacement character where the source has an en dash.
These are now the first two controlled reconciliation cases for this milestone.
The full evidence and required safeguards are in
`MILESTONE_1_POST_MERGE_AUDIT.md`; the implementation brief is
`MILESTONE_2_IMPLEMENTATION_PROMPT.md`.

**Milestone 1 evidence at implementation start (21 September 2026):** the review surface groups by
Product/Model, filters by mapped/unmapped/conflict/needs-review, sorts by path,
name, or last observed change, and opens preserved association history. The
API projection surfaces duplicate active code owners, catalog-reference
mismatches, missing/changed files, verified moves, and known parse/external-link
findings. A unique candidate move is only reported after recorded size and
timestamp match and the file hash verifies. Endpoint tests cover duplicate
ownership, changed metadata, observation timestamps, catalog mismatch, and a
verified move, and CLI dry-run/workspace-selection safety. At this 21 September
check, the backend had 62 tests (61 pass and one platform skip); current
verification is 78 tests (77 pass and one platform skip). The original two
dependency-free Node tests cover mapped-file grouping, review/search filters,
moved-path search, and last-observed sorting; current frontend coverage is 10
Vitest tests.
The post-change local browser smoke loaded 504 files, verified grouping,
search/status/sort, timestamps, and keyboard-opened details. It used the
in-memory adapter and had no actual conflict rows to display. The live browser
smoke now verifies all three persisted association-history drawers, audit
events, and queued status. A browser Save was exercised against
the new owner-approved HF-MS Drum mapping and its resulting history/audit row
was verified. No live conflict row was available to inspect. This remains a
partial milestone because there is no durable issue-resolution queue or
owner/deferral workflow, and no live conflict/supersede case has been reviewed.

The earlier in-memory Explorer smoke was exercised against the configured root:
the read-only `S1KHF/Local/Safari 1000 HF Local V 4.2.ods` preview showed 17
sheets, 2,626 × 21 cells on the selected sheet, formula indicators, and 61,130
external references. Product → Model → Code selection validated and saved a
temporary in-memory association, and the queued-success message appeared. The
API reported `adapter=in-memory` and `writeEnabled=false`; the local server was
stopped afterward, discarding that test association. That smoke did not write
to a source ODS or Grist document. The first two owner-supplied associations
were saved through the guarded Grist repository; the HF-MS Drum association was
saved through the guarded browser UI.

**Historical Milestone 0.5 checkpoint (23 September 2026; superseded by the
Milestone 2 status dated 29 September below):** the durable
issue lifecycle, bounded dry-run/materializing scan, filtered CSV/JSON export,
source-revision preview/apply, identity-cleanup preview/apply, and versioned
directory mapping proposal/approval workflows are implemented behind the
storage-neutral repository contract. The API and Reconciliation UI expose
evidence, history, audit, required-reason actions, optimistic versions, and
guided duplicate-code owner review. Classification samples and rule decisions
are recorded in `MILESTONE_0_5_CLASSIFICATION_REVIEW.md`.
The current scanner still promotes hash drift to a `changed_file` issue
candidate; D-033 clarifies this is only a source-revision signal until semantic
costing/material comparison. Semantic snapshots, field-level diff, and a
reviewable Grist change-set flow are required follow-up work. For Material Cut
List, D-034 sets the workbook-scoped active key to Machine Piece Description,
Material to Cut, Dimension to Cut (mm), Quantity Nos, and Optional Item Group 1.
At this checkpoint, a changed key was treated as removal plus addition. D-051
now supersedes that matching rule in part: a changed key may be paired as a
probable modification only with unique strong evidence; otherwise it remains
ambiguous for owner resolution or separate add/remove when no plausible match
exists.

Verification passes 78 Python tests (77 pass, one expected platform skip),
2 dependency-free Node tests, 10 Vitest tests, and the TypeScript/production
build. The synthetic browser smoke rendered the Explorer and Reconciliation at
1280 × 720 and 390 × 844. At 390 px, the document width matched the 375 px
layout viewport and no element overflowed horizontally. The issue component
test exercises keyboard selection, required reasons, and focus transfer to the
details heading. Owner visual acceptance remains open.

Milestone 0.5 is **not at its exit gate**. A read-only live scan still has 8
unmaterialized candidates: 3 `external_link_review`, 1 `changed_file`, 2
`invalid_identity_encoding`, and 2 `unmatched_active_identity`. The durable
register remains at 68 historical open `blank_description` issues; no candidate
was materialized and no live issue was assigned, deferred, resolved, or
reopened. The `changed_file` candidate is hash drift only; under D-033 it is a
source-revision signal until semantic comparison confirms an unresolved
business discrepancy. The validated v3 schema plan proposes one `DirectoryProductMapping`
table and 2 table extensions, and remains unapplied. Grist governance writes
fail closed with `SCHEMA_MIGRATION_REQUIRED` until that migration is explicitly
approved and applied. The HF-MS semantic comparison and two model cleanups also
remain pending; Milestone 0.6 stays gated until confirmed pilot issues have
owners and a reasoned open, deferred, or audited resolved state.

Deliverables:

- grouped/filtered mapped-files screen;
- unmapped, duplicate, moved, changed, missing, and catalog-mismatch queues;
- association history and supersede-with-reason workflow;
- directory-to-Product proposal and approval workflow;
- exportable reconciliation report.

Exit gate: all pilot mapping exceptions have a status, owner, and resolution or deferral reason.

### Work package 0.6 — Shared CLI material-mapping services

**Outcome:** the UI/API reuse proven CLI mapping knowledge rather than reimplementing it.

Deliverables:

- domain service for aliases, `ODSComparableMaterial`, alternate-size normalization, and option scoping;
- adapters that preserve existing CLI commands;
- versioned mapping rules and validation report;
- golden regression fixtures from existing comparisons.

**Extraction seam identified (19 September 2026):** `app/verification.py`
contains CLI comparison orchestration, material-index building, mapped-target
selection, Tool Shop fallback, and option-scope filtering;
`app/material_mapping_manager.py` owns YAML editing/validation, with config and
normalization in `app/config_models.py` and `app/utils.py`. The first safe
boundary is a pure material-mapping service called by the existing CLI adapter
and the future API. The rules have not yet been moved or duplicated; this
remains planned work requiring golden CLI-equivalence tests.

Exit gate: legacy CLI outputs remain equivalent for selected fixtures and API results identify the same materials.

### Work package 0.7 — S1KHF read-only pilot

**Outcome:** one real workbook is understood end to end as immutable source observations.

Deliverables:

- sheet contracts for all relevant S1KHF sheets;
- row/line observations with source cell provenance;
- dependency/external-link graph;
- classification of base, options, additions, deductions, and spares composition;
- comparison report against existing Grist records where comparable.

Exit gate: owner signs off the sheet interpretation and discrepancy report. Phase 0 acceptance criteria in the requirements baseline are met.

## Phase 1 — Read-only ingestion and costing parity

### Delivery Milestone 2 — Interim ODS costing reconciliation and S1KHF pilot

**Status: COMPLETE for the agreed S1KHF Milestone 2 scope (29 September 2026).**

**Outcome:** ODS remains the authority during this transition. Refresh and
Calculate use a selected explorer workbook and current local RawSteel and
SteelRateLog ODS dependencies. Those actions only read sources and a disposable
LibreOffice-refreshed copy. A separate explicit Reconcile action persists
semantic state and audit evidence to Safari Manufacturing. Source ODS and
Costing-New are never written.

**Implementation:**

- `app/milestone2.py` parses RawSteel, the available SteelRateLog dump, the
  product-local SteelLog, selected-workbook formula inputs, active and
  historical MCL lines, other configured process lists, and Total Summary
  option headings. It pins source hashes and formula/cell provenance.
- Costing Review initiates LibreOffice `FULL_UPDATE` on a temporary sibling
  copy, recalculates, calculates active lines, and removes the copy. The
  selected workbook remains the default for its assigned Model Codes; alternate
  scenario workbooks do not override that selection.
- `CostingSnapshot` stores the accepted semantic content and source revisions.
  `CostingChangeSetItem` stores classified rate and structure changes with
  previous/current states, source evidence, affected lines, CR references, and
  cost impact when available. Each item and baseline change receives an audit
  event.
- The five agreed MCL fields are exact-match identity. Unique strong semantic
  evidence can pair a changed key as a probable modification. Matching never
  uses MCL ID or row position. Configured process-list movement, option group
  changes, additions/removals, material/dimension/quantity edits, formula and
  parameter changes, and rate changes have separate classifications.
- Ambiguous matching is shown for owner resolution. Deterministic changes do
  not need line-by-line owner review; the explicit Reconcile action records the
  full change set and advances the baseline. CR Log is carried as evidence and
  never auto-accepts a change.
- Grist writes stay staged until change items, source observation, and audit
  records are durable. Optimistic baseline/source checks, actor attribution,
  idempotency, and retry recovery are enforced. Safari schema v4 was applied to
  the validated Safari Manufacturing document and the follow-up plan is clean.

**S1KHF real cycle:** Safari had no accepted costing snapshot before this work,
so there was no historical semantic baseline from which to reconstruct earlier
changes. The selected workbook was refreshed and calculated: 42 active MCL
lines, 16 historical lines excluded, zero blocked lines, active total
₹11,056.401709. The explicit reconcile action established the first accepted
semantic baseline. A second real refresh/compare found zero semantic changes;
repeating acceptance with the same state is a no-op. Source workbook and linked
ODS hashes were verified unchanged and the disposable copy was removed.

**Validation:** `python -m unittest discover -s tests -v` passed 106 tests
(105 passed, one platform skip); semantic and Grist persistence/retry fixtures
are included. Python compile checks passed. UI tests passed (2 Node view-model
tests and 10 Vitest tests), and TypeScript checking plus the production build
passed. The live S1KHF refresh/compare/reconcile/baseline cycle is recorded
above; no source ODS or linked ODS hash changed.

**Limitations and deferred items:** this first baseline cannot recreate changes
that predate its acceptance. Additional invalid-rate cases beyond the confirmed
rules, automatic Google Sheets dump acquisition, expanded product-family
parity, and future per-material/per-product costing configuration remain later
work. The pilot snapshot is about 667 KB of JSON content and was accepted by
the current Safari deployment; substantially larger workbooks may need chunked
evidence storage if they exceed that deployment's request-size limit. The pilot
matched identity and cost behavior on the current selected workbook; it does
not claim broad parity across every product family.

**Exit decision:** accepted snapshots, governed change-set persistence, semantic
rate/structure comparison, ambiguity handling, CR evidence, read-only refresh
and calculation, explicit acceptance, idempotency, and the S1KHF baseline cycle
are implemented. Remaining deferred items do not block the defined Milestone 2
exit criteria.

### Work package 1.1 / Delivery Milestone 3 — Normalized part and process-line ingestion

Milestone 2 already extracts configured Material Cut, Tool Shop, CNC, Stores, and Labour/Paint/Packing lines into accepted semantic snapshots for the S1KHF pilot. The next data-model milestone must normalize those records before broad product-family expansion. It introduces stable `LineMaster` identities, immutable `LineRevision` rows, typed process details, immutable `SourceLineObservation` rows, and explicit `SourceLineMapping` records. It must retain values and formulas separately, keep `In Use = No` rows historical, use the D-034 composite key for exact matches, and pair a changed key as a probable modification only with unique strong evidence. Ambiguous identity requires owner reconciliation; CR Log is evidence, never an automatic match or approval. The accepted JSON snapshot remains a parity oracle during migration, not the primary searchable line store.

Milestone 3 starts with a schema and mapping plan against the accepted S1KHF
snapshot. Its implementation scope is `ProductPart`, `PartRevision`,
`PartComponentRevision` where supported by source evidence, `LineMaster`,
`LineRevision`, the five typed process-detail families, material/purchase-item
identity references, workbook/sheet/row observations, governed source-line
mappings, and row-level revision audit. It includes the narrowly required
extraction of existing CLI material-mapping rules with golden CLI equivalence.
It does not implement the Costing Configuration builder, Manufacturing
Configuration, Production Plans, stock reservations, live rates, or ODS
write-back. Exact scope and acceptance tests are in
`MILESTONE_3_IMPLEMENTATION_PROMPT.md`.

**2 October implementation checkpoint:** v5 defines 13 normalized tables and
the local source projection covers all 313 configured pilot rows. A guarded
Grist writer, key-based retry, local inspection API/UI, and the pilot direct
material-alias service are implemented. Ten duplicate-composite rows retain
unresolved identity. The Safari document was rediscovered in the Work
workspace and a live schema plan found 13 new tables with no v4 column
alterations. Those tables and 2,043 planned normalized records were applied;
the post-apply schema and row diffs are empty. The source hash changed after the accepted snapshot;
previously covered physical fields match and the current MCL active plus
historical caches reconstruct workbook C7. The row import diff follows the
reviewed import; owner resolution remains exit work. See
`MILESTONE_3_PILOT_RECONCILIATION.md`.

Exit gate: one accepted S1KHF source revision is represented in queryable
child rows; every row has source provenance and a stable or explicitly
unresolved identity; active/historical counts and supported totals reconcile
with the accepted semantic baseline; repeat import and interrupted-write
recovery converge; the existing CLI still yields equivalent mappings.

### Work package 1.2 — Dependency and approved-rate resolution

Resolve references to Spares Master, Steel Log, Material masters, and other product workbooks. Replace fragile path semantics with explicit dependency records while retaining the original formula/reference as evidence.

### Work package 1.3 — Explainable costing engine

Calculate base, additions, deductions, replacements, quantities, overhead, and final totals using decimal arithmetic and a pinned rate set. Every result must drill down to source lines and applied rules.

### Work package 1.4 — Parity expansion

Reach approved tolerances on S1KHF, then extend to representative product families including shared parts, motors/engines, imported/local variants, spares, and GC variants.

Phase gate: agreed sample coverage, tolerance, rounding policy, and all unexplained differences resolved or explicitly accepted.

## Phase 2 — Controlled synchronization

- Create proposed change sets between immutable observations and curated Safari Manufacturing records.
- Add field-level conflict handling, approvals, retries, idempotency keys, and audit events.
- Allow narrowly scoped, reversible updates only after preview.
- Keep `Costing-New` synchronization one-way or field-owned; never create circular writers.
- Add backup and restore drills.

Phase gate: repeated sync runs converge, failure recovery is proven, and no source workbook changes occur.

## Phase 3 — Configuration workflows

- Reusable versioned Product Parts and recursive composition.
- Costing Configuration builder with base/add/remove/replace/quantity/rate rules.
- Manufacturing BOM, routing, source department, issue route, and per-part batch policies.
- Versioned Production Plans that instantiate an approved Manufacturing Configuration with independent requested quantities per part/subassembly, available/reserved stock consideration, and explicit overproduction or multi-run batch reasons.
- Store Issue Slip generation without double counting Tool Shop items routed through Stores.
- Spare-parts configuration with legacy Bush applicability.
- Effective-date publication, draft/review/approved lifecycle, and comparison between revisions.

Phase gate: users can create, approve, cost, and manufacture a representative new configuration without editing raw tables.

## Phase 4 — Live purchase and material rates

- Read Google Sheets and existing Grist as **rate observations**.
- Match suppliers/items/materials using reviewed aliases and confidence levels.
- Define approval policy for promoting an observation to an approved costing rate.
- Support “costlier only” as an explicit policy option, not a hidden rule.
- Preview affected models and cost deltas before approval.
- Version and pin rate sets so historical costs remain reproducible.

Phase gate: Purchase stops duplicate entry for the approved workflow and users can explain why each costing rate was selected.

Rate readiness for cutover is per consuming Model Code: every material used by
its approved active lines must resolve to a current, valid, governed rate in
Safari Manufacturing, with the applied rate set pinned for reproducibility.
The presence of imported line rows or an observed rate log alone is not an
authority switch.

## Phase 5 — ODS write-back and operational cutover

- Generate controlled ODS copies/exports first; never overwrite originals during development.
- Verify formulas, named ranges, external links, styles, row/column structure, and LibreOffice recalculation.
- Add backups, file locks, conflict detection, rollback, and signed change manifests.
- Run parallel operations and user acceptance.
- Permit costing-authority cutover at either a single Model Code or a whole
  Product Model, as appropriate for each product. A whole-model decision
  requires every active code in that model to meet the gates; other codes may
  remain ODS-authoritative during a gradual code-level transfer.
- Before switching a scope, verify complete import and mapping of every
  required process sheet/line, governed rates for every consumed material,
  approved configuration and calculation parity, and owner signoff. Record the
  effective scope, source/rate revisions, actor, reason, time, and rollback
  state; preserve exactly one effective authority per Model Code.
- Route normal costing, cut-list, and inspection reads by that effective
  authority. The Grist-authoritative path must use typed records and pinned
  rates without opening or reparsing the ODS for every UI request; keep ODS
  monitoring/provenance separate from the interactive read path.
- Measure and expect a dramatic reduction in UI load time after cutover.
  Benchmark against the ODS-backed path and set an explicit latency target
  during the cutover trial; imported rows by themselves do not satisfy this
  performance gate.
- Switch data authority domain-by-domain, with a documented rollback window.

Phase gate: approved parity, operational readiness, backups, training, and owner authorization.

## Phase 6 — Authentication and platform decision

### Authentik and roles

- Integrate OIDC with Authentik.
- Map identity groups to app roles while keeping fine-grained grants in Safari Manufacturing.
- Enforce policy in API/domain services, not only in the UI.
- Add role-change audit and service-account handling.

### Grist versus PostgreSQL decision

Keep Grist through validation because it minimizes migration risk for 5–6 active users. Evaluate PostgreSQL only after real workload evidence exists. Decision criteria include transaction/concurrency needs, query complexity, audit/approval requirements, background job load, schema migration control, backup/restore, reporting usability, operating cost, and staff maintainability.

Likely evolution if limits are reached: PostgreSQL becomes the authoritative application database while Grist remains a governed operational/reporting interface—not a second independently edited source.

## Milestone evidence checklist

For every completed milestone, record:

- requirement IDs delivered;
- decision IDs added or changed;
- schema/API versions;
- automated test command and result;
- real fixture/pilot used;
- reconciliation counts;
- screenshots or demo notes where useful;
- known limitations and next gate;
- Git commit(s) and release/tag if created.

## Ongoing workflow stages - 3 October 2026

The owner-requested twelve-stage workflow is an active implementation goal.
Stage numbers do not replace delivery milestone numbering. Uncommitted Milestone
3 work was preserved in a local Git-metadata archive, manifest and binary patch.

1. Saved association retrieval: implemented; selections, version, status, history
   and audit load from the repository. Validate and Save remain separate.
2. Full-width Workbook Preview: implemented; automatic disposable-copy refresh
   on tab selection, progress, hashes, timestamp and explicit errors.
3. Persisted, auditable processing states separate from authority: next.
4. Files and Product Models -> Model Codes navigation with on-demand reconciliation.
5. Required process sheets/Summary and structural versus pricing differences.
6. Reviewed canonical Part mapping, including individual blank-description rows.
7. Reviewable, idempotent typed imports and immutable revision handling.
8. Model Code configuration/cost and structurally gated Mark processed action.
9. Fast stored-record reads, optional ODS reconciliation and measured latency.
10. Governed CR lifecycle before routine approved edits; separate write-back assessment.
11. Spares ingestion/editor and all-tab Google Sheets integrations.
12. Repeat processing of user-selected files and continued documentation updates.

The ten ambiguous pilot identities, temporary Part ownership and later-source
revision import limitation remain open. See WORKFLOW_IMPLEMENTATION.md for
verification and next gates. First slices do not claim processing completion.

### 5 October workflow progress

Stage 3 lifecycle foundation is implemented and Safari v6 adds one immutable
FileProcessingEvent table. State/history UI, source/association/version guards,
retry recovery and audit are verified. Completion gates depend on stages 5-8
and remain disabled. Stage 4 navigation and on-demand reconciliation are next.

5 October stage 4 slice: Files and Product Models -> Model Codes navigation implemented. Code selection reads stored Grist rows only; revision/source provenance and explicit reconciliation entry points are present. Full structural/configuration reconciliation remains stage 5. Read-only live main S1KHF pilot: 313 rows, 1.934 seconds, no ODS read; MS Drum reports no accepted snapshot. This is one measurement, not a stage 9 performance comparison.

Stage 5 is partial: required-sheet coverage, exact quantity/2-decimal kg evidence and nonblocking interim price differences are visible. User supplied representative HF/H Summary workbooks and confirmed global Part-name uniqueness. Next major slice: reviewed Part mapping UI and immutable storage, then per-code base/options/additions/deductions configuration and spares extraction. Completion remains unavailable.

Stage 6 Part review slice implemented: canonical selection/unique creation, individual blank rows, audited typed assignment batches, stale-evidence checks and durable retries. Live v7 adds one review table and six Part creation fields; no business assignments made. Pilot has 55 unresolved groups / 100 active rows. Next gate: reviewed Part references in typed import plans and per-code Summary/Paint/Packing configuration. Completion remains gated; stages 5-8 are still partial.

### 6 October prerequisite before further Part mapping

PART-002 / D066 takes precedence over the previously stated next import gate. First implement permanent Part identity and SM-P sequence allocation, maintained master shortcodes, automatically generated scope/description/variant names, metadata history/aliases and separate engineering revisions. Sharing scope only determines naming and advisory warnings; it must not restrict Model Code configuration or auto-assign codes. Verify multiple designs shared by different subsets of one Model. Then adapt/advance Part mapping and import integration. Existing stage 6 work is preserved as a prototype and must not be treated as satisfying this prerequisite. See PART_IDENTITY_REQUIREMENTS.md.

PART-003 / D067: the upcoming Part creation/mapping work must initialize all managed Parts at Rev A and prohibit later engineering revisions until the governed CR flow is implemented. Metadata naming/scope versions remain distinct. The implementation handoff is docs/PART_CREATION_MAPPING_IMPLEMENTATION_PROMPT.md; implement creation first, then mapping, before resuming later workflow stages.
