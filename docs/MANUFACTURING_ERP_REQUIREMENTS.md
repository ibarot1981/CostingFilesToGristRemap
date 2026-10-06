# Safari Manufacturing ERP Frontend — Requirements Baseline

Status: approved working baseline for Phase 0  
Updated: 28 September 2026
Source of truth during Milestone 2: product costing ODS files, `MaterialCostDB.ods` / `RawSteel`, and the `Spares List - Master.ods` / `SteelRateLog` snapshot

## 1. Product outcome

Build a purpose-designed manufacturing frontend named **Safari Manufacturing**. It must provide real-time product costing, rapid product configuration, manufacturing configuration, product-part maintenance, and spare-parts configuration without creating another independently maintained data island.

The application will initially use a new Grist document named **Safari Manufacturing** as its structured operational store. Existing ODS costing files remain the costing source of truth until controlled parity and cutover gates are passed. The existing `Costing-New` Grist document continues operating during the transition and must not be structurally disrupted.

The architecture must isolate the UI and business rules from Grist so that a future migration to PostgreSQL does not require rewriting the frontend or costing engine.

## 2. Confirmed business terminology

### 2.1 Product hierarchy

The canonical identity hierarchy is:

`Product → Product Model Number → Product Model Code → commercial description`

Examples and exact display names come from `Product-ProductModelNo-ModelCode.ods`. Imports preserve the supplied text. Alternate spellings and old codes are aliases, not silent replacements.

- **Product** is the commercial family, such as Mini Crane.
- **Product Model Number** is the engineering/costing family, such as JKM500.
- **Product Model Code** is a sellable costing configuration, such as JKM500-WR.
- **Product Part** is a reusable manufactured or purchased assembly/component that may be shared across products and models.

### 2.2 Product Model Codes

A code represents a configuration of product parts and options. Code fragments may carry meaning—such as `S1K`, `HF`, `L`, `E`, `P`, `K`, and `M`—but the system must not assume one universal parser until the grammar is explicitly approved for each product family.

The authoritative association is therefore an explicit configuration record, not a cost inferred only from the characters in a code.

### 2.3 Three separate configurations

The system must not collapse these concepts:

1. **Costing configuration** — what Sales prices and sells; it selects a base and explicit additions/deductions.
2. **Manufacturing configuration** — how the product is made, routed, batched, and issued. Multiple costing codes may use distinct manufacturing variants even when their price differences are small.
3. **Spare-parts configuration** — serviceable parts and assemblies, including legacy applicability and sub-part composition.

Each configuration is versioned and has effective dates. Relationships between them are explicit.

### 2.4 Legacy Bush models

Bush-type configurations are not active sellable models. They remain available only for legacy spare-parts identification and applicability.

### 2.5 GC duplicate codes

The duplicate GC variants are distinguished by their 7.5 and 10 ratings. The approved persisted codes are `GCMC-7.5`, `GCMC-10`, `GCMC18-7.5`, and `GCMC18-10`. The original supplied duplicate values remain as source aliases/provenance.

## 3. Source-of-truth and transition boundaries

| Data domain | Current authority | Interim/Milestone 2 treatment | Intended authority after controlled cutover |
|---|---|---|---|
| Product/model/code list | Supplied ODS catalog | Import to Safari Manufacturing with provenance; reconcile duplicates | Safari Manufacturing after approval |
| Product costing and formulas | Explorer-selected workbook for the assigned Model Codes; other workbooks may be sample/scenario variants | Read-only ingest, explain, compare, and snapshot the selected file; other files do not override its default unless explicitly selected | Safari Manufacturing costing engine; ODS remains an export/sync target during transition |
| Interim steel material names, weight factors, and default rates | `C:\Irshad\Safari\DRWGD\Products Costing\Template DB\MaterialCostDB.ods` / `RawSteel` | Read-only extract; preserve exact names, source rows/cells, formulas, cached values, and source revision | Reconcile into Safari after parity; Costing-New mapping is deferred |
| Interim steel-rate observations used by product costing | `C:\Irshad\Safari\DRWGD\Products Costing\Template DB\Spares List - Master.ods` / `SteelRateLog` | Read-only dump snapshot; preserve original row order, formulas, cached values, and date text; no direct Google Sheets read | Governed rate observations/approval workflow after ODS parity |
| Model-specific material composition and resolved workbook inputs | Mapped product costing ODS | Read-only semantic extraction and parity against the workbook's own cached revision | Safari Manufacturing after controlled parity/cutover |
| Canonical material mapping and future rate authority | `Costing-New` Grist `MasterMaterial` | Deferred during Milestone 2; do not read or write it for interim parity | Import/map after ODS parity and owner approval |
| Upstream rate-log origin | Google Sheets range imported into the ODS dump | Record lineage from the dump formula; no fixed refresh cadence is promised; do not connect directly in Milestone 2 | Scripted dump acquisition is a future automation task; freshness threshold can be set with that workflow |
| New registry, mappings, and audit | None | Safari Manufacturing Grist | Safari Manufacturing, later portable to PostgreSQL |

### 3.1 Current rate workflow

For Milestone 2, reproduce the current ODS workflow before integrating `Costing-New`. `MaterialCostDB.ods` / `RawSteel` supplies exact interim steel material names, weight factors, and default rates. The `Current Rate per KG` column means **Default Rate per KG**: use it only when no matching SteelRateLog row exists. `Rate Range Per KG` is preserved as source evidence but is not used by the inspected pilot.

The costing workbooks consume a snapshot of purchase observations from `Spares List - Master.ods` / `SteelRateLog`, which is populated from Google Sheets by an `IMPORTRANGE` for `SteelRateLog!A2:F5000`. Google Sheets is upstream lineage only in this milestone; the application reads the dump and does not read Google Sheets directly. Before each costing comparison, the operator refreshes the selected workbook's external sheets so formulas consume current external data. A saved workbook cache is an older observation, not current source-of-truth data. Pin both the refreshed dependency snapshot and the selected workbook revision. The workbook's selected rate can therefore be the last row-ordered observed rate plus its ₹2/kg safety margin, without promoting a live Google Sheets or Grist value into a new canonical master.

