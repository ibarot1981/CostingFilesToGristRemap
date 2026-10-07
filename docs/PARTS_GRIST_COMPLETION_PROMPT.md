# Implementation prompt: complete Grist Parts, composition and purchased-Part costing

Use **GPT-6 Luna with Extra High reasoning (`gpt-6-luna`, `xhigh`)** in the implementation chat. Prompt text does not switch the running model.

Work in `D:\Irshad\Dev\Python\CostingFilesToGristRemap`. Continue the existing Part implementation on `codex/part-identity-foundation` and PR #3 after checking their current state. This is an implementation request: complete the changes below, test them, update the existing PR where appropriate, and report actual persistence and remaining gates. Preserve existing uncommitted work. Do not rebuild the application or create competing Part/configuration/costing masters.

## Read first and establish the current state

Read applicable repository instructions and:

- `docs/PARTS_IMPLEMENTATION_REVIEW.md`
- `docs/PART_IDENTITY_REQUIREMENTS.md`
- `docs/PARTS_ENTITY_DIAGRAM.md` and `docs/parts-entity-diagram.html`
- `docs/PARTS_UI_DESIGN.md`
- `docs/PART_CREATION_MAPPING_IMPLEMENTATION_PROMPT.md`
- Relevant architecture, API/schema, roadmap, decision, workflow and broader entity-model documents.
- Current Part identity/mapping, Grist/schema, normalized line, configuration/costing, API/UI and test code.

The 7 October review found that managed Parts and new mappings are stored only in local SQLite, Grist Part writes are explicitly rejected, the explorer is a grouped flat list, and drawings/typed line details/actual code usage are unavailable. The reviewed live document had one temporary unallocated Part, one numeric Part revision and zero mapping reviews; the local registry had zero managed Parts. Recheck these facts; do not assume production data is still empty. Passing tests alone do not establish that Grist persistence or the requested UI exists.

The newer owner clarifications below take precedence over stale scope exclusions in older documents. Initial Part composition, purchased-Part records and their rate resolution are included in this task. The full CR approval process, automatic ODS Summary-to-configuration import, production planning and a complete purchasing ERP remain later work.

## 1. Make Grist canonical for Part business data

Extend existing `ProductPart`; preserve stable UUIDs and permanent `SM-P-000001` numbers. Store generated names, status, scope/target, description/variant, current metadata reference and engineering baseline reference in Safari Manufacturing in Grist. Add typed metadata-version, name-alias, scope-shortcode and shortcode-history records. Reuse `AuditEvent` for appropriate lifecycle/governance events.

Store Part creation, metadata changes, retirement, mappings, engineering baseline, composition, optional drawing links and purchased-Part/vendor/purchase records in Grist. A SQLite dictionary shaped like a Grist record is not persistence. Do not leave Grist as read-only legacy storage for this slice. Successful Save must correspond to a durable, readable Grist result.

SQLite may remain a coordinated number allocator/reservation/recovery journal, but not the sole canonical business store. Guarantee number/name/request uniqueness across supported concurrent application instances. Document and enforce the deployment boundary: independent per-host SQLite sequences are unacceptable. Implement durable reservation/publication and recovery after timeouts, response loss and partial failures; an identical retry returns the same Part, number and result, and a changed payload conflicts. Do not rely on count, unchecked maximum-plus-one, process-only locks or imaginary Grist unique constraints. Detect incomplete publication and make it recoverable without duplicate records. Do not report success on an incomplete write.

Inspect equivalent existing Grist entities before creating new ones. Produce an explicit schema/migration plan, compatibility checks, backup and reconciliation evidence. Verify the exact configured Safari Manufacturing document/workspace before applying authorized schema/data changes. Never target Costing-New or alter source ODS. Do not mutate schemas during ordinary application startup. Use isolated test documents/data for mutation tests; do not invent live Parts or purchases for demonstrations.

Preserve existing UUIDs, numbers, metadata/aliases, request keys, mapping history, normalized lines and source provenance during migration. Review legacy numeric revisions and temporary/unallocated identities without silently converting them to engineering baselines or assuming CR approval. After migration, read back records through the real Grist adapter, restart the application and verify that identity/history remain available independently of a local business-data registry.

## 2. Preserve identity, naming and revision rules

Generate names from maintained scope shortcode + entered description + optional meaningful design variant. Support Global, Product, Product Model and Model Code. Use typed target references and validate relationships. Sharing scope controls names and advisory warnings only; it never restricts configuration and never auto-assigns descendant codes.

Current and historical normalized names must remain globally unambiguous, including retired Parts. Preserve aliases and immutable actor/reason/time history. Reviewed scope/name changes preserve UUID, Part number, physical definition and engineering A. Distinct Chassis designs within the same Model have distinct numbers and meaningful variants; do not concatenate their Model Codes into names or invent random name suffixes.

All managed Parts, purchased Parts, assemblies and subassemblies start and remain **engineering Rev A until the approved CR flow exists**. Store an explicit engineering label, separate from numeric legacy/source, line, mapping, drawing-file and metadata versions. Rev A is the initial baseline, not proof that a CR was approved. Reject Rev B+ and informal overwrites of established physical definitions across UI/API/services/import paths. Metadata renaming and purchase-rate changes never create an engineering revision.

