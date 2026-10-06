# Delivery Milestone 3 / Phase 1 package 1.1 — pilot evidence

Checked 2 October 2026. Milestone 2 was committed as `476ad3a` on `main` and
pushed to `origin/main` before this branch was created. The Milestone 3 branch
is `codex/milestone-3-normalized-s1khf`.

## Source and baseline

The selected local workbook is `S1KHF/Local/Safari 1000 HF Local V 4.2.ods`.
Its SHA-256 was `2a977a02191e5ae67e9d21b614d5a17f49974e30a423c45360d6ac5516c6b0ac`
during the first Milestone 3 inventory, then changed to
`d32f66eee6df2bae6b5fb304963e050d356eb6d460fdbe292b4b6d73b16c774b`
by 2 October. A before/after hash around the 2 October parse was unchanged.
The implementation made no source ODS write.

The archived Milestone 2 report records an earlier product hash
`03ca141083439c273951b39795387f26a72c722f04759c09c309a5ed363b1a80`
and active MCL value `11056.40170916941886973910`. The current workbook's
active MCL cached line sum is `13410.3535336550805`, a difference of
`2353.95182448566163026090` across source revisions. Its plain-SUM MCL
summary includes `75.4392989586195` from two historical rows; that is the
named Milestone 2 historical-cache exception. This difference is **not**
absorbed into the ±₹100 business tolerance. The accepted Safari v4 snapshot
was subsequently read from the validated Safari Manufacturing document in
the Work workspace. Its selected workbook hash is the first Milestone 3
inventory hash (`2a977a…`). The existing ignored `.env` contained a Grist API
key but lacked the separate Safari document/workspace selectors; those were
rediscovered read-only and restored to the ignored local configuration.

The accepted snapshot has 58 MCL rows, 42 active, and active cached cost
`11056.4017091694268`. The current workbook has the same 58/42 rows and
identical MCL physical signatures. Its active cached line total is
`13410.3535336550805`; historical cached lines contribute
`75.4392989585977`. Their sum reconstructs the current workbook C7 cache
`13485.7928326137` within `2.18E-11` numeric representation noise. Across
all five sheets, row counts and previously covered non-cost fields match the
accepted snapshot. The new parser additionally covers CNC weights, Store
issue-slip fields, and Tool Shop weights/costs; those fields have no earlier
accepted comparator and are reported separately. Thus the changed workbook
is confirmed as cost drift for **previously covered fields**, while the
accepted v4 snapshot remains immutable.

The [line-by-line cost-drift register](MILESTONE_3_COST_DRIFT_LINES.md)
lists every changed cached line value: 23 active MCL rows and 51 CNC rows
(39 active, 12 historical). No Tool Shop, Stores, or Labour/Paint/Packing
cached line values changed. The MCL active delta is
`2353.9518244856537`; CNC active delta is `562.6900`. The register uses exact
row/identity matches, not a tolerance or inferred line merge.

| Configured sheet | Active | Historical | Total |
|---|---:|---:|---:|
| 5. Material Cut List Price | 42 | 16 | 58 |
| Tool Shop Items | 16 | 7 | 23 |
| Stores and Consumables List | 168 | 0 | 168 |
| CNC Cut List | 42 | 14 | 56 |
| Labour - Paint - Packing | 8 | 0 | 8 |
| **Total** | **276** | **37** | **313** |

The projection and live Safari import create 313 row observations, 313 mappings, 313 line
masters/revisions/details, 48 exact or YAML-mapped Materials, 105 source
PurchaseItem candidates, five process operations, five source WorkCenter
candidates, and one explicitly temporary unallocated S1KHF Part/revision.
No PartComponentRevision is inferred. Ten rows have duplicate exact business
composites and remain `ambiguous` with individual unresolved masters and typed
children. Other rows have `proposed_exact` mappings; this is not owner approval.
Current source row counts and cached values reconcile exactly to the projection
by sheet. Live Safari queries return all 313 rows, including 37 historical
rows, with no sheet/status count differences. The accepted snapshot is retained
as the physical baseline; current rate/cost observations use the current
workbook hash and the cost-drift assessment above.