The active Google Sheets rate log is periodically archived/truncated to keep lookups manageable, typically around 2,000 rows; older entries may be removed from the active log after being retained in an archive. New entries are appended below existing entries. Latest Rate is the last matching row in the current refreshed snapshot, independent of date ordering or locale interpretation. Max Rate is the maximum non-zero rate among rows available in that same snapshot, with latest/default fallback when there is no non-zero rate. It is not a lifetime maximum and can change when older rows are archived out of the active source. Grist may retain immutable historical observations, but costing must distinguish those from the current snapshot/window so archived history does not silently change current results. Preserve each raw date value and any parsed normalized date for evidence; a date parsed after the observation date is informational only and is not a blocker.

If a workbook's RawSteel lookup formula ends before the complete master list, record a consistency warning. It is not a costing blocker when no active line in the selected workbook depends on omitted rows. If an active selected line does depend on an omitted row, block that line's costing until its inputs resolve. Shorter ranges in unselected workbooks are warning-only for the currently assigned Model Codes; extend those workbooks for consistency when convenient and recheck dependencies if one is later selected. The selected `Safari 1000 HF Local V 4.2.ods` workbook's range was extended by the owner.

The workbook selected in the explorer is the default costing source for its assigned Model Codes. Other ODS files in the same folder may be maximum-rate samples or scenario workbooks; their formulas do not change the selected default or raise an issue solely because they differ. If a different workbook is explicitly selected for those codes, reproduce that workbook's own selector and formula contract. In the inspected S1KHF pilot, `1. Iron and Steel!L1` is `Latest Rate`. Latest Current Rate is the final matching `SteelLog` occurrence in the current source snapshot, even when Date of Entry values regress or tie; the last matching row wins. Max rate is the maximum non-zero rate available in the current snapshot and falls back to latest/default when no non-zero value exists. The pilot adds a hardcoded ₹2/kg safety margin after selecting the rate. It is a buffer for material-price fluctuation between costing/quotation and order confirmation, not part of the observed purchase price. A no-log fallback has semantic `rateLogDate = null`; preserve any cached 1900-era display as evidence without treating it as a business date.

An entry whose parsed date is after the observation date is not by itself invalid: Google Sheets and LibreOffice can interpret date formats differently. Preserve the raw date and parsed value as informational evidence; date does not determine Latest Rate and does not block display. Current blocking checks cover a missing, nonnumeric, or nonpositive price and a missing or unparseable date. A blocked material's effective rate and dependent line costs are hidden, with a warning shown beside every active Material Cut List row using that exact material; the warning identifies the SteelRateLog source row and cell. Historical `In Use = No` lines are excluded. The remaining invalid-entry criteria stay open for ambiguous cases not covered by those rules. Retain raw source values and formula-parity evidence for audit. Business parity tolerance is ±₹100 for the overall costing total; exact line and component differences remain visible, and differences beyond the tolerance block acceptance.

Other product workbooks can use a different selector or formula/range variant. Discover and record those variants before generalizing. `Costing-New` canonical material mapping and rate synchronization happen only in a later milestone after ODS parity.

Milestone 2 reads four sheet contracts explicitly: `RawSteel` for interim material facts; `SteelRateLog` in the fixed master dump for rate observations; each selected product's `SteelLog` for its imported, workbook-local observation snapshot; and `1. Iron and Steel` for weight factors, rate selection, and formula/cache evidence. Before current costing review, the operator can choose **Update External Sheets** in the workbook preview. The app uses LibreOffice UNO to update local linked ODS content and recalculate a temporary sibling copy, then previews that result. It verifies that the selected workbook and linked source hashes remain unchanged and discards the temporary copy. Only local ODS sources below the configured costing root are supported; remote links fail closed. This reads the latest saved local dependency files and does not fetch fresh data from upstream Google Sheets. If refresh is unavailable, label a calculation against the newest captured dependency snapshot provisional and do not accept it as current. Extract every RawSteel row even when a workbook formula has a bounded range; parity also records and respects the selected workbook's range. Out-of-range rows are consistency warnings when no active selected line uses them and blockers only when an active line depends on missing lookup values. Short ranges in unselected workbooks do not affect currently assigned codes and remain warning-only until those workbooks are selected. The product's `5. Material Cut List Price` supplies model-specific active and historical composition and calculated line values. Preserve exact source material display text; a normalized helper may aid search and duplicate detection but must never silently merge names. Each observation records file hash, source sheet/row/cells, formula, cached value, and observation time where present.

For future costing configuration, keep the safety margin separate from the purchase-rate observation. The proposed default is ₹2/kg for all materials, with an optional per-material override and effective-date/audit context. Milestone 2 still reproduces the selected legacy workbook's hardcoded formula exactly. Raw-material transport is generally a shared costing amount (₹1.50/kg in the pilot); supplier-specific actual freight may differ, and a higher amount can be reflected in the product quotation/cost. Unloading (₹0.50/kg in the pilot) and fabrication (₹19.50/kg) are internal labor rates, currently universal. Future costing configuration should keep these defaults and allow product-specific overrides.

No fixed dump acquisition interval or stale-age threshold is currently promised. Still refresh external sheets before each current-costing operation. Record the dump file hash, modification time, newest valid entry date, and observation time; do not infer stale/acceptable status from an arbitrary age. Scripted dump acquisition and any freshness threshold belong to a future automation design.

Business rounding direction is upward. For the proposed costing output, round each active line's Grand Total Cost of Piece up to the next whole rupee before summing line totals; retain exact component calculations separately for traceability and parity.

Future rate workflows must keep these distinct:

- observed purchase rate;
- approved costing rate;
- effective date and source;
- approver and reason;
- rate set used by a cost calculation.

Direct Google Sheets access and Costing-New material/rate integration are deferred in Milestone 2. The current ODS workbook's rate-selection formula is reproduced as-is for parity; this does not define a future rate-promotion policy. Refresh and Calculate never write. The explicit Reconcile action may write the accepted semantic snapshot and governed change-set/audit records to Safari Manufacturing. No Milestone 2 process writes source ODS or Costing-New.

