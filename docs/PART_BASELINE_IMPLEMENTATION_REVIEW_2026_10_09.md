# Implementation review — 9 October 2026

Reviewed HEAD `5e9d894`, including changes since `78d389e`. No application code or production records were changed during review.

## Status

Substantially implemented, but not ready for full acceptance. Mapping save policy, persistent collapsed groups, search freshness, reduced register reads, retained navigation views, normalized baseline processing and workbook comparisons are present. Rev A is preserved; proposals remain pending approved CR control.

Verification run independently:
- Full Python suite: 226 tests, 225 passed, 1 skipped.
- Full Vitest suite: 42 tests across 10 files passed.
- TypeScript validation and Vite production build passed.
- Read-only Safari Manufacturing validation confirmed all seven baseline/comparison tables exist; each has zero records.
- Existing material-substitution integration fixture was replayed in MemoryGrist and its generated field differences inspected. No live business fixtures were created.

No fresh live-browser visual verification or independent before/after performance timing was performed in this review. Earlier measurements in the implementation document remain developer-reported samples.

## Outstanding findings

### P1 — Manual correspondence matching creates incorrect field differences

`app/part_baseline.py:1011` passes the serialized IncomingValues string through `_row_fields`, which returns an empty dictionary for strings, before JSON parsing. Consequently incoming_physical is always empty on this path.

Reproduction: run the existing test fixture for MS Plate to Aluminum substitution and inspect FieldDifferences on its generated proposed_modification. IncomingValues correctly preserves aluminum, quantity 2, dimension 3 x 25 and weight 0.11; FieldDifferences instead says those values, units and family become null. Existing assertions check status/count/proposal but not the actual changed values.

Parse IncomingValues directly, validate its physical object and test that only material changes in this case. Do not permit proposals generated from malformed evidence.

### P2 — Failed comparison cannot be retried from its button

`ui/src/PartBaselinePanel.tsx:153` disables the compare button whenever attempt exists, including attempt.operation === compare. runWrite retains an attempt after any error. The button changes to Retry comparison but remains disabled; reloading restores the same attempt. Allow retry of the matching operation while retaining exact key and payload. Cover response-loss recovery in UI tests.

### P2 — Validation failure freezes baseline inputs

runWrite records an attempt before server validation and retains it for every error. Establish can be clicked with missing family choices/completeness confirmations. After the backend rejects the incomplete request, all relevant controls are disabled by Boolean(attempt), with no revise/cancel/reconcile action. The user can only resubmit the invalid payload.

Validate completeness before submission, and provide safe editable recovery for confirmed pre-write validation failures. Preserve/reconcile uncertain partial writes before abandoning an attempt; do not indiscriminately clear request identity after timeouts.

### P2 — Most difference decisions do not revalidate current workbook/mapping context

`app/web.py:782` revalidates source hash, association and group evidence only for use_different_part. Keep baseline, match correspondence and propose change can act on an old comparison after workbook or saved mapping changes. The service checks the current baseline pointer, not the incoming context.

Apply appropriate fresh source/association/mapping validation to every active-workflow decision, including baseline-only deletions. If historical-evidence annotations are intentionally supported, identify them separately rather than implying they resolve current workbook processing. Test changed hash, association and mapping with an unchanged baseline.

### P2 — Difference decision retries create new request IDs

`ui/src/PartBaselinePanel.tsx:111` uses crypto.randomUUID for each click and retains no decision attempt. A lost committed response followed by another click can append a second keep/proposal decision, or make correspondence matching fail because the first request already used that row.

Persist an exact decision attempt (comparison, action, payload and key), lock conflicting edits while unresolved, and recover/retry using the same request. Cover lost-response keep, match, proposal and replacement decisions.

## Implemented areas confirmed by code/tests

- Initial mapping assignment does not require a user reason; replacement/clear is enforced in backend and UI.
- Groups default collapsed, persist workbook/group state and preserve pending choices.
- Search displays loading and prevents stale-query result selection.
- Register joins table snapshots rather than repeatedly fetching four tables per Part.
- Explicit baseline gathers all mapped applicable families, retains source provenance and normalized requirements, and prevents competing establishment requests.
- Complete/incomplete comparison evidence distinguishes matches, additions, modifications, deletions and ambiguities.
- Keep-baseline and pending CR proposal records preserve accepted Rev A and historical costing records.
- App navigation test covers four choices, creation handoff, individual save and remaining batch save.

## Acceptance recommendation

Fix the five findings, add tests asserting actual change values and failure recovery, and rerun the relevant suites. Then verify the baseline/comparison UI in an isolated browser and disposable Grist document. Existing CR publication and Summary-authority project gates remain outside this prompt and must not be described as implemented.

## Resolution follow-up — 9 October 2026

The five findings above are fixed in the current PR working tree. Rev A and prior comparison evidence remain immutable.

