# Review of Grist Parts completion — 7 October 2026

Reviewed commit: `9338f39` (`9338f394c355c36b4964bc312adbe728573e76fe`).
Branch: `codex/part-identity-foundation`.
PR: https://github.com/ibarot1981/CostingFilesToGristRemap/pull/3

## Verdict

The earlier SQLite-only persistence gap is addressed: managed Part business records now have Grist write/read paths, and the v8 schema is deployed in Safari Manufacturing. The collapsible explorer, composition, optional drawing links, vendor/specification capture and latest-purchase resolver are present. However, five independently reproduced defects prevent completion approval. Passing baseline tests do not exercise these failure cases.

This review used read-only live Grist requests and isolated temporary registries with the repository's `MemoryGrist` test double. No production Part, purchase, mapping or schema was modified. The application code was not changed by this review.

## 1. P1 — Exact purchase retries fail against normal Grist DateTime values

Location: `app/grist_parts.py:1009–1026`, `capture_purchase`.

Grist stores `TransactionAt` as epoch seconds through `grist_datetime`, but duplicate detection reads it with `datetime.fromisoformat(str(value))`. That rejects the persisted numeric timestamp. The duplicate transaction check then raises `PART_PURCHASE_DUPLICATE_CONFLICT` before the request-key replay check can return the saved result.

Reproduction: create a Part, purchase specification, vendor and reviewed SKU mapping. Call `capture_purchase` with a posted invoice and request key `buy1`, then repeat the identical call. The first call succeeds; the second raises `PART_PURCHASE_DUPLICATE_CONFLICT`. This also breaks the UI's exact retry after a saved purchase response is lost.

Required correction: normalize persisted DateTime cells with the existing Grist conversion helpers and establish request-key replay/conflict handling before transaction-identity deduplication. Test both the same request and a new request importing identical evidence, using real epoch-second cells and response loss after commit.

## 2. P1 — A different rename can publish another request's pending metadata

Location: `app/grist_parts.py:478–503`, `update_metadata` and `_ensure_keyed`.

Pending metadata is addressed only by Part UUID plus version. `_ensure_keyed` returns an existing row without validating that its request key/fingerprint and contents belong to the current operation. The stale-version exception also permits continuation when any pending row for that version exists.

Reproduction: from metadata v1, request A renames the Part to `S1K — Frame A — Standard`. Simulate response loss after Grist commits metadata v2 but before the master pointer update, using `MemoryGrist.lose_next_metadata_response`. Submit different request B, still based on v1, renaming to `S1K — Frame B — Standard`. B reports success and its request journal becomes `published`, while:

- `ProductPart.DisplayName` is Frame B;
- `CurrentMetadataVersion` points to A's v2 with Frame A;
- the application returns Frame A from that metadata reference.

Required correction: bind pending publication to its originating operation, reject conflicting requests for the same version and verify the complete master/metadata/alias result before publication success. Preserve exact recovery for A. Add tests for a second request during partial publication, and for retries after later metadata changes.

## 3. P1 — Finalizing an assembly does not freeze its child definitions

Owner clarification: child Parts are independent reusable entities. Their current names/scope/nonphysical metadata must remain editable and automatically appear in all current consuming assembly views without parent reconfiguration or re-finalization. This finding applies only to changes to the referenced child's physical definition. Historical evidence retains its recorded name/version; current display can show updated metadata.

Location: `app/grist_parts.py:647–724`, `add_component` and `finalize_revision`.

Parent finalization checks only the parent's draft state. It accepts references to child Rev A records that remain draft and mutable. Hashing a child revision ID does not freeze that child's physical definition.

Reproduction: create Parent, Child and Leaf at A. Link Parent → Child, finalize Parent, then add Child → Leaf. The child edit succeeds while Parent remains finalized. Consequently, the expanded physical definition of a finalized assembly changes without a new engineering revision or CR.

Required correction: require an immutable/finalized child baseline before parent finalization, recursively validate the definition closure or pin an equivalent immutable snapshot. Do not require child finalization just to draft a parent composition, but do enforce it before establishing the immutable parent baseline. Test nested/shared children and refusal of post-finalization indirect changes.

The purchase specification also needs review as part of the physical baseline: `create_purchase_specification` does not check draft state, and finalization's definition hash excludes manufacturer/specification identity. Separate physical purchase specification from mutable commercial evidence and make the finalization boundary explicit.

## 4. P1 — Reusing a cost-run selection resolves a new purchase instead of its saved evidence

**Superseding owner decision, 7 October:** the reproduction below remains evidence of the old ambiguity, but its original cost-run-based remedy is replaced by `LIVE_COST_AND_SNAPSHOT_REQUIREMENTS.md`. Live calculations always resolve applicable current inputs without writing history. Only explicit Save Cost Snapshot persists normalized immutable structure, quantities, rates and evidence; opening an old snapshot reads those frozen records. Incorporate this architecture before fixing this finding. Do not make a persisted run/selection result the owner of ordinary live viewing.

Location: `app/grist_parts.py:1047–1084`, `purchase_detail`.

Evidence keys include the newly resolved purchase and rate. The operation always resolves current history first; it does not look up an existing immutable result by cost run and configuration selection. A newer purchase therefore creates another evidence row for the same logical costing selection and returns the newer rate. The old row is retained, but there is no stable result for that run/selection.

Reproduction: purchase at INR 100, save evidence for `run1`/`sel1`, then capture a later purchase at INR 120. Call the evidence operation again with the same run/selection. It returns 120 and creates a second evidence row instead of returning the existing 100 result.