### 3.2 ODS revisions, semantic changes, and reconciliation

A changed path, size, modification time, or file hash is a **source-revision
signal**, not by itself a business reconciliation issue. Costing workbooks are
actively maintained, so routine revisions are expected. A file-level change
must trigger inspection and, when the workbook can be read, a comparison of
the relevant costing and material data with the last accepted semantic
snapshot. Matching sheet counts or a changed hash alone cannot establish
whether business data changed.

Authority is field-specific in Milestone 2: the explorer-selected costing
workbook is authoritative for its assigned Model Codes, model-specific
composition, and the exact formulas/cached values in that workbook revision;
`MaterialCostDB.ods` / `RawSteel` is authoritative for
interim steel names, weight factors, and default fallback rates; and
`Spares List - Master.ods` / `SteelRateLog` is authoritative for the purchase
rate observations available to the costing workbooks. The dump is a snapshot
of the upstream Google Sheets range, but the application does not query Sheets
directly. Safari Manufacturing reconciles material and costing changes from
these ODS sources after semantic comparison. `Costing-New` canonical mapping
and rate synchronization are deferred until after ODS parity.

For the costing workbook's `5. Material Cut List Price` sheet, the active-line
matching key is scoped to the source workbook/Product Model and combines
`Machine Piece Description`, `Material to Cut`, `Dimension to Cut (mm)`,
`Quantity Nos`, and `Optional Item Group 1`. `Item Line No` values such as
`MCL001` are retained as source evidence but are not unique identifiers. A
blank `In Use` means the row is active; `In Use = No` means it is historical
and excluded from current costing totals, but retained in snapshots/history. The
`Date` column records when the item's last purchase price was recorded, not
when the workbook changed. `Remarks` and `CR Log` are preserved as explanatory
change context, not used as matching-key fields. A blank optional item group
is a valid key value; it is not a missing-key error.

The five-field composite is the exact-match identity, not a rule that every
changed key becomes an unrelated removal and addition. When quantity,
dimension, material, option group, or another key field changes, compare the
remaining semantic fields and CR context. Pair a changed-key line as a
probable modification only when the candidate is unique and the evidence is
strong; classify and show every changed field and its cost impact. If evidence
is ambiguous, stop and request an owner decision. If no plausible candidate
exists, report a removal and addition. Never infer identity from MCL ID or row
position. `Date`, `Remarks`, and `CR Log` remain evidence; a CR reference can
explain a structural change but never silently accept it. Duplicate composite
keys remain ambiguous. If `In Use` changes from blank to `No`, the line becomes
historical and is excluded from current costing; a transition from `No` to
blank makes it active again. Preserve raw values and source row/cell
provenance; never hard-delete historical lines.

An ODS revision is expected and is not itself an issue. The costing system must initiate or require and verify an external-sheet refresh before every current-costing calculation or parity comparison; do not rely on a user remembering to refresh. In the current ODS workflow, this is the user's **Update External Sheets** action. Saved formula caches may be stale; retain them as historical evidence, not as current rate authority. If refresh cannot be completed or verified, stop current-cost acceptance and label any replay against the newest captured source snapshot provisional. Record the refreshed source revision and compare semantic contents with the prior accepted snapshot.

When semantic comparison finds changed purchase rates or costing structure,
persist a governed Safari change-set item with previous and current values,
source evidence, affected line, CR reference when present, and cost impact
where calculable. These valid source changes are not data-quality defects and
do not need line-by-line owner review when identity and classification are
clear. A separate Reconcile action records the change set and advances the
accepted semantic snapshot; Refresh and Calculate remain read-only. Hold
acceptance when line identity or reason/classification cannot be determined
safely, and provide an owner-resolution path. Invalid rates and incomplete
inputs remain blocking data-quality findings. A hash/mtime change alone
creates no semantic change item. Unselected scenario/sample workbooks do not
affect assigned Model Codes. Material identity, weights, and default fallback
rates come from RawSteel; current purchase-rate observations come from the
refreshed SteelRateLog dependency; the explorer-selected product workbook
remains authoritative for its assigned Model Codes and model-specific
structure/formulas. Future-looking parsed dates alone do not block. Do not read
Google Sheets directly or use `Costing-New` as an interim prerequisite for
Milestone 2.

The comparison and follow-on workflow must:

1. Preserve the prior immutable file observation and semantic snapshot, then
   record the newly observed revision and its source provenance.
2. Parse and compare relevant material rows, costing inputs, formulas, and
   resulting cost values. Report additions, changes, removals, and affected
   Product Models/Model Codes with source sheet/row/cell references.
3. Determine authority by data domain using the matrix above. While ODS remains
   authoritative, the explicit Reconcile action writes idempotent change-set
   items to Safari Manufacturing and advances the accepted semantic snapshot.
   It records actor, reason, source revision, old/new values, and audit history.
4. Keep Refresh and Calculate read-only. A source revision with no semantic
   delta records an observation only. Clear rate or structure changes are
   persisted without line-by-line owner review; an ambiguous match requires
   owner reconciliation before baseline advancement. A CR reference is
   evidence, not automatic approval.
5. Create durable issues for unresolved validation conditions such as an
   unmapped material, invalid rate, ambiguous identity, broken
   formula/dependency, or incomplete comparison. Preserve valid semantic
   changes as governed change-set records rather than data-quality issues.
6. If the workbook cannot be parsed or the relevant comparison is incomplete,
   report that specific failure as an issue and do not claim that the costs or
   material list are unchanged.

Stable issue fingerprints are based on the affected entity and unresolved
semantic condition, not on the whole-workbook hash alone. Accepting a new file
observation must not change its Product Model/Model Code association.
Reconciliation remains a separate, explicit, idempotent operation with source
provenance and preserved history.

### 3.3 Product costing authority cutover

ODS-to-Grist ingestion is a transfer of records, not an immediate transfer of
costing authority. For each sellable Model Code, the selected ODS workbook
remains the source of truth until its required process sheets and line items are
completely imported and reconciled, every consumed material has a current,
valid, governed rate available in Safari Manufacturing, and the Grist costing
calculation passes the agreed parity and owner-acceptance gates. Imported rows
alone do not make Grist authoritative.

