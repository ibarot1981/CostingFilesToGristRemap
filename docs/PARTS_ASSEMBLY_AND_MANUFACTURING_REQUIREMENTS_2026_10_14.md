# Parts manufacturing visibility and nested assemblies

Owner-approved scope: 14 October 2026. Task: `parts-assembly-manufacturing-20261014`.

The owner requests an always-visible Canonical Part column in line-level mapping, a useful view of the MCL, Tool Shop and CNC entries composing a Part, and creation/browsing of nested assemblies. Assembly assignment to workbooks, products, models or Model Codes and any replacement/propagation workflow are expressly deferred. Purchased/store Parts may eventually be assembly children; record that design requirement without implementing store/purchase workflows now.

An assembly is a managed canonical identity with an explicitly selected assembly classification and its own independent permanent number sequence. Use `SM-A-000001` onward for new assemblies and subassemblies; subassembly describes an assembly used as a child, not another identity type or sequence. Existing `SM-P-` identities keep their numbers and composition; do not reclassify or renumber them. Assembly classification must not impose mutually exclusive manufacturing/purchase composition types. A draft assembly may initially be empty, then receive existing Parts or assemblies with explicit quantities. Naming scope and intended sharing retain existing advisory behavior.

## Immutable acceptance criteria

### c1
Every displayed source-line table in Part Mapping shows a Canonical Part column regardless of selection/assignment mode. Each row truthfully identifies its own saved Part (permanent number and current name) or unassigned/review state; do not borrow a neighboring/group assignment or hide the column when selection is cleared. Maintain existing draft-versus-saved indicators, one-Part-per-line rules, selection controls and horizontal-scroll usability.

### c2
From a selected Part in Parts, a clear Manufacturing baseline action/section exposes its actual accepted MCL, Tool Shop and CNC requirements, grouped and labelled understandably, including available descriptions, quantities/UOM, material, dimensions/weight and workbook/sheet/row provenance. Preserve Tool Shop Remarks and CNC Plate Part to Cut with blank/missing distinctions. Current mapped evidence, if displayed, is explicitly distinguished from accepted baseline requirements; mappings alone must never be represented as an established baseline. Handle unestablished, empty, incomplete/unavailable and legacy evidence truthfully. Merely viewing must perform no business writes or baseline changes.

### c3
Parts offers explicit assembly creation with existing scope/name/variant/reason validation, Rev A and stable identity rules. New assemblies and subassemblies receive durable, unique, independent SM-A numbers; ordinary Parts retain the SM-P sequence unchanged. Allocation, retries, cross-kind request-key conflicts, interrupted publication, restart and local-journal recovery preserve reserved identities and cannot duplicate/reuse numbers or advance the other kind's sequence. Preserve old request payload/recovery compatibility, aliases, retired numbers, existing naming uniqueness and writer-coordinator protections; never use record counts or unchecked max-plus-one allocation.

### c4
A draft assembly can add existing managed Parts and assemblies as children with explicit positive finite quantities per parent and units, using durable references to child identity and engineering revision. Assemblies can contain subassemblies at multiple levels and share children. Reject self-reference and indirect cycles server-side. Respect existing draft/finalized physical-definition locks and finalized-child requirements for parent finalization; do not silently finalize, revise, reconfigure or change a child's definition. Saving or retrying component additions does not duplicate or lose composition.

### c5
Assemblies are identifiable and clickable in the Parts explorer. Opening one shows immediate children with permanent number, current name, pinned revision and quantity/UOM, and distinguishes component Parts from subassemblies. Clicking a subassembly allows continuing through at least three assembly levels and into a leaf Part, with a clear return path preserving parent context and explorer state. Handle empty, unavailable and invalid legacy relationships without infinite traversal or misleading empty-success states. Current child metadata updates continue to resolve by stable identity without altering pinned definitions.

### c6
No new assembly assignment, automatic replacement, product/model configuration or costing workflow is introduced. New SM-A assemblies are not offered by existing mapping/configuration assignment controls and cannot be assigned through their write endpoints while that workflow is deferred; existing assignments and legacy SM-P compositions continue to work. Creation/composition/browsing writes do not alter workbook mappings, existing configurations, live costs, snapshots, accepted manufacturing baselines or existing Part numbers. Record future purchased/store-child support and assembly assignment as deferred requirements, with no store import, vendor/purchase expansion, automatic baseline establishment or CR implementation.

### c7
New business data, assembly classification, sequence reservations and component links persist through the authoritative Grist path and existing recovery model, including reload/restart; SQLite remains a coordinator/journal rather than a business fallback. Any necessary schema changes are additive, versioned and tested in isolation, with migration/read-compatibility evidence for existing records and pending create requests. Prepare migration instructions but apply no production schema/business writes, merges or deployment. Preserve every preexisting workspace edit and all completed task history.

### c8
Submit a finished exact commit on the designated new PR, with independently reproducible evidence for every criterion and all required checks. Include meaningful regressions for unconditional line visibility, manufacturing evidence display, mixed part/assembly allocation and recovery, nested composition/cycle/lock enforcement, persistence and assignment deferral. Exercise actual browser flows with isolated fixtures and explicitly label mocked versus real persistence evidence. Report genuine limitations/unverified checks; do not claim production persistence or migration was exercised.

## Mandatory checks

- `python-suite`: full repository Python suite using the project virtual environment; identify any existing platform skip.
- `ui-suite`: full UI suite, including standalone model tests.
- `ui-build`: TypeScript checking and production Vite build.
- `assembly-invariants`: focused service/API regressions for numbering, concurrent allocation, retries/recovery (including old pending requests), nested components, invalid quantities/cycles, finalized locks and deferred assignment guards.
- `isolated-grist-persistence`: isolated nonproduction Grist create/read-back/restart/recovery and schema compatibility for Parts, assemblies and components. If no isolated Grist service is available, this check stays unverified and blocks acceptance; never substitute mocked evidence or use production.
- `isolated-browser-flows`: browser verification of always-visible per-line assignments, manufacturing evidence/empty states, Part and assembly creation, nested drill-down/back navigation, reload and deferred assignment visibility.

## Implementation and review boundaries

The paired Luna chat alone implements application source. Default maximum five rounds. Claim exact task/round, retain token, create a concrete phase goal without a token budget, checkpoint meaningful progress, and persist through exact-SHA helper publication. After `awaiting_review`, stop application edits and HEAD movement. Sol independently reviews every criterion/check against the submitted immutable commit and publishes acceptance or bounded corrections. A report is not acceptance; acceptance is not merge/deploy/production-write permission.

Do not overwrite the existing modified/untracked design documents. This dated requirement supersedes prior global-numbering wording only for newly created assemblies' separate sequence; preserve old identities and historical documents. Scope changes require the owner, not a correction-round invention.