Required correction under the updated scope: replace side-effectful historical evidence generation with pure live resolution and explicit normalized snapshot publication. Snapshot Save/retry pins the reviewed quantities, metadata, rate/conversion/evidence and total; a completed snapshot never resolves a new rate. Test later purchases and backdated evidence, and prove repeated live viewing creates no history/evidence rows. The new continuation prompt includes minimum explicit configuration/live evaluation, snapshot UI, reminder policy and comparison implementation; the former blanket costing-engine deferral no longer excludes that required work. Automatic Summary import and CR approval remain separate gates.

## 5. P2 — Draft or voided reversals invalidate posted purchase evidence

Location: `app/purchased_parts.py:137–145`, reversal/supersession processing.

The resolver filters eligible purchase status, but applies reversal and supersession references from all dated records without checking whether those reversing operations are posted/completed. This allows an unposted return or correction to change the rate.

Reproduction: one posted purchase gives `status=available`. Add a later `RecordType=return`, `Status=draft`, `ReversesRecord` pointing to it. Resolution becomes `unavailable`. The draft return should have no effect on posted costing evidence.

Required correction: define and enforce eligible effective statuses for reversal/correction operations, validate their references and preserve as-of behavior. Test draft, voided, posted and future-dated reversals independently.

## Verified deployment and checks

The configured document identity/workspace was validated as Safari Manufacturing, separate from the legacy target. A read-only schema plan for `safari-parts-grist-2026-10-07.v8` has zero new tables, missing columns or type updates: the declared schema is applied.

Read-only production inventory:

- `ProductPart`: one legacy record, zero managed UUID Parts.
- `PartRevision`: one legacy record.
- `LineMaster`: 313 rows across all statuses, preserved.
- `PartRegistryCoordinator`: one row.
- Part metadata, aliases, shortcodes, requests, line bridges, components, drawings, vendors, purchase specifications, vendor mappings, purchases, cost evidence and mapping reviews: zero rows.

Canonical Grist persistence is therefore implemented and deployed, but there are no real managed Part/purchase examples in production to inspect. Isolated test evidence establishes the current write paths; it is not a claim that business records have already been populated or migrated.

Checks rerun:

- Python: 174 tests run, 173 passed, one platform skip.
- Vitest: 25 tests passed across eight files.
- Mapped-file view-model checks: two passed.
- TypeScript type check and production build: passed.
- `git diff --check`: passed before and after this documentation-only review.

The five failures above were reproduced separately using current service/resolver code; they are absent from the passing baseline suite.

## Remaining scope and delivery status

Legacy identity/numeric-revision reconciliation, actual per-code configuration records, automatic Summary import, complete cost-engine consumption and the CR workflow remain explicitly deferred. Their absence must remain visible; a Part source mapping is not a configuration selection or completed costing workflow.

PR #3 was open at review time and references the reviewed commit. No merge or production mutation was performed. Correct the five findings, add behavior-based regression tests, then repeat the affected checks and targeted review before claiming the completion gate has passed.

## Resolution follow-up — 8 October 2026

The continuation in PR #3 addresses the five reviewed defects and implements the accepted D070 replacement:

- Purchase capture normalizes Grist epoch timestamps before transaction comparison, validates same-request fingerprint before dedupe and distinguishes exact retries, conflicting retries and cross-key transaction duplicates.
- Metadata publication requires the exact pending request/fingerprint, checks master/current metadata/alias agreement, resumes each partial boundary and does not roll back later published names on old retries.
- Finalized Part revisions pin a recursive physical closure. A parent cannot finalize while a child baseline is draft; later child physical edits are blocked. Current child metadata still propagates without parent reconfiguration. Purchase specs are part of physical closure; commercial purchase changes remain separate.
- The legacy purchase-rate-evidence endpoint now returns 410. GET rate and Live Cost paths write no evidence.
- Only posted/completed effective purchase reversals/corrections invalidate an eligible actual; drafts, voids and future reversals do not.

Schema `safari-cost-snapshots-grist-2026-10-08.v9` is deployed. Ten configuration/rate/snapshot/policy/publication tables were created and `PartComponentRevision.SourcingRoute` was added after a native backup passed SQLite integrity and SHA-256 checks. No types changed and the reviewed production record counts remained unchanged. Real persistence/recovery was exercised in a disposable Grist document, which was moved to Trash. Live Cost added no history/evidence; same-key recovery completed frozen INR 200 after a newer INR 120/unit purchase, and snapshot comparison attributed INR 40 to rate while historical detail continued to show INR 100/unit.

Current full-suite totals and browser-check details are recorded in the PR #3 description and `IMPLEMENTATION_ROADMAP.md`. Direct Grist users with table-write access can edit records outside the app; historical reads now detect changed normalized snapshot rows through a checksum and fail closed. Summary import/authority integration, CR approval beyond A and reviewed legacy identity/numeric-revision migration remain open.

## Intended-sharing cascade follow-up — 8 October 2026

The Part cascade and intended-sharing work is implemented in schema v10 and documented in `PART_INTENDED_SHARING_IMPLEMENTATION.md`. One Product, optional single Model and multiple explicitly selected codes are independent of the single naming anchor. Normalized Grist pair rows, version/fingerprint state, immutable add/remove events and request-payload recovery preserve one Part identity without configuring codes or creating cost history.

`GET /api/parts/{part_id}` now separates advisory intended codes from actual configuration usage. Actual usage reads direct selections from current published configuration revisions; indirect/component use is still not expanded and is stated in the UI. Real Grist persistence and isolated browser creation/removal were verified; new v10 tables remain empty in production. No Part/business fixtures were introduced during migration. See the current deployment, backup hash and test evidence in `PART_INTENDED_SHARING_IMPLEMENTATION.md`.