The authority decision may be made for one **Model Code** or for an entire
**Product Model**, depending on the product. A Product Model cutover covers all
of its active Model Codes only when each one satisfies the gates; otherwise
eligible codes may cut over individually while the others continue to use ODS.
Record the effective scope, authority, source revision/rate set, decision time,
approver, reason, and rollback state. Resolve the effective authority to one
unambiguous choice per Model Code before costing; never combine ODS line
requirements with Grist rates or Grist line requirements with ODS rates
without an explicitly reviewed mixed-source rule.

For an ODS-authoritative code, the application continues to refresh/read the
selected workbook and its dependencies. For a Grist-authoritative code, normal
costing, cut-list, and inspection reads use the approved typed line revisions
and pinned rate set in Grist, without parsing the ODS on each UI request. The
ODS remains provenance and a separately monitored comparison/export source.
This route should **decrease load times dramatically**; measure the current
ODS-backed baseline and the Grist-backed latency at cutover rather than assume
that merely copying rows has made the UI fast. No S1KHF Model Code has been
declared Grist-authoritative by Milestone 3.

## 4. Evidence from the current system

### 4.1 Catalog and canonical workbook

The supplied product catalog contains 24 products, 42 model numbers, 175 data rows, and 173 distinct model-code values. Duplicate GC codes and blank descriptions must become reconciliation issues rather than being silently corrected.

The pilot workbook is:

`C:\Irshad\Safari\DRWGD\Products Costing\S1KHF\Local\Safari 1000 HF Local V 4.2.ods`

It contains 17 meaningful sheets and extensive external references. It demonstrates the existing calculation chain:

`material/purchase masters → process sheets → Total Summary option pools → model-code cost`

### 4.2 Workbook sheet interpretation

| Sheet/domain | Role in costing |
|---|---|
| Material Cut List | Raw material cut lines, MCL identifiers, quantities, material, transport/unloading, and fabrication cost |
| Pivot | Reporting/aggregation derived from cut-list data |
| PaintDB | Workbook-local cache of paint master information |
| SteelLog | Steel rate history/reference |
| Iron & Steel | Material, weight, current/latest/maximum rate selection and cost |
| Tool Shop Items | Machined parts, material, labour/vendor cost, and TSI identifiers |
| Stores and Consumables List | Store issue lines, purchase price, freight/handling, quantities, and SCL identifiers |
| CNC Cut List | Plate/CNC parts and CCL identifiers |
| Labour - Paint - Packing | Labour, painting, packing, and related finishing costs |
| Total Summary | Base totals, option pools, additions/deductions, and final model-code costs |
| SparesDB | Workbook-local spare/source cache |
| Cost Log | Historical costing output |
| Spares Detail | Recursive composition of spare items from process lines and sub-parts |
| Spares Summary | Spare-part costing output |

A read-only check of the S1KHF pilot found 42 rows with blank `In Use` and 16
rows marked `No`. The proposed composite key was unique across those 42 active
rows. Three duplicate key pairs occurred among the inactive historical rows,
so snapshots must preserve inactive source rows with revision/row provenance
instead of assuming the active-line key uniquely identifies every historical
record.

Blank or auxiliary sheets remain observable but do not become business domains unless confirmed.

### 4.3 Existing CLI knowledge that must be reused

The current CLI already implements important mapping knowledge and is not to be replaced by a second implementation:

- exact ODS material aliases in `config/material_mapping.yaml`;
- existing alias and comparison behavior, with future mappings to Grist `MasterMaterial` and `ODSComparableMaterial` deferred until after ODS parity;
- alternate-size normalization;
- option-scope filtering;
- ODS-to-Grist comparisons and verification reports;
- existing Product Part, MS List, Tool Shop, and CNC mappings.

This logic must move behind shared domain services usable by both CLI and API, with regression tests proving the CLI results remain stable.

### 4.4 Existing Grist findings

`Costing-New` contains valuable data but currently mixes identity, membership, and calculated values. Product model numbers are represented as products in places, model-code and cost fields are combined, and Stores records link directly to model codes and issue slips. Costing and manufacturing configurations are not cleanly separated.

This structure should be mapped and reconciled, not modified in place to become the new application schema.

## 5. Core business rules

### 5.1 Directory and file registry

- The configured root is `C:\Irshad\Safari\DRWGD\Products Costing`.
- Every root directory or sub-directory may be associated with a Product; inheritance may be proposed but requires user confirmation.
- The UI must present an explorer-style directory tree with search, filters, file metadata, workbook health, and mapped/unmapped state.
- A user explicitly marks an ODS workbook as a costing file before it enters the processing queue.
- A costing file belongs to exactly one Product Model Number.
- One Product Model Number may have multiple costing files.
- One Product Model Code may have exactly one active costing-file association.
- Multiple files for the same model must contain disjoint active sets of model codes.
- A first-time association may omit its reason; superseding or revising an existing association requires a reason. Every save is audit logged. Reassignment closes the old association; it does not erase history.
- Path, size, modification time, and content hash are recorded. Moving or
  changing a file creates a detectable source-revision signal. It becomes a
  reconciliation issue only when semantic comparison finds an unresolved
  condition.
- Archives, templates, generated outputs, encrypted/unreadable files, and master databases are classified separately from candidate costing files.

### 5.2 Product identity and aliases

- Names from the supplied catalog are the import canonical values.
- IDs are immutable and independent of display names.
- Aliases store old spellings, filenames, directory names, and historical codes.
- Duplicate codes are import blockers unless they have an approved disambiguation. The approved GC disambiguation uses `-7.5` and `-10` suffixes.
- The supplied catalog spelling is canonical. An example or filename spelling such as `S1KHFLEP` versus catalog-style ordering such as `S1KHFELP` is retained as an alias candidate or reconciliation issue, not auto-corrected.

### 5.3 Costing configuration

- A model code resolves to a versioned costing configuration.
- The configuration selects a base assembly and explicit additions and deductions.
- Add/deduct operations reference stable part or cost-line IDs, not spreadsheet row numbers.
- A component of a component can be substituted using a typed rule: replace, add, remove, quantity override, or rate override.
- Every calculated cost must explain its full breakdown and the rule that included or excluded each line.
- Reusable parts have one canonical definition and version; downstream configurations reference that revision.

