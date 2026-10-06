# Prompt for the next implementation chat — Delivery Milestone 3

Copy the prompt below into a new chat in this project and select **Luna / Extra
High**, as requested for the implementation sessions. The roadmap's **Delivery
Milestone 3** implements
**Phase 1 work package 1.1**. Decimal `0.x`/`1.x` labels are phase work
packages, not competing delivery numbers.

---

Implement **Safari Manufacturing Delivery Milestone 3: normalized Product Part
and process-line ingestion for the selected S1KHF costing workbook** in:

`D:\Irshad\Dev\Python\CostingFilesToGristRemap`

This is an implementation task, not a request for another proposal. Work
through code, UI, storage, verification, and documentation. First inspect
`git status`, branch, recent commits, and the existing Milestone 2 work. The
checkout may contain uncommitted Milestone 2 and design changes. Preserve
them; do not reset, overwrite, or commit unrelated work. Establish a
reviewable baseline and report if Milestone 2 has not yet been merged.

Read these files before designing schema or editing code:

- `docs/MILESTONE_NUMBERING_AUDIT.md`
- `docs/IMPLEMENTATION_ROADMAP.md`, especially 0.5–0.7 and 1.1
- `docs/MANUFACTURING_ERP_REQUIREMENTS.md`, especially §§2, 3, 5, 6.2–6.4
- `docs/DECISION_LOG.md`, especially D-034, D-051–D-056
- `docs/ARCHITECTURE.md`, `docs/API_SCHEMA.md`
- `docs/data-model.html`, `docs/requirements-status.html`
- `docs/MILESTONE_2_IMPLEMENTATION_PROMPT.md`
- the relevant parser, CLI mapping, domain, repository, Grist schema, API,
  frontend, and test code

## Position and objective

Milestone 1 / PR 1 delivered the Phase 0 foundation and received a
conditional pass. The roadmap records the agreed Milestone 2 S1KHF semantic
snapshot/parity scope as complete. Phase 0 exit gates remain open, especially
work-package 0.6 CLI mapping extraction and 0.7 full pilot review.

Milestone 3 is **Phase 1 work package 1.1**. Its outcome is a queryable,
normalized representation of one accepted S1KHF workbook revision in Safari
Manufacturing. The accepted Milestone 2 semantic JSON is the verification
baseline during migration. Do not treat a 667 KB JSON cell or chunked JSON
as the primary operational line store.

The complete 45-entity interactive ER page is a target design. Implement only
the Milestone 3 subset below. Keep repository interfaces storage neutral so a
future PostgreSQL adapter can replace Grist without changing the UI.

## Scope to implement

1. **Source evidence:** workbook/sheet/row observations pinned to source
   hash, parser version, exact sheet name, row/cell addresses, formulas,
   cached values, active/historical state, and external dependency evidence.
   Reuse existing `CostingSnapshot`/`FileObservation` where appropriate; avoid
   duplicating the full semantic JSON on every child row.
2. **Reusable identity:** `ProductPart`, immutable `PartRevision`, and
   `PartComponentRevision` only where the S1KHF workbook gives sufficient
   evidence of a parent/child relationship. Do not infer shared Part identity
   merely because two descriptions look similar. Permit a clearly labelled
   temporary unallocated S1KHF Part for lines that lack a confirmed owner.
3. **Stable process lines:** `LineMaster` and immutable `LineRevision`.
   Every active or historical MCL, Tool Shop, CNC, Stores, and
   Labour/Paint/Packing row supported by the configured pilot sheets must
   become a child record with a stable or explicitly unresolved identity.
4. **Typed details:** material-cut, Tool Shop, CNC, Store, Paint, and
   non-paint Labour/Packing detail tables or a carefully justified equivalent
   schema. Store material/item, dimensions, quantity, UOM, weight, process
   time, rates/cached cost, formulas, and source evidence in typed columns
   suitable for filtering. Separate physical requirement fields from the
   historical resolved rate/cost used for parity.
   The source has one `Labour - Paint - Packing` sheet: classify its rows
   through reviewed activity evidence, and keep an unclassified state where
   paint versus labour versus packing cannot be determined safely.
5. **Shared references:** canonical `Material`, `PurchaseItem`,
   `ProcessOperation`, and `WorkCenter` records needed for the pilot. Preserve
   exact ODS display names and reviewed aliases. A shared material does not
   imply that two usage lines or Product Parts are the same.
6. **Governed mapping and audit:** `SourceLineMapping` and row-level change
   items link each imported source observation to a Line Master and each new
   revision to its predecessor, actor, reason, and source evidence. Show
   ambiguous or unmatched proposals for review. CR references are evidence,
   not identity or automatic approval.
7. **Read-only inspection UI:** from the mapped S1KHF file and model code,
   users can inspect normalized rows by sheet/process/part/material/status,
   open a row to see master, revisions, source cells/formulas, and audit, and
   filter historical `In Use = No` records separately. Show mapping exceptions
   and normalized-versus-snapshot reconciliation counts/totals. The UI uses
   application APIs, never direct Grist requests.

Summary headings and additions/deductions remain source observations in this
milestone. The Costing Configuration builder and rule engine come later; do
not manufacture a definitive configuration from headings alone.

## Matching rules

- Keep the D-034 five-field MCL composite for exact matching within one
  workbook: Machine Piece Description, Material to Cut, Dimension to Cut
  (mm), Quantity Nos, and Optional Item Group 1.
