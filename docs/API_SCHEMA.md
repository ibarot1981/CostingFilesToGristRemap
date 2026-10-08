# Phase 0 API and schema contract

> Owner scope update D070, 8 October 2026: Live Cost, explicit normalized Cost Snapshots, policy inheritance and attributable comparisons are implemented in the v9 base schema. Schema v10 adds normalized, audited Part intended-sharing relationships. Ordinary live viewing and intended-sharing edits persist no costing history/evidence. Existing source-workbook CostingSnapshot remains separate.

All JSON errors have a stable `detail.code` and `detail.message`.

| Endpoint | Purpose |
|---|---|
| `GET /api/explorer/tree?path=` | One lazy directory level under `COSTING_ROOT`. |
| `GET /api/explorer/inspect?path=` | On-demand file metadata, ODS health, sheet count, links, and hash. |
| `GET /api/catalog/files?query=&directory=&status=&classification=&extension=&offset=&limit=` | Recursive ODS path search and file registry view; returns metadata only (no workbook inspection/hash) with mapped/unmapped/conflict filters and pagination. |
| `GET /api/catalog/preview?path=&sheet=&start_row=&row_count=&start_col=&column_count=` | Bounded ODS preview from the saved workbook cache; active Material Cut List rows affected by confirmed invalid SteelRateLog entries show warnings and hide the effective rate and dependent costs. |
| `POST /api/catalog/preview/refresh?path=&sheet=&start_row=&row_count=&start_col=&column_count=` | Ask headless LibreOffice to update local ODS links and recalculate a temporary copy, then return the bounded preview and pinned dependency hashes. Remote links and sources outside `COSTING_ROOT` fail closed; no source workbook is saved. |
| `POST /api/catalog/costing-review` | Body: `{"path":"<root-relative selected .ods path>"}`. Refreshes the selected workbook's local ODS links on a disposable copy, reads current RawSteel and SteelRateLog sources, returns active-only calculations and compares semantic state with the accepted Safari snapshot. Reports rate-source changes, material/process-line additions, removals, probable modifications, list movements, option-group changes, CR evidence, source formulas/cells, and cost impact. Ambiguous identities block reconciliation. This endpoint is read-only and returns `EXTERNAL_REFRESH_FAILED`, `COSTING_SOURCE_UNAVAILABLE`, or `COSTING_REVIEW_UNAVAILABLE` on failure. |
| `POST /api/catalog/costing-review/accept` | Body includes path, the preview's `semanticHash` and `sourceHashes`, `acceptedSnapshotKey`, optional `ambiguityDecisions`, and `reason`; requires `Idempotency-Key`. Repeats refresh and comparison, rejects stale source/baseline state, resolves explicit ambiguous-line choices, then persists accepted semantic snapshot/change-set/audit items to Safari Manufacturing. The actor comes from trusted Authentik proxy headers. ODS and Costing-New are not written. |
| `GET /api/catalog/normalized?path=&sheet=&process=&master=&part=&material=&status=&offset=&limit=` | Read-only S1KHF row inspection. Queries normalized Grist child rows when the accepted snapshot and v5 rows exist; otherwise returns a clearly labelled `projection_only` view (or `local-unaccepted` when no accepted snapshot is configured). Returns source hashes, sheet/status counts, mapping exceptions, and paged typed rows with master, revision, source cells/formulas and audit. It does not create records. |
| `GET /api/products` | Catalog Products. |
| `GET /api/products/{product_id}/models` | Models for a Product. |
| `GET /api/models/{model_id}/codes` | `active` selling Codes, `available` unowned active Codes, `conflicts` with current owner details, and `legacy` spares-only Codes. |
| `POST /api/associations/validate` | Validate a proposal and return structured conflicts. |
| `POST /api/associations` | Save a validated proposal; supports `Idempotency-Key`. |
| `GET /api/associations/{file_id}/history` | Active and superseded history, linked codes, and audit events. |
| `GET /api/processing-queue?status=&limit=` | Durable association batches, newest first; default limit 100, maximum 500. |
| `GET /api/mapped-files?status=&product_id=&model_id=` | Groupable ODS review rows with mapped/unmapped/conflict status, reconciliation issue codes, latest repository observation, and filesystem mtime fallback. `status` accepts `mapped`, `unmapped`, `conflict`, and `needs-review`. |
| `GET /api/reconciliation/issues?status=&issue_type=&severity=&owner=&product_id=&model_id=&file_id=&query=&offset=&limit=` | Search/filter durable issues. Default page 100, maximum 500. |
| `GET /api/reconciliation/issues/{issue_id}` | Issue evidence plus observation history, association histories for related files, and audit trail. |
| `POST /api/reconciliation/issues/{issue_id}/actions/{action}` | `assign`, `unassign`, `keep-open`, `defer`, `resolve`, or `reopen`; lifecycle actions require a reason and `expectedVersion`. Mutations support `Idempotency-Key`. |
| `POST /api/reconciliation/scan` | Scan up to 100 registered ODS files; defaults to `{"dryRun":true,"limit":50}` and never resolves absent conditions. An explicit materialization can create durable `invalid_rate_entry` issues for bad rate-log rows affecting active MCL lines. |
| `GET /api/reconciliation/export?format=csv|json&...` | Export the filtered register, up to 5,000 issues; larger exports return `EXPORT_LIMIT_EXCEEDED` rather than silently truncating. |
| `POST /api/reconciliation/issues/{issue_id}/source-revision/preview` | Compare stored/current path, size, mtime, SHA-256, readability, sheet count, external references, and current code ownership. |
| `POST /api/reconciliation/issues/{issue_id}/source-revision/apply` | Accept the reviewed revision; Irshad actor, reason, issue version, old hash, current hash, and idempotency key are required. |
| `POST /api/reconciliation/identity-cleanup/preview` | Plan replacement-character model cleanup and list all governed reference checks. |
| `POST /api/reconciliation/identity-cleanup/apply` | Supersede/inactivate one verified model without deletion; Irshad actor, reason, issue version, and idempotency key are required. |
| `GET /api/directory-mappings?status=` | List versioned Product-directory proposals and decisions. |
| `POST /api/directory-mappings` | Propose a root-confined relative directory-to-Product mapping; does not assign files. |
| `POST /api/directory-mappings/{mapping_id}/{action}` | Approve, reject, or supersede a mapping with reason, `expectedVersion`, and idempotency key. |