1. **Manual correspondence differences:** the match path now parses serialized `IncomingValues` directly and validates the normalized physical object, family, quantity, units and numeric values. It compares against the selected normalized baseline requirement with the same normalized physical-field policy used by automatic comparisons. Invalid or missing physical evidence returns `PART_COMPARISON_EVIDENCE_INVALID` before request or decision rows are written. Regression assertions cover MS Plate → Aluminum, unchanged quantity/dimension/units/weight, legitimate multi-field changes, malformed/missing evidence, generated differences and proposal references.
2. **Comparison retries:** an unresolved comparison attempt stays in local storage with its Part, operation, original payload and idempotency key. Only that same operation can retry; Part switching and review actions remain locked. UI regressions cover a failure before completion, reload recovery and exact argument reuse. Backend comparison publication continues its deterministic Grist recovery path.
3. **Baseline validation recovery:** the panel explains missing applicability, completeness, source-readability and append confirmations and blocks incomplete submissions. Server errors carry structured retry dispositions through `ApiError`. Only definite pre-write validation failures unlock correction; timeouts and uncertain/partial publications keep the exact attempt. A reload test confirms that an unresolved baseline request can resume with the same key and payload while competing actions stay blocked. If Grist reports partial work but this browser has no saved request identity, the UI blocks a second publication; the backend refuses changed evidence on a same-key partial request and blocks competing request keys until the original is recovered.
4. **Current decision evidence:** every decision action now checks current workbook path/identity/hash, association key/version, saved mapping version/policy, the complete assigned source-group fingerprint set, source-family completeness and the selected Part's active/published identity. The accepted baseline revision pointer is also checked. Baseline-only deletions use the current workbook hash, mapping version and complete-family evidence even though the difference has no incoming row. Tests change hash, association and mapping for all four actions and confirm no decision or proposal is written.
5. **Decision attempt identity:** the UI persists comparison, action, difference keys, exact body (reason/match/replacement/context) and key through reload, locks conflicting edits, and shows saving/uncertain/retry/recovered states. The server request fingerprint now includes the complete API payload as well as the normalized decision fields, so the same key with a changed path or other body field conflicts. Exact response-loss recovery tests cover keep, match, proposal and replacement and assert there are no duplicate business rows.

### Verification performed for this follow-up

- Full project Python suite in `.venv`: 232 tests passed, 1 skipped. The focused Part baseline suite: 23 tests passed; retry-disposition web tests: 3 passed.
- Full Vitest suite: 53 tests passed across 12 files. Standalone mapped-files checks: 2 passed.
- TypeScript validation, production Vite build, Python byte-compilation and `git diff --check` passed.
- An isolated browser fixture with a local mocked API was prepared, but the computer-use helper failed to activate the available browser window on both the initial and one refreshed attempt. The helper was stopped as directed; no user browser tab was navigated. Automated UI coverage is the available verification for this turn.
- No real or disposable Grist writes were performed for these fixes. Backend write/retry assertions use `MemoryGrist`; they are not represented as real Grist persistence evidence. The new schema requirement is documented below and has not been applied to production.

### Historical evidence handling

No historical Grist row was rewritten. To identify possible records from the former manual-match defect, review `PartWorkbookDecision` rows whose `Action` is `match_correspondence`; follow each linked `PartRequirementDifference` and its `ResolvedFromDifference` to compare serialized `IncomingValues.physical` against `FieldDifferences`. Flag records that show unchanged normalized fields as null or as differences. Preserve their original request, observation and decision evidence. Recompare from verified workbook/mapping context and append a separately attributable correction only after owner review; do not edit prior evidence in place. No such production records were queried or remediated during this follow-up.

The full product remains open for the project's existing CR and Summary-authority gates. The PR stays open and this follow-up does not authorize deployment or schema application.

## Integration verification completion — 9 October 2026

The recommended disposable integration and isolated-browser work above is now complete. It was performed against native Grist copies of the configured Safari Manufacturing document, never the configured production document. The clean v12 copy received only `PartWorkbookComparison.MappingVersion` as Numeric; the subsequent plan was empty, and the 64 copied tables retained all 2,493 original records and their fingerprints. Production was independently rechecked by explicit document ID and still has precisely that one pending additive column, with no pending type updates or table creation.

Real Grist publication/readback covered baseline, initial mapping without a reason, normalized requirements and workbook provenance, comparison, manual correspondence resolution, all four decision actions, replacement mapping draft, stale evidence, baseline-only deletion, incomplete baseline rejection, malformed evidence, and exact retries after controlled response loss. Fresh Grist clients/registries saw no duplicate baseline, comparison, mapping, decision or proposal rows. Same-key changed payloads conflicted. Existing accepted Rev A requirements stayed unchanged; proposals stayed pending CR and replacement selection remained a draft until a separate mapping save. Legacy comparison rows missing `MappingVersion` require recomparison and were not backfilled from current state.

The browser was run against the local app pointed at the disposable Grist document. At desktop and narrow widths it confirmed reason-free initial mapping, adjacent reasons for replacement/clear, retained collapsed drafts through navigation/reload, baseline-completeness blocking and correction guidance, Part and accepted-requirement summaries, and decision retry recovery across reload. Response loss reproduced a stuck busy state because the browser fetch could stay pending after the connection reset. A 30-second abort now covers a request that truly stalls, while incomplete response bodies are reported as uncertain immediately; either keeps the request and exposes a retry using the same body and key. The browser dropped a real comparison response after Grist committed, displayed an enabled Retry comparison control, and recovered the same row after the exact retry. Fresh Grist readback confirmed one row for that request key.

The configured production document was not migrated, and no production Part/mapping/baseline/snapshot row was written. The PR is open and unmerged; application rollout remains pending. The duplicate exact-name documents mean the standard name-based setup command refuses target selection, so the runbook uses the explicitly configured ID and separate metadata validation. See [the rollout runbook](PART_BASELINE_V12_PRODUCTION_ROLLOUT_RUNBOOK.md).