### 5.4 Manufacturing configuration

- Manufacturing configuration is separate from the selling/model-code configuration.
- It includes BOM revision, routing, source department, issue route, batch quantity, yield/scrap assumptions, and effective dates.
- Different parts within a product may use different batch quantities.
- A model code maps to an approved manufacturing configuration version, but several codes may share one configuration where appropriate.
- P/K variants such as Prince and Kirloskar remain distinct when their store issue or manufacturing needs differ.
- An approved manufacturing configuration is a reusable definition; a production plan instantiates it for an actual run and may assign a different requested quantity to each part or subassembly. For example, one S1KHFELP plan may request Drum Assembly 80, Chassis 32, and Support 64.
- Per-part production quantities are not required to equal the finished-product quantity. The plan must explain whether the difference is planned stock, an overlapping multi-run batch, minimum economic batch quantity, or another approved reason.
- A long-running common component may be produced for multiple future finished-product batches. Available and reserved stock must be considered before the next plan creates another batch, so the system can deliberately skip it rather than manufacture it twice.
- Manufacturing batch planning must not alter the approved costing configuration, Product Part revision, or standard manufacturing configuration. It creates versioned plan lines that reference them.

### 5.5 Stores and Tool Shop

- A Store Issue Slip contains externally purchased items required for a production batch.
- Some Tool Shop-manufactured spare items are physically routed through Stores and appear on an issue slip, including lines currently marked `NO`.
- The model must preserve both **source department** (for example Tool Shop) and **issue route** (for example Stores).
- Such an item is costed once even when it participates in both processes.
- Tool Shop operations may include lathe, CNC lathe, milling, facing, turning, and related processes.

### 5.6 Spare parts

- Spare configurations support assemblies, sub-parts, applicability by product/model/configuration, and legacy applicability.
- Shared parts are maintained once and referenced by many configurations.
- Bush-type legacy information remains searchable for spares without appearing as an active selling option.

### 5.7 Safe synchronization

- Scans, previews, imports, and comparisons are read-only by default.
- Every import records source file, sheet, row/cell provenance, hash, parser version, timestamp, and outcome.
- Every sync is idempotent and produces a proposed change set before apply.
- No process writes to `Costing-New` or an ODS workbook in Phase 0.
- Writes to Safari Manufacturing must validate the configured Grist document identity and reject the legacy document ID.
- Setup must discover an authorized Grist workspace, create or safely reuse exactly one document named `Safari Manufacturing`, record its returned document ID outside source control, validate its identity, and only then apply the schema.
- Document creation must be idempotent: if the name already exists, the workflow stops for validation rather than creating a duplicate or choosing one silently.
- Conflicts go to a reconciliation queue; they are not resolved with last-write-wins.
- ODS write-back begins only after round-trip fixtures, backups, formula/link preservation, and explicit approval.

## 6. Target architecture

### 6.1 Application boundaries

- **React/TypeScript UI** — explorer, workbench, preview, reconciliation, and configuration screens.
- **FastAPI application layer** — API contracts, workflow orchestration, validation, and audit context.
- **Domain services** — identity, association rules, workbook parsing, configuration resolution, costing, rate approval, reconciliation, and synchronization.
- **Repository interfaces** — storage-neutral contracts.
- **Grist adapter** — initial repository implementation for Safari Manufacturing.
- **ODS adapter** — read-only first; controlled write-back later.
- **Google Sheets adapter** — deferred; Milestone 2 records the upstream `IMPORTRANGE` lineage but reads only the fixed ODS dump.
- **PostgreSQL adapter** — optional later implementation of the same repository contracts.

The frontend never calls Grist directly and never embeds Grist column IDs in UI components.

### 6.2 Safari Manufacturing domain groups

| Domain | Principal records |
|---|---|
| Identity | Product, ProductModel, ProductModelCode, IdentityAlias |
| File registry | CostingFile, DirectoryProductMapping, FileModelAssociation, FileCodeAssociation, FileObservation |
| Parts | ProductPart, PartRevision, PartComponent, PartApplicability |
| Stable process-line identity | LineMaster, LineRevision, MaterialCutLineDetail, ToolShopLineDetail, CNCLineDetail, StoreLineDetail, PaintLineDetail, LabourPackingLineDetail |
| Source-to-master mapping | WorksheetSnapshot, SourceLineObservation, SourceLineMapping |
| Costing configuration | Feature, Option, CostingConfiguration, CostingConfigurationRevision, ConfigurationPartSelection, ConfigurationLineOverride, ConfigurationCostAdjustment |
| Manufacturing | ManufacturingConfiguration, ManufacturingConfigurationRevision, ManufacturingPartPolicy, BOMLine, RoutingOperation, BatchPolicy, ProductionPlan, ProductionPlanPartLine, ProductionBatch, StoreIssueSlip |
| Spares | SpareConfiguration, SpareLine, LegacyApplicability |
| Rates | Material, PurchaseItem, RateObservation, ApprovedCostingRate, RateSet |
| Cost output | CostRun, CostSnapshot, CostBreakdownLine |
| Governance | ImportBatch, SourceMapping, ReconciliationIssue, ChangeSet, AuditEvent |

The physical Grist schema may optimize names and references, but must preserve these domain boundaries.

### 6.3 Stable line identity and immutable revisions

- Every logical MCL, Tool Shop, CNC, Store, Painting, Labour, and Packing requirement has a stable `LineMaster` identity, normally owned by a reusable `ProductPart`.
- Changeable values such as material, quantity, dimensions, weight, process time, routing, and formula evidence belong to immutable `LineRevision` and typed detail records.
- A material or purchase-item master identifies what is consumed; a line master identifies why and where it is consumed. Multiple line masters may reference the same material.
- Parsed ODS rows remain immutable `SourceLineObservation` records. `SourceLineMapping` explicitly associates an observation with a canonical line master; worksheet row position is never the permanent business identity.
- A moved but semantically unchanged ODS row creates a new observation without creating a new business revision. Quantity, material, formula, weight, or process changes create a new line revision under the same master. A genuinely new requirement creates a new line master.
- Current views may expose `CurrentApprovedRevision` for convenience, but history is superseded rather than overwritten or deleted.
- Summary-sheet content is decomposed into part selections, exact line overrides, true cost adjustments, and calculated cost output; it is not stored as one generic summary-line master.

