# Phase 0 API and schema contract

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

## Part identity and mapping implementation — 7 October 2026

PART-002 / D066 and PART-003 / D067 are implemented in the application on branch `codex/part-identity-foundation`. `GET /api/parts` searches current names, historical aliases, Part numbers and read-only legacy Grist names. `GET /api/parts/scope-targets` returns active Product, Product Model and Model Code targets plus maintained shortcodes. `GET /api/parts/preview` server-generates the scope/description/variant name and reports collisions. `POST /api/parts/shortcodes` audits shortcode maintenance; `POST /api/parts` allocates a permanent number and stable UUID with a durable idempotency key. Creation initializes Rev A and rejects conflicting client revision assertions.

`GET /api/parts/{part_id}` returns the stable identity, metadata/lifecycle history and mapping history, with explicit unavailable states for line, drawing and per-code configuration data not yet connected. `GET /api/parts/{part_id}/metadata-preview` returns before/after naming, current source assignment evidence and a fingerprint. `POST /api/parts/{part_id}/metadata` requires the current metadata version and usage fingerprint; it preserves identity/number/Rev A, records aliases and audit history, and rejects design-variant changes. `POST /api/parts/{part_id}/retire` preserves the retired identity and reserved number.

`GET /api/parts/mappings` and `POST /api/parts/mappings` retain the source/hash/association/version checks described above. New assignments use the stable Part UUID and pinned Rev A in local SQLite; existing Grist Part and mapping records remain readable and are never changed by this Part slice. Out-of-scope usage is advisory and does not block Save. Part creation and assignment remain separate.

The new registry is `state/safari_parts.sqlite3` by default, or `SAFARI_PART_DATABASE_PATH`. SQLite unique constraints and `BEGIN IMMEDIATE` serialize application writers sharing one local database file on one host. This supports concurrent processes on that host; a network share or multi-host deployment is unsupported. Back up with `python scripts/backup_part_registry.py`; the backup uses SQLite's online backup API and verifies integrity. No new Grist schema was applied: Safari remains at its previously reviewed v7 schema. No live Part or assignment was created or migrated by this implementation. Legacy `PartRevision` numeric values remain unverified and are not promoted to CR-approved revisions. The exact live compatibility diff and cutover require a separately reviewed migration plan.
