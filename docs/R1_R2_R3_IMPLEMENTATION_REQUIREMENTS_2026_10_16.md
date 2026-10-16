# R1–R3 approved implementation requirements — 16 October 2026

The owner approved R1, R2 and R3 in Reviewer - Safari Manufacturing and authorized a new PR and assignment to the fresh Implementor - Safari Manufacturing chat (Luna, existing Extra High settings). This document makes that approved scope reviewable; the running discussion is retained in PENDING_OWNER_REQUIREMENTS_2026_10_15.md.

Workflow task: `safari-r1-r2-r3-20261016`. One combined task, five implementation rounds maximum. Reviewer chat: `01a13f57-5890-7e60-813d-87ccdcfee496`. Implementor chat: `01a13f57-7fff-77d3-9456-2ebd4b0c1bc9`.

## R1 — CNC Part Mapping proportions

**c1**: In CNC Part Mapping, give Plate Part to Cut more usable width and narrow Qty Of Part Used, Per Part Weight (Kg) and Total Weight (Kgs). Names must be easier to read with less needless wrapping; numeric values and headers remain accessible. Apply consistently to the supported compact/expanded/split presentations. Preserve MCL and Tool Shop column widths. Preserve all saved mappings, source values and physical provenance. Exact pixel widths are an implementation choice, not an owner requirement.

## R2 — Product Model Code descriptions and management

**c2**: Wherever the application offers Product Model Code selection, show code plus description, retaining useful Product Model context. Show No description for blank values. Inventory all selection surfaces, including workbook association, intended sharing, Part scope/configuration and costing where applicable. Where a selector already supports search, include description alongside the existing code/Product Model matching. Preserve existing eligibility, ownership, legacy and conflict rules.

**c3**: Provide a central Product Model Code management screen under an existing Product and Product Model. Permit adding and editing descriptions on existing codes, persisting them in the canonical application repository and showing saved values after reload and in selectors. Existing blank descriptions remain usable until filled in. Editing a description must not change code identity, parent hierarchy, associations, Part definitions or costing history.

**c4**: Permit creating a Product Model Code under an existing Product Model with a required nonblank description, without requiring an ODS workbook. Validate the code using existing identity/uniqueness conventions, report errors clearly and make successful creations available in appropriate selectors under existing eligibility rules. Creation must not implicitly associate a workbook, assign Parts or configure costing. Product/Product Model creation, code renaming/deletion and bulk description backfill are outside this request.

## R3 — Manufacturing baseline review workspace

**c5**: Provide a prominent Review manufacturing baselines action in the main Part Mapping toolbar for the selected workbook. Open a dedicated full-width workspace, with searchable mapped-Part navigation showing per-Part baseline status. Preserve workbook context and unsaved mapping drafts when opening and returning. Review/publication remain explicit per Part; draft selections are not saved mappings.

**c6**: In the selected Part review, clearly show Part identity, workbook context, MCL/Tool Shop/CNC evidence, mapped counts, applicability/completeness confirmations, existing-content warnings and specific blockers. Keep Establish Part baseline visible with an understandable disabled reason, show actual publication success, update status and permit proceeding to another Part. No clipped or unreachable controls in the supported desktop and narrow layouts.

**c7**: Support comparison with an established baseline in the same spacious workspace, with accepted versus incoming values side by side where space permits and accessible stacked presentation when narrow. Provide an action from the empty Accepted Manufacturing baseline section in Parts into this workspace with Part and workbook context. Where more than one workbook is mapped, require explicit baseline source choice; do not silently choose the first file. Explain unavailable/no-workbook states.

**c8**: Preserve existing baseline completeness, stale evidence, unresolved/deleted-line review, append-existing-content, recovery/retry and immutable evidence safeguards. Establishment must not automatically finalize Rev A. Preserve explicit finalization and existing comparison decisions/Change Request boundaries. No automatic bulk establishment/finalization and no loss of pending mapping drafts during navigation.

## Preservation and review gate

**c9**: Deliver only approved R1–R3 changes on the designated new PR/branch, preserve every preexisting tracked/untracked edit and immutable workflow artifact, and publish a truthful exact-commit report covering c1–c9 and every mandatory check. Do not modify owner servers, production Grist/business data/schema, Costing-New, schedules or model/reasoning settings during implementation. No merge or deploy is authorized. Local migration code, if genuinely required, may be prepared/tested without applying production changes.

## Mandatory checks

- `python-suite`: full existing Python test suite plus meaningful backend/API regressions for new description editing/creation and persistence, identity/eligibility and baseline invariants. Record real commands, counts and any skips.
- `ui-suite`: existing UI tests plus meaningful regressions for R1 layout scoping, all inventoried R2 selectors/search/management and R3 navigation, draft preservation, blockers, publication and comparison.
- `ui-build`: TypeScript checking and production Vite build via the repository's established build command.
- `isolated-persistence`: exercise new write paths through an isolated repository/API, verifying saved descriptions/new codes after reload and preservation of existing associations/identity. If the Grist adapter changes, independently test the actual Grist adapter on a disposable isolated document, not production; retain it through review with recorded ID/workspace/purpose and follow protocol backup/Trash cleanup after verification. Local fixtures supplement rather than substitute for an untested changed Grist adapter.
- `isolated-browser-r1-r2-r3`: actual browser verification against isolated local servers and test data: CNC compact/expanded/split readability and unchanged MCL/TS widths; description creation/edit/reload/search and code eligibility across selection surfaces; full R3 open/return with drafts, per-Part confirmations/disabled reasons, successful establishment, established comparison and Parts-entry/multiple-workbook choice. Check normal desktop and narrow layouts for reachable controls. Do not use production writes or restart owner servers.
- `exact-commit-preservation`: confirm submitted full SHA, branch, designated PR/worktree, clean new application changes and protected local hashes. Acceptance requires independent verification of every criterion and mandatory check at that SHA.

## Explicit exclusions

No new roadmap features, rate/material integration, assembly assignment expansion, production migration, merger/deployment or owner process restart. Preserve the existing SemanticEvidence schema drift. Any real external dependency must be recorded truthfully; ordinary pending work or tests are not blockers. Task SUCCESS requires all three requirements implemented and independently verified, not merely assigned or submitted.