### 6.4 Milestone 3 normalization boundary

Milestone 3 is the delivery effort for Phase 1 work package 1.1. The
interactive ER model in `data-model.html` is the target design; the first
implementation covers its source-evidence, reusable-part identity,
process-line identity/revision, typed process-detail, material/purchase-item
reference, and row-audit subset for the selected S1KHF pilot. All imported
line values must be queryable as child rows in Safari Manufacturing. The
existing accepted semantic JSON remains a comparison baseline during the
transition and must not be the sole operational store for these rows.

The importer must propose an exact source-observation-to-line mapping and
reuse a stable `LineMaster` only when identity evidence is sufficient. An ODS
row number, MCL ID, name similarity, or CR reference alone is insufficient.
Uncertain mappings require an explicit review state; they must not silently
merge two requirements. A temporary, clearly marked unallocated part may
hold a line until its reusable Product Part ownership is resolved. A common
material can be referenced by many distinct lines; commonality of a whole
part is recorded only after its Product Part identity is confirmed.

Historical `In Use = No` lines retain their source and line history and are
excluded from current totals. Rates and calculated per-run values must retain
their exact source/resolved values for parity, while rate approval and future
rate sets remain later work. An observed rate change alone does not revise a
physical requirement line. Summary headings/adjustments remain source
evidence in this milestone; their sellable configuration rules are delivered
in the subsequent configuration work.

Milestone 3 also extracts only the existing CLI material mapping behavior
needed by the pilot into a shared service, with golden CLI-equivalence tests.
No source ODS or Costing-New write is authorized. The phase numbering and
remaining Phase 0 gates are recorded in `MILESTONE_NUMBERING_AUDIT.md`.

**Implementation checkpoint, 2 October 2026:** the selected local workbook
projects 313 configured rows into individual observations, mappings, line
masters/revisions, and typed detail records; 10 duplicated exact composites
remain explicitly unresolved. The physical v5 schema and guarded append-only
Grist importer are implemented and were applied to the validated Safari
Manufacturing document: 13 v5 tables and 2,043 normalized records. The
inspection UI queries those rows and labels its local projection when Safari
is unavailable. Current source cost drift was confirmed against the accepted
physical fields and workbook MCL C7; ten identities remain unresolved. Exact counts, values, unresolved identity,
CLI extraction limits, and Phase 0 gates are in
`MILESTONE_3_PILOT_RECONCILIATION.md`.

## 7. Required UI workflows

### 7.1 Costing Explorer

The primary screen uses the established Seey-style visual language with a distinct navy, copper, and warm-sand palette.

- Left: filesystem tree rooted at Product Costing, with expand/collapse, search, filters, badges, and folder mapping.
- Centre: selected file details and association workbench.
- Right: workbook preview with sheet selector, dimensions, formula/external-link indicators, warnings, and read-only cell grid.
- User selects Product, Product Model, and one or more eligible Model Codes.
- The UI validates conflicts before Save and shows the proposed registry changes.
- Successful Save queues the file for read-only processing and records the audit event.

### 7.2 Mapped Files and reconciliation

- Group or filter by Product, Model, Code, path, status, last scan, and workbook health.
- Show source revisions and their semantic comparison status separately from durable issues. A hash-only change is not shown as a business issue before content comparison.
- Show unresolved reconciliation issues such as unmapped materials, duplicate-code conflicts, catalog mismatches, unresolved external-link review, and parse failures, with source evidence and affected costing records.
- Compare proposed ODS identity, material, and costing changes with Safari Manufacturing; show field/row-level differences and proposed target updates. Compare to `Costing-New` only in a later milestone after ODS parity and an approved mapping.
- Let an authorized user accept an alias, correct a mapping, supersede an association, or defer an issue with a reason.

### 7.3 Later screens

- Product and reusable Part Explorer.
- Costing Configuration builder with base/add/deduct/replace rules.
- Manufacturing Configuration and batch builder.
- Store Issue Slip preview.
- Spare-parts configuration and legacy lookup.
- Rate observations and approved-rate workflow.
- Explainable cost breakdown and version comparison.

## 8. Authentication and access control

Authentication and production role enforcement are deferred until the workflow is proven. Domain services must still accept an actor and policy decision so authorization can be added without redesign.

Planned identity source is Authentik via OIDC, using the same organizational users as Grist. Application roles should be app-managed and group-mappable:

- Viewer;
- Costing Editor;
- Manufacturing Planner;
- Rate Approver;
- Data Steward;
- Administrator.

Grist credentials remain server-side. Browser clients never receive the Grist API key.

## 9. Non-functional requirements

- Windows paths are handled safely and cannot escape the configured root.
- All mutations are auditable and reversible by superseding records.
- Money uses decimals and explicit currency/precision rules.
- Quantities include units and conversions.
- Long scans and workbook parses are background jobs with progress and resumability.
- Imported source records are immutable observations; curated records are separately versioned.
- Tests cover formulas-as-text extraction, external links, aliases, duplicates, conflict handling, and idempotency.
- Semantic comparison tests cover the active composite key, valid blank optional groups, inactive historical retention, probable modification matching for changed key fields when evidence is unique, ambiguity without guessed additions/removals, `In Use` transitions, duplicate active keys, CR evidence, and process-list movement.
- Logs and exported reports exclude secrets.
- The application remains usable when Grist is temporarily unavailable for read-only filesystem browsing and cached metadata.

## 10. Delivery phases

