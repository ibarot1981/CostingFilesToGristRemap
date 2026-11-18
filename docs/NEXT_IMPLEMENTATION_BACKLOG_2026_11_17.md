# Next implementation backlog — 17 November 2026

Planning consolidation requested by the owner; no implementation dispatch, new PR or production writes are authorized by this document alone. Existing Part mapping/manufacturing baselines, Model Code configuration foundations, Live Cost and explicit immutable Cost Snapshots are implemented. Extend and integrate these services rather than create parallel identities or calculators.

Requirements sources: SPARES_MASTER_AND_MODEL_CODE_ISSUE_SLIPS_2026_11_17.md and recorded owner answers; MANUFACTURING_ERP_REQUIREMENTS.md (including later FLOW updates); LIVE_COST_AND_SNAPSHOT_REQUIREMENTS.md. This backlog distinguishes agreed behavior from recommended delivery choices.

## B1 — Spares master and reviewed source migration

- Shared purchased/in-house spares master with create/edit, description, category, units, active state, provenance and legacy Item Code lookup. Classify explicitly; do not infer purchasing type from code text.
- Read selected ODS SparesDB and its Template DB/Spares List - Master.ods lineage; import existing spare definitions from Spares Detail/Spares Summary through a preview that exposes duplicate/conflicting/missing identities. No source-file or Costing-New writes.
- Recommend a neutral permanent code such as SM-S-000001; retain old Item Codes as aliases with provenance. This exact numbering format is a proposal, not yet an owner-approved allocator rule.
- Removing a spare from a configuration removes that reference; preserve historical references when retiring a used master item. Record audit and recover imports without duplicate identities.
- Exit: imported/new purchased and in-house codes can be searched, maintained and reloaded with legacy identity and unresolved-source status visible. Do not claim an in-house code is fully costed until its composition/rates resolve.

## B2 — Spare composition and automatic dependency propagation

- Build a spare from whole canonical Parts, explicitly selected canonical manufacturing lines, existing spare codes and combinations, with quantities/units and ODS quantity-override semantics.
- Whole-Part membership and explicit selected-line membership are distinct. Preserve stable identities through row moves or Part reassignment; flag missing/ambiguous/removed dependencies.
- Saved linked Part/mapping changes propagate automatically through dependent CURRENT ACCEPTED spare definitions, as explicitly chosen by the owner; create auditable accepted versions without requiring manual approval of every affected spare. Earlier accepted versions and saved monetary snapshots remain unchanged. Underlying Part engineering controls remain intact.
- Show cost/weight breakdown and where-used effects; reject cycles and prevent charging a whole Part and its included occurrence again.
- Exit: a nested spare recalculates correctly after a relevant saved change, updates current accepted spare versions automatically, retains history and recovers without duplicate publication.

## B3 — Consistent material, purchased-item and process-rate resolution

- Extend existing read-only Costing-New material integration using reviewed aliases and the existing MaterialLatestRate/default selection policy; purchased items use their explicit eligible purchase-rate policy. Keep their evidence/bases distinct.
- Proposed efficiency design: batch source reads, share one coherent input set within a calculation, explicit refresh/timestamps and bounded invalidation if caching is introduced. Detect conflicting/changing inputs; missing rates are unavailable, never silently zero.
- Provide explainable material, purchased-item, Tool Shop/CNC/other applicable process rates and unit conversions. Reuse existing commercial-rate services and frozen snapshot evidence.
- A persistent local rate mirror or scheduled synchronization is not yet required; add only after measured need and an agreed policy. No direct Google Sheets integration is included.
- Exit: all materials/items/processes consumed by the pilot resolve to valid attributable rates, with measured read performance and reproducible calculation inputs.

## B4 — Per-spare selling-price policies

