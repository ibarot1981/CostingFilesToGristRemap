# Part requirement table editor and costing inputs — 21 November 2026

Status: **Owner approved implementation dispatch on23November2026: "displatch for implementation. ensure luna exra high and not terra medium."**

This is a new bounded follow-up to deployed B2-P1 / PR9, not a modification of its immutable task or review history. Baseline: main `1f65b42e246b270ea551c66b2b181d03bf6fd0a1` (accepted implementation `61803d881a116658f8e3cd4a9334ee9b1a4d7274`). Sol owns architecture and independent review; after explicit owner confirmation, Luna Extra High implements and handles subsequent correction rounds under the saved routing policy. The owner now authorizes the bounded implementation and dispatch below. Production migration, merge/deployment, unrelated packages and a new scheduler remain outside this authorization.

## Problem and requested outcome

Part Mapping displays cut-length evidence for MCL/Tool Shop and per-part weight for CNC. The established Part editor must display and maintain those same saved manufacturing facts. Replace the large repeated field cards with compact, family-specific editable tables. Users must be able to add a visibly new row, modify an existing row, confirm removal, and inspect exactly which row and values changed in a published engineering revision.

Owner screenshot: `C:/Users/ibaro/AppData/Local/Temp/codex-clipboard-e9cb6a6e-e025-4fb4-b175-2635df379946.png`, Part `SM-P-000008`. It illustrates the oversized card editor and missing cut-length input. The owner reports Add requirement does not create a row. That interaction defect must be reproduced and diagnosed; its root cause has not been established by this planning review.

## Established authority and scope

- Safari Manufacturing Grist remains authoritative after baseline establishment. Display/edit/save/reload/publication/costing must work without the original ODS. Part Mapping is a useful comparison surface, not a dependency of the saved Part editor.
- Reuse the existing Part definition, draft, CR, Irshad approval, publication, dependency-impact and durable original-request recovery services. Saving a draft is separate from publishing an accepted successor, including for an established initial Rev A that is not yet finalized.
- Preserve Part identity, requirement/component occurrence identities, previous accepted physical revisions, configuration pins and immutable monetary snapshots. Changes propagate to current assemblies/configurations through the existing reviewed publication closure. Spare composition remains pending its separate package.
- Shared Part requirements remain separate from Model Code-specific Issue Slip selections and entry overrides. A manufacturing requirement routed through Stores is not automatically a purchased item and must not be costed twice.
- This package repairs the editor and the use of its physical consumption inputs through existing costing policies. It does not introduce new commercial-rate policies, ODS comparison/adoption completion, spare composition, full Model Code costing cutover, general RBAC or a new spreadsheet application.

## 1. Display the actual saved costing inputs

Use consistent business labels in the accepted read-only view, editable table, CR review and revision change history.

| Row type | Required normal columns | Conditional columns / details |
| --- | --- | --- |
| MCL | Material/item picker, quantity, quantity unit, **Dimension to Cut**, cut-length unit, row action | Existing relevant material/process details; remarks where supported; explained consumption/rate basis |
| Tool Shop (TS) | Item/material picker, quantity, quantity unit, **Dimension to Cut**, cut-length unit, remarks, row action | Existing process/work-centre details and machining/making inputs where relevant |
| CNC | Plate/part to cut, material picker, quantity, quantity unit, **Per Part Weight (kg)**, row action | Length/width/thickness and their dimensional unit when relevant; total consumption weight as a labelled derived value; relevant process details |
| Store / purchased component | Existing canonical item/Part picker, quantity, unit, sourcing/issue route, row action | Existing specification/manufacturer details as applicable; pinned child revision visible where a canonical Part is selected |

Rules:

1. MCL/TS cut length corresponds to saved `DimensionMM` / `DimensionInches`, `DimensionText` and the appropriate unit/evidence. It is **not** interchangeable with the generic `Length`/`Width`/`Thickness` triplet. A valid single cut length must not force the user to invent width and thickness.
2. CNC per-part weight corresponds to saved `PartWeightKg`. Do not silently substitute total weight, a different quantity, zero, or an inferred density calculation. Preserve distinct saved `WeightKg`/`WeightGrams` and their meaning where present.
3. Prepopulate existing values from the accepted Grist definition or the saved draft. Preserve blanks, missing evidence, explicit zeros and conflicting legacy values distinctly; unknown is not zero. Where equivalent mm/inch representations exist, validate consistency and preserve provenance rather than silently changing the authoritative value.
4. Use mm/inch cut-length controls with explicit units and kg for the named CNC per-part weight. Preserve precision; an unchanged save must not round or rewrite old values. Conversions must use the existing supported unit policy and record an intentional edit coherently across numeric/text/exact-value representations.
5. Opening/closing the editor or changing an unrelated field must not erase hidden physical/process/specification attributes, rate evidence, or source cost dates. Unclassified legacy identities remain visible for review, not silently repaired or dropped.

## 2. Tabular editing and adding rows

- Use separate family tables or explicit family tabs: **MCL**, **Tool Shop**, **CNC**, **Store**. Each active table shows its applicable columns; do not display all families' irrelevant fields in every row. A full Excel feature set is unnecessary.
- Keep the accepted view read-only by default with Edit Part, Revision history and Compare with ODS. Entering Edit Part opens these tables using the same saved draft, not a second editing system.
- Provide an obvious Add row control for each table, or an Add requirement control requiring an explicit family choice before insertion. Do not silently choose the first applicable family.
- Each successful Add row action creates exactly one uniquely identified draft row in the chosen table. Make it visible, scroll into view if needed, and focus its first meaningful editable cell. Repeated deliberate clicks create distinct rows. Busy/recovery locks must prevent accidental or conflicting actions and explain why an action is unavailable.
- Adding the first requirement in a previously inapplicable manufacturing family requires an explicit applicability decision in the draft. Do not re-request initial establishment confirmations. Store is an editor choice mapped to existing component/purchase semantics, not a new invented manufacturing process family.
- Existing rows are editable in place. Preserve stable identities when changing values. Replacing a child Part or changing a manufacturing row's family must follow existing identity rules; do not disguise removal/addition as a mutation of an unrelated occurrence. For existing rows, a family change can be handled explicitly as confirmed removal plus a new row.
- Quantity and short unit controls should be compact; material/item pickers and remarks should have substantially more space. Provide readable headings, keyboard focus, inline errors and usable horizontal scrolling on narrower screens. Stable row keys must keep focus and errors attached to the intended row after insertions/removals.
- Normal editing must not require JSON, internal record IDs, duplicate unexplained quantity boxes or a free-text material name in place of a usable picker. Preserve advanced saved attributes behind a labelled details control where necessary.

## 3. Pickers and validation

- Material/item pickers search existing canonical choices and show their human-readable names/codes. Populate them using actual master display fields; blank option labels are unacceptable. Retain a legacy saved label if its canonical match is unresolved, and require an explicit valid resolution where existing publication rules require one. Do not silently create new master identities.
- Units are selected from the existing supported/master units, filtered for the field and identity. Preserve a legacy unit visibly even if it cannot currently be selected for a new row. Length is a numeric value plus a supported unit, not an arbitrary list of fixed lengths.
- Validate on field edit/blur and before Save/Request approval. Quantity accepts finite positive numbers; reject alphabetic/alphanumeric values, NaN/infinity, invalid signs and malformed decimals, including pasted input. A temporary blank while typing must not silently become zero. Apply whole-number restrictions only where the existing unit/business rule requires them.
- Cut length and CNC per-part weight accept finite positive values when required for that row's consumption basis. Validate other dimensions and units according to their family, without applying the plate-dimension triplet rule to a single MCL/TS cut length.
- Reject unresolved required material/item/child identity, unsupported unit combinations, inconsistent dependent values and component cycles. Show the row and field that needs attention. Block Save/approval for an invalid changed/new row; mirror the checks on the server so direct API requests cannot bypass them.
- Retain unchanged legacy data under existing compatibility rules with explicit warnings. Missing consumption/rate inputs must remain visible and cannot be described as a complete cost. Missing external rates alone are not a reason to prevent saving an otherwise valid physical definition.

## 4. Costing must consume the maintained inputs

Trace the whole path: table -> API validation -> Grist draft -> approved physical revision -> normalized consumption -> existing cost calculation. Merely displaying or saving a cut length/weight is insufficient if current costing continues using a stale quantity.

