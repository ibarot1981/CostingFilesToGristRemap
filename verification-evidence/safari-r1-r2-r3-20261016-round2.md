# Safari R1–R3 implementation verification — round 2

Task: `safari-r1-r2-r3-20261016`
Branch: `codex/r1-r2-r3-manufacturing-ui`
Pull request: [#5](https://github.com/ibarot1981/CostingFilesToGristRemap/pull/5)

This round implements only the bounded F1–F3 corrections under existing criteria c6–c8. Round 1's immutable report and evidence remain unchanged.

## Browser corrections

### F1 — refresh the selected Part on return

In the isolated Grist fork, selected Part `SM-P-000027` (`1a43e384-f724-4bca-b5e5-a8c86e41d87c`) had an established baseline. After returning from the review workspace and switching away from Parts and back, the same Part remained selected and the Manufacturing baseline section stayed open. Overview reported `established`; accepted MCL requirements showed quantity `5`, material `ms plate qa-2`, and the originating workbook hash. Rev A remained `draft`. This exercises the cached Parts view return path without reselecting the Part or reloading the page.

### F2 — list only current workbooks mapped to this Part

Before the changed-workbook probe, Part 27's source picker offered only `JK Mini Crane · Browser-QA-2.ods`; the unrelated `Browser-QA.ods` was absent. After the probe and fixture restoration, the saved current line no longer matched the restored source fingerprint, so the picker truthfully showed `No associated workbook available` and `No current saved workbook line assignments are linked to this Part. Map one of its source lines before baseline review.` The automated UI coverage also verifies the zero-current-mapping message and that a global mapped-workbook request is skipped.

### F3 — recover from stale evidence and decide a changed comparison

The review workspace initially loaded the original workbook SHA `ba297865bb307d8f56f397b952074c1ad566730a98cdb5cdf27ffd76231843ad`. After preserving the exact ODS bytes in `C:\Users\ibaro\.codex\backups\safari-qa-r123\20261016\round2-browser-QA-2-original.ods`, I changed the synthetic MCL row from material `MS Plate QA-2`, quantity `5` to `MS Plate QA-R2`, quantity `7`. The new SHA was `69babe5f87ee8dd3b5e7bb9389ed6aecef1f828d4fcdc58008b831f0cf9a8099`.

Comparing against the loaded old review returned HTTP 409 `PART_COMPARISON_STALE`. The UI exposed an enabled `Refresh review` control; refreshing loaded the new hash. Because the source-line fingerprint had changed, I explicitly reassigned the synthetic row to Part 27 and recorded the old fingerprint removal review in the QA document. After confirming MCL Applicable and complete, and Tool Shop/CNC not applicable, comparison completed. I matched the incoming row to its accepted baseline requirement; the resulting comparison showed one proposed modification with old and incoming values side by side. The `Keep existing baseline` decision was saved, preserving Rev A while retaining the incoming difference as comparison evidence. Server logs recorded stale compare HTTP 409 and subsequent compare, mapping and decision requests as HTTP 200.

The original ODS bytes were restored and compared byte-for-byte with the backup. Restored SHA-256: `ba297865bb307d8f56f397b952074c1ad566730a98cdb5cdf27ffd76231843ad`.

## Mandatory checks

### `python-suite` — pass

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Result: **302 tests ran; OK; 1 skipped.**

### `ui-suite` — pass

Command: `npm.cmd test` with bundled Node.js 24.19.0 prepended to `PATH`.
Result: standalone checks passed **2/2**; Vitest passed **15/15 files and 118/118 tests**.

### `ui-build` — pass

Command: `npm.cmd run build` with bundled Node.js 24.19.0 prepended to `PATH`.
Result: TypeScript passed; Vite transformed **1,599 modules** and completed the production build.

### `isolated-persistence` — pass

Verified the changed `GristSafariRepository` adapter against isolated document `wML6Wn57Vp525XdpWBXyG5`, workspace `2`, after creating, editing, reloading and validating QA Product Model Code ID `179` (`QA-R123-10F3C33F`). Its description persisted; blank description was rejected; 178 previous codes, Product/Model identities and workbook associations were preserved; the code remained selector-eligible.

### `isolated-browser-r1-r2-r3` — pass

Local isolated Vite/API servers ran at `127.0.0.1:44320` and `127.0.0.1:44321`. Browser checks used only QA document `wML6Wn57Vp525XdpWBXyG5` and synthetic fixture files. F1–F3 observations are recorded above; round 1 browser coverage remains in `verification-evidence/safari-r1-r2-r3-20261016-round1.md`.

### `exact-commit-preservation` — pass

The workflow helper validates the report's exact HEAD SHA, designated branch/worktree, protected pre-existing hashes and clean scoped implementation state during report publication. Only Parts/baseline UI changes, regression tests and this round's evidence are included in the implementation commit. No merge, deployment or production Grist/schema writes occurred.

## Isolated Grist retention

The disposable QA document remains retained for independent review. A native backup taken after the browser flow is `C:\Users\ibaro\.codex\backups\safari-qa-r123\20261016\round2-after-browser.grist`, SHA-256 `5fb63a3d723df6dcc6d73aacaafca858a738f81b6ffe3f757cc325fbd62c3932`; SQLite integrity check returned `ok`. It includes the round 2 comparison and explicit decision evidence. The synthetic ODS source was restored to its original bytes and SHA. The QA document is for disposable testing only; no production Grist target was opened or modified.
