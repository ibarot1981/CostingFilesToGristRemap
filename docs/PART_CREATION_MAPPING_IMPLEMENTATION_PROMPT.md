# Implementation prompt: Part creation first, then Part mapping

Implement the accepted Part creation and mapping requirements in `D:\Irshad\Dev\Python\CostingFilesToGristRemap`. This is an implementation request. Complete and verify Part creation first; then adapt Part mapping. These prerequisites take priority over later typed imports, per-code Summary configuration, processing completion, performance, CR management and spares work. Continue the existing Safari costing workflow rather than starting another implementation project.

## Inspect and preserve the current baseline

Inspect git status, branch, recent commits, remotes and applicable AGENTS.md instructions. Preserve any uncommitted work before editing. Read:

- `docs/PART_IDENTITY_REQUIREMENTS.md` — PART-002 and PART-003, including acceptance cases.
- `docs/DECISION_LOG.md` — D065, D066 and D067, preserving historical decisions.
- `docs/WORKFLOW_IMPLEMENTATION.md`, `docs/IMPLEMENTATION_ROADMAP.md`, `docs/MILESTONE_NUMBERING_AUDIT.md`, `docs/MANUFACTURING_ERP_REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/API_SCHEMA.md`, `docs/data-model.html` and `docs/requirements-status.html`.
- Existing Part mapping, normalized import/store/projection, processing, repository, Grist schema/client, API and UI code/tests.

The current Part screen is a prototype. Its canonical PartKey is derived from the name, which does not satisfy permanent identity. Existing PartRevision storage is numeric. The previously verified pilot had only a temporary unallocated Part and no reviewed Part assignments; recheck actual current data before planning migration. Existing normalized pilot records and source history must survive. Do not assume names or row positions are reliable business identity.

## Accepted business rules

1. Every managed Part, assembly and subassembly has a permanent internal identity and unique Part number, independent of name, scope, material, Model Code and engineering revision.
2. Number format: `SM-P-000001`, centrally allocated across Safari Manufacturing. Numbers are never reused, including after retirement. Gaps are acceptable. Do not allocate from spreadsheet rows, Grist IDs, counts or unchecked maximum-plus-one.
3. All managed Parts start and remain at **Rev A until the CR flow is implemented**. A later Part engineering revision means the approved CR process was followed. Do not implement informal Rev B+ editing or automatically revise Parts during mapping, refresh or imports. Enforce this server-side across every write path; UI restrictions alone are insufficient.
4. Keep name/scope metadata versions, mapping/source/line versions and Part engineering revisions distinct. Audited name/scope changes preserve Rev A if the physical design is unchanged and do not imply CR approval. Defer controlled physical changes to existing Part definitions until the CR flow exists. Independently distinct new Parts receive new numbers at Rev A.
5. Sharing Scope has four levels: Global, Product, Product Model and Model Code. It derives naming and advisory warnings only. It must not restrict configuring any Model Code with a Part. Actual configuration references record usage; applicability must not become a restrictive whitelist.
6. Generate names automatically from maintained scope shortcodes, the description entered by the user and an optional meaningful design variant: `<shortcode> — <description> [— <variant>]`. Users enter Chassis, for example, rather than the complete prefixed name. Preview before Save. Missing/ambiguous master shortcodes require resolution; do not silently guess them.
7. Preserve global normalized-name uniqueness using Unicode NFKC, collapsed whitespace and case folding. On collision, offer the existing Part or ask for a meaningful distinction; never silently append a random or sequential suffix. A reviewed temporary Design 01 designation is allowed when the actual variant description is pending.
8. Several distinct shared designs can have the same Model scope and base description. Illustrative example: `S1KHF — Chassis — Standard` and `S1KHF — Chassis — Reinforced`, each with its own permanent number and different subsets of codes. Do not list Model Codes in the name. The descriptions/counts in examples are not confirmed actual designs.
9. A Drum shared by approximately 90% of Safari 1000 codes can use Product scope while only those actual configurations reference it. A broad scope never automatically assigns all descendant codes.
10. Using a Part outside its scope warns and remains saveable. Do not auto-rename or auto-broaden scope. Offer a separate reviewed scope/name change with before/after preview, affected-use evidence, actor, reason and date. Preserve identity/number, aliases, engineering revision and existing references. Historical records retain the names and revisions used at the time.

## Slice 1: implement Part creation and identity management