- Keep **quantity of pieces/occurrences**, **physical consumption per piece**, **total consumption**, and **commercial rate/unit** distinct. Define the mapping to existing `QuantityPerPart`, detail `Quantity`/`QuantityUOM` and weight/length fields before implementation. Multiplicity is applied exactly once at each level.
- Under an eligible existing length-based rate, changing MCL/TS cut length must change consumption/cost appropriately. Under a kg-based CNC rate, changing per-part weight must change consumption/cost appropriately. A per-piece process rate must retain its existing meaning and must not be multiplied by weight merely because weight is present.
- Use existing validated material conversion evidence when a length-to-weight conversion is needed. Do not invent density, weight-per-metre, scrap allowance, minimum charge or a new pricing policy. If required conversion/rate evidence is absent, expose the exact missing input and return incomplete cost rather than zero, a stale cached total or a fabricated conversion.
- Show a concise, unit-labelled calculation basis; any rate-basis control that must remain editable must explain its relationship to piece quantity. Prefer derived read-only consumption over asking the user to enter duplicate quantities without explanation.
- Preserve commercial rate authority, cost-date/aging semantics and old snapshots. A physical edit/recalculation does not make an old rate newly updated. No automatic monetary snapshot or full-costing authority decision follows from publication.

## 5. Removal and row-level history

- Remove row asks for confirmation identifying the family and item. Cancel leaves the draft unchanged. Confirm removes it immediately from the active draft table; it must not remain as an editable deleted row or reappear after draft Save/reload. A short removal summary can identify changes pending publication.
- Removing a never-saved new row does not create an accepted historical requirement. Removing an existing accepted row changes only the proposed successor membership. The current accepted view remains unchanged until Irshad approves publication; afterward the removed row is absent from the current definition.
- Revision history must expose a readable **Changed rows** view for each managed revision: family, human-readable item, stable occurrence reference, Added/Modified/Removed, field label, old value/unit and new value/unit. Include actor, reason, time and before/after revision labels. Example: `MCL / Flats 40 x 10 / Dimension to Cut: 210 mm -> 250 mm`.
- History must show confirmed removals with their complete prior saved values and additions with their published values. For a modified row show only actual business field changes; unchanged internal metadata, JSON ordering or unit formatting alone must not create spurious changes.
- Read history from saved immutable CR/definition/revision records in Grist, including previously published managed revisions where the evidence exists. Label unavailable legacy evidence explicitly; do not invent before values. History remains available in a fresh browser without the original ODS.

## Dependency review and implementation guidance

Inspected on the baseline above:

- `ui/src/PartMappingView.tsx` already labels MCL cut length and CNC per-part weight in source tables. Tool Shop cut-length evidence is part of mapping/baseline normalization. Reuse the meanings, not an ODS read at editor-open time.
- `app/part_baseline.py` normalizes cut length and writes `DimensionMM`, `DimensionInches`, `DimensionText`, `PartWeightKg`, other weight fields and dimensions to `LineDetail`. Diagnose any projection gaps from stored rows before proposing a source re-import or migration.
- `ui/src/PartDefinitionMaintenance.tsx` currently renders generic Length/Width/Thickness controls and no explicit cut-length/per-part-weight controls. Add requirement appends to local state and automatically chooses a family; diagnose the reported invisible/non-created row, including viewport, applicability, state reload and recovery-lock behavior.
- `app/part_definition.py` already preserves these detail fields, validates identities and materializes immutable successors. Its generic dimension validation and detail/rate quantities need review for family-specific behavior. Maintain its physical-row ownership and original-key publication guards.
- `app/live_cost.py` currently consumes saved normalized detail quantity multiplied by linked Part quantity. Verify that physical edits update or correctly supply this basis; do not assume that changing a detail weight/length alone changes cost.
- Current history cards show revision metadata; CR review has a structural diff. Extend saved-history projection and human-readable row diff rather than relying on temporary client state or rendering raw JSON paths as the primary history UI.
- Review `ui/src/api.ts`, editor CSS, material/unit options and existing component/purchase services. Prefer existing storage. Any necessary additive schema or compatibility change must be justified and verified in disposable Grist, with separate deployment authorization later.

## Bounded acceptance criteria