The validation request uses `path` or `fileId`, `productId`, `modelId`,
`codeIds`, optional `reason`, optional `expectedVersion`, optional
`expectedHash`, and optional `supersede`. A valid response includes the current
expected association version/hash; the UI carries those values into save. The
save request also sends an `Idempotency-Key` header, which the UI retains across
network retries and replaces when a new proposal is validated. Any `actor`
field in a save body is ignored: audit attribution is read from trusted
authentication-proxy headers, with a local-development fallback. These headers
must be overwritten by the deployment proxy; this API does not itself provide
authentication or role enforcement. Save remains disabled until validation
returns `valid: true`.

Repeated canonical Model Codes that are not explicitly resolved remain
inactive and produce error-level reconciliation issues. Association validation
rejects inactive or unresolved selections with `UNRESOLVED_CATALOG_CODE`,
including direct API requests that bypass the UI's active-code list. Approved
GC rating variants remain separate canonical codes with their source values
preserved as aliases/provenance.

The mapped-files view merges registered repository rows with the current
root-confined ODS tree. Its projection detects duplicate active code owners,
catalog-reference mismatches, missing/changed files, verified moves, known parse
failures, and known external-link warnings. Rows with an association also expose
its `processingBatch` when one exists. `lastObservedAt` is the repository
observation time; `lastObservedChange` distinguishes that recorded file metadata from the
filesystem-mtime fallback used when no observation exists. A list request does
not inspect or hash every workbook; hashes remain on-demand during inspection
or association validation. For a missing mapped file, move detection considers
only a unique candidate with matching recorded size and timestamp, then verifies
its hash before reporting `moved_file` and `movedTo`. Derived warnings remain
separate from durable issues until an explicit scan materializes them; a
warning disappearing does not resolve a persisted issue. Source revision
acceptance is a separate preview/apply workflow and never modifies the source
ODS.

