# Prompt for a new Luna Extra High implementation session

Copy the text below into a new Codex task and select **Luna / Extra High** reasoning. Open the existing repository as the project.

---

You are implementing the first usable milestone of the Safari Manufacturing ERP frontend in the existing repository:

`D:\Irshad\Dev\Python\CostingFilesToGristRemap`

Work autonomously through implementation and verification. Do not merely propose a plan. Preserve the existing CLI and all unrelated user changes. Begin by reading these files completely:

- `docs/MANUFACTURING_ERP_REQUIREMENTS.md`
- `docs/IMPLEMENTATION_ROADMAP.md`
- `docs/DECISION_LOG.md`
- `docs/requirements-status.html`
- `README.md`
- `ui/README.md`
- existing `app`, `config`, `scripts`, `tests`, and `ui` source relevant to this milestone

Inspect `git status` before editing. Existing dirty or untracked files may be user work; do not discard, reset, overwrite, or commit unrelated changes. Use `apply_patch` for hand edits. Use `rg`/`rg --files` for discovery.

## Milestone objective

Deliver the first usable Phase 0 vertical slice, including Milestones 0.2–0.4: guarded **creation and schema bootstrap of the Safari Manufacturing Grist document**, canonical identity foundation, and a safe, testable **Costing Explorer and File Association** workflow. The user must be able to browse the configured Product Costing filesystem as a real tree, inspect an ODS workbook, choose its Product and Product Model, choose one or more eligible Product Model Codes, preview the proposed change, and save the association to the Safari Manufacturing repository.

This milestone must build on the current read-only UI/API work rather than replace the repository wholesale.

## Authoritative business rules

1. The costing root is configurable and defaults to:
   `C:\Irshad\Safari\DRWGD\Products Costing`
2. Product, Product Model Number, and Product Model Code names come from:
   `Product-ProductModelNo-ModelCode.ods`
3. A costing file belongs to exactly one Product Model.
4. A Product Model can have multiple costing files.
5. A Product Model Code can have only one active costing-file association.
6. Therefore files for the same model may own disjoint active code sets.
7. An association stores path, normalized path, file identity/hash, Product, Model, selected Codes, actor, reason, timestamps, and source provenance.
8. Reassignment supersedes history; it never deletes the old association.
9. Bush variants are legacy-spares-only and cannot be selected as active selling configurations.
10. Duplicate GC source codes map to the approved persisted values `GCMC-7.5`, `GCMC-10`, `GCMC18-7.5`, and `GCMC18-10`. Preserve original values as source provenance/aliases.
11. Preserve catalog display text. Do not silently rewrite `S1KHFLEP`/`S1KHFELP` or infer a universal Model Code grammar.
12. All workbook operations in this milestone are read-only.

## Data safety—non-negotiable

- Do not modify, rename, move, recalculate, or save any source ODS file.
- Do not write to the existing `Costing-New` Grist document.
- Use distinct settings, including `SAFARI_MANUFACTURING_GRIST_DOC_ID` and a workspace selector such as `SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID`, rather than reusing the legacy `GRIST_DOC_ID` implicitly.
- Before any Grist mutation, validate that the target is explicitly configured as Safari Manufacturing and is not the legacy document ID.
- Default schema and data commands to dry-run.
- Creating the new document is an authorized deliverable. Use the authenticated Grist API to discover organizations/workspaces and create a document named exactly `Safari Manufacturing` in the explicitly selected writable workspace.
- Make document creation idempotent. Search the selected workspace first. If exactly one document with the exact name exists, validate it and offer reuse; if multiple matches exist, stop and report them; never create a duplicate or select one silently.
- If discovery returns more than one writable workspace, require explicit selection through configuration or a clear interactive prompt; never guess.
- Record the returned document ID only in ignored local configuration or clearly instruct the user how to set it. Do not commit API keys, document IDs tied to a private deployment, or generated secrets.
- If credentials lack document-creation permission, still complete and test the command and schema plan using fakes, then report the exact permission blocker. Do not substitute the legacy document.
- Never expose API keys to the browser or logs.
- Conflicts return a structured proposed resolution; never apply last-write-wins.

