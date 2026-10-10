# Safari Manufacturing Phase 0 architecture

Updated 24 September 2026 with the read-only Milestone 2 material/rate parity design.

The UI calls FastAPI only. FastAPI uses the domain records in `app/domain.py`
and repository/service contracts in `app/repository.py`; it does not expose
Grist table or column identifiers to the browser.

The default adapter is `InMemorySafariRepository`. It is deterministic, safe
for local development, and visibly labelled by the UI. `GristSafariRepository`
is selected only with `SAFARI_REPOSITORY=grist` and the explicit
`SAFARI_MANUFACTURING_GRIST_DOC_ID`; `GristClient.from_safari_environment()`
rejects the legacy `GRIST_DOC_ID`.

Filesystem reads are behind `FilesystemCatalog`. Relative IDs are resolved
under `COSTING_ROOT`, traversal/absolute paths and reparse points are rejected,
and hashes are computed only when a file is inspected or associated. ODS
preview reads cached values through `pyexcel-ods3` and formula attributes
directly from the package without LibreOffice or write-back.
The Explorer's debounced search uses the recursive `/api/catalog/files` path
query, which returns ODS metadata without opening workbooks or calculating
hashes; the UI reconstructs ancestor folders and expands only matching paths.
It caps the visible search result set at 1,000 and reports when a query should
be refined.

`app/grist_admin.py` is the administration boundary. `main.py safari-setup`
performs workspace discovery and exact-name lookup, defaults to plan-only, and
requires `--apply` for document creation/reuse and `--schema-apply` for the
versioned foundation schema. Ambiguous workspaces, duplicate exact names,
permission failures, connectivity failures, and legacy-target mismatches are
typed errors. Normal API startup does not create documents or tables.

The association service enforces one file-to-model owner and one active file
per Model Code. Saves carry an actor, reason, expected version/hash, and
idempotency key. Supersede creates inactive history records and audit/import
queue records; history retains the superseded code set and it never applies
last-write-wins silently. The mapped-files endpoint merges repository rows
with unregistered ODS nodes so unmapped files are visible without hashing the
entire root.
`GET /api/processing-queue` exposes persisted association batches newest first
with a bounded status filter. It is read-only and reports the queued safe stub;
it does not run a costing parser.

Reconciliation is a durable domain workflow. The expanded `SafariRepository`
protocol covers issue lifecycle, source observations/revisions, identity
cleanup, directory mappings, audit history, association detail, and queue
operations. Issue fingerprints are stable across scans; a repeat updates
last-seen facts, while a condition disappearing from a derived scan never
resolves the persisted issue. Status changes use optimistic versions,
idempotency keys, actor/reason attribution, and audit events. Explicit bounded
scans default to dry-run; the lightweight Mapped Files list still uses saved
observations and filesystem metadata instead of opening every workbook.

Accepting a changed source revision rechecks the current SHA-256 and current
Model Code ownership, then updates only the repository's current file
fingerprint and appends a new immutable `FileObservation`. The association is
retained and the audit payload records old/new hashes. Identity cleanup is a
plan/apply operation: it checks model codes, file associations, aliases, and
other governed references, then marks the row inactive with its canonical
replacement rather than deleting it. Directory-to-Product mappings have their
own versioned proposal/approval history; approved deeper paths override an
inherited parent, and this projection never assigns a file or Model Code.

The v3 schema adds ProductModel supersession fields, durable issue evidence and
lifecycle fields, and `DirectoryProductMapping`. A read-only v3 schema plan is
checked before governance writes; if any planned schema change remains, those
writes fail with `SCHEMA_MIGRATION_REQUIRED` before the first Grist mutation.
Read-only repository hydration treats the not-yet-created directory mapping
table as empty. Schema changes remain an explicit guarded setup operation and
never run during API startup.

The Grist adapter reloads canonical identity, aliases, reconciliation issues,
file observations, associations, code links, import batches, and audit events
when it hydrates the repository. It decodes Grist `Any` source values into the
domain projection, so history, audit, and queue records survive a fresh API
process. The adapter translates the logical schema into Grist column metadata
and updates prior active rows only when an explicit supersede is saved.
`safari-catalog --grist-plan` reads the validated Safari document and reports
canonical identity creates/updates; `--apply` revalidates the exact configured
document/workspace before the explicit upsert. Matching source hashes are
no-ops, row-level provenance/aliases/issues are preserved, and retries resolve
existing identities rather than blindly creating duplicates. The general
Grist client rejects Safari writes unless the configured document name,
workspace, writable access, and legacy-document guard are rechecked.
Repeated canonical Model Codes remain inactive with an error-level issue until
resolved; the association validator rejects them. Approved GC rating variants
remain separate identities with source aliases/provenance.