1. **Phase 0 — Foundation and controlled mapping:** governance, guarded creation and schema bootstrap of the Safari Manufacturing Grist document, canonical identity import, File Explorer, associations, mapped-files reconciliation, CLI mapping extraction, and S1KHF pilot.
2. **Phase 1 — Read-only ODS ingestion and cost parity:** first reproduce the current ODS material and rate workflow using RawSteel, the SteelRateLog dump, the product-local SteelLog, and the product workbook's formulas/caches; produce immutable semantic snapshots and an S1KHF parity report before expanding ingestion. Next, normalize reusable part and process-line identities, revisions, typed child rows, and source mappings for S1KHF while comparing with that accepted baseline. Dependency inventory, approved-rate resolution, explainable configuration costing, and broader parity follow. Hash drift alone does not create a business issue. Costing-New writes and direct Google Sheets access are deferred.
3. **Phase 2 — Controlled synchronization:** reviewed, idempotent change sets from the authoritative source for each data domain into Safari Manufacturing, approved ODS targets, and narrowly scoped legacy data; retain provenance and audit history.
4. **Phase 3 — Configuration workflows:** reusable parts, costing configurations, manufacturing configurations, batching, issue slips, and spares.
5. **Phase 4 — Live rates:** Google Sheets observations, Grist observations, approval policy, rate sets, and recalculation impact.
6. **Phase 5 — ODS write-back and operational cutover:** round-trip-safe exports, backups, user acceptance, and authority switch.
7. **Phase 6 — Authentication and platform decision:** Authentik/RBAC rollout and evidence-based Grist-versus-PostgreSQL decision.

Detailed gates and deliverables are in `IMPLEMENTATION_ROADMAP.md`.

## 11. Phase 0 acceptance boundary

Phase 0 is complete only when:

- exactly one Safari Manufacturing Grist document has been created or explicitly validated, and its ID is kept in local/deployment configuration rather than source control;
- Safari Manufacturing has an approved, version-controlled schema manifest applied to that validated document;
- the canonical product hierarchy is imported with provenance and reported exceptions;
- the full directory can be navigated without modifying ODS files;
- a user can associate files according to the stated cardinality rules;
- conflicts and changes appear in a reconciliation view;
- a changed ODS path/size/mtime/hash is shown as a source revision until its relevant business content is compared; only unresolved semantic discrepancies become durable issues;
- where an authoritative ODS change needs to be reflected in Safari Manufacturing, the application previews the affected material/costing records and proposed updates before the Phase 2 sync;
- the S1KHF pilot parses into immutable observations;
- existing CLI material mapping tests still pass through shared services;
- no uncontrolled writes occurred to `Costing-New` or source ODS files;
- decisions, requirements, architecture, and implementation status are current in Markdown and `requirements-status.html`.

## 12. Open decisions for Milestone 2 and later gates

1. Select the target Grist workspace during setup if the authenticated account exposes more than one writable workspace.
2. Define the rate-promotion policy and thresholds in Phase 4.
3. Select the first additional workbook families for parity after the S1KHF pilot.
4. Define ODS write-back scope and acceptable LibreOffice recalculation behavior before Phase 5.
5. Complete the invalid SteelRateLog entry criteria beyond confirmed invalid values such as nonnumeric prices. A future-looking parsed date alone is informational and does not block rate display.

## 13. Milestone 2 pilot acceptance criteria

The S1KHF pilot pins source hashes and observation time; extracts every RawSteel row and both dump/product rate-log snapshots with row/formula/cache provenance; refreshes external sheets before current costing; reproduces exact-name matching, the selected workbook's selector, last-row Latest Rate behavior, max/fallback behavior, and its ₹2/kg safety margin; records semantic null for a no-log date; and compares active Material Cut List identity, factors, rate/date, grams, material cost, transport, unloading, fabrication, and grand total against refreshed calculations and saved-cache evidence. It distinguishes routine source revisions from semantic changes and uses only the explorer-selected workbook for its assigned Model Codes. Business comparison uses ±₹100 overall-total tolerance, while exact line/component evidence remains visible. Current totals use active rows only; new output rounds each active line's Grand Total Cost of Piece upward to a whole rupee before summing, while legacy formula parity remains at source precision. Confirmed invalid rate rows block display for every workbook using that material until user resolution; future-looking dates alone are informational. Latest Rate follows the final matching row, and Max Rate covers only entries in the current refreshed source snapshot.

The complete pilot cycle also requires disposable-copy LibreOffice refresh; read-only calculation and semantic comparison; classification of rate changes and structural changes against the accepted baseline; owner resolution only for ambiguous identity; an idempotent Reconcile action that persists change-set items and audit evidence and advances the baseline; and a second run that confirms stable accepted state. Apply schema changes only to the validated Safari Manufacturing document. Do not write source ODS or `Costing-New`. Run Python tests, frontend tests, Python syntax checks, and UI type/build checks, and record the real S1KHF cycle and limits. If Safari has no earlier accepted snapshot, disclose that the first run establishes a starting baseline and do not claim historical changes that cannot be reconstructed.

On 29 September 2026, the selected S1KHF workbook was refreshed through LibreOffice on a disposable copy and recalculated to ₹11,056.401709 across 42 active MCL lines; 16 historical lines were excluded and no line was blocked. Safari had no earlier accepted costing snapshot, so the first reconcile established its starting semantic baseline. Schema v4 was applied only to the validated Safari Manufacturing document. The implementation records rate-source and design-structure changes in separate governed change-set items, with line state, source evidence, CR references where present, cost impact where calculable, and audit events. Deterministic changes need no line-by-line review; ambiguous matches require owner resolution. A repeated S1KHF scan against the new baseline reports no semantic differences. Additional invalid-rate cases, automatic Google Sheets dump acquisition, and broader product-family parity remain deferred. Out-of-range lookups with no active selected-line dependency remain warnings.

## 14. Current Phase 0 implementation evidence

The first implementation slice now provides the safe filesystem explorer,
read-only ODS inspection, canonical catalog reader, storage-neutral identity and
association records, an in-memory repository, and an explicit Safari Grist
administration/schema path. The in-memory adapter is the default local mode and
is labelled in the UI. On 19 September the owner-selected Safari document was
revalidated against the then-current versioned 11-table manifest; fake-client
tests cover plan, duplicate-name, permission, and retry contracts. A 23
September read-only v3 plan now proposes one new `DirectoryProductMapping`
table and extensions to `ProductModel` and `ReconciliationIssue`. That schema
upgrade remains unapplied pending explicit approval. The canonical catalog has
a guarded Grist diff/upsert path that remains dry-run by default.

