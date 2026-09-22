# Phase 0 API and schema contract

All JSON errors have a stable `detail.code` and `detail.message`.

| Endpoint | Purpose |
|---|---|
| `GET /api/explorer/tree?path=` | One lazy directory level under `COSTING_ROOT`. |
| `GET /api/explorer/inspect?path=` | On-demand file metadata, ODS health, sheet count, links, and hash. |
| `GET /api/catalog/files?query=&directory=&status=&classification=&extension=&offset=&limit=` | Recursive ODS path search and file registry view; returns metadata only (no workbook inspection/hash) with mapped/unmapped/conflict filters and pagination. |
| `GET /api/catalog/preview?path=&sheet=&start_row=&row_count=&start_col=&column_count=` | Bounded read-only ODS preview. |
| `GET /api/products` | Catalog Products. |
| `GET /api/products/{product_id}/models` | Models for a Product. |
| `GET /api/models/{model_id}/codes` | `active` selling Codes, `available` unowned active Codes, `conflicts` with current owner details, and `legacy` spares-only Codes. |
| `POST /api/associations/validate` | Validate a proposal and return structured conflicts. |
| `POST /api/associations` | Save a validated proposal; supports `Idempotency-Key`. |
| `GET /api/associations/{file_id}/history` | Active and superseded history, linked codes, and audit events. |
| `GET /api/processing-queue?status=&limit=` | Durable association batches, newest first; default limit 100, maximum 500. |
| `GET /api/mapped-files?status=&product_id=&model_id=` | Groupable ODS review rows with mapped/unmapped/conflict status, reconciliation issue codes, latest repository observation, and filesystem mtime fallback. `status` accepts `mapped`, `unmapped`, `conflict`, and `needs-review`. |

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
its hash before reporting `moved_file` and `movedTo`. This is a discrepancy review surface, not yet an
audited issue-resolution queue.

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
`config/safari_manufacturing_schema.json` and `app/schema.py`. Its tables are
Product, ProductModel, ProductModelCode, IdentityAlias, CostingFile,
FileObservation, FileModelAssociation, FileCodeAssociation, ImportBatch,
ReconciliationIssue, and AuditEvent.