## 3. Support optional and mixed Part composition

A Part can have only MCL entries, only Toolshop entries, only CNC entries, any combination of these, child Parts/subparts, purchased components, or any mixture. No manufacturing category is mandatory. Purchased Parts can exist and be configured without fabrication lines. Do not make these characteristics mutually exclusive Part types.

Connect stable `ProductPart` ownership to existing `LineMaster`/`LineRevision`/`LineDetail`. Use an explicit PartRevision-to-LineRevision bridge where necessary to pin exact requirements and preserve historical definitions. Retain source observations/mappings and quantity/weight provenance. Source observation or mapping does not silently approve changed engineering content.

Use `PartComponentRevision` for parent engineering revision → child engineering revision plus positive quantity and unit basis per one parent unit. Reject self-links and recursive cycles. Reusable children can appear in several parents. Initialize a new draft Part's A definition through an explicit reviewed finalization step; once established, physical definition changes require the future CR flow. Metadata and commercial purchase evidence remain editable through separate audited operations. If the current lifecycle has no safe draft/finalization distinction, add the minimum explicit one and document it rather than leaving Rev A indefinitely mutable.

Provide Part-detail controls to review/link initial process requirements and child Parts, show quantities and normalized weights, and navigate to child details. Do not silently rewrite accepted pilot line ownership just because a source group was mapped. Show conflicts/migration needs accurately.

Add optional revision-specific drawing links with drawing identity, path/link, file version and audit. Provide safe open/preview for an existing valid file; preserve references across renaming. Do not fabricate drawings, overwrite files or equate a drawing-file version with an engineering revision.

## 4. One purchased Part, many vendors, latest actual purchase rate

This requirement is new and does not exist in the current ODS design. Implement typed authoritative Grist data and minimal capture/review APIs/UI. Do not infer or fabricate vendor/purchase history from ODS.

A physically equivalent purchased item has one canonical `ProductPart` and number, independent of vendor. Configuration and component selection choose that Part/revision once. Bearings, engines, motors etc. use the same identity and Rev A rules. Different specifications remain different Parts; do not merge vendor items by description alone.

Use or introduce these responsibilities after checking actual schema:

- `PartPurchaseSpecification`: revision-specific physical/specification and procurement catalog information, with a Part/PartRevision reference and optional existing `PurchaseItem` reference. Procurement catalog identity does not replace canonical Part identity.
- `Vendor`: one canonical vendor identity, not a duplicate per rate.
- `VendorPartMapping`: reviewed equivalence between a vendor's item/SKU and the canonical purchase specification. Different vendors may map to the same Part.
- `PartPurchaseRecord`: immutable actual purchase evidence linked to the vendor mapping and applicable Part revision/specification. Capture transaction identity, purchase date/time, document reference, quantity, UOM, rate basis, currency, discount/charges where relevant, status and actor/reason/time/request evidence. Corrections/reversals preserve original evidence and identify the replacement/invalidated record.

Provide minimal vendor/item mapping and Record purchase controls reachable from purchased-Part details, plus purchase-history display. This is not a request for orders, approvals, inventory receipt automation or a full accounts system.

Implement one server-side purchased-Part rate resolver used by current Part/configuration costing. Choose the **latest eligible actual purchase across all vendors supplying the same compatible canonical Part specification/revision**. Do not choose the cheapest vendor, preferred vendor, latest quote, newest imported row or latest manually entered material rate. Backdated entry must not displace a later actual purchase merely because it was entered last. Vendor/rate changes never create a new Part number or engineering revision.

Initial policy to document and expose clearly:

- Use the recorded actual purchase transaction date/time as the primary ordering basis, not record-entry time. Eligible evidence represents a posted/completed purchase; quotations, drafts, voids and returns are not price-setting purchases. Identify which receipt/invoice evidence is canonical so the same purchase is not counted twice.
- Respect a calculation's as-of date. Historical calculations pin the selected purchase record, vendor, date, normalized unit rate, quantity/UOM/currency and calculation basis; a later purchase never rewrites an existing costing result.
- Normalize quantity/UOM and monetary basis only with explicit supported conversion evidence. Never mix currencies, pack prices and per-piece prices silently. If the latest purchase cannot be priced comparably, show that evidence as unavailable/review-needed; do not silently skip it to an older cheaper/comparable purchase.
- Distinguish quoted/base unit price from the applied costing basis. Make treatment of discounts, taxes, freight and other charges explicit; do not invent a landed-cost policy or exchange rate. The initial supported same-currency/UOM basis may use a documented purchase unit rate while unresolved charges/conversions are visibly excluded or blocked from authoritative costing.
- For equal purchase timestamps with different rates and no authoritative transaction order, show an explicit conflict and require an audited resolution. Do not choose by vendor, cheapest price or accidental row order. Repeated ingestion of the same transaction is idempotent.
- No valid purchase history means a visible rate-unavailable state, never zero or an invented ODS rate fallback. Configuration may still select the Part; authoritative cost/completion must identify the missing evidence rather than presenting an apparently complete zero cost.

