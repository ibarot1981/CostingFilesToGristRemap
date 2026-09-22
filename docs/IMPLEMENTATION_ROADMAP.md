# Safari Manufacturing — Implementation Roadmap

Updated: 21 September 2026

Planning rule: every milestone ends with a demonstrable artifact, reconciliation evidence, tests, and updated documentation/status.

## Operating rules for every milestone

1. Inspect `git status` before work and preserve unrelated user changes.
2. Update the requirement ID, decision record, schema/API contract, tests, and `requirements-status.html` in the same change.
3. Default all imports and synchronization to dry-run.
4. Never write to a source ODS file or `Costing-New` unless that capability has passed its later approval gate.
5. Store source provenance and produce a reconciliation report for every import.
6. Demonstrate the milestone against a named fixture and the selected real pilot; do not rely only on mocks.
7. Commit in small, reviewable units. Do not include secrets, local `.env` values, downloaded business files, or generated caches.

## Phase 0 — Foundation and controlled mapping

### Milestone 0.1 — Governance and source boundaries

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

### Milestone 0.2 — Safari Manufacturing document creation and schema bootstrap

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

### Milestone 0.3 — Canonical identity import

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

### Milestone 0.4 — Costing Explorer and associations (first implementation milestone)

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
  UI has 2 dependency-free Node view-model tests and 5 Vitest component/workflow
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
  audit events, three queued association ImportBatch rows (five batches total,
  including the two catalog imports), and hashes matching all three source
  ODS files. Neither source workbook nor `Costing-New` was modified.
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
  remain unique; all three source hashes still match; and the five import
  batches comprise two applied catalog batches plus three queued read-only
  association batches. The 62-test Python suite, seven UI tests, and production
  build were rerun successfully.
- Live Grist discovery returned four writable workspaces; the owner explicitly
  selected Work. Setup safely reused the exact Safari Manufacturing document,
  applied and verified the complete foundation schema, and removed only the
  empty default table. The approved catalog is populated and its repeat plan is
  a no-op. Three user-supplied S1KHF mappings now persist with audit and queue
  records, satisfying the several-file pilot gate. The owner explicitly
  supplied the HF-MS Drum `-MS` codes; no variants were inferred. The source
  catalog contained no Bush variants, so live persistence for that category
  was not exercised (classification remains covered by tests).

### Milestone 0.5 — Mapped Files and reconciliation

**Outcome:** mappings can be reviewed and corrected without hidden overwrites.

**Current evidence (21 September 2026):** the review surface groups by
Product/Model, filters by mapped/unmapped/conflict/needs-review, sorts by path,
name, or last observed change, and opens preserved association history. The
API projection surfaces duplicate active code owners, catalog-reference
mismatches, missing/changed files, verified moves, and known parse/external-link
findings. A unique candidate move is only reported after recorded size and
timestamp match and the file hash verifies. Endpoint tests cover duplicate
ownership, changed metadata, observation timestamps, catalog mismatch, and a
verified move, and CLI dry-run/workspace-selection safety. The backend now has
62 tests (61 pass and one platform skip); two dependency-free Node tests cover
mapped-file grouping, review/search filters, moved-path search, and
last-observed sorting.
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

Deliverables:

- grouped/filtered mapped-files screen;
- unmapped, duplicate, moved, changed, missing, and catalog-mismatch queues;
- association history and supersede-with-reason workflow;
- directory-to-Product proposal and approval workflow;
- exportable reconciliation report.

Exit gate: all pilot mapping exceptions have a status, owner, and resolution or deferral reason.

### Milestone 0.6 — Shared CLI material-mapping services

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

### Milestone 0.7 — S1KHF read-only pilot

**Outcome:** one real workbook is understood end to end as immutable source observations.

Deliverables:

- sheet contracts for all relevant S1KHF sheets;
- row/line observations with source cell provenance;
- dependency/external-link graph;
- classification of base, options, additions, deductions, and spares composition;
- comparison report against existing Grist records where comparable.

Exit gate: owner signs off the sheet interpretation and discrepancy report. Phase 0 acceptance criteria in the requirements baseline are met.

## Phase 1 — Read-only ingestion and costing parity

### Milestone 1.1 — Normalized process-line ingestion

Implement typed observations for Material Cut, Tool Shop, CNC, Stores, and Labour/Paint/Packing. Preserve workbook values and formulas separately; never convert unknown fields to guessed business meanings.

### Milestone 1.2 — Dependency and approved-rate resolution

Resolve references to Spares Master, Steel Log, Material masters, and other product workbooks. Replace fragile path semantics with explicit dependency records while retaining the original formula/reference as evidence.

### Milestone 1.3 — Explainable costing engine

Calculate base, additions, deductions, replacements, quantities, overhead, and final totals using decimal arithmetic and a pinned rate set. Every result must drill down to source lines and applied rules.

### Milestone 1.4 — Parity expansion

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

## Phase 5 — ODS write-back and operational cutover

- Generate controlled ODS copies/exports first; never overwrite originals during development.
- Verify formulas, named ranges, external links, styles, row/column structure, and LibreOffice recalculation.
- Add backups, file locks, conflict detection, rollback, and signed change manifests.
- Run parallel operations and user acceptance.
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
