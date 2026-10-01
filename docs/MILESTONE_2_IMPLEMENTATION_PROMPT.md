# Prompt for the next Luna Extra High implementation session

Copy the text below into a new Codex task, select **Luna / Extra High**, and open the existing repository project.

---

You are implementing the next Safari Manufacturing milestone in:

`D:\Irshad\Dev\Python\CostingFilesToGristRemap`

PR 1 is merged. This task delivers **Phase 0 Milestone 0.5: durable reconciliation and mapping governance**, but it must first close the post-merge Milestone 1 gaps in the exact order below.

Work through implementation and verification, not only planning. Begin by reading these files completely:

- `docs/MILESTONE_1_POST_MERGE_AUDIT.md`
- `docs/MANUFACTURING_ERP_REQUIREMENTS.md`
- `docs/IMPLEMENTATION_ROADMAP.md`
- `docs/DECISION_LOG.md`
- `docs/ARCHITECTURE.md`
- `docs/API_SCHEMA.md`
- `docs/requirements-status.html`
- `docs/MILESTONE_1_IMPLEMENTATION_PROMPT.md`
- relevant backend, frontend, schema, test, and README files

Inspect `git status`, current branch, and recent history first. Preserve unrelated work and never reset or overwrite user changes. Use `apply_patch` for hand edits and `rg`/`rg --files` for discovery. Do not create a commit or PR unless the user asks.

## Non-negotiable safety boundary

- Product costing ODS files remain read-only. Do not save, rename, move, recalculate, or modify them.
- Do not write to `Costing-New` or use its document ID as a Safari target.
- All live Grist changes target only the validated `Safari Manufacturing` document in the explicitly selected Work workspace.
- Default every scan, cleanup, schema migration, and reconciliation action to plan/dry-run.
- Irshad is the sole Phase 0 approver. A live resolution, source-revision acceptance, directory approval, or identity deactivation requires a clear preview and Irshad's explicit confirmation in this task.
- Never delete history. Supersede/deactivate records and append observations/audit events.
- Never create a fake live conflict merely to exercise the UI. Use synthetic/in-memory fixtures or an isolated test document.
- Never print or commit API keys, private deployment configuration, or document IDs.

## Priority 0 — Close Milestone 1 audit gaps first

Do not begin the general Milestone 0.5 feature work until these gaps have working code, tests, and an accurate status.

### Gap A — Changed HF-MS Drum source

The post-merge audit found that the current file:

`S1KHF/Local/HF-MS Drum/Safari 1000 HF Local V 4.2 - MS Drum.ods`

does not match its stored live FileObservation hash. Existing code already derives `changed_file`, but there is no durable issue or resolution action.

Implement:

1. A read-only comparison that records stored and current path, size, mtime, SHA-256, workbook readability, sheet count, and external-reference count.
2. An idempotent, fingerprinted `changed_file` ReconciliationIssue. Repeated detection updates `LastSeenAt` without duplicating open issues.
3. UI actions:
   - **Accept new source revision**;
   - **Defer** with reason;
   - **Keep open / needs investigation**;
   - **Reopen** a resolved/deferred issue.
4. Accepting a revision must append a new immutable FileObservation, retain the old observation, update the CostingFile current fingerprint only after approval, preserve the file/model/code association, and create an AuditEvent identifying the old and new hashes.
5. Revalidate the existing Model Code ownership and current file hash at apply time. Fail closed if the file changes again between preview and apply.
6. Show the changed revision and full observation history in Mapped Files.

Do not accept the live revision automatically. Generate and verify the plan, then ask Irshad for explicit approval before applying it.

### Gap B — Encoding-corrupted Safari SS models

The canonical catalog ODS contains `Safari – SSV` and `Safari – SSM` with an en dash. Live Safari Manufacturing also contains two active, unreferenced corrupted rows: `Safari � SSV` and `Safari � SSM`. The valid canonical rows already own their codes.

Implement:

1. Import validation that detects Unicode replacement characters and other invalid identity text. It must fail closed or emit an error-level reconciliation issue; it must never normalize the corrupted value into the canonical identity silently.
2. A dry-run identity cleanup plan that proves, immediately before apply, that each corrupted model owns no active Model Codes, file associations, directory mappings, or other governed references.
3. A reviewed action that marks the corrupted model inactive/superseded and records the canonical replacement ID. Do not delete the record.
4. An AuditEvent and resolved ReconciliationIssue carrying actor, reason, source evidence, previous value, and canonical replacement.
5. Regression tests using the real characters `–` and `�`, including UTF-8 console/report output.

Do not apply the live cleanup until Irshad sees and explicitly approves the exact two-row plan.

### Gap C — Storage-neutral repository contract

Expand `SafariRepository` so the API depends on a real protocol/abstract contract rather than the concrete `InMemorySafariRepository`. Include identity, file registration/observation, validation/save, association detail/history, mapped files, reconciliation issue lifecycle, audit events, directory mappings, and processing queue operations. Add contract tests that run against the in-memory implementation and a fake-client Grist implementation.

### Gap D — Stale evidence and acceptance

- Correct every document that still says all three source hashes match.
- Correct the test count to the currently verified count.
- Record an automated keyboard/accessibility smoke for new controls and perform responsive browser checks at a normal desktop width and a narrow width.
- Leave owner visual acceptance as a named checklist item if Irshad has not explicitly accepted it.

## Milestone 0.5 — Durable reconciliation and mapping governance

### 1. Reconciliation domain and schema

Version the Grist schema manifest and add the minimum durable fields/tables required. Prefer extending `ReconciliationIssue` and using `AuditEvent`; introduce new tables only when the domain genuinely requires them.

Each issue needs stable identity and lifecycle data, including:

- deterministic fingerprint;
- issue type and severity;
- entity type/entity ID and optional CostingFile reference;
- source path/row/cell evidence where applicable;
- structured detected facts and proposed resolution;
- status: `open`, `in_review`, `deferred`, `resolved`, or `reopened`;
- assigned owner;
- detected/first-seen and last-seen timestamps;
- version for optimistic concurrency;
- resolution action/reason;
- resolved/deferred/reopened actor and timestamp.

Rules:

- importing or scanning the same unresolved condition is idempotent;
- a disappeared derived condition is not silently resolved;
- resolving/deferring/reopening requires a reason and appends an AuditEvent;
- stale versions and changed facts return structured conflicts;
- an issue may reference immutable observations instead of copying unverifiable text;
- Grist cell/table names stay inside the adapter.

Provide a dry-run schema diff and guarded explicit apply. Revalidate document name, ID, and workspace immediately before mutation. Normal API startup must never migrate schema.

### 2. Issue detection and materialization

Materialize, at minimum:

- changed file;
- missing file;
- verified moved-file candidate;
- unreadable/parse error;
- external-link review warning;
- duplicate active Model Code ownership;
- catalog/model/code reference mismatch;
- invalid identity encoding;
- unmatched active identity rows left by an import.

Keep lightweight Mapped Files listing free of full-workbook hashes. A deliberate scan/inspect action may calculate hashes and materialize issues with progress/result reporting.

### 3. Reconciliation API

Add stable DTOs and endpoints for:

- list/filter/search issues by status, type, severity, Product, Model, file, and owner;
- issue details with evidence, observation history, association history, and audit trail;
- assign/unassign;
- defer, resolve, reopen;
- preview and apply accepted source revision;
- preview and apply identity cleanup;
- export the current filtered reconciliation register as CSV and JSON;
- run a bounded reconciliation scan and return its counts without resolving anything.

Use stable machine-readable error codes, optimistic version checks, idempotency keys for mutations, bounded pagination, and correct HTTP statuses.

### 4. Mapped Files and reconciliation UI

Turn the existing read-only discrepancy projection into a working review surface while retaining the navy/copper/warm-sand Seey feel.

Required UI:

- Reconciliation queue with status/type/severity/owner/Product/Model filters and search;
- clear distinction between a derived warning and a durable governed issue;
- issue details with source facts, prior/current hashes and observations where relevant;
- assign, defer, resolve, and reopen forms with required reasons;
- preview-before-apply for revision acceptance and identity deactivation;
- association history and audit timeline;
- guided supersede flow from a conflicting code/file, showing the current owner and exact history that will close;
- exported CSV/JSON report for the filtered view;
- loading, empty, permission, stale-conflict, and partial-failure states;
- keyboard navigation, focus management, labels, non-color-only status, and responsive behavior.