Association history returns the selected code display values and matching
`AuditEvent` records for each active or superseded association. The processing
queue endpoint reads durable association `ImportBatch` rows and supports a
case-insensitive status filter. Processing remains a safe visible stub: a newly
saved association is marked `queued`, and no costing parser is executed.

Explorer tree, inspection, and recursive search project `conflict` from active
code-owner links rather than relying only on the cached `CostingFile` mapping
status. The catalog conflict filter therefore agrees with Mapped Files while
remaining metadata-only; ownership indexes are built once per request.

The versioned foundation schema is described in
`config/safari_manufacturing_schema.json` and `app/schema.py`. V4 includes
Product, ProductModel, ProductModelCode, IdentityAlias, CostingFile,
FileObservation, FileModelAssociation, FileCodeAssociation, ImportBatch,
ReconciliationIssue, DirectoryProductMapping, `CostingSnapshot`,
`CostingChangeSetItem`, and AuditEvent. V4 adds model supersession metadata,
issue evidence/lifecycle fields, immutable semantic costing baselines, and
governed change-set items with CR/source/cost evidence.

The v4 plan was revalidated against the configured Safari Manufacturing target
on 29 September 2026. It created `DirectoryProductMapping`, `CostingSnapshot`,
and `CostingChangeSetItem`, extended `ProductModel` and `ReconciliationIssue`,
and changed no existing column types. The guarded apply completed, and the
follow-up plan had no remaining table or column changes. Governance mutations
recheck the current schema before their first write and return
`SCHEMA_MIGRATION_REQUIRED` if it drifts.

The semantic JSON payloads are stored as compact JSON text inside Grist
`Any` cells, then decoded by the repository on read; Grist cell values cannot
accept arbitrary nested objects directly. Snapshots keep full costing
semantics and formula/cell evidence, but rate evidence stores the selected and
last source rows plus source hashes rather than duplicating the entire rate
history for every material.

## Selected-file extensions - 3 October 2026

GET /api/explorer/association?path= resolves a root-confined ODS path and returns
{fileId, current, product, model, codes, history, mappingStatus, processingBatch}.
Nested paths use a query parameter rather than slash-bearing route IDs.
Unassociated files return current:null, empty codes/history. The read does not
register a file or persist observations. The older association-detail route
returns the same expanded DTO. Grist reads refresh durable state.

Preview refreshMetadata.status now also accepts no_external_links, with a
source hash and null refreshed-copy hash. Linked workbooks retain
refreshed_temporary_copy. Errors remain EXTERNAL_REFRESH_FAILED; cached values
are never silently presented as fresh. No schema migration for stages 1-2.

## Processing lifecycle API - 5 October 2026

GET /api/processing/state?path= returns state, effectiveState, version,
sourceHash, recordedSourceHash, sourceChanged, associationKey/Version,
schemaAvailable, stateBasis and immutable history. A changed source/association
projects changes_pending; GET never persists that change. Initial new/associated
states derive from the registered source/current saved association.

POST /api/processing/state requires path, state, reason, expectedVersion,
expectedHash, expectedAssociationKey/Version and Idempotency-Key. Proxy headers
supply the actor. Replays return the original event; stale evidence, changed
key payloads, absent associations and invalid transitions return typed 409s.
Extraction must match the accepted source hash. Completion actions fail with
PROCESSING_GATES_UNAVAILABLE until structural/configuration evidence is connected.

Schema safari-processing-2026-10-05.v6 adds FileProcessingEvent with typed
transition/audit/source columns, stable domain file/association keys, request
fingerprint and version. One atomic event insert includes its audit evidence.
Duplicate versions/requests require review; no last-write-wins selection.

## Stored Model Code records (5 October 2026)

GET /api/model-codes/{code_id}/records accepts sheet/process/master/part/material/status filters and bounded offset/limit. It resolves exactly one active owner, reads the latest accepted file snapshot and joins stored Safari child records without opening or hashing an ODS. Duplicate/inconsistent owners and duplicate accepted snapshot keys fail with 409. Responses include readAt, sourceRead=false, source/association, accepted/observed timestamps, snapshot/hash, status and total/items. Unassociated, no_accepted_snapshot, storage_unavailable and schema_unavailable are explicit empty states.

