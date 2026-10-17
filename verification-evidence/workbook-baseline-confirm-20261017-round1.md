# Workbook baseline confirmation round 1 verification

Task: `workbook-baseline-confirm-20261017`, round 1. The browser run used a synthetic ODS fixture, `InMemorySafariRepository`, and `GristPartRegistry` over `MemoryGrist`. The fixture reported `gristAccess: false`; it did not read `.env`, contact Grist, or touch the owner servers on ports 4320/4321.

## Required checks

| Check | Result | Evidence |
| --- | --- | --- |
| `python-suite` | Pass: 308 tests, 1 platform-dependent skip | `python -m unittest discover -s tests -v`; baseline-specific cases are also listed by the focused run below. |
| `ui-suite` | Pass: 124 Vitest tests across 15 files, plus 2 mapped-files view-model tests | Bundled Node ran `ui/node_modules/vitest/vitest.mjs run --config vitest.config.ts` and `ui/test/mappedFilesViewModel.test.mjs`. |
| `ui-build` | Pass: TypeScript check and Vite production build | Bundled Node ran `ui/node_modules/typescript/bin/tsc --noEmit` and `ui/node_modules/vite/bin/vite.js build`; build emitted `dist/assets/index-Cv2EeMNf.js` and completed successfully. |
| `isolated-persistence` | Pass: 34 focused baseline tests | `python -m unittest discover -s tests -p test_part_baseline.py -v`. Includes `test_workbook_api_preview_and_confirm_persist_multiple_parts_and_reload` and `test_workbook_bulk_confirmation_excludes_pending_rows_and_recovers_same_batch`. |
| `isolated-browser` | Pass: complete Chrome workflow | Reproduction steps and captured result are below and in `workbook-baseline-confirm-20261017-round1-browser.json`. |
| `exact-commit-preservation` | Final gate at publication | The workflow helper verifies PR/branch/worktree, exact HEAD, clean application paths, and all preexisting protected-file hashes while publishing the implementation report. |

The persistence test runs the actual `GristPartRegistry` against isolated `MemoryGrist`, confirms two Parts through the API, then constructs a fresh registry and reloads both baselines. It verifies the source hash, actor, requirement rows, pending-row exclusion, observation and revision-line counts. The partial-failure test injects response loss after one Part has persisted, retries the same batch request, and verifies that the original operations recover without duplicate requirement, observation, or family records. The adapter implementation in `app/grist_parts.py` was unchanged in this round.

## Chrome workflow

Run the fixture from the repository root:

```powershell
.\.venv\Scripts\python.exe tests\isolated_workbook_baseline_browser_app.py --port 44570
```

In `ui/`, start the isolated Vite proxy with the supported bundled Node:

```powershell
& 'C:\Users\ibaro\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' node_modules\vite\bin\vite.js --config vite.workbook-baseline-qa.config.ts
```

From the repository root, run the browser flow and capture its JSON:

```powershell
$env:BROWSER_EVIDENCE_PATH = (Join-Path (Get-Location).Path 'verification-evidence\workbook-baseline-confirm-20261017-round1-browser.json')
& 'C:\Users\ibaro\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tests\isolated_workbook_baseline_browser.mjs
```

The recorded run used incoming source hash `a1dae8991a34815a87deff38b5a3d032d33968436b31141992ad73dfa19d733c` and prior workbook hash `4e9f7f9907b236fd7017740a02875f8a428f506b530bce7660899f3cf074dae2`.

- The preview showed 3 ready Parts, 1 already established Part, 0 blocked Parts, and 2 pending MCL rows. The pending rows remained actionable and excluded; workbook completion stayed false.
- The ready action established three Parts across MCL, Tool Shop, and CNC. The browser dropped the first response and retried with the identical request key and payload; all three results returned idempotently. Counts ended at 4 baselines, 4 revision lines, 5 observations after comparison, and 5 saved mapping rows.
- A separate blocked-Part simulation left confirmation enabled for unaffected ready Parts. A stale-preview response cleared the saved attempt, exposed the refresh notice, and allowed a fresh confirmation; no same-key retry button remained.
- A 390-pixel viewport kept the confirmation button within the viewport (`x=31`, width `328`, scroll width `390`).
- Existing CNC comparison returned `incomplete_evidence` while showing accepted length `120` and incoming length `140` with their respective workbook hashes; no existing baseline was replaced.
- Pending row 3 returned to Part Mapping for explicit review. The unsaved draft, `unsaved` filter, selected incoming workbook, and accepted-baseline detail context survived confirmation and navigation. Switching to the prior workbook and back restored the incoming workbook and draft.
- Browser JavaScript errors: none. The fixture exposed only the local memory-backed API and never accessed Grist.
