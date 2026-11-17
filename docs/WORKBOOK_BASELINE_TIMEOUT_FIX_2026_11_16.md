# Workbook baseline confirmation progress and completion recovery

Owner requested implementation on 16 November 2026 after diagnosis of the real S1KHF Local workbook confirmation: 23 Parts / 100 requirements published successfully over approximately 205 seconds while the UI abandoned the response after 30 seconds and retained an uncertain retry prompt. Fix the long-operation UX and completion recovery; retain the accepted manufacturing baseline semantics.

## Acceptance criteria

- **c1** Long workbook confirmations lasting beyond the former 30-second timeout remain understandable: show an explicit running state and truthful observable progress/status, and reconcile the final per-Part outcomes. Raising or removing the timeout alone is insufficient. Do not claim percentages or completion without server evidence.
- **c2** Recover the original confirmation after a lost response, page reload or reopening the workbook. Recognise verified completed original operations and clear the obsolete retry warning; retain the original request identity/payload for genuinely pending, failed or uncertain operations. This includes a browser holding a saved attempt from the existing PR6 implementation. Read-only reconciliation of completed operations must not republish production business data.
- **c3** Preserve per-Part idempotency and safe partial-failure recovery: no duplicate baselines/requirements on retry, accurate established/retained/blocked/failed outcomes, no false total-success message, no overlapping writes, and explicit actionable failure when recovery is unavailable. Preserve saved attempts until their outcome is established. Background execution, if introduced, must not leave orphaned work or misreport interrupted jobs after restart.
- **c4** Preserve original workbook/source/mapping freshness checks, pending-row exclusions, established-baseline protection, separate Rev A finalization and current filters/drafts/workbook navigation. Reconciliation must not infer a different request succeeded from an unrelated established baseline or mix workbooks.
- **c5** Independently reproducible verification covers an actual isolated browser confirmation beyond 30 seconds, lost-response/reload recovery (including legacy saved attempts), partial failure/retry, fresh-adapter persisted completion and no duplicates. Full Python/UI checks and UI build pass. Keep production data/schema, source ODS, unrelated edits and owner servers intact; test with isolated data. Submit exact committed source on a draft PR. No automatic merge, deployment or production baseline writes are included.

## Mandatory checks

python-suite; ui-suite; ui-build; isolated-long-running-browser; recovery-persistence; exact-commit-preservation.

Use the existing Luna implementation and Sol independent-review protocol, five rounds maximum. Preserve all historical task artifacts and model/reasoning settings. This scoped fix is complete only on independent acceptance, not at assignment or submission.