The scope is shared_file_baseline; configurationStatus remains review_required. These rows are source-file evidence, not an implemented per-code configuration or approval of costing authority. Reconciliation is explicitly opened, then refreshed through the existing costing-review operation. Line revisions are pinned to the requested accepted snapshot.

Costing-review adds processing_evidence (mandatory-sheet coverage, exact quantities, 2-decimal kg comparisons, missing/changed evidence status, Part/blank-row review, Summary inventory and explicit completionAvailable=false) and interim_rates (read-only Costing-New field values, reviewed material aliases, ODS/current differences, read time, default/latest basis and unavailable status). Rate failure is reported without replacing structural evidence. These fields do not modify snapshot acceptance or confer costing authority.

## Canonical Parts and source assignments — 5 October 2026

POST /api/parts requires name, reason and Idempotency-Key; trusted proxy headers supply actor. Name uniqueness spans every Safari ProductPart record after Unicode NFKC, whitespace collapse and case folding. Creation stores audit/retry fields in the same row. Duplicate/ambiguous names fail with PART_NAME_EXISTS/PART_NAME_CONFLICT. Replays return the original canonical Part.

GET /api/parts/mappings?path= reads a root-confined saved workbook plus Safari records. It returns sourceBasis=saved_workbook, sourceHash, associationKey/Version, mapping version, schemaAvailable, parts, exact-description groups, individually scoped blank rows, unresolvedGroups and immutable history. Temporary Parts are not selectable. No record write or external-sheet refresh occurs.

POST /api/parts/mappings requires path, decisions (group-key to Part-record-ID strings), reason, expectedHash, expectedVersion, expectedAssociationKey/Version and Idempotency-Key. Partial selection is supported; unassigned groups remain unresolved. New batches contain one typed PartMappingReview row per selected source row, referencing ProductPart. Stale source/association/mapping evidence, unknown groups, noncanonical/ambiguous Parts and conflicting/incomplete histories return typed errors. A final recheck precedes the guarded atomic record batch. Processed files must record Changes Pending before new assignments. Retry fingerprints include all reviewed tokens and choices.

Schema safari-part-review-2026-10-05.v7 adds PartMappingReview and ProductPart NameKey/CreatedActor/CreatedReason/CreatedAt/CreateRequestKey/CreateFingerprint. Current reviewed mappings reduce costing-review.processing_evidence.partRowsRequiringReview; partMapping reports version, schema availability, reviewed row count and unresolved groups. No assignment silently changes accepted line masters, configuration or costing authority.

## Grist-backed Parts API and schema — 8 October 2026