The catalog importer now keeps unresolved repeated canonical codes inactive
and exposes an error-level reconciliation issue; the association validator
rejects those identities. The real catalog still yields the same 24 Products,
42 Models, 173 Codes, 6 aliases, and 68 blank-description issues. The
implementation deliberately does not claim full Phase 0 completion: the
approved catalog is imported and its repeat diff is a no-op. Three
owner-provided S1KHF Bearing Type mappings are persisted in the validated Safari
document with 19 unique active code links, actor/reason, audit events, and
queued import batches. The hashes matched at association time; a 23 September
post-merge audit found that the HF-MS Drum source subsequently changed. The
application detects its hash drift. That establishes a new file revision, but
the current Phase 0 implementation has not compared its material rows or
costing values with the prior accepted snapshot, so it does not establish that
a business issue exists. The two other mapped source hashes still match on the
23 September read-only check.
The HF-MS Drum code set was confirmed from the workbook's `Total Summary`
Bearing Type rows and saved through the browser UI; the live Mapped Files
history and queued state were verified. Its 61,125 external references remain
visible as a review warning. The several-file pilot gate is satisfied. The
Milestone 0.5 workflow is implemented and covered with synthetic/fake-adapter
tests; live issue materialization and resolution, an owner-reviewed live-safe
conflict/supersede walkthrough, and owner UI acceptance remain open.

## Ongoing processing workflow agreement - 3 October 2026

These owner instructions extend earlier milestone-specific boundaries.

- FILE-009: Restore saved Product, Model, Codes, version, status and history on
  selection. Validate checks only; a separate Save persists with audit.
- PRE-003: Full-width preview automatically refreshes external local sheets on
  selection using a disposable copy, with progress, result, hashes and errors.
- FLOW-001: Persist/audit new, associated, extracted, mapping review,
  reconciliation review, ready to store, processed and changes pending states.
- FLOW-002: The sole user may mark processed only after line items, quantities,
  weights, optional groups, Parts and Model Code configuration are resolved.
  Processing does not approve costing authority for any Code or Model.
- FLOW-003: Costing-New stays read-only and supplies reviewed interim material
  prices. Show ODS price variances and missing rate evidence. Price variance
  alone never blocks processing; earlier total-cost acceptance tolerances must
  not be reused as processing gates.
- PART-001: Select existing canonical Parts or create uniquely named Parts.
  Each blank-description row requires an individual assignment. Tool Shop/CNC
  need Parts; Store Issue does not. Paint/Packing and reviewed Summary
  configuration belong with Model Codes.
- FLOW-004: Files show workbook records; Model Codes show stored Grist records,
  both with on-demand reconciliation and explicit revisions/timestamps.
- FLOW-005: After pilot completion, process files selected by the user, in any order.

Owner inputs remain stage-specific: mandatory sheets and numeric tolerances,
two Summary examples, current-rate selection, Part reuse/name uniqueness,
CR approval/automatic proposals, write-back scope, spare retirement/codes,
and Google Sheet IDs/tabs/access/cadence. Configure credentials locally.
The current one-active-file-per-Code rule is retained pending cross-family confirmation.

### FLOW-001 implementation progress - 5 October 2026

The immutable processing event store, optimistic source/association/version
checks, idempotent retry, trusted actor attribution and selected-file history
UI are implemented. New/Associated derive from saved registry/association state
until an explicit transition is recorded. All eight lifecycle states are defined.
Completing Ready to store/Processed still requires the later structural and
Model Code configuration evidence services; UI/API cannot assert completion.

Workflow stage 4 navigation is implemented: Files for workbook evidence; Product Models -> Model Codes for stored shared file-baseline records with on-demand reconciliation. Per-code configuration/structural completion gates remain open; stored records and processing status do not confer authority. Stage 5 questions on mandatory sheets, numerical tolerances, Summary examples and interim rate selection were presented on 5 October.

5 October processing rules accepted: all five process lists plus Total Summary/Spares Detail/Spares Summary are mandatory. Quantities match exactly; weights match at 2 decimals kg. Final cost-total and price variances are informational for file processing. Reviewed Part assignments and structural Model Code configuration remain gates. New Part names must be globally unique. Read-only interim Costing-New rates use MaterialLatestRate with MaterialRateLog evidence, otherwise Default_MaterialRate.

5 October implementation evidence: Part Mapping supports canonical selection or globally unique creation, exact-description groups, individual blank rows, and reasoned source-pinned assignment history. Store Issue does not require a Part. Paint/Packing and reviewed Summary-to-Code configuration remain open. Existing line masters are not silently reassigned; importing reviewed Parts is the next gate. Pilot remains unprocessed with 55 unresolved groups / 100 active source rows.

### PART-002 — Part identity and automatic naming prerequisite (6 October 2026)

Accepted requirement; not implemented. Each Part has a permanent unique number independent of its name and sharing scope. Generate names from maintained scope shortcodes plus the entered description and meaningful design variant. Global, Product, Product Model and Model Code scope affect naming and warnings only; out-of-scope Model Code configuration is allowed. Multiple shared chassis designs within a Model have separate numbers and variant names, and explicit code usages. Scope/name changes record metadata versions and searchable aliases without changing identity or engineering revision. Central number allocation must be durable, unique and retry-safe; numbers are never recycled.

This foundation must be implemented and verified before advancing Part mapping screens or reviewed-Part imports. The current screen remains an incomplete prototype. Detailed rules, scenarios and exit checks: [Part identity requirements](PART_IDENTITY_REQUIREMENTS.md).

### PART-003 — Rev A until governed CR revisions exist

Owner instruction, 6 October 2026: all managed Parts start and remain at Rev A until the CR flow is implemented. Part engineering revisions require that process and its approval evidence; block later revisions through every write path until available. Name/scope metadata history and source/line revision history are separate and do not imply Part CR approval. Implement this guard with PART-002 before progressing Part mapping.