`app/grist_types.py` is the shared adapter boundary for Grist's normal cell
representation: Ref values are numeric row IDs and DateTime values are epoch
seconds. Repositories and catalog imports use these conversions; typed legacy
reference/date wrappers remain readable. Association writes persist an
idempotency request key and canonical payload fingerprint alongside the
association, audit event, and import batch. Since Grist does not provide a
multi-table transaction for this HTTP sequence, a retry locates those records,
rejects a key reused for different input, and fills missing children/audit/queue
records before it returns. It creates children before deactivating prior active
records, preventing an interrupted request from leaving no active owner.

The save endpoint ignores a caller-supplied `actor` and takes attribution from
identity headers supplied by the hosting authentication proxy (falling back to
a stable local-development identity). This is attribution, not authentication:
deployment must prevent direct untrusted access and configure the proxy to
overwrite, not pass through, `x-authentik-*` headers. Role enforcement remains
deferred under D-013.

The existing CLI mapping extraction seam is deliberately kept separate from
the Phase 0 explorer. `app/verification.py` owns comparison orchestration,
`build_master_material_index`, `mapped_material_targets`, Tool Shop fallback,
and option-scope filtering. `app/material_mapping_manager.py` owns YAML editing
and validation; `app/config_models.py` and `app/utils.py` provide configuration
and normalization. A future pure `material_mapping_service` can receive those
inputs and be called by both the existing CLI adapter and API. Phase 0 does not
rewrite or duplicate those rules; golden fixtures and CLI-equivalence checks
remain Milestone 0.6 work.

## Milestone 2 ODS material and rate snapshot

`app/milestone2.py` is a read-only parser/resolver/reporting boundary. It opens
ODS ZIP/XML content without LibreOffice or link refresh and retains formula
text, typed cached values, displayed values, source cells, and source rows. The
pilot flow reads `MaterialCostDB.ods` / `RawSteel`,
`Spares List - Master.ods` / `SteelRateLog`, and the selected product workbook's
`SteelLog`, `1. Iron and Steel`, and `5. Material Cut List Price` sheets. It
records file hash, size, mtime, and observation time. Google Sheets and Grist
are not read by the parser/resolver. Refresh, Calculate, and semantic
comparison are read-only. The separate governed Reconcile action may write the
accepted semantic snapshot and change-set/audit records to Safari Manufacturing;
source ODS and Costing-New remain read-only.

The explorer-selected product workbook is the default authority for its
assigned Model Codes; other files may be sample/scenario workbooks and have no
effect unless explicitly selected. The interim material identity is the
exact `RawSteel.Unique Item List` text.
RawSteel contributes material type, size, kg/metre or kg/square-metre,
grams/inch, default rate, and unused rate-range evidence. The dump contributes
ordered purchase observations; the product's local SteelLog represents the
rate-log snapshot actually present in that workbook revision. The resolver
reproduces the formula: final matching row in the current snapshot for Latest
Rate, maximum non-zero rate available in that snapshot for the maximum branch,
fallback to the RawSteel default when no rate is logged, selected-workbook selector, then the observed ₹2/kg safety
margin. The margin buffers material-price fluctuation between costing and
order confirmation; it is separate from purchase-rate observations. Keep the
no-log semantic date null even if the workbook cache displays a 1900-era date.
Exact source names remain unmerged; normalized names are search/detection
helpers only. Extract every RawSteel row but evaluate product parity against
the selected workbook's actual formula range. An unused out-of-range master
row is a consistency warning; it blocks costing only if an active selected
line depends on missing lookup values. Short ranges in alternate workbooks are
warning-only for current assignments and can be extended for consistency.