Schema `safari-part-intended-sharing-grist-2026-10-08.v10` is applied to the validated Safari Manufacturing Grist document on top of the v9 Live Cost/Snapshot base. It adds normalized intended-sharing links, sharing state/history and a durable request payload column. The business records are canonical in Grist. The local `SAFARI_PART_DATABASE_PATH` SQLite file is a single-host reservation, serialization and recovery journal only. Do not copy a local DB as a substitute for Grist backup or use independently initialized allocators on multiple hosts.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/parts?search=&scope=&target_id=&include_retired=` | Search canonical managed Parts and visible read-only legacy records by number, current name or historical alias. |
| `GET /api/parts/scope-targets` | Active Product, Product Model and Model Code targets plus maintained shortcodes. |
| `GET /api/parts/preview?scope=&target_id=&description=&variant=&exclude_id=` | Server-generated name preview and normalized collision status. |
| `POST /api/parts/shortcodes` | Create/update a scope shortcode with actor, reason and `Idempotency-Key`. |
| `POST /api/parts` | Create stable UUID + permanent number, generated metadata/alias and Rev A in Grist. Optional `selectedProductId`, `selectedProductModelId` and `intendedModelCodeIds` save initial advisory sharing in the same recoverable Save. Actor comes from trusted request context; reason and `Idempotency-Key` are required. |
| `GET /api/parts/{part_id}` | Stable Part detail with metadata/alias/mapping/lifecycle history, normalized `intendedSharing`, direct `usedIn` configuration selections, and joined line, child, drawing and purchase sections. |
| `PUT /api/parts/{part_id}/intended-sharing` | Save the complete intended-code set with `expectedVersion`, `intendedModelCodeIds`, reason and `Idempotency-Key`; optional Product/Model filter IDs are validated against each code. |
| `GET /api/parts/{part_id}/metadata-preview` | Proposed name/scope change and current reference fingerprint. |
| `POST /api/parts/{part_id}/metadata` | Version-checked metadata change; preserves identity/number/A and records prior name as alias. |
| `POST /api/parts/{part_id}/retire` | Audited retirement; never releases or reuses number/name reservations. |
| `GET /api/parts/mappings?path=` / `POST /api/parts/mappings` | Read source groups and save reviewed PartMappingReview rows with stable Part, revision and metadata references plus source hash, association, actor/reason and retry evidence. Part selection/creation remains separate from assignment Save. |
| `GET /api/parts/{part_id}/composition` / `POST /api/parts/{part_id}/components` | Read or add a child Part pinned to the child revision with positive quantity/UOM; cycles are rejected. |
| `POST /api/parts/{part_id}/finalize-revision` | Finalize the initial A definition. Finalized physical content is locked; later engineering revisions require a future approved CR workflow. |
| `GET /api/part-line-candidates` / `POST /api/parts/{part_id}/process-lines` | Search existing normalized lines and link an exact line revision/process type/quantity to Rev A. |
| `POST /api/parts/{part_id}/drawings` / `GET /api/parts/drawings/{drawing_key}/open` | Link an optional local file or HTTPS drawing with file version/audit; open only a validated local file link. |
| `POST /api/parts/{part_id}/vendors` | Add canonical Vendor identity. |
| `POST /api/parts/{part_id}/purchase-specifications` | Add purchased physical specification against the Part and engineering revision; optional `PurchaseItem`. |
| `POST /api/parts/{part_id}/vendor-mappings` | Save reviewed Vendor SKU equivalence to the same canonical Part specification. |
| `POST /api/parts/{part_id}/purchases` | Capture immutable posted actual purchase against a reviewed vendor mapping, with transaction identity/date, quantity/UOM, currency, extended amount, discount/charges, actor/reason and idempotency key. |
| `POST /api/parts/{part_id}/purchase-specifications/{specification_id}/unit-conversions` | Add an explicit, reasoned and evidenced UOM conversion. |
| `POST /api/parts/{part_id}/purchase-specifications/{specification_id}/currency-conversions` | Add an explicit dated currency conversion with source evidence. |
| `GET /api/parts/{part_id}/purchase-rate?as_of=` | Show current or as-of rate resolution, vendor equivalents, transaction history and explicit unavailable/conflict state. |
| `POST /api/parts/{part_id}/purchase-rate-evidence` | Returns HTTP 410 `SNAPSHOT_REQUIRED`; rate inspection cannot write evidence. |
| `GET /api/model-codes/{code_id}/costing-configuration` / `PUT ...` | Read or publish an explicit, versioned Part-occurrence configuration for one Model Code. Source mappings and workbook baselines are not inferred as a BOM. |
| `GET /api/model-codes/{code_id}/live-cost` | Disposable current calculation with manifest, stable line/occurrence identity, rate provenance and unresolved warnings. This path writes no history or purchase evidence. |
| `GET /api/model-codes/{code_id}/cost-snapshots` / `POST ...` | List completed history or explicitly save a reviewed, complete Live Cost result as normalized Grist rows. Publishing rows are hidden; exact retries resume the same frozen payload. |
| `GET /api/cost-snapshots/{snapshot_key}` | Read frozen header, Part occurrences, lines and evidence only; verifies the saved row checksum and never resolves current rates. |
| `POST /api/model-codes/{code_id}/cost-comparisons` | Compare Live to last/selected snapshot or two snapshots for that code. Returns quantity/configuration, rate, structural and residual impact with documented Q×R attribution. |
| `GET /api/model-codes/{code_id}/cost-policy` / `PUT /api/cost-snapshot-policies/{scope_type}` | Resolve or version the System → Product Model → Model Code reminder policy; Manual suppresses inheritance, and no schedule creates snapshots automatically. |
| `POST /api/line-masters/{line_master_id}/process-rates` | Capture an explicitly sourced, dated, versioned process rate for Live Cost. |

Mutating calls use stable idempotency keys; reusing a key with changed payload returns a conflict. After response loss, retry the identical key and payload. Inspect `PartRegistryRequest` and the relevant business table before manual recovery; do not allocate a second Part because a request timed out. One Grist `PartRegistryCoordinator` binds the configured host/local allocator. A different host/coordinator cannot take over automatically. Intended sharing is advisory: its save does not configure a code, assign quantities, create mappings or snapshots, or alter Part naming/identity/revision. The naming anchor remains a single independent Global/Product/Model/Code scope.

### Revision and compatibility boundaries

New managed Parts use `ProductPart.EngineeringRevision=A` and a separate `PartRevision.RevisionLabel=A` baseline with Draft/Finalized status. The pre-existing numeric `PartRevision.Revision` field remains untouched as legacy/source evidence. Metadata version, source mapping version and drawing file version are not Part engineering revisions. Rev B+ cannot be created until the governed CR workflow and approval evidence exist.

`PartRevisionLine` pins exact line revisions. `PartComponentRevision` pins parent/child revision plus quantity/UOM and occurrence sourcing route. MCL, Toolshop and CNC links are optional and may be mixed. Drawings are optional. `PartIntendedModelCode` records advisory sharing separately from explicit per-code `CostingConfiguration`/`ConfigurationPartSelection`. `Used in configurations` derives direct selections from current active published configuration revisions. Component/indirect usage is not included in this view. Automatic Summary import/authority integration is not implemented.

### Purchased-Part costing policy

Select the latest eligible posted/completed actual purchase by transaction timestamp across all reviewed vendors for the same canonical Part specification/revision. `RecordedAt` is audit only; a backdated purchase does not displace a later transaction. Quotes, drafts, voids, returns and reversed purchases are excluded. Identical transaction/line replay collapses idempotently; contradictory duplicate payloads and different-rate ties at the latest timestamp produce a visible conflict.

Applied rate is net merchandise per costing UOM: deduct explicit discount, exclude tax/freight/other charges. Use explicit reviewed UOM conversion and dated currency conversion evidence. If the newest eligible transaction is incomparable, return unavailable/review-needed; do not choose an older rate. No eligible history returns unavailable, never zero or MaterialRateLog/default fallback. Read-only rate resolution does not write evidence. Explicit Save Cost Snapshot persists the selected record/vendor/date/rate/cost basis in `CostSnapshotRateEvidence`; historical detail does not resolve a newer purchase. Existing MaterialRateLog behavior remains unchanged.

### Deployed schema and verification boundary

The v9 base apply was additive after a native `.grist` backup passed SHA-256 and SQLite integrity checks. It added ten configuration/rate/snapshot/policy/publication tables and one component-route column. The v10 apply used a separate verified native backup and added `PartIntendedSharingState`, `PartIntendedModelCode`, `PartIntendedSharingEvent` and `PartRegistryRequest.Payload`; no existing column type changed. Live production retains 1 legacy ProductPart, 1 legacy numeric PartRevision and 313 LineMaster rows, with 0 mapping reviews, 0 Part sharing rows and 0 managed Part/purchase fixtures. One coordinator row is bound. No legacy ownership or revision was silently migrated.

Isolated temporary Grist documents verified real API create/read-back/restart, metadata alias history, mapping, child quantity, process-line linkage, multiple vendors, current and historical purchase selection, configuration/policy persistence, Live Cost no-write, same-key snapshot recovery after an actual committed-row timeout, frozen historical reads and rate attribution. The cascade browser document verified one Part with two intended codes, then an audited removal, while configuration usage stayed empty. Test documents were moved to Trash after readback. Automatic Summary import/authority integration, CR approval and legacy identity migration are still open.
