# Safari Manufacturing Phase 0 architecture

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
