# Milestone 1 Post-Merge Audit

Audit date: 23 September 2026  
Audited baseline: `main` at merge commit `c9e0a0a` (PR 1)

## Verdict

Milestone 1 is a **conditional pass**. Its primary user journey is implemented and verified: the Safari Manufacturing document and schema are guarded, the canonical catalog is present, the filesystem Explorer and bounded ODS preview work, and three S1KHF files are associated to one Product Model with 19 unique Model Code owners, audit events, and queued batches.

It is not a clean Phase 0 exit. Two live reconciliation conditions and several planned Milestone 0.5 controls remain. They must be addressed before starting the shared material-mapping extraction or S1KHF costing ingestion.

## Verification performed

- Repository was clean before this audit.
- Original Milestone 1 baseline: 62 Python tests ran (61 passed and one expected platform-dependent symlink skip); 2 Node view-model tests and 8 Vitest tests passed.
- Current Milestone 0.5 implementation verification: 78 Python tests ran (77 passed and one expected platform-dependent symlink skip); 2 Node view-model tests and 10 Vitest tests passed; TypeScript check and production build passed.
- Guarded Grist dry-run rediscovered four writable workspaces and required explicit selection.
- With the previously approved Work workspace selected, setup validated the existing Safari Manufacturing document and returned a no-op schema plan for `safari-foundation-2026-09-19.v2`.
- Read-only live repository inspection found 24 Products, 44 Product Models, 173 Model Codes, 6 aliases, 68 issues, 3 Costing Files, 3 active file/model associations, 19 active code links, 5 batches, and 3 audit events.
- All live reads were non-mutating. No ODS file, Safari record, or legacy Grist record was changed during this audit.

## Planned versus actual at the original audit baseline

| Area | Planned outcome | Verified actual | Result |
|---|---|---|---|
| Safari document setup | Create or safely reuse exactly one named document; reject legacy target; idempotent schema | Exact document/workspace validation works; schema plan is a no-op; tests cover duplicates, permissions, uncertain create, and wrong target | Met |
| Canonical identity | Import source values, aliases, GC disambiguation, Bush classification, provenance, visible exceptions | Canonical 24/42/173 identity set is present; approved GC codes and aliases exist; 68 blank-description issues exist | Mostly met; corrupted duplicate rows remain active |
| Explorer safety | Lazy filesystem tree confined to configured root | Implemented with traversal, absolute-path, symlink/junction/reparse checks and tests | Met |
| Workbook preview | Read-only, bounded sheets/cells/formulas/external-link indicators | Implemented and tested for bounds, formulas, encrypted/corrupt input, unsupported extensions, and external links | Met |
| Association rules | One file → one model; model → many files; code → one active file | Three live S1KHF files map to one model; 19 active codes have unique owners | Met |
| Concurrency and retry | Hash/version validation and idempotent recovery | Implemented in memory and Grist adapters; partial-write and key-collision tests pass | Met |
| Audit and queue | Actor, reason, history, audit event, and visible queued batch | Persisted for all three live mappings and exposed in Mapped Files | Met for first save |
| Supersede/conflict | Explicit reason and preserved history; conflicts visible | Domain/repository tests pass; no owner-reviewed live or isolated-browser conflict/supersede walkthrough exists | Partial |
| Mapped Files | Filter/sort/group, health discrepancies, details/history | Projection and UI exist; changed/missing/moved/catalog/conflict detection has automated coverage | Partial; no durable resolution workflow |
| Directory mapping | Approved directory-to-Product inheritance | Not implemented | Not met; Milestone 0.5 |
| CLI mapping reuse | Identify extraction seam without duplicating rules | Seam documented; CLI behavior retained | Met for Milestone 1; extraction remains Milestone 0.6 |
| Accessibility/UI acceptance | Keyboard/basic accessibility and responsive owner acceptance | Semantic controls and component tests exist; owner acceptance and a recorded desktop/narrow-width walkthrough remain open | Partial |
| Documentation | Requirements, roadmap, decisions, API/architecture, status current | Substantial evidence exists, but post-merge live drift made several “hash unchanged” statements stale | Corrected by this audit |

## Carry-forward gaps as recorded at the original audit baseline

### G-01 — Changed mapped HF-MS Drum workbook (high)

The current file at:

`S1KHF/Local/HF-MS Drum/Safari 1000 HF Local V 4.2 - MS Drum.ods`

no longer matches the hash stored with its live Safari Manufacturing observation. The Mapped Files API correctly reports `changed_file` plus `external_links`; the other two mapped files still match their stored hashes.

Required response (updated by D-033):

- do not overwrite the stored hash or association automatically;
- inspect the changed workbook read-only and compare semantic costing/material contents with the last accepted snapshot;
- treat hash drift as a source-revision signal; create an issue only for an unresolved semantic discrepancy or failed/incomplete comparison;
- propose valid source changes with provenance and a reviewable change set;
- let Irshad explicitly accept a new source revision, defer it, or reject/revisit it;
- accepting it creates a new immutable FileObservation and AuditEvent while preserving the earlier observation and association history.

### G-02 — Two active encoding-corrupted Product Models (high)

The canonical ODS contains:

- `Safari – SSV` at source row 172;
- `Safari – SSM` at source row 174.

