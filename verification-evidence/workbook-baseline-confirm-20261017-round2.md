# Workbook baseline confirmation round 2 verification

Task: `workbook-baseline-confirm-20261017`, round 2. The isolated browser run used the synthetic ODS fixture, `InMemorySafariRepository`, and `GristPartRegistry` over `MemoryGrist`. The fixture reported `gristAccess: false`; it did not read `.env`, access production Grist, or use the owner services on ports 4320/4321.

## Required checks

| Check | Result | Evidence |
| --- | --- | --- |
| `python-suite` | Pass: 310 tests, 1 platform-dependent skip | `\.venv\Scripts\python.exe -m unittest discover -s tests -v`; completed in 81.111 seconds. |
| `ui-suite` | Pass: 125 Vitest tests across 15 files, plus 2 standalone mapped-files tests | Bundled Node v24.19.0 ran `ui/node_modules/vitest/vitest.mjs run --config vitest.config.ts` and `ui/test/mappedFilesViewModel.test.mjs`. |
| `ui-build` | Pass: TypeScript check and Vite production build | Bundled Node ran `ui/node_modules/typescript/bin/tsc --noEmit` and `ui/node_modules/vite/bin/vite.js build`; Vite transformed 1,599 modules and completed successfully. Existing bundle-size advisory remains (500.07 kB JS). |
| `isolated-persistence` | Pass: 36 focused baseline tests | `\.venv\Scripts\python.exe -m unittest discover -s tests -p test_part_baseline.py -v`. Includes the ready/blocked/retained mixed-result regression and API recovery after actual `GristError` following a durable family write. |
| `isolated-browser` | Pass: complete Chrome/Playwright flow | `tests/isolated_workbook_baseline_browser.mjs`; captured JSON is `verification-evidence/workbook-baseline-confirm-20261017-round2-browser.json`. |
| `exact-commit-preservation` | Performed by helper publication against the committed exact SHA | `publish-implementation` validates PR #6, branch/worktree/HEAD, clean application paths and all protected workspace hashes. Its immutable report records the result. |

## F1: truthful mixed outcomes

The service regression uses three Parts with no pending rows: one ready, one already established, and one blocked by a finalized revision. One request establishes the ready Part and returns all three outcomes, with counts `ready=1`, `already established=1`, `blocked=1`, `pending=0`. The result is `partial`, `totalSuccess=false`, and `workbookComplete=false`; the earlier accepted baseline and its requirements remain unchanged. The UI regression and browser simulation assert the same visible counts, a “needs review” result, and distinct retained/blocked messages.

## F2: recovery after uncertain Grist writes

The API regression injects `app.exceptions.GristError` after `PartBaselineFamily` rows are committed to the isolated `MemoryGrist` adapter. The first result identifies `GristError` and sets `retryable=true`. Retrying the same original payload and idempotency key completes the batch without duplicates: 2 baseline rows, 6 family rows, 4 revision lines and 4 source observations.

The UI regression verifies that a retryable `GristError` result keeps the key and payload in local storage across workspace unmount/re-entry, and that retry uses the same request. The browser flow drops the HTTP response after the isolated write, reloads the workspace, confirms the saved key and payload are unchanged, then retries idempotently. Final isolated state has 4 baselines, 4 revision lines and 5 observations after the existing comparison flow; browser JavaScript errors: none.

## Scope and environment

Only the round-2 F1/F2 corrections under c4 and c6 were implemented. The owner services remain stopped through independent review as required by the current prompt. Temporary test servers on ports 44570/44571 were stopped after the browser run. No merge, deployment, production Grist/schema writes or owner-service restarts occurred.