Exercise the conflict/supersede UX with a deterministic in-memory fixture. Do not alter the three valid live associations solely for demonstration.

### 5. Directory-to-Product proposal and approval

Add a versioned `DirectoryProductMapping` domain and persistence model:

- normalized relative directory path;
- selected Product;
- inheritance flag and clear precedence rules;
- status: proposed, approved, rejected, superseded;
- proposer, approver, reason, timestamps, and version;
- audit trail.

Rules:

- mappings remain proposals until approved by Irshad during Phase 0;
- a deeper approved path overrides an inherited parent mapping;
- no mapping may escape the configured root;
- rejecting/superseding preserves history;
- the Explorer displays proposed versus approved/inherited mappings and never silently assigns a Product to a file;
- directory mapping does not bypass explicit file-to-Model and code ownership validation.

### 6. Classification review

Sample representative real paths from costing candidates, archives, masters/templates, generated outputs, Export workbooks, unreadable packages, and support databases. Produce a review report without changing files. Refine narrowly supported classification rules and add regression fixtures; do not infer business meaning from filenames when workbook evidence is required.

## Tests

Add focused automated coverage for:

- issue fingerprint/idempotency and last-seen updates;
- lifecycle transitions, required reason, actor, audit, and optimistic version conflict;
- no silent resolution when a derived issue disappears;
- changed-file double-check between plan and apply;
- immutable observation history and accepted revision;
- invalid replacement-character identities and canonical en-dash preservation;
- cleanup refusal when any governed reference exists;
- inactivation/supersession without deletion;
- directory path safety, inheritance precedence, approval, rejection, supersession, and actor policy;
- duplicate active code conflict and guided supersede history;
- CSV/JSON export escaping and filtering;
- repository contract parity across memory and fake Grist adapters;
- schema diff/apply idempotency and legacy-target guard;
- API error/status contracts;
- component workflows, keyboard/focus behavior, and narrow-width rendering;
- regression of all existing CLI, backend, and frontend tests.

Use synthetic fixtures for mutation tests. Real ODS files stay external and read-only.

## Live verification sequence

1. Run the full existing tests before edits.
2. Implement and pass all tests with the in-memory/fake adapters.
3. Run a schema dry-run against the validated Safari document and present the exact diff.
4. Apply the schema only after explicit confirmation.
5. Run read-only reconciliation and show the exact HF-MS changed-file issue and two corrupted-model issues.
6. Ask Irshad separately before accepting the HF-MS revision or deactivating the two corrupted rows.
7. After any approved action, re-read Grist and the filesystem to prove:
   - prior observations/history remain;
   - association/code cardinality remains valid;
   - audit and issue state are correct;
   - source ODS hashes did not change because of the app;
   - no legacy document records changed.
8. Run browser smoke tests against memory first, then read-only live data. Use a live mutation only for an explicitly approved business resolution.

## Documentation and completion

Update in the same change:

- `docs/MILESTONE_1_POST_MERGE_AUDIT.md` with closure evidence;
- `docs/IMPLEMENTATION_ROADMAP.md` with Milestone 0.5 evidence and exit status;
- `docs/MANUFACTURING_ERP_REQUIREMENTS.md` if a requirement changes;
- `docs/DECISION_LOG.md` for new/superseded decisions;
- `docs/ARCHITECTURE.md` and `docs/API_SCHEMA.md`;
- `docs/requirements-status.html` with accurate statuses and test evidence;
- root/UI READMEs with commands and workflow instructions.

Do not mark Milestone 0.5 complete until every pilot exception has an owner and either an open/in-review reason, a deferral reason, or an audited resolution. Do not begin Milestone 0.6 material-mapping extraction in this task except for a narrowly necessary compatibility refactor.

At handoff, report:

- planned versus delivered behavior;
- schema/API changes;
- exact tests/build/smoke commands and results;
- issue counts by status/type before and after approved actions;
- whether the HF-MS revision and corrupted identity cleanup were only planned or explicitly applied;
- evidence that ODS and `Costing-New` were untouched;
- remaining owner decisions and the next gate.

Show `git diff --check` and final `git status --short`. Leave the worktree reviewable and do not commit unless asked.

---