## Backend/domain deliverables

### 1. Safari Manufacturing document creation

Extend the Grist client or add a dedicated administration adapter that can:

- list accessible organizations and writable workspaces;
- list documents in the selected workspace;
- create a document named exactly `Safari Manufacturing`;
- retrieve enough document metadata to validate name, workspace, and document ID;
- distinguish permission, connectivity, duplicate-name, and validation errors.

Provide a clearly named CLI/setup command with a dry-run/plan stage and an explicit apply stage. The apply command must show the base URL, organization, workspace, intended document name, and legacy document ID guard before creation. It must be safe to retry after an uncertain response by re-listing exact-name matches before attempting another create.

After creation or validated reuse, run the schema plan and explicitly apply the reviewed foundation schema to that document. Normal API startup must never create documents or tables.

### 2. Storage-neutral domain model

Create or refine typed models and repository interfaces for at least:

- Product
- ProductModel
- ProductModelCode
- IdentityAlias
- CostingFile
- FileObservation
- FileModelAssociation
- FileCodeAssociation
- ImportBatch
- ReconciliationIssue
- AuditEvent

Keep Grist table/column identifiers inside the Grist adapter. The API and frontend consume domain DTOs.

### 3. Repositories

Provide:

- an in-memory implementation for automated tests and local development;
- a Grist implementation for Safari Manufacturing;
- a versioned Grist schema manifest/plan for the above foundation tables;
- an idempotent schema-diff command with dry-run as the default;
- an explicitly named apply option protected by document-identity validation.

Do not automatically create or change tables on normal API startup.

### 4. Canonical catalog service

Implement an importer/reader for the supplied catalog ODS. Preserve source values and row provenance. Detect duplicates and blank descriptions. It is acceptable in this milestone to serve identities from an imported repository seed or a read-through service, but the design must support the Milestone 0.3 audited import without changing frontend contracts.

### 5. Filesystem catalog service

Implement recursive, lazy-load-friendly directory endpoints. Return stable relative IDs and include:

- name, type, relative path, extension, size, modified timestamp;
- candidate classification;
- mapped/unmapped/conflict status;
- readable/encrypted/parse-error state where known;
- sheet count and external-link warning after inspection;
- content hash computed on demand or by background inspection, not for every node on every tree request.

Confine all resolved paths to the configured root. Defend against `..`, absolute-path injection, symlinks/junctions/reparse points escaping the root, encoded separators, case differences, and unsupported extensions.

### 6. Workbook preview

For a selected ODS file, expose:

- sheet list and dimensions;
- a bounded cell window with formula/value distinction when available;
- external-reference count/warnings;
- parse/encryption errors as typed responses;
- pagination or row/column limits to prevent loading an entire workbook into the browser.

Never use LibreOffice in a way that saves/recalculates the source.

### 7. Association workflow

Provide endpoints to:

- retrieve Products;
- retrieve Models for a Product;
- retrieve active, available, legacy, and conflict Codes for a Model;
- validate a proposed file/model/code association;
- save a valid proposal with optimistic concurrency/idempotency;
- retrieve current association and history;
- list/filter mapped and unmapped files.

Validation must catch:

- code already owned by another active file;
- file assigned to a different model;
- model not belonging to Product;
- code not belonging to Model;
- duplicate/unresolved catalog code;
- changed file since preview;
- missing/unreadable file;
- legacy-spares-only selection.

A successful save creates an audit event and a processing-queue/import-batch record. Queue execution may remain a safe stub with visible status if full processing is outside this milestone.

Use proper HTTP status codes plus stable machine-readable error codes.

## Frontend deliverables