Material Cut List active rows retain the D-034 composite key. Blank `In Use`
means active and `No` means historical. The parity service compares identity,
factors, rates/date, grams, material cost, transport, unloading, fabrication,
and grand total against workbook caches. It calculates current costing from
active rows, excludes `In Use = No` historical rows from totals, and reports
the pilot's plain-SUM cached summary contribution from two historical lines as
a confirmed legacy formula/cache mismatch. Proposed costing output rounds each
active line's Grand Total Cost of Piece upward to a whole rupee before summing;
legacy workbook parity remains at source precision. File revision/hash
changes alone are not business issues. Clear semantic changes become governed
Safari change-set items when the explicit Reconcile action is run. Unresolved,
invalid, ambiguous, or unsupported inputs remain reconciliation issues.
Selector/range differences in unselected scenario files are inventoried but
are not issues for the active Model Codes. External sheets are refreshed before
each current costing operation. No fixed automated dump-acquisition cadence or
stale-age threshold is set; a future script will acquire the dump and define
freshness policy.

The final matching source row in the current refreshed snapshot determines
Latest Rate even when entry dates regress or tie. Max Rate is the maximum
non-zero rate present in that snapshot, not a lifetime maximum. The upstream
rate log is periodically archived/truncated, typically around 2,000 active
rows; Grist may retain that history, but it must not widen the current costing
snapshot. Future-looking parsed dates are informational because Google Sheets
and LibreOffice may interpret date formats differently; they do not block a
rate. Confirmed invalid entries block the effective displayed rate for that
material across every consuming workbook until a user resolves the issue; the
UI must hide the rate and use a clear error highlight. Nonnumeric price is a
confirmed invalid example; the full invalid-entry taxonomy remains open. Business parity tolerance is
±₹100 at the overall costing-total level. Preserve exact line/component deltas
for diagnosis; the `1e-9` comparison guard is only numeric representation
noise, not the business tolerance.

The Costing Review UI calls `POST /api/catalog/costing-review` for the
explicitly selected product workbook. The API initiates and verifies
LibreOffice refresh before calculating, reads the current RawSteel and
SteelRateLog ODS snapshots, and returns active-line calculations plus
source/formula/cache evidence. It compares the refreshed semantic state with
the last accepted Safari snapshot. Exact five-field MCL keys pair directly;
changed keys are paired only when unique strong evidence supports a probable
modification. Matching supports the configured Tool Shop, Stores, CNC, and
Labour/Paint/Packing lines, detects movements between these lists, and uses
Total Summary headings for option-group changes. MCL ID and row position are
never identity. Ambiguous candidates block reconciliation and are shown for
owner resolution. A CR Log value is carried as evidence and does not bypass
reconciliation. Historical `In Use = No` rows are retained but excluded from
current totals. A blocked active rate/input or unexpected `In Use` value
withholds the current total.

`POST /api/catalog/costing-review/accept` repeats refresh and comparison to
reject stale source hashes or a stale baseline, resolves explicit owner
decisions, and calls the repository acceptance method. `CostingSnapshot` stores
the semantic content, source hashes, accepted actor/time/reason, and previous
snapshot link. `CostingChangeSetItem` stores each rate-source or design change
with before/after state, provenance, CR reference, and calculable cost impact.
`AuditEvent` records each item and the baseline change. Refresh and Calculate
never write. The explicit Reconcile action advances the baseline once, is
idempotent, and leaves a partial Grist write staged until change items,
observation, and audit records are durable. Only Safari Manufacturing receives
these writes; ODS and Costing-New remain unchanged.

The versioned v4 schema adds `CostingSnapshot` and `CostingChangeSetItem` and
includes the pending directory-mapping and issue-evidence extensions. The
validated Safari Manufacturing target was migrated and the follow-up schema
plan showed no remaining difference. The selected workbook policy, safety-margin
meaning, current transport/labor rates, no fixed dump cadence, full-master
extraction, historical-row exclusion, whole-rupee upward line-total rounding,
last-row rate ordering, informational-only future-looking dates, current-snapshot
Max Rate scope, and ±₹100 total parity tolerance are confirmed. Workbook
preview can request LibreOffice's UNO `FULL_UPDATE` linked-content update,
recalculate a disposable sibling copy, and pin the selected/dependency hashes. Only local
ODS links below the costing root are accepted; remote sources fail closed.
This reads the latest saved local dependency files rather than fetching Google
Sheets. Confirmed bad rate rows are shown beside every active MCL line using
that material; the effective rate and dependent costs are hidden. A deliberate
reconciliation scan materializes durable rate issues, and issue resolution
rechecks the source row. The complete invalid-entry taxonomy remains open for
ambiguous cases. Future-looking parsed dates are informational because Google
Sheets and LibreOffice may interpret date formats differently; the last
matching row determines Latest Rate. Max Rate covers only non-zero entries in
the current refreshed rate-log snapshot. Since the upstream log is periodically
archived or truncated (typically around 2,000 rows), retained Grist history
must not widen the current costing snapshot or change its maximum.