Those valid live Product Models already exist and own their active codes. Two additional active models contain the Unicode replacement character instead of the en dash:

- `Safari � SSV`;
- `Safari � SSM`.

The corrupted rows own no active Model Codes. They are data-quality remnants, not source identities.

Required response:

- produce a dry-run cleanup/reconciliation plan;
- prove that the corrupted rows have no active code, file, or other governed references;
- do not delete them;
- after explicit approval by Irshad, mark them inactive/superseded, record the canonical replacement IDs, audit the action, and resolve the issue;
- make future catalog imports detect invalid replacement characters and fail closed or create an error-level issue.

### G-03 — Reconciliation is a projection, not a workflow (high)

Mapped Files derives issues but does not durably assign, defer, resolve, reopen, or audit them. Implement the complete Milestone 0.5 issue lifecycle and an exportable report.

### G-04 — Supersede/conflict UX has not passed an owner-reviewed scenario (medium)

The underlying logic is tested, and the Explorer exposes the supersede flag, but the Mapped Files workflow does not provide a complete guided resolution. Exercise this first in an isolated/in-memory fixture; do not manufacture a conflict in the live document.

### G-05 — Directory-to-Product proposals and inheritance are absent (medium)

Implement versioned proposed/approved/rejected/superseded mappings. During Phase 0, only Irshad may approve them.

### G-06 — Repository protocol is narrower than the implemented adapters (medium)

`SafariRepository` declares only identity-list operations while the API relies on file, association, history, issue, audit, and queue behavior inherited from the concrete in-memory class. Expand the storage-neutral contract and add adapter contract tests before extending workflows.

### G-07 — Broader classification and UI acceptance remain open (low)

Review candidate classifications across representative real directories and record desktop plus narrow-width acceptance. Add focused automated accessibility assertions for the issue workflow; do not claim full WCAG conformance.

## Milestone 0.5 implementation closure update (23 September 2026)

The code and synthetic/fake-adapter coverage now address the carry-forward gaps:

- **G-01:** changed-source preview records old/current metadata and hashes; the
  durable issue, stale-hash recheck, accept/defer/keep-open/reopen workflow,
  immutable observation history, and audit persistence are implemented. The
  live HF-MS revision remains preview-only. This implementation detects hash
  drift but does not yet compare semantic costing/material contents; under
  D-033, hash drift alone must remain a revision signal, not a business issue.
- **G-02:** catalog import rejects replacement-character identity rows without
  converting them to en-dash identities. Cleanup requires an exact canonical
  match and a fresh zero-reference plan, supersedes without deletion, and
  carries source evidence into the audit/resolved issue. The two live rows
  remain active pending explicit approval.
- **G-03/G-06:** `SafariRepository` now declares the storage-neutral API surface;
  contract behavior is covered by the in-memory and fake-client Grist adapters.
  Issues support assignment, defer/resolve/reopen/keep-open, reason/audit,
  optimistic version checks, stable fingerprints, and CSV/JSON export.
- **G-04/G-05:** the deterministic duplicate-code UI fixture shows the active
  owner and exact association/code history and opens that owner in the Explorer.
  Versioned directory mappings support proposals and Irshad-only approval,
  rejection, supersession, safe paths, and inheritance precedence.
- **G-07:** representative root classifications and evidence are recorded in
  `MILESTONE_0_5_CLASSIFICATION_REVIEW.md`. The synthetic browser rendered at
  1280 × 720 and 390 × 844; the narrow layout has no horizontal overflow. A
  component keyboard test selects an issue and verifies focus moves to the
  details heading. Owner visual acceptance remains open.

The read-only live register still has 68 open historical `blank_description`
issues. A dry-run reports 8 additional unmaterialized candidates: 3
`external_link_review`, 1 `changed_file`, 2 `invalid_identity_encoding`, and 2
`unmatched_active_identity`. Under D-033, the `changed_file` result is hash drift
and a source-revision signal; semantic costing/material comparison has not yet
shown whether it is a business issue. No candidate was materialized or resolved. The
validated v3 schema diff remains unapplied: it adds one `DirectoryProductMapping`
table and extends `ProductModel` and `ReconciliationIssue`, with no type changes.
Governed Grist writes fail closed until the v3 migration is explicitly approved
and applied. No source ODS or `Costing-New` document was written.

Milestone 0.5 is **implemented but not complete**: each pilot exception still
needs a durable owner and a reasoned open/in-review, deferred, or audited
resolved state. The HF-MS semantic comparison must determine whether a Grist
change set or unresolved issue exists. Any resulting Grist sync and the two
identity cleanups need their separate explicit approvals before apply.

## Current gate and next milestone

The current gate is approval and guarded application of the exact v3 schema
diff, followed by a read-only semantic comparison of the HF-MS workbook.
Materialize only confirmed unresolved issues; preview any ODS-authoritative
change set for Safari Manufacturing separately. The two corrupted Product
Models also need explicit cleanup approval. These are live mutations and were
not part of this implementation run. Milestone 0.6 (shared CLI
material-mapping services) and 0.7 (S1KHF read-only costing ingestion) begin
only after the Milestone 0.5 reconciliation exit gate is satisfied.

Milestones 0.6 (shared CLI material-mapping services) and 0.7 (S1KHF read-only costing ingestion) begin only after the reconciliation exit gate is satisfied.
