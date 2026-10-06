# Safari Manufacturing numbering audit

Audit date: 1 October 2026; implementation status updated 2 October 2026. This is a planning/status audit, not a claim that
pending live Grist actions or owner acceptance have occurred.

## Finding

The roadmap mixed two counters. `0.1`–`0.7` and `1.1`–`1.4` were written as
phase-scoped milestones. The implementation prompts and PRs used consecutive
delivery numbers: Milestone 1 and Milestone 2. These are different units of
work. Calling the next effort “Milestone 3” is correct **as a delivery number**;
calling it “Phase 1, Milestone 1.1” is also correct **as a work-package number**.
The roadmap now uses *work package* for decimals and *Delivery Milestone* for
whole numbers. Existing filenames are kept to preserve history.

| Phase package | Delivery history | Evidence-based status at this audit |
|---|---|---|
| 0.1 Governance/source boundaries | Milestone 1 foundation | Rules and safety guards documented/implemented; owner acceptance of every Phase 0 gate is not established |
| 0.2 Safari document/schema | Milestone 1 | Safari document and foundation schema validated; later v4 applied per Milestone 2 evidence |
| 0.3 Canonical identity | Milestone 1 | Catalog imported; two encoding-corrupted active legacy rows remain governed cleanup candidates |
| 0.4 Explorer/associations | Milestone 1 / PR 1 | Pilot file/code journey merged; owner UI acceptance and live supersede walkthrough remain open |
| 0.5 Mapped Files/reconciliation | Milestone 2 carried governance forward | Workflow and schema exist; pilot issue ownership/resolution and owner visual acceptance are not recorded as complete |
| 0.6 Shared CLI material mapping | Partial in Milestone 3 | Direct YAML alias resolution is shared by CLI verification and importer; wider alternate-size/option comparison behavior and full golden coverage remain open |
| 0.7 S1KHF read-only pilot | Milestone 2 advanced much of it | Semantic snapshot/parity pilot is recorded complete; separate Phase 0 owner signoff, full sheet interpretation, and Costing-New comparison are not established |
| 1.1 Normalized part/process rows | Milestone 3, in progress | v5 schema and 2,043 rows applied to Safari; 313 process lines are queryable and repeat import is a no-op; ten identities and owner gates remain open |
| 1.2 Dependency/approved rates | Later | Planned |
| 1.3 Explainable configuration costing | Later | Planned |
| 1.4 Broader parity | Later | Planned |

## Current position

- Milestone 1 is merged at PR 1 and received a conditional post-merge pass.
- The agreed S1KHF **Milestone 2 scope** was committed as `476ad3a` and
  synced to `origin/main` on 1 October 2026. Its accepted live baseline is
  recorded in the roadmap and independently read from the validated Safari
  document during Milestone 3.
- Phase 1 has begun. Phase 0 remains open against its formal exit gate. Phase
  labels describe work streams, not a promise that every earlier gate is
  closed before later learning starts.
- The current delivery is **Milestone 3**, implementing **Phase 1 work package
  1.1**. It carries the necessary Phase 0 work-package 0.6 mapping extraction
  and 0.7 pilot review into its entry/acceptance plan. It must report the
  remaining 0.5–0.7 gate items explicitly, even if Milestone 3 implementation
  succeeds.

## Immediate sequence

1. Preserve/review the current Milestone 2 working tree and establish a
   reviewable baseline before branching or changing the Safari schema.
2. Inventory the accepted S1KHF snapshot, existing Costing-New/CLI mapping
   knowledge, and every configured process-sheet contract. Resolve only the
   mappings necessary for the S1KHF pilot; leave uncertain identities in a
   reviewed queue.
3. Plan normalized schema and import with an exact Safari-target dry run.
4. Implement Milestone 3 in slices: source observations, canonical part and
   line identity, typed revisions/details, reviewed mappings, row audit, and
   query/reconciliation views.
5. Compare normalized rows to the accepted semantic snapshot and source ODS;
   test idempotent retry, source safety, CLI equivalence, and real pilot reads.
6. Record Milestone 3 acceptance and separately close or defer each Phase 0
   exit item with owner evidence.

The detailed implementation brief is `MILESTONE_3_IMPLEMENTATION_PROMPT.md`.