## Target normalized line and configuration model

The accepted semantic snapshot is transition evidence and a parity oracle; it is
not the target query model. Each logical MCL, Tool Shop, CNC, Store, Painting,
Labour, or Packing requirement receives a stable `LineMaster`, normally owned by a reusable
`ProductPart`. Immutable `LineRevision` rows carry changing common values and one
typed detail row carries process-specific values. `SourceLineObservation` stores
what was read from an exact workbook/sheet/row, while `SourceLineMapping` records
the reviewed link to the canonical line. Consequently, moving an unchanged ODS
row does not create a business revision, while a material, quantity, weight,
formula, or process change does.

Costing and manufacturing use the same approved Product Part and line revisions
but are separate versioned aggregates. `CostingConfigurationRevision` describes
the complete sellable BOM for a Model Code and its part selections, exact line
overrides, and true cost adjustments. `ManufacturingConfigurationRevision`
describes standard BOM, routing, department, issue route, and batch policy.
`ProductionPlan` instantiates the manufacturing configuration for a real run;
its `ProductionPlanPartLine` rows may request different quantities for Drum,
Chassis, Support, and other subassemblies. The plan records stock coverage and
the reason for quantities exceeding the finished-product quantity without
mutating the costing configuration or reusable part definitions.

The maintained interactive entity/relationship reference is
`docs/data-model.html`; schema-changing work must update it together with the
requirements, decision log, roadmap, physical schema manifest, and tests.
The full diagram is a target design. Delivery Milestone 3 (Phase 1 work
package 1.1) implements the selected S1KHF subset: source observations,
reviewed source-to-master mappings, reusable Part identity where evidenced,
stable process Line Masters, immutable revisions, typed process details,
material/purchase-item references, and row-level audit. The existing accepted
semantic snapshot is retained as a parity baseline. Costing and manufacturing
configuration builders and actual production-plan quantities remain later
schema/workflow slices. `docs/MILESTONE_NUMBERING_AUDIT.md` records the two
numbering systems and the outstanding Phase 0 gates.

The Milestone 3 implementation adds `app/normalized.py` as a pure projection
of the accepted `CostingSnapshot.SemanticContent`, `app/normalized_store.py`
as memory and guarded Grist append-only adapters, and
`app/normalized_import.py` as a local/Safari dry-run planner. Schema v5 adds
13 tables, including one typed `LineDetail` table with process discriminator
and nullable process-specific numeric fields. `SourceLineObservation` pins
all observed row cells, including uninterpreted columns, plus formula/cached-cell evidence, workbook and dependency hashes, parser version,
sheet and row. `SourceLineMapping` records proposed/ambiguous identity;
`LineAuditItem` links immutable revisions. A temporary S1KHF Part is used
until real reusable Part ownership is confirmed. No component hierarchy is
inferred from descriptions. Grist has no unique indexes declared through the
current API, so adapters reject duplicate deterministic keys before writes.
Staged writes validate the target, accepted snapshot, current source hash, and
rate dependency hashes, and
resume by key after partial failure. Normal API startup never migrates.

The current UI/API queries normalized Grist child rows when an accepted Safari
snapshot and v5 rows exist. Otherwise it inspects a labelled projection,
including a `local-unaccepted` mode when Safari credentials are absent. It
does not treat that projection as persisted Grist data. The live plan
confirmed current cost drift against previously covered accepted physical
fields and reconciled active plus historical MCL caches to workbook C7.
Schema v5 and 2,043 normalized rows were applied to Safari Manufacturing;
repeat plan/import found no additional work. Remaining owner decisions are recorded in
`MILESTONE_3_PILOT_RECONCILIATION.md`.
The v5 table keys, Ref cardinalities, search fields, and migration status are
listed in `MILESTONE_3_SCHEMA_PLAN.md`.

## Future costing-authority routing