## Scope and known limits

- Schema v5 added 13 new tables with Grist Ref relationships to the validated
  Safari Manufacturing document. The pre-apply diff was 13 created tables,
  zero added columns, and zero updated columns; the post-apply diff is empty.
  No Costing-New schema or data change was applied.
- The CLI `safari-normalized` defaults to a local read-only plan. Its
  `--safari-plan` path compares the accepted physical fields and current MCL
  total with its workbook cache, then prints the live schema/import plan.
  `--apply` requires that plan's digest. Staged writes revalidate Safari,
  the accepted baseline, the current source and dependency hashes, use
  deterministic keys, and resume missing children on retry.
- The inspection UI shows a labelled local projection when no accepted Safari
  baseline is configured. It filters sheet/process/part/material/status,
  pages all rows, and displays source cells/formulas, revision and audit data.
  The API now queries persisted Grist child rows. The live query returned 313
rows, 37 historical rows, six rows for one Material search, and 23 rows for
one Part-evidence search. All 313 queried rows have persisted master, revision,
detail, and audit references. Active cached totals summed from persisted
observations equal the source projection in each of the five sheets exactly
at the stored decimal precision. The labelled local fallback remains available.
- The CLI extraction covers direct ODS YAML material aliases and the pilot
  importer. AlternateSize remains a proposal and option group stays on usage
  lines. The wider package 0.6 comparison fallback and manager rules still
  need extraction and additional golden fixtures.
- Subsequent live workbook revisions are not yet supported end to end. A
  cost-only reimport would reuse the same physical `LineRevision`/`LineDetail`
  key while changing its cached cost, which the immutable Grist writer must
  reject. A newly accepted snapshot can likewise change the temporary
  `PartRevision` snapshot reference. The importer must load prior normalized
  state, retain physical revision identity, append new source/rate observations,
  and create governed revisions only for confirmed physical changes. The live
  no-op retry test covers the **same** source revision, not this later-revision
  path.
- The ten ambiguous rows are persisted as separate unresolved records. Owner
  decisions and a governed way to apply reviewed line/Part mappings have not
  yet been implemented; the inspection UI is read-only.
- Configured process columns have no reliable time fields. The shared
  Labour/Paint/Packing sheet's eight current records use Paint Code/Name,
  Price Per Litre, and Qty Used Litres. Keyword-classified paint activities
  are marked `paint`; other items remain `unclassified`. No labour or packing
  activity is inferred from this source. Store issue-route and Tool Shop
  source department remain separate; no cross-list total is formed that could
  double-count an internally made item issued through Stores.

## Verification

`python -m unittest discover -s tests -q` passes 119 tests (one expected
skip); Python compilation and `npm run build` pass. The 11 Vitest tests pass using the bundled Node 24 runtime;
the system Node 18 cannot start Vitest 4 (`statfsSync` export unavailable).
The earlier local browser walkthrough selected the S1KHF file,
displayed 313 process rows and 10 exceptions, filtered 37 historical rows,
and opened a CNC row's formula/source/audit detail. The live API now reports
`persistence: grist`, 313 rows, no persisted sheet/status count differences,
and confirmed cost drift. The reviewed import created 2,043 records across
12 populated tables; `PartComponentRevision` remains empty. A repeat live
plan proposed zero creates and a repeat apply wrote zero rows. All 313
observations pin the current source hash. Mapping statuses are 303
`proposed_exact` and 10 `ambiguous`; owner approval of those identities remains
open.

Phase 0 remains open: 0.5 pilot issue ownership and owner visual acceptance;
0.6 full shared CLI material/alternate/option mapping extraction and golden
equivalence; 0.7 complete sheet interpretation, Costing-New comparison, and
owner pilot signoff. The two encoding-corrupted active legacy identities and
the Milestone 1 association/supersede walkthrough also remain governed gates.
