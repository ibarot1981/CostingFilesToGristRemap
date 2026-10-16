# Safari R1–R3 implementation verification — round 3

Task: `safari-r1-r2-r3-20261016`
Round: 3 — bounded F4 correction under c7/c8
Branch: `codex/r1-r2-r3-manufacturing-ui`
Pull request: [#5](https://github.com/ibarot1981/CostingFilesToGristRemap/pull/5)

This round keeps the actual Part Mapping workbook path durable after a one-shot replacement selection is consumed. The regression test exercises Files workbook A, Part 27's mapped baseline workbook B, an explicit replacement, and later navigation. The draft remains scoped to B, and selecting A does not reuse B's draft.

## Source and regression changes

- `ui/src/App.tsx`: stores the current mapping workbook path independently of the selected Files workbook; baseline, Parts and contextual Part Mapping callbacks carry the workbook actually being mapped.
- `ui/src/PartMappingView.tsx`: tracks which workbook produced the loaded review and avoids persisting A's state under B's session key during a path transition.
- `ui/test/partMappingAppNavigation.test.tsx`: adds an App-level A-to-B regression covering the saved correspondence, explicit replacement, editable unsaved B draft after selection consumption, contextual Parts navigation, re-entry, and isolation from A.

## Actual isolated browser flow

Chrome used the isolated Vite/API servers on `127.0.0.1:44320` and `127.0.0.1:44321`. The API adapter validated the exact QA target before writes: Grist document `wML6Wn57Vp525XdpWBXyG5`, workspace `2`, titled `QA - Safari Manufacturing R1-R3 20261016 Round 1`. No production Grist document or owner server was used.

1. Selected `Browser-QA.ods` (A) in Files, then selected `SM-P-000027` (`S1KHF — Reviewer Baseline R1 — QA only`) in Parts.
2. Its mapped workbook picker selected `Browser-QA-2.ods` (B). The fixture was backed up at `C:\Users\ibaro\.codex\backups\safari-qa-r123\20261016\round3-browser-QA-2-original.ods` with original SHA-256 `ba297865bb307d8f56f397b952074c1ad566730a98cdb5cdf27ffd76231843ad`.
3. Changed only the synthetic B fixture for the comparison to material `MS Plate QA-SOL-R3`, quantity `9`; changed SHA-256 was `d993792903d03fd53417200467de323c148df9c2d61963d86a58489e961b9d3a`. After explicitly revalidating the current QA source row to Part 27 and recording an audit reason for the absent prior R2 fingerprint, a fresh Part 27 baseline review no longer showed the prior-source-missing warning. The mapping page still showed one group needing review; that did not block the Part 27 baseline review.
4. In the browser, confirmed MCL applicable and complete, and Tool Shop/CNC not applicable. Compared B against the accepted Part 27 baseline (original hash `ba297…`). The comparison showed the incoming material/quantity change. Saved the explicit correspondence to the accepted MCL line `ms plate qa-2 · 3 x 25 · Qty 5` with a review reason.
5. Searched and selected replacement `SM-P-000001 · S1KHF — Chassis — Local`, supplied an explicit QA-only reason, and chose **Use a different Part**. The app returned to Part Mapping on B and staged `SM-P-000001` for the changed row. It remained unsaved; editing its mapping reason enabled **Save lines**, but no mapping save was performed.
6. From B's Part Mapping page, opened the mapped Part 26 row through **View Part**, then returned to Part Mapping. The page still showed B and its one unsaved replacement line.
7. Selected A in Files and reopened Part Mapping. It showed `Browser-QA.ods`, no B replacement, and zero unsaved changes. Selecting B in Files and reopening Part Mapping restored the B context and its one unsaved `SM-P-000001` replacement. This also covered the ordinary same-workbook B selection and navigation path.

The browser flow did not change Part 27's accepted baseline. A fresh read from the QA adapter after the flow reported baseline status `established`, source hash `ba297865…`, one accepted requirement and Rev A lifecycle `draft`. The changed fixture was restored byte-for-byte from the backup; restored SHA-256 is `ba297865bb307d8f56f397b952074c1ad566730a98cdb5cdf27ffd76231843ad`.

## Mandatory checks

| Check | Result | Evidence |
|---|---|---|
| `python-suite` | Pass | `.venv\\Scripts\\python.exe -m unittest discover -s tests`: 302 tests, 1 skipped, OK. |
| `ui-suite` | Pass | Bundled Node 24.19.0: standalone mapped-files checks 2/2; Vitest 15/15 files and 119/119 tests. |
| `ui-build` | Pass | Bundled Node 24.19.0: TypeScript passed; Vite production build transformed 1,599 modules. |
| `isolated-persistence` | Pass | Against the exact QA target above, created and edited Model Code 181 (`QA-R123-1263C7C9`), reloaded the saved description, rejected blank description, preserved the prior 180 codes, Product/Model identities and workbook associations, and confirmed selector eligibility. |
| `isolated-browser-r1-r2-r3` | Pass | Actual Chrome flow above covers distinct A/B contexts, explicit correspondence and replacement, retained editable B draft, A isolation, contextual Parts return and same-workbook re-entry. |
| `exact-commit-preservation` | Pending helper publication | The workflow helper will verify exact HEAD, branch/PR, protected workspace hashes and clean scoped implementation state when the report is published. |

No merge, deployment, production Grist/schema write, or owner-process restart occurred. The isolated QA document remains retained for the independent review round.