- Apply D-051 for changed-key rows: pair a probable modification only when
  unique strong semantic evidence supports it; otherwise leave a reviewable
  ambiguity or an explicit add/remove. MCL ID and row position are never the
  stable identity.
- A moved ODS row with unchanged business meaning creates new source
  evidence but retains its Line Master and business revision.
- A quantity, material, dimensions, weight, formula, or process change
  creates a new immutable Line Revision under the same master when identity is
  established. A genuinely new requirement creates a new master and first
  revision. Removal retires/supersedes; do not delete history.
- A changed observed/approved rate by itself does not create a new physical
  Line Revision. Pin the actual rate/cost used in the source observation or
  calculation evidence for reproducibility.
- `In Use = No` lines are historical and excluded from current semantic
  totals; blank `In Use` follows existing Milestone 2 active rules.
- Preserve Tool Shop source department and Stores issue route separately.
  Do not double count an internally made item issued through Stores.

## CLI mapping carry-forward (Phase 0 package 0.6)

Extract only the material/alias/alternate-size/option-scope behavior needed
for the S1KHF mapping into a pure shared service called by both the existing
CLI and the new importer. Start with golden outputs from the current CLI.
The CLI command surface and selected comparison results must remain
equivalent. Do not copy its rules into a second implementation. Record what
part of package 0.6 remains outside this milestone.

## Implementation order

1. **Inventory and baseline:** inspect the selected S1KHF workbook, its
   accepted `CostingSnapshot`, configured sheets, real parser output, the
   existing Costing-New/CLI material mappings, and current Safari v4 schema.
   Report row counts, active/historical classifications, sheet coverage,
   unsupported columns, and ambiguities. Treat Costing-New as read-only
   mapping evidence, not a write target.
2. **Logical/physical schema plan:** specify tables, typed fields, Ref
   cardinalities, stable keys, revision uniqueness, indexes/search keys,
   source mapping uniqueness, statuses, and audit links. Update
   `docs/data-model.html` with actual/next/later scope. Produce a deterministic
   Safari-target schema diff and dry-run; normal API startup never migrates.
3. **Import plan:** parse the accepted snapshot/current source revision into
   proposed Part/Line/Material identities, typed rows, and mapping decisions.
   Show what will be created, reused, superseded, or left unresolved. Require
   explicit owner resolution for ambiguous identity. No silent name merge.
4. **Persistence:** implement memory and guarded Grist adapters through the
   same repository contract. Use deterministic keys, idempotent upserts or
   append-only revisions, optimistic source/baseline checks, staged status,
   and retry recovery because Grist writes across tables are not atomic.
5. **UI/API:** add the inspection, filters, provenance, revision/audit detail,
   and exception review surface. Keep the Seey-like Safari navy/copper feel.
6. **Reconciliation:** compare normalized source row counts and active totals
   against the accepted 667 KB semantic baseline and refreshed source ODS.
   Report every difference by sheet/line; do not hide it behind the ±₹100
   business tolerance. Preserve the documented cached-summary mismatch from
   historical `In Use = No` rows as a named reconciliation exception.
7. **Documentation/status:** update requirements, architecture, API schema,
   roadmap, decision log, HTML status register, interactive ER page, and
   README together with implementation. Keep numbering as Delivery Milestone
   3 / Phase 1 work package 1.1; report Phase 0 gates separately.

## Acceptance evidence

- Supported S1KHF rows are individual queryable Grist records with proper
  master/child references; the existing snapshot remains accessible.
- Every row has an exact source observation and mapping status. Every
  confirmed business line has one stable master and immutable revision
  history; ambiguous lines stay reviewable.
- A query finds all lines using one Material, all lines owned by one Part,
  all historical rows, and all changes to one Line Master.
- Active/historical counts reconcile by sheet. Supported line values and
  active totals reconcile to the Milestone 2 baseline; explain any difference
  in a report rather than adjusting the source.
- Repeat import is a no-op. Source row reorder without semantic change does
  not create a business revision. Changed quantity creates one revision.
  Interrupted multi-table writes resume or remain staged safely.
- Golden CLI mapping fixtures and the existing CLI still pass.
- Synthetic tests cover uncertain mapping, shared materials versus distinct
  lines, common Part reuse only after confirmation, typed process details,
  Store/Tool Shop double counting, Grist refs, audit, and wrong-document guard.
- Tests/build and a real read-only S1KHF browser walkthrough pass. Source ODS
  and Costing-New hashes/records remain unchanged by the implementation.

## Boundaries and handoff

Do not implement the full Costing Configuration builder, Manufacturing
Configuration, Production Plan/batch scheduling, stock reservation, Store
Issue Slip generation, spare configuration, direct Google Sheets access,
approved-rate promotion, or ODS write-back here. They remain visible in the
ER model as later scope.

Use only the validated Safari Manufacturing document for schema/data writes.
Show the exact live schema and import plans before any live apply. If a live
owner decision is needed for uncertain mapping or an irreversible migration,
complete code, fixtures, dry runs, and the reviewable plan first, then ask for
that specific decision. Do not claim live normalization complete if it was
only tested in memory. Preserve the current Milestone 2 accepted baseline.

At handoff, report planned versus actual scope, table/row counts, mappings
confirmed/unresolved, exact reconciliation differences, tests/build/browser
results, Safari live apply status, Phase 0 open gates, and final `git status`.
Leave the worktree reviewable; do not commit or create a PR unless requested.

---