Design and implement the minimum typed master, shortcode, metadata history/alias and number-allocation storage needed for these rules. Record actual schema/API decisions. Use stable references to scope targets; validate Product/Model/Code relationships. Keep physical engineering definitions separate from metadata.

The allocator must be durable, atomic and unique across supported concurrent application instances, and recover after restart or response loss. Grist's current schema API does not declare unique constraints; a process-local lock alone is insufficient. Choose a storage/allocator mechanism that actually guarantees allocation under the deployment model. Reservations keyed by creation request must survive partial failures and retries. Do not allow the same key to create a second Part or changed payload. Add ignored local configuration/backup instructions if a separate durable allocator is needed; do not commit credentials or generated databases.

Provide a Part register/search and a clear creation form: scope, scope target, description, design variant, generated-name preview and reason. Show permanent number and Rev A on the saved Part. Prefer server-derived names and revision values; reject conflicting client assertions. Show duplicates and errors clearly. Creation does not assign source rows.

Provide the audited metadata change process needed for a Part's scope/name evolution, old-name search and collision checks. Scope changes preserve number and Rev A. Prevent aliases from ambiguously resolving to another Part. Keep retired identities/numbers searchable for history.

Prepare a compatibility/migration plan for existing name-derived Part keys and numeric revision fields. Preserve all current references and history; do not silently recreate Parts or claim legacy numeric values prove CR approval. Report the exact migration diff before any live application and independently verify the Safari target. Apply only necessary verified changes within the authorized workflow.

Verify and report this slice before proceeding with mapping. Part creation is the first deliverable.

## Slice 2: adapt Part mapping to the new foundation

Replace the prototype's free-name creation with selection from the new Part register or the new creation process. Show number, generated name, scope, variant and Rev A together. Search using current names, previous aliases and numbers. Different descriptions may explicitly select the same Part; never auto-merge by similarity. Creating a Part must remain separate from saving assignments.

Keep exact-description groups for active MCL, Tool Shop and CNC rows, and show every active blank-description row individually. Each requires an explicit assignment before completion. Store Issue rows do not require a Part. Preserve historical/inactive source evidence. Paint/Packing and Summary-to-Code configuration remain subsequent work; do not claim they are implemented by this slice.

Save stable Part identity/reference and the pinned Rev A, with workbook hash, association/version, mapping version, sheet/row/description, actor, reason, time and retry evidence. Show assignments and immutable history; partial saves leave unassigned groups unresolved. Reject stale source/association/version tokens and conflicting/incomplete batches. Retain retry payloads after response loss. Ignore late UI responses after file changes; do not silently carry choices into a newer source review.

Assess selected-file Model Codes against advisory scope. Show affected out-of-scope codes without blocking mapping/configuration solely because of scope. Do not auto-populate actual per-code BOM configuration from a file assignment or broad scope.

Preserve processed-file reopening rules and separation from costing authority. Current valid assignments may reduce outstanding Part review evidence; they must not silently rewrite accepted line masters, create engineering revisions, approve CRs or mark files processed.

## Validation and boundaries

Use meaningful tests for allocation concurrency/restart/response-loss, duplicate names/aliases, all four name scopes, shared variants within one Model, scope expansion with stable references/history, advisory out-of-scope Save, Rev A enforcement, individual blank rows, stale evidence and safe mapping retries. Exercise actual browser creation then mapping in isolated test data, including a visible warning and successful out-of-scope Save. Verify the normal configured screen read-only; do not invent live canonical Parts or assignments just for a walkthrough.

Run appropriate Python/UI checks, TypeScript and production build. Use the bundled compatible Node runtime if the default Node cannot run the installed Vitest. Update requirements, decisions, roadmap, architecture, API/schema notes, interactive model/status and workflow progress honestly after each slice.

Keep source ODS files unchanged, refresh only disposable copies, and keep Costing-New read-only. Verify exact Safari Manufacturing document/workspace before live writes. Preserve deterministic keys, source provenance, immutable history and existing uncommitted work. Do not implement later CR states/approval workflows merely to unlock revisions; Rev A remains fixed in this work. Do not infer costing authority from mapping or processing status.

Report completed behavior, tests/browser evidence, migration/schema/data changes, unresolved shortcode/design decisions and the next open gate. Do not label Part creation/mapping complete while number uniqueness, naming history, advisory scope behavior or the Rev A guard remains unresolved.
