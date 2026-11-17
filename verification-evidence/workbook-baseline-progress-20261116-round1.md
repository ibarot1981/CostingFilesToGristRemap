# Workbook baseline confirmation progress — round 1 verification

Task: `workbook-baseline-progress-20261116`, round 1, PR #7 (`codex/workbook-baseline-progress`). All data used for the delayed browser run was isolated QA data; owner services on ports 4320/4321 and production Grist/schema/source ODS were not touched.

## Environment

- Windows checkout at `D:\Irshad\Dev\Python\CostingFilesToGristRemap`
- Python 3.10.6 from `.venv`
- Node 24.22.1 from the bundled Codex runtime
- Google Chrome through Playwright for browser verification

## Mandatory checks

| Check | Result | Evidence |
| --- | --- | --- |
| `python-suite` | Pass: 311 tests, 1 skipped | `.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| `ui-suite` | Pass: 15 Vitest files / 128 tests; standalone mapped-files view-model: 2 tests | Node 24: `node_modules/vitest/vitest.mjs run --config vitest.config.ts`; `test/mappedFilesViewModel.test.mjs` |
| `ui-build` | Pass: TypeScript no-emit and Vite production build | Node 24: `node_modules/typescript/bin/tsc --noEmit`; `node_modules/vite/bin/vite.js build`. Vite emitted its existing advisory that the minified JS chunk is slightly over 500 kB. |
| `isolated-long-running-browser` | Pass: real confirmation took 31,463 ms with a server-side 31-second delay; original response was lost; UI displayed server-backed `0 of 3` confirmed / `3 in progress`; the original attempt then reconciled after reload | Isolated QA app on port 44570 and Vite on 44571; `tests/isolated_workbook_baseline_browser.mjs`; detailed capture in `verification-evidence/workbook-baseline-progress-20261116-round1-browser.json` |
| `recovery-persistence` | Pass: exact original request reconciles from durable Grist records after constructing a fresh registry adapter; read-only reconciliation does not change MemoryGrist tables. The focused regression and interrupted-publication recovery tests also pass in the full suite. | Focused command: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_part_baseline.py -k test_workbook_api_preview_and_confirm_persist_multiple_parts_and_reload -v`; relevant cases in `tests/test_part_baseline.py` include `test_workbook_reconciliation_does_not_attribute_an_unrelated_baseline_to_saved_attempt` and `test_workbook_api_retries_grist_error_after_durable_family_write_with_original_batch`. |
| `exact-commit-preservation` | Pass when submitted: workflow publication validates the report SHA equals checkout HEAD and verifies every pre-existing workspace edit hash remains unchanged. No unrelated files are staged. | Exact SHA and helper publication outcome are recorded in the immutable round-1 implementation report. |

## Browser observations

The browser captured no JavaScript errors. It exercised an actual delayed confirmation, response loss, in-progress server status, same-key read-only reconciliation, and a legacy PR6-format saved attempt recovered after reload. The confirmation endpoint was called once; recovery did not issue a second business write. Final isolated fixtures contained four baselines (three new and one retained), four PartRevision lines, and five mapping records. Two pending source rows remained excluded and the UI did not claim whole-workbook completion. Existing blocked/stale preview, baseline comparison, workbook navigation, narrow viewport, and unsaved mapping draft behavior also passed.

The browser capture SHA-256 is `74f8c9db8a9308ee53dbdca501fbc79a543b1caa097cd11a14edebd145a67ad7`.
