# Safari R1–R3 implementation verification — round 1

Task: `safari-r1-r2-r3-20261016`
Branch: `codex/r1-r2-r3-manufacturing-ui`
Pull request: [#5](https://github.com/ibarot1981/CostingFilesToGristRemap/pull/5)

This record covers only the approved R1–R3 scope. The exact submitted commit is in the immutable round 1 implementation report.

## Criteria

- **c1:** CNC Part Mapping column proportions are scoped to CNC, including compact, expanded and split behavior. The Plate Part to Cut label has more space; quantity and weight fields are narrower. MCL and Tool Shop widths remain unchanged. UI coverage checks compact and expanded CNC details while preserving source-line mapping evidence; the isolated browser inspection covered the mapping groups and split assignment flow.
- **c2:** Product Model Code selectors display code, description and useful Product Model context; blank descriptions remain eligible and render as `No description`. Workbook association, intended sharing, Part scope/configuration and costing surfaces were inventoried. Existing selector search includes descriptions while retaining prior eligibility rules.
- **c3:** Product Models contains the code management surface. Existing descriptions can be edited; the canonical Grist adapter persists them. Reload and selector display were verified in the isolated browser. Repository/API regressions protect code identity, Product/Model hierarchy, workbook associations and existing Part/cost history.
- **c4:** A code can be created under an existing Product Model without a workbook. The description is required and nonblank. Code uniqueness/identity checks and selector eligibility are preserved; creation does not associate a workbook, assign Parts or configure costing.
- **c5:** Part Mapping opens the full-width baseline workspace with searchable mapped-Part navigation and status. An unsaved Tool Shop mapping draft was still present after returning from the workspace.
- **c6:** Initial review showed unassigned-line blockers for MCL, Tool Shop and CNC and kept publication disabled. After each family was mapped and explicitly confirmed Applicable and complete, publication succeeded. The page showed the accepted workbook/hash and existing Rev A content warning; the Part status updated to Established.
- **c7:** Parts provides a baseline entry point. After a fresh page load, the Part had two available mapped workbooks; the selector showed `Choose a workbook explicitly`, and review stayed disabled until a source was selected. Selecting `Browser-QA-2.ods` opened review with that workbook and its hash. Comparison showed the accepted MCL values beside incoming values at the default desktop width and stacked at 720×900. The synthetic row showed material `MS Plate` → `MS Plate QA-2` and quantity `2` → `5`.
- **c8:** Establishment recorded the initial manufacturing baseline while Rev A remained `draft`; the page directs finalization to the separate Engineering baseline control. The comparison correspondence was saved as a review decision, leaving the accepted baseline hash unchanged. Existing stale evidence, completeness, append confirmation, retry/recovery and immutable-evidence paths remain covered by the backend/API and UI suites.
- **c9:** Changes were staged by explicit path selection. The immutable workflow helper verifies the exact 40-character HEAD SHA, designated branch/worktree, all protected pre-existing hashes and clean new application changes when the implementation report is published. PR #5 remains a draft; no merge or deployment occurred.

## Mandatory checks

### `python-suite` — pass

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Result: **302 tests ran; OK; 1 skipped.** This used the repository virtual environment. An initial system-Python run was missing FastAPI; the project-environment rerun passed.

### `ui-suite` — pass

Command: `npm test` with Node.js **24.19.0** prepended to `PATH`.
Result: the standalone Node suite passed **2/2**; Vitest passed **15/15 files and 114/114 tests**. The machine default Node 18 could not load the installed Vitest's `node:fs` import; rerunning under the bundled Node runtime passed.

### `ui-build` — pass

Command: `npm run build` with Node.js **24.19.0** prepended to `PATH`.
Result: TypeScript check passed; Vite transformed **1,599 modules** and completed the production build.

### `isolated-persistence` — pass

Command: `.\.venv\Scripts\python.exe scripts/isolated_safari_r123_server.py --mode verify-model-code --doc-id wML6Wn57Vp525XdpWBXyG5 --workspace-id 2 --document-name "QA - Safari Manufacturing R1-R3 20261016 Round 1" --costing-root "C:\Users\ibaro\.codex\qa\safari-r123-20261016\costing" --part-database "C:\Users\ibaro\.codex\qa\safari-r123-20261016\part-registry.sqlite3"`

Result: **pass** on the changed `GristSafariRepository` adapter. Created QA code ID `176` (`QA-R123-BE3703EF`), edited and reloaded its description, rejected a blank description, preserved 175 existing codes, Product/Model identities and workbook associations, and confirmed it appears in the existing eligible selector list.

### `isolated-browser-r1-r2-r3` — pass

Target: local isolated Vite/API servers on `127.0.0.1:44320` and `127.0.0.1:44321`; Grist document `wML6Wn57Vp525XdpWBXyG5`, workspace `2`, titled `QA - Safari Manufacturing R1-R3 20261016 Round 1`; synthetic fixtures only under `C:\Users\ibaro\.codex\qa\safari-r123-20261016\costing`. No production document or workbook was opened for these browser checks.

Observed browser flows: create/edit/reload/search a Product Model Code and read its description in selectors; save synthetic workbook associations; create a QA Part and map MCL, Tool Shop and CNC rows; enter and return from the baseline workspace with a draft; establish a baseline with explicit per-family evidence; compare a changed second synthetic workbook; save an explicit line correspondence. The UI showed a disabled publication action while blockers remained, a success notice after establishment, and no Rev A finalization. Two workbook candidates required an explicit selection. At the default desktop viewport, accepted and incoming values were side by side; at 720×900, navigation and review stacked and all controls remained reachable by scrolling without horizontal clipping; comparison values stacked. The temporary viewport override was reset after the check.

### `exact-commit-preservation` — pass

The report publication command performs the exact-SHA, branch/worktree and protected-hash checks against task state. Only scoped application, regression-test, QA-support and evidence paths are included in the implementation commit; pre-existing local documents and workflow inputs remain untouched. PR #5 is updated on the designated branch and no merge/deployment is authorized.

## Isolated Grist retention

The QA document is retained through independent review as required. The pre-write native backup is `C:\Users\ibaro\.codex\backups\safari-qa-r123\20261016\before-test-writes.grist` (SHA-256 `1e86186abce7ee857c21a2fe9488b6295b292c165560d9e4549f3db7ce5fdd7e`, SQLite integrity `ok`). The post-browser native backup and manifest are `C:\Users\ibaro\.codex\backups\safari-qa-r123\20261016\final-after-browser-verification.grist` (recorded below after the last QA write). Keep the fork through review; protocol cleanup is to move only the recorded disposable document to recoverable Trash after independent verification, without permanently emptying Trash.

Final post-browser backup record: SHA-256 `9d647c82a56b0a9bba4c7174ce64e0150cb03588c1ff7014bae1c79ee2875f30`; SQLite `PRAGMA integrity_check` returned `ok`. The API log recorded baseline establishment and both comparison writes as HTTP 200. CUA screenshots were inspected at default desktop width and at 720×900; the temporary viewport override was reset.
