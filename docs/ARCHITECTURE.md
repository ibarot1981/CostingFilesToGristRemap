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
