# Workbook-wide manufacturing baseline confirmation — 17 October 2026

Owner explicitly requested implementation after approving a single workbook confirmation, excluding pending unmapped ODS rows. This supersedes R3 per-Part/per-family confirmation and its ban on explicit bulk establishment for this scoped feature only. Other safeguards and separate finalization remain. Task workbook-baseline-confirm-20261017; five rounds maximum.

**c1**: Provide a prominent workbook-level Confirm manufacturing baselines action in Part Mapping/baseline workspace. Preview mapped Parts ready for initial establishment, already-established Parts, blocked Parts and pending ODS source rows. One explicit confirmation establishes every ready Part without visiting each Part or repeating MCL/Tool Shop/CNC applicability checkboxes. Workbook scope means all three manufacturing sheets in the selected ODS, not merely the displayed or filtered sheet/group.

**c2**: Derive Part membership, source-family applicability and requirements on the server from saved current line mappings and successfully read MCL/Tool Shop/CNC evidence. Include split/mixed groups correctly, include every mapped row exactly once in its assigned Part, and exclude unsaved drafts, unassigned and unreviewed rows. Missing/unreadable sheets are blockers, not inferred not-applicable. Do not require users to repeat confirmations already represented by saved current mappings.

**c3**: Pending active ODS rows without valid saved Part assignments remain explicit actionable review exceptions with sheet/row and counts. They are excluded from baseline requirements and must not globally prevent initial establishment for otherwise ready mapped Parts. Do not silently assign pending rows, mark them reviewed, claim the workbook fully complete, or treat their absence as authoritative deletion evidence. Preserve affected-Part blockers for stale/ambiguous assignments, unresolved removed lines and invalid physical evidence; unaffected ready Parts may proceed. Inactive/excluded source rows retain existing policy.

**c4**: Use fresh server-validated workbook hash, association identity/version, mapping version and Part revision/baseline state. A stale preview or concurrent source/mapping change cannot establish from obsolete evidence. Bind one bulk confirmation to stable per-Part request identities and evidence; retries after timeout/partial failure must recover or resume original operations without duplicate requirements/baselines. Report each Part established/already established/blocked/failed and pending rows truthfully; never show total success on partial failure. No requirement for cross-Part atomicity, but partial outcomes and safe retry are mandatory.

**c5**: Never overwrite an established baseline: skip it with clear established status and retain comparison for changed incoming evidence. Preserve separate Rev A finalization, revision locks, immutable provenance, append-existing-content and Change Request boundaries. Existing content must not be silently replaced or appended without an explicit informed choice; ready-path bulk confirmation must not require redundant per-family clicks. No automatic costing/configuration/association or Part identity changes. Later mapping of excluded pending rows is handled through ordinary baseline comparison/review for established Parts.

**c6**: Show publication progress/results and remaining review exceptions in the full-width workspace; refresh per-Part status and Parts accepted-baseline details after confirmed writes, preserving selected workbook, filters and unsaved drafts across open/return, contextual Parts navigation and distinct-workbook switching. Controls remain reachable on desktop and narrow screens. Existing individual review/comparison remains available for genuine exceptions.

**c7**: Deliver only this approved change on a new PR based on merged PR5 main, with meaningful independent-verifiable tests/evidence and exact submitted SHA. Preserve all existing tracked/untracked local edits, immutable workflow artifacts, R1/R2 behavior, production Grist/schema/Costing-New, owner servers and current model/reasoning settings. No merge, deploy or production test writes authorized by this implementation request.

## Mandatory checks

- **python-suite**: Full Python suite plus meaningful service/API tests: all mapped multi-Part/multi-sheet establishment with no family clicks, pending rows do not block unrelated mapped Parts, mixed/split membership, missing sheet, stale preview/concurrent mapping, removed-line review, existing baseline/content/finalized revision and partial failure/idempotent retry.

- **ui-suite**: Full UI suite plus actual workflow regressions for preview/single confirmation/results and pending-row links, safe failure/retry, no duplicate submit, selected workbook/draft preservation and refreshed Parts details.

- **ui-build**: TypeScript checking and production Vite build using supported bundled Node.

- **isolated-persistence**: Exercise real baseline persistence through isolated repository/API and fresh reload: multiple Parts, pending exclusions, exact counts/provenance, retries after injected partial failure and existing baseline preservation. Test actual Grist adapter on a labelled disposable QA document if its behavior/write paths change; record exact ID/workspace/purpose, retain through independent review then verified native backup plus recoverable Trash per protocol. Never restore/write previously trashed QA or production for tests.

- **isolated-browser**: Actual browser on isolated servers/data: one confirmation establishes multiple mapped Parts across MCL/Tool Shop/CNC without per-Part family clicks, pending row remains review while ready Parts succeed, status refresh/return and no duplicate retry. Verify existing baseline skip/comparison, stale/blocker presentation, desktop/narrow controls and draft/workbook preservation. No production writes/restarts.

- **exact-commit-preservation**: Verify designated PR/branch/worktree and exact submitted full SHA, no new dirty application source, every protected local hash unchanged. Review immutable Git objects only.

## Scope

Initial workbook baseline establishment only, no bulk finalization or replacement of accepted baselines. No new direct authoring/material-rate/assembly features. Implementation and independent review only; prior PR5 merge/deploy authorization does not extend automatically to this new PR.
