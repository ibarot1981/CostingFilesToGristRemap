# Parts page design

Owner direction recorded 6 October 2026. Implementation model: GPT-6 Luna (`gpt-6-luna`) with Extra High (`xhigh`) reasoning. This document describes the proposed UI; no application screen is implemented by recording it.

Use a dedicated Parts tab reachable from Part Mapping. Reuse Files page components, styling, typography, field controls, validation, selected-row behaviour and keyboard accessibility. The layout has two panes; a full details view is a separate navigation state, not a third pane.

## Explorer and working panel

Left: Parts explorer, initially approximately 280px wide at desktop size. Search by number, current name or alias; group by sharing scope and its target. Rows show description/variant, permanent number and Rev A. Show current selection and a New Part action. Filtering/sorting/pagination must handle larger registers without loading all line details.

Right: guided creation when New Part is selected; otherwise the selected Part's summary and detail sections. Stack panes on smaller screens and preserve selection/search on return.

Creation questions:

1. Where is the Part intended to be shared? Global, Product, Product Model or Model Code.
2. Which Product/Model/Code? Show the appropriate master selection; Global uses the maintained global shortcode.
3. What is it called? User-entered description, such as Chassis.
4. What distinguishes this design? Optional meaningful variant, required to resolve ambiguity/collision.
5. Why is it being created? Audited reason.

Show the generated name inline in the same panel, not another pane. Show permanent number as allocated on Save and engineering revision as A. Validate with the server and keep duplicate resolution understandable. Save creates only the Part. Optional drawings can be linked in its details; lack of a drawing does not block creation.

## Full Part details

Open full Part details navigates using stable identity and preserves an explicit Back to Parts return context. Suggested sections:

- Overview: number, generated name, description/variant, scope/target, Rev A, status and metadata version.
- Process lines: linked MCL, Tool Shop and CNC requirements with available material/item, quantities, weights and source evidence. Open individual records for full measurements, provenance and audit. Show unresolved/historical status and empty/unavailable states. Additional Part relationships may be displayed when already available; do not infer ownership of Store Issue or Paint/Packing rows.
- Drawings: optional linked drawing/file name, associated Part revision, separate drawing/file version and a safe preview/open action where available. A renamed Part retains drawing references; drawing changes do not automatically revise its engineering definition.
- Used in: explicit Model Code references and revision usage. Out-of-scope use warns without blocking Save or broadening scope automatically. Scope is never a configuration whitelist.
- History: separate engineering baseline (Rev A), audited name/scope metadata changes, aliases and source assignment history. No Rev B+ editing until governed CR exists.

New Parts correctly show no process lines, drawings or code uses until those relationships are saved. Older imported records must not falsely claim CR approval or newly implemented relationships.

## Mapping navigation

Part Mapping can open Parts creation or selected-Part details, carrying the workbook, group/row and draft mapping choices as return context. On return, a selected/new Part may populate the relevant choice; assigning the source rows still requires the explicit mapping Save. Warn about unsaved edits when navigation could discard them. Do not use similarity matching to silently assign Parts.

## Files layout proposal

The separate workbook preview page makes a two-pane Files layout reasonable: file explorer on the left and association/details on the right, with Open Workbook Preview navigation. The owner's Files-layout wording was ambiguous; this is a proposal, not an instruction to refactor Files as part of Part creation. Confirm the exact Files change separately if implementation would include it.

## Verification

Verify creation, collision resolution, generated names, explorer search/selection, full details/back navigation, optional drawing empty states and mapping return context. Preserve Parts' number and Rev A across scope/name changes. Exercise out-of-scope use as warning-only. Use isolated test records for interactive creation; do not fabricate live drawings, Part records or assignments.