| ID | Reviewer must demonstrate |
| --- | --- |
| pe-c1 | Existing saved MCL/TS cut lengths and CNC per-part weights appear with correct values/units in read-only/editor/review surfaces, survive unrelated edits and fresh reload, and work with the original ODS unavailable. |
| pe-c2 | Compact family tables show the appropriate columns, usable control sizes and keyboard/row focus. Existing rows are editable; advanced data survives without normal JSON editing. |
| pe-c3 | Actual Add row works for MCL, TS, CNC and Store: explicit family, exactly one visible uniquely identified row per action, first-row/applicability behavior, repeat additions and correct disabled/recovery behavior. |
| pe-c4 | Usable existing material/item/unit pickers and matching client/server validations reject malformed quantity, invalid consumption/units/identities and cycles, with row/field errors and preserved legacy compatibility. |
| pe-c5 | Known-rate fixtures prove cut-length and CNC weight edits change normalized consumption/cost correctly, including quantity multiplicity and unit conversion exactly once. Missing conversion/rates are incomplete, never zero or stale complete totals. |
| pe-c6 | Cancel/confirm removal behaves correctly; confirmed rows disappear from draft and saved reload, accepted membership changes only after approved publication, and all prior definitions/references/snapshots remain intact. |
| pe-c7 | Published history shows readable Added/Modified/Removed row and field before/after values with units and audit context, including removed rows and evidence-backed earlier revisions, after a fresh session without ODS. |
| pe-c8 | Existing controlled publication, dependency preview/automatic current assembly/configuration versioning, stable identities, date/aging, stale/concurrent guards and original-key recovery survive the new editor. Uncertain operations lock competing edits and recover exactly once after loss/restart. |

## Required verification and handoff

All gates require independent evidence against the exact submitted commit. A missing actual check is unverified, not pass.

1. `python-suite`: full repository tests and meaningful server regressions for physical input mapping, family validation, consumption arithmetic, immutable history and publication recovery.
2. `ui-suite`: full UI/standalone tests plus actual user interactions for add/edit/remove, typed and pasted invalid numbers, picker labels, focus, family switching, saved-history diffs and pending-operation locks.
3. `ui-build`: TypeScript and production build on supported Node24.
4. `isolated-grist-persistence-recovery`: actual disposable Grist draft/publication/fresh-adapter readback; initial and later revisions; unavailable storage; original-key retry after partial write, lost response and process restart; old-schema compatibility if affected. Record exact target/workspace/purpose and native backup SHA/integrity.
5. `isolated-browser`: reviewer independently exercises all four row types on actual UI/API/Grist. Include the screenshot's MCL+CNC/TS-inapplicable scenario, existing rows, visible addition, numeric validation, cancel/confirm deletion, Save/reload, Irshad review/publication, field history and missing-workbook operation. No production test records.
6. `cost-date-history`: independently checked arithmetic with supplied eligible rate/conversion fixtures; missing-data behavior, preserved source dates/ages, immutable historical snapshot checksums and no-write read/calculation checks.
7. `exact-commit-preservation`: designated branch/PR/full SHA, protected tracked/untracked files and deployment configuration unchanged, original coordinator binding retained. Preserve prior B2-P1 reports. No source ODS/Costing-New or production writes during implementation/review; no merge/deploy without later authorization.

Sequence: owner confirmation -> create one new immutable bounded task and dispatch the prepared assignment to the existing Luna Extra High execution chat -> independent Sol review -> separate owner merge/deployment decision. This corrective package should precede B2-P2/spare composition so they reuse a usable editor with complete physical inputs. Existing implementation execution and scheduler remain stopped until explicitly authorized.

## Clarifications and decisions before dispatch

No blocking business clarification is needed to prepare this spec. The following interpretations are explicit for the owner's confirmation:

- Excel-style means family-specific editable tables, not workbook import/export, formula authoring or a general grid product.
- Confirmed removal is immediate in the draft; accepted removal still uses the existing Irshad-approved revision workflow.
- Store means the supported shared canonical purchase/component requirement; Model Code Issue Slip selection is separate, and manufacturing routed through Stores stays manufacturing.
- Use existing unit/rate/conversion policies. If implementation investigation finds an unsupported conversion that requires a new business rule, report that specific decision before dispatching an expansion; do not invent one or claim complete costing.

**Owner confirmation received on23November2026. Dispatch this bounded package to the existing execution chat with model `gpt-6-luna` and reasoning `xhigh`.**
