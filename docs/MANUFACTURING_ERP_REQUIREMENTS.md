# Safari Manufacturing ERP Frontend — Requirements Baseline

Status: approved working baseline for Phase 0  
Updated: 21 September 2026  
Source of truth during transition: product costing ODS files and the approved ODS material masters

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

| Data domain | Current authority | Phase 0 treatment | Intended authority after controlled cutover |
|---|---|---|---|
| Product/model/code list | Supplied ODS catalog | Import to Safari Manufacturing with provenance; reconcile duplicates | Safari Manufacturing after approval |
| Product costing and formulas | Mapped product ODS file | Read-only ingest, explain, compare, and snapshot | Safari Manufacturing costing engine; ODS remains an export/sync target during transition |
| Material master and costing rates | Approved ODS masters | Read-only ingest with source cells and timestamps | Approved rate set in Safari Manufacturing |
| Operational purchase rates | Google Sheets and existing Grist flow | Observe/import later; do not automatically promote | Rate observations feeding an approval workflow |
| Existing operational records | `Costing-New` Grist | Read-only or narrowly scoped synchronization | Migrate domain-by-domain after reconciliation |
| New registry, mappings, and audit | None | Safari Manufacturing Grist | Safari Manufacturing, later portable to PostgreSQL |

### 3.1 Current rate workflow

Purchase personnel enter purchase information in Google Sheets and Grist. The costing workbooks do not use those live entries. They read from the periodically downloaded `Template DB/Spares List - Master.ods` and from its steel-rate log references.

The Google Sheet used by Purchase is not the same logical sheet as the costing master. A newly observed purchase price does not automatically replace the approved master price. The master is generally changed only when the item becomes costlier and a user exercises judgment. Grist may contain newer operational prices that are not approved costing prices.

The future design must therefore keep these distinct:

- observed purchase rate;
- approved costing rate;
- effective date and source;
- approver and reason;
- rate set used by a cost calculation.

Automatic promotion logic is explicitly deferred. No Phase 0 process may replace approved ODS rates with the latest Grist or Google value.

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

Blank or auxiliary sheets remain observable but do not become business domains unless confirmed.

### 4.3 Existing CLI knowledge that must be reused

The current CLI already implements important mapping knowledge and is not to be replaced by a second implementation:

- exact ODS material aliases in `config/material_mapping.yaml`;
- mapping to Grist `MasterMaterial` and `ODSComparableMaterial`;
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
- Path, size, modification time, and content hash are recorded. Moving or changing a file creates a detectable reconciliation event.
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
- **Google Sheets adapter** — rate observations later.
- **PostgreSQL adapter** — optional later implementation of the same repository contracts.

The frontend never calls Grist directly and never embeds Grist column IDs in UI components.

### 6.2 Safari Manufacturing domain groups

| Domain | Principal records |
|---|---|
| Identity | Product, ProductModel, ProductModelCode, IdentityAlias |
| File registry | CostingFile, DirectoryProductMapping, FileModelAssociation, FileCodeAssociation, FileObservation |
| Parts | ProductPart, PartRevision, PartComponent, PartApplicability |
| Process cost | MaterialCutLine, ToolShopLine, CNCLine, StoreIssueLine, Paint/Labour/PackingLine |
| Costing configuration | Feature, Option, CostingConfiguration, ConfigurationRule |
| Manufacturing | ManufacturingConfiguration, BOMLine, RoutingOperation, BatchPolicy, StoreIssueSlip |
| Spares | SpareConfiguration, SpareLine, LegacyApplicability |
| Rates | Material, PurchaseItem, RateObservation, ApprovedCostingRate, RateSet |
| Cost output | CostRun, CostSnapshot, CostBreakdownLine |
| Governance | ImportBatch, SourceMapping, ReconciliationIssue, ChangeSet, AuditEvent |

The physical Grist schema may optimize names and references, but must preserve these domain boundaries.

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
- Show unmapped files, duplicate-code conflicts, moved/changed files, catalog mismatches, external-link issues, and parse failures.
- Compare proposed ODS identity/process data with Safari Manufacturing and, later, `Costing-New`.
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
- Logs and exported reports exclude secrets.
- The application remains usable when Grist is temporarily unavailable for read-only filesystem browsing and cached metadata.

## 10. Delivery phases

1. **Phase 0 — Foundation and controlled mapping:** governance, guarded creation and schema bootstrap of the Safari Manufacturing Grist document, canonical identity import, File Explorer, associations, mapped-files reconciliation, CLI mapping extraction, and S1KHF pilot.
2. **Phase 1 — Read-only ODS ingestion and cost parity:** normalized observations, dependency inventory, costing engine, and explainable parity against selected workbooks.
3. **Phase 2 — Controlled synchronization:** reviewed change sets between Safari Manufacturing, approved ODS targets, and narrowly scoped legacy data.
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
- the S1KHF pilot parses into immutable observations;
- existing CLI material mapping tests still pass through shared services;
- no uncontrolled writes occurred to `Costing-New` or source ODS files;
- decisions, requirements, architecture, and implementation status are current in Markdown and `requirements-status.html`.

## 12. Open decisions that do not block Milestone 1

1. Select the target Grist workspace during setup if the authenticated account exposes more than one writable workspace.
2. Define the rate-promotion policy and thresholds in Phase 4.
3. Select the first additional workbook families for parity after the S1KHF pilot.
4. Define ODS write-back scope and acceptable LibreOffice recalculation behavior before Phase 5.

These questions are recorded now; none requires guessing in order to build the read-only registry and mapping foundation.

## 13. Current Phase 0 implementation evidence

The first implementation slice now provides the safe filesystem explorer,
read-only ODS inspection, canonical catalog reader, storage-neutral identity and
association records, an in-memory repository, and an explicit Safari Grist
administration/schema path. The in-memory adapter is the default local mode and
is labelled in the UI. The owner-selected Safari document has been revalidated
and its live schema matches the versioned 11-table manifest exactly; fake-client
tests cover plan, duplicate-name, permission, and retry contracts. The canonical
catalog has a guarded Grist diff/upsert path that remains dry-run by default.

The catalog importer now keeps unresolved repeated canonical codes inactive
and exposes an error-level reconciliation issue; the association validator
rejects those identities. The real catalog still yields the same 24 Products,
42 Models, 173 Codes, 6 aliases, and 68 blank-description issues. The
implementation deliberately does not claim full Phase 0 completion: the
approved catalog is imported and its repeat diff is a no-op. Three
owner-provided S1KHF Bearing Type mappings are persisted in the validated Safari
document with 19 unique active code links, actor/reason, audit events, and
queued import batches; fresh reads confirm all three ODS hashes are unchanged.
The HF-MS Drum code set was confirmed from the workbook's `Total Summary`
Bearing Type rows and saved through the browser UI; the live Mapped Files
history and queued state were verified. Its 61,125 external references remain
visible as a review warning. The several-file pilot gate is satisfied, while
owner UI acceptance and the complete Mapped Files conflict/supersede and
issue-resolution workflow remain open.