Importing source rows into Grist is not an authority switch. The effective
costing authority is resolved for each Model Code from an approved, versioned
assignment: either a code-level decision or a Product Model decision covering
all eligible active codes. The assignment records its effective time, source
revision and rate set, approver, reason, and rollback state. One code must
resolve to one authority; a whole-model cutover waits until every active code
meets the required sheet/line, rate, parity, and owner gates.

The ODS route performs the existing source refresh, parse, and comparison.
The future Grist route reads approved typed current line revisions and a
pinned governed rate set through the repository, without opening the ODS for
each costing, cut-list, or inspection request. Source drift monitoring and
provenance remain separate background/review work. This read-path split is
required for the expected large reduction in UI load time after cutover;
benchmark both routes before claiming the performance gain. Milestone 3 has
not switched any Model Code to Grist authority.

## Selected-file workflow - 3 October 2026

SafariRepository.association_detail returns a storage-neutral selected-file DTO.
Grist refreshes durable state under its association lock before constructing it.
The root-confined path endpoint neither registers files nor saves observations.
UI hydration is independent of workbook parsing; selection generations reject
late responses and saved history reloads after Save. Validation stays separate.

The full-width Workbook Preview tab invokes disposable-copy refresh on each
selection. Progress is indeterminate; metadata includes source/copy/dependency
hashes and check time. Failures clear displayed preview values. No-link files
report no_external_links without launching LibreOffice. This reads saved local
dependencies; upstream Google Sheets refresh is a later integration. Processing
and authority are separate; normal stored inspection will not require cutover.

## Processing lifecycle foundation - 5 October 2026

app/processing.py owns the state machine and immutable transition ledger;
repositories supply a memory or guarded Grist event store. File/association
keys remain storage-neutral. The API obtains fresh ownership and current ODS
SHA-256, verifies reviewed source/state/association tokens and derives extraction
evidence from the accepted repository snapshot. Client assertions cannot enable
completion. The selected-file ProcessingPanel renders the API state and audit.

FileProcessingEvent stores transition and audit together in one append-only
row to avoid partially applied cross-table state/audit writes. Request keys and
fingerprints recover lost responses; duplicate versions fail for review.
Read-only old-schema access remains possible, while event writes need v6.
Ready/processed gates remain unavailable pending structural and configuration
services. Costing authority is neither read nor changed by a state transition.

## Explorer source paths (5 October 2026)

Files retains source inspection/preview and workbook process lines. Product Models -> Model Codes uses a separate stored-record endpoint, with no filesystem resolution, source hash calculation or workbook parse on selection. It displays accepted baseline provenance and permits opening the source file or on-demand source reconciliation. The shared file baseline is labelled separately from the future per-code Summary configuration. GristNormalizedStore selects revisions belonging to the requested snapshot rather than the newest unrelated revision. Stage 9 caching and before/after load measurement remain open.

Processing evidence is a separate read-only projection from the refreshed semantic source and accepted baseline. It compares weight fields excluded by legacy cost comparisons, labels missing historical evidence separately from value mismatch and uses exact quantity/decimal kg rules. Costing-New interim display reads MasterMaterial and MaterialRateLog after exact source-name validation; no legacy write method is invoked. Structural/configuration completion remains unavailable pending reviewed mappings/configuration/spares/import.

## Source-scoped Part review — 5 October 2026

app/part_mapping.py separates canonical master creation from source assignment. Memory/Grist stores share the same contract. Canonical creation audits one row; mappings use immutable typed batches with ProductPart references. Description groups are scoped to file, saved source SHA-256 and association version. Blank descriptions are individual coordinates. Current groups must have complete, unambiguous latest assignments before reducing unresolved reconciliation evidence. The API rechecks source and association before append; durable fingerprints recover lost responses. UI retains retry payloads and ignores late responses after file selection changes.

Global normalized names include legacy/inactive Parts. The service serializes writes and rejects detected collisions. Grist lacks declared uniqueness constraints; concurrent external writers require review if they create duplicates. Schema v7 was applied only after a narrow reviewed diff. Stage 7 import must integrate these reviewed references with immutable typed revisions. Paint/Packing and Summary belong to reviewed Model Code configuration; Store Issue has no required Part.

## Canonical Parts in Grist — 7 October 2026