Retain the Seey application feel but use Safari Manufacturing’s distinct navy/copper/warm-sand palette. Build a responsive three-pane workbench:

1. **Explorer:** collapsible directory tree, breadcrumb, search, mapped/status/type filters, keyboard selection, badges, loading/error/empty states.
2. **Association workbench:** selected file metadata; cascading Product → Model → Code controls; multi-select codes; existing owner/conflict information; optional reason; Validate and Save actions; proposed-change summary.
3. **Workbook preview:** sheet tabs/select, bounded grid, dimensions, formula and external-link warnings, and clear read-only label.

Add a **Mapped Files** route/view with sortable/filterable rows grouped by Product/Model, owned codes, path, mapping status, last observed change, and a details/history drawer. It may defer the full supersede workflow, but must accurately display conflicts and history returned by the API.

Disable Save until validation succeeds. Make destructive implications explicit when a future supersede is proposed. Provide accessible labels, focus states, useful keyboard behavior, and no color-only status communication.

Do not put mock business data directly in components. Local development data must come through the repository/API boundary and be visibly marked when using the in-memory adapter.

## Existing CLI reuse

Study the CLI material and Grist mapping code, especially `config/material_mapping.yaml`, verification logic, Grist access, Product Part/MS/Tool Shop/CNC mappings, aliases, alternate-size normalization, and option scoping. Do not rewrite those rules into the UI in this milestone. Identify the shared-service extraction seam and make only safe refactors needed by this vertical slice. Existing CLI behavior and commands must continue to work.

## Tests

Add focused automated tests for:

- root confinement and traversal variants;
- reparse/symlink escape where platform support permits;
- ODS-only preview and bounded results;
- unreadable/encrypted/corrupt ODS response;
- catalog hierarchy and duplicate detection;
- file-to-model and code-to-file cardinality;
- invalid cross-product/model/code combinations;
- optimistic concurrency and idempotent retry;
- changed file between validation and save;
- history preservation on supersede service logic;
- legacy document write guard;
- Grist workspace discovery, exact-name lookup, idempotent create/reuse, duplicate-name refusal, permission errors, and uncertain-response retry behavior;
- schema bootstrap targeting only the returned/validated Safari Manufacturing document ID;
- API contracts and error codes;
- representative frontend component/workflow behavior;
- regression coverage for any touched CLI service.

Use small synthetic fixtures in the repository. Real business ODS files remain external and must not be committed.

## Documentation and traceability

Before finishing, update all affected documentation:

- `docs/MANUFACTURING_ERP_REQUIREMENTS.md` for any approved requirement change;
- `docs/DECISION_LOG.md` for any new or superseded decision;
- `docs/IMPLEMENTATION_ROADMAP.md` with actual milestone evidence/limitations;
- `docs/requirements-status.html` so every affected requirement has accurate status, implementation reference, test evidence, and last-updated date;
- root and UI READMEs with exact setup/run/test commands and required environment variables;
- API/schema documentation and a concise developer architecture note if not already present.

Do not mark a requirement Implemented merely because a screen exists. Use Implemented only when behavior and verification are complete; otherwise use Partial, Documented, Planned, or Blocked.

## Required verification and handoff

Run the relevant Python and frontend tests, type checks, lint/build, and a manual smoke test. If dependencies are missing, install only normal project dependencies and document the command. Do not weaken tests to pass.

At the end, report:

- the user-visible outcome;
- files changed and key architecture decisions;
- exact commands and results;
- which repository adapter was exercised;
- the created or validated Safari Manufacturing document name/workspace and whether its schema was applied; do not print secrets;
- confirmation that no source ODS or legacy Grist writes occurred;
- remaining limitations and the next milestone gate;
- any external permission or ambiguous-workspace condition that blocked document creation or schema apply.

Do not create a Git commit unless asked. Leave the worktree reviewable and show `git diff --check` plus final `git status --short`.

---