Keep material pricing separate: latest entered MaterialRateLog rate when available, otherwise Default Material rate. Do not change that existing rule to purchase-date ordering. Purchased-Part rates come from the new actual-purchase history.

Integrate immutable purchased-Part rate evidence into existing/planned cost-run and breakdown responsibilities; do not build a competing cost engine. Where the full costing/configuration workflow is not yet implemented, deliver and test the shared resolver, Part-detail rate display and the real consumer interfaces without claiming full end-to-end Summary costing exists. Avoid double counting: a purchased child's cost must not also be added as an unrelated fabrication requirement. For mixed Parts, distinguish purchased child quantities, direct manufacturing requirements and supported alternative sourcing; do not silently add both buy and make costs for the same fulfillment path.

## 5. Complete Parts UI and mapping

Keep the dedicated Parts tab, reachable from Part Mapping. Reuse Files-page typography, controls, spacing, colors, tree behavior and two-pane layout. Implement an actual **collapsible Parts explorer**, with hierarchical scope/target nodes, visible expand/collapse controls, keyboard navigation and persistent selection/expansion/search. Grouping must not hide out-of-scope usable Parts. Preserve existing legacy/unallocated visibility without falsely labelling them managed A Parts.

The right pane provides guided naming/creation and selected-Part details. Full details show Overview, Process lines, Components, Purchase details/history/current rate when applicable, Drawings, Used in and History. Show accurate empty/unavailable states and links to child Parts. Verify edit prepopulation, stale asynchronous response handling, direct UUID routes and back/return navigation. Do not require a manufacturing line or drawing for Part creation.

Store mappings in Grist with typed Part, pinned engineering revision and metadata-version/name-used references, plus source hash/association/version/sheet/row/actor/reason/time/request evidence. Preserve active MCL/Tool/CNC exact-description groups and individually reviewed blank-description rows; Store Issue rows still do not require a Part. Preserve partial Save, stale review rejection, durable retries and processed-file reopening rules.

Creation and mapping Save remain separate. Navigation to/from Parts preserves workbook/group/draft context; creating or selecting a Part does not automatically save an assignment. Outside-scope use warns and remains saveable. Source mappings are not actual per-code BOM selections and do not confer costing authority or processing completion.

Preserve the existing conceptual `CostingConfiguration` → `CostingConfigurationRevision` → `ConfigurationPartSelection` design for explicit code usage, pinning exact Part revisions. Do not auto-populate usage from broad scope or file mapping. Automatic Summary parsing remains later work; clearly distinguish implemented manual/reference capabilities from deferred imports.

## 6. Required verification and delivery

Add meaningful tests and isolated browser evidence for:

1. Real Grist Part creation/read-back/restart, metadata/aliases, fixed A, retirement and mapping persistence; concurrent allocation, lost responses, partial Grist publication and recovery without duplicates. Verify migrated identity/history remain stable.
2. All naming scopes, same-model Standard/Reinforced variants, scope promotion without renumbering/revision, global name/alias conflicts and warning-only out-of-scope Save.
3. MCL-only, Toolshop-only, CNC-only, all-three, purchased-only, child-only and mixed Parts; reusable children, quantity basis, nested navigation, cycle rejection, pinned history and prevention of informal post-finalization physical edits.
4. One purchased Part supplied by two vendors at different rates: an older purchase from Vendor A and newer purchase from Vendor B select B for current costing while configuration still references the same Part. A later purchase from A changes current rate without changing identity/A. Older historical results remain pinned.
5. Backdated purchase entry, quote/void/return exclusion, duplicate transactions, equal-time conflicts, incompatible currency/UOM, explicit conversions and no-purchase-history behavior. Verify existing MaterialRateLog/default behavior remains unchanged and bought/made costs are not double counted.
6. Actual PartsView interactions: collapsible explorer, guided creation, edit prepopulation, detail routes, components/purchase controls, valid drawing links and mapping return/retry context. Existing mapping-only UI tests do not cover the Parts screen.

Run the appropriate Python/UI suites, TypeScript check and production build using the compatible bundled runtime if necessary. Use quantities that match exactly and weights compared to two decimal places in KG where source reconciliation applies. Final configuration-total differences caused by rates must be shown but are not completion gates; missing/unresolved required input evidence is a separate issue. Do not add mirrored implementation tests without meaningful behavior.

Update requirements, decisions, architecture, API/schema, roadmap/status/workflow and both entity diagrams. Show actual deployed tables separately from proposals and remove stale claims about SQLite being canonical or Grist Part writes being prohibited. Add recovery/backup instructions and an honest migration report. Update the existing PR title/body to describe the final change and validation. Do not merge solely because tests pass, and do not claim later CR/Summary/purchasing workflows are complete.

Deliver a concise report of completed behavior, actual Grist tables/columns and persistence evidence, migration/recovery results, automated/browser checks, documented commercial-rate assumptions and any genuinely unresolved gate. If a deployment/migration is blocked, finish the concrete reviewable implementation and report the exact blocker rather than silently falling back to SQLite business storage.