- Each item chooses percentage above cost, amount above cost, fixed selling amount, or another spare code's selling price with percentage OR fixed-amount adjustment. Zero adjustment means exact match; reject selling-price dependency cycles.
- Default all new policies to40% above cost: SP=cost*1.40. Label markup distinctly from margin; a separate margin-on-SP method has not been requested explicitly.
- Recalculate cost-based/linked current prices as dependencies change; fixed prices stay fixed. Preserve effective policy/price history and imported selling-price evidence. Selling price does not become component cost by default.
- Exit: all four methods and linked increases/decreases work, nested dependencies update, unavailable inputs remain visible, and prior saved prices remain frozen. Tax basis, rounding and any additional margin method must be resolved using explicit existing policy or narrow clarification before this package is finalized.

## B5 — Model Code Part configuration and ODS Issue Slips

- Extend existing versioned Model Code configuration with explicit Part/assembly selections, quantities, base/add/deduct/substitution rules and source provenance. Association with a workbook or baseline does not itself create a configuration.
- List Issue Slip numbers/descriptions exactly from the selected ODS. The user selects relevant slip numbers for the selected Product Model Code, expands their entries and includes/excludes entries independently for that configuration.
- Add/remove spare-code references and preserve ODS source quantities, units, NO flags, optional groups, legacy Item Codes and item-line identity. Any override is explicit; do not silently deduplicate separate legitimate occurrences of the same item across slips.
- Preserve source department versus issue route: a manufactured item routed through Stores is costed once. Keep exclusions/configuration changes separate from shared slip/master definitions and from other Model Codes.
- Exit: two codes using the same workbook can have different Parts, slip selections and item overrides, reload independently and produce explainable contributions without changing the source ODS.

## B6 — Model Code labour, paint and packaging

- Import/reconcile relevant Labour - Paint - Packing evidence and assign appropriate lines, consumption/quantity, rates and applicability to each Model Code configuration. Include variant differences and provenance.
- Account for process/labour already included in Part or spare costs; do not add it twice. Preserve freight/handling/recovery policies as explicit categories where applicable, not unexplained blanket percentages.
- Exit: pilot finishing/packing/labour costs are attributable, configurable per code and have no missing or duplicate contributions.

## B7 — Full pilot costing, snapshots and ODS reconciliation

- Integrate B1–B6 into existing Live Cost and explicit Cost Snapshot services. Provide Parts, spares, Stores, material/process/labour/paint/packaging totals and drill-down with rate/configuration evidence.
- Use one selected Model Code from the completed S1KHF workbook first. Compare equivalent quantities and rate bases with a verified refreshed disposable ODS copy, explain differences and resolve missing structural inputs. A price variance alone does not invalidate completed mapping or processing.
- Preserve exact reviewed input generations, no-write Live Cost and immutable saved monetary facts; repeat calculation/save/reload/retry tests. Surface incomplete configuration/cost instead of a misleading complete total.
- Exit: owner accepts the pilot breakdown and all required input domains; independent review verifies persistence, recovery, source safety and relevant UI/performance behavior.

## B8 — Explicit per-code authority cutover

- Record authority, scope, approved configuration/version/rate basis, effective decision time, actor/reason and rollback route. Eligible codes can cut over independently; whole-model cutover requires every active code to qualify.
- After explicit acceptance, ordinary costing/cut-list reads use stored Safari definitions without reparsing ODS; ODS remains provenance and an explicit reconciliation/export source.
- Costing-New may remain the upstream material-price authority while Safari owns configuration/costing; document the domain boundary and do not silently mix unreviewed source bases.
- Exit: auditable authority and rollback behavior work for one pilot code; compare normal read latency with the ODS route. No automatic authority switch merely because import/mapping/baseline succeeded.

## Recommended implementation sequence

All B1–B8 packages also require the cross-cutting B9 persistence and B10 cost-aging requirements below; these are not optional work postponed until after cutover.

1. B1 identity/import foundation.
2. B2 spare composition and dependency behavior; use existing rate services initially and complete B3 before claiming complete live costs.
3. B3 and B4 rate/pricing completion.
4. B5 code configuration and Issue Slip editor.
5. B6 finishing/labour/packaging.
6. B7 full pilot acceptance, then separately approved B8 cutover.