PART-002/D066 and PART-003/D067 are implemented in the current Part slice. `app/grist_parts.py` is the Grist-backed business write/read authority: Safari Grist stores the canonical `ProductPart`, permanent `SM-P-000001` number, generated name and typed scope, metadata versions and aliases, shortcode history, explicit Rev A baseline, mapping references, composition, drawings, vendors, purchase specifications and purchase evidence. `app/part_mapping.py` writes assignments through the same registry. The local SQLite journal only coordinates the single supported host, reserves monotonic numbers and makes request recovery deterministic; it is not a Part business-data registry.

Schema `safari-parts-grist-2026-10-07.v8` was additively applied to the validated Safari Manufacturing document after downloading and checking a native Grist backup. Existing legacy Part rows, numeric revisions and line ownership were left unchanged. Live production has one legacy `ProductPart`, one legacy numeric `PartRevision`, 313 `LineMaster` rows, zero `PartMappingReview` rows, and zero managed Part/purchase fixture rows. One `PartRegistryCoordinator` row binds the supported writer host. Real create/update/restart/purchase persistence was verified in an isolated temporary Grist document; its synthetic records were not copied into production.

The coordinator requires every application process on the writer host to share the same durable local SQLite path. A second host or changed allocator must not take over automatically; rebind/migration requires deliberate operator recovery. The request journal `PartRegistryRequest` and entity-level request keys allow retries to reconcile a Grist commit whose response was lost. For recovery, retry the exact same key and payload, then inspect Grist request/entity rows; a changed payload with the old key is a conflict. Do not manually create a replacement Part after a timeout. Preserve a current native `.grist` backup before schema or bulk data changes and verify its hash and SQLite integrity. The schema command enforces this before apply.

`ProductPart.EngineeringRevision` and `PartRevision.RevisionLabel` explicitly identify managed engineering A. The older numeric `PartRevision.Revision` stays untouched as legacy/source evidence. Rev A can be finalized and its physical definition is then locked; no Rev B+ creation endpoint exists until the approved CR workflow is implemented. Metadata version, mapping/source version and drawing file version are not engineering revisions.

`PartRevisionLine` pins exact line revisions and process types; `PartComponentRevision` pins child engineering revisions and positive quantity/UOM. All MCL, Toolshop and CNC categories are optional and mixable. Revision-specific drawings are optional. Vendor equivalents and actual purchases attach to one canonical Part specification, so adding a vendor never duplicates the Part. The purchase resolver reads the latest eligible actual purchase by transaction time across vendors. Rate inspection is read-only; only an explicit Cost Snapshot stores frozen purchase evidence. It does not use or change `MaterialRateLog` selection.

The dedicated Parts UI has a collapsible scope/target tree and full detail/capture sections. Actual per-code `CostingConfiguration` / `ConfigurationPartSelection` and Live Cost are implemented below. Summary parser/authority integration, CR approvals and legacy identity classification/migration remain unimplemented. `Used in` reports real explicit configuration references rather than misrepresenting source mappings as usage.

## Accepted next architecture: Live Cost and explicit snapshots (D070)

The owner's attached 7 October discussion separates a read-only disposable current Model Code calculation from explicit immutable monetary snapshots. The implementation uses a common live evaluator with transient input provenance, and normalizes saved results as `CostSnapshot` → `CostSnapshotPart`/`CostSnapshotLine` → typed rate evidence. Existing workbook `CostingSnapshot` remains source/import evidence. The old side-effectful purchase-rate evidence API returns 410; only Save Cost Snapshot publishes a reviewed frozen result. Live viewing creates no historical/evidence records.

Schema v9 is applied additively: ten new configuration/rate/snapshot/policy/publication tables and `PartComponentRevision.SourcingRoute`; no existing column types or production rows changed. System/Model/Code inherited reminder policies, explicit snapshot UI, all three comparison modes, stable occurrence/line matching and Q×R attribution are implemented. Snapshot reads verify a checksum of frozen normalized rows; changes made directly in Grist are detected and rejected by the application. A real isolated Grist run verified no writes during Live Cost, policy inheritance/Manual suppression, actual-purchase selection, recovery after an actual committed-row timeout, historical immutability and rate attribution. Direct edits by Grist users with table-write access remain an administrative governance boundary. Automatic Summary import/authority integration, CR approval and legacy classification remain separately gated. See [full requirements and Grist relationships](LIVE_COST_AND_SNAPSHOT_REQUIREMENTS.md), [visual model](live-cost-snapshot-model.html) and [defect review](PARTS_COMPLETION_REVIEW_2026_10_07.md).