Use bounded implementation/review packages through the established Luna/Sol workflow; one active task at a time, acceptance criteria agreed per package. Definition of done for a package: committed source, appropriate automated checks, actual isolated browser/persistence/recovery evidence, independent acceptance and explicit owner merge/deployment decision. B1 is the recommended first implementation package; assignment has not been made by this planning step.

## B9 — Grist is the authoritative business store (owner confirmed 17 November 2026)

- Persist all saved spare identities/legacy aliases/types/compositions/accepted versions, Model Code configurations, Issue Slip selections/entry overrides, pricing rules/price history, source/rate evidence, finishing/labour/packaging requirements, snapshots and authority decisions in the validated Safari Manufacturing Grist document. Costing-New remains the read-only upstream rate source where explicitly designated; retain its exact source identity/date/version evidence in Safari.
- Existing production Part mappings are already stored in Grist PartMappingReview. Existing accepted manufacturing baseline and family records, Part revision links and normalized line requirements/provenance are already Grist records. Local SQLite is a coordination/reservation/recovery journal, not a business-data fallback; browser storage may retain unsaved drafts/original request payloads for recovery but is never the authority for saved business facts.
- After saving, a fresh browser/device/session must retrieve the same business facts from the service/Grist without depending on the old browser storage. Preserve the existing supported writer/coordinator deployment boundary; this does not authorize independent SQLite allocators on multiple hosts.
- Grist unavailability must produce an explicit unavailable/pending outcome. Never silently save canonical facts only to browser storage or substitute local business data. Verify durable publication/readback and idempotent recovery in each package.

## B10 — Per-input cost aging and transparent aggregate age (owner requested 17 November 2026)

- Every consumed material, purchased spare/item and applicable process/labour/paint/packaging rate must expose its authoritative cost/rate last-updated or entry date and aging in days. Record original source record/version, entry/update date and any distinct transaction/effective date; do not conflate these with application fetch/import/calculation timestamps.
- Owner example: a purchased cost entered30days ago shows30days of aging. A manufactured Part must expose the aging of each material/rate it consumes, not merely its Part creation date or most recent calculation date.
- Proposed day convention: calendar-date difference in the configured business timezone (Asia/Kolkata initially), zero for today. Missing dates show unknown; future dates show an explicit source-date anomaly. Do not invent a recent date or silently clamp an anomaly to fresh.
- Refreshing/reimporting an unchanged source record or recalculating a Part/spare/Model Code must not reset cost aging. A newly recorded authoritative cost/rate event can advance the cost date even if its numeric price is unchanged; retain its evidence. When only an effective/purchase date is available, label that basis explicitly rather than claim a known entry/update date.
- Show per-input dates/ages in cost breakdowns, spare/Part detail and Model Code Live Cost. For composites with several inputs, retain each input's age; recommend an aggregate "oldest input cost: N days" plus an unknown-date indicator, separately from "last recalculated". Do not use a recent recalculation to hide older underlying inputs or misrepresent an aggregate as one unique source cost date.
- Selling-price history has its own price/policy date. Automatically recomputed selling prices must continue to expose the age of underlying cost inputs, while fixed selling-price dates remain independent.
- Save source cost dates and age-at-calculation in immutable Cost Snapshots so historical evidence stays frozen. Any age evaluated today for historical source facts is a separate explicitly labelled view; it must not rewrite the captured snapshot age.
- Persist authoritative date/evidence/history in Grist; calculate current day differences consistently on the service. A ticking stored age value is not the authority. Configurable stale-age thresholds, warning colours or refresh reminders are not yet specified and must not block costing merely because an input is old.
- Exit: purchased cost and manufactured material age match source dates; day-boundary, missing/future date, nested oldest-input, reload/persistence and frozen-snapshot behavior are independently verified. Repeated fetch/recalculation does not make an old cost appear new.
