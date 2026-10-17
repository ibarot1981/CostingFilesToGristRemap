# Pending owner requirements — 15 October 2026

Status: owner authorized a new PR and Luna implementation of R1, R2 and R3 on 16 October 2026. The approved requirements below are assigned together through immutable workflow task `safari-r1-r2-r3-20261016`, with five rounds maximum and independent exact-commit review. No merge, deployment or production changes are authorized.

## R1 — CNC Part Mapping column widths

Owner request: In CNC Part Mapping, “Plate Part to Cut” is too narrow, while quantity and the two weight columns use too much space.

Required behavior:
- Give “Plate Part to Cut” more width so part names are easier to read and wrap less.
- Narrow “Qty Of Part Used”, “Per Part Weight (Kg)”, and “Total Weight (Kgs)” to recover space for the part name.
- Apply this width adjustment specifically to CNC Part Mapping.
- Keep MCL and Tool Shop (TS) column widths as they are for now.

Exact widths have not been specified; choose appropriate proportions when implementation is authorized.

Visual reference: owner-attached screenshot `C:/Users/ibaro/AppData/Local/Temp/codex-clipboard-52ba1619-2c63-477e-a1ac-bf4fcf4d4152.png`. It shows part names wrapping into several short lines while the quantity and weight columns contain short numeric values with substantial unused space. Treat the screenshot as visual context, not as separate instructions.

## R2 — Product Model Code descriptions and management UI

Recorded 16 October 2026. Status: requirement and all five clarification decisions confirmed by the owner using the grill-me/grilling interview; implementation authorized on 16 October.

Required behavior:
- Display the Product Model Code description wherever the user selects model codes throughout the application.
- Provide a UI to add descriptions to existing Product Model Codes.
- Provide a UI to create new Product Model Codes with descriptions.

Visual references: owner-attached screenshots show Eligible Product Model Codes in workbook association (currently displaying “No description”) and the Intended sharing model-code selector (currently showing Product Model name below each code). These are examples of affected selection surfaces, not an exhaustive scope limit or separate instructions.
- `C:/Users/ibaro/AppData/Local/Temp/codex-clipboard-191e3dbb-830a-4214-af9c-dd03e07dd853.png`
- `C:/Users/ibaro/AppData/Local/Temp/codex-clipboard-e2085f2b-4ce1-4e69-b3d2-a794fbb2c3b2.png`

Owner-confirmed decisions (16 October 2026; answered Yes to Q1–Q3, then Yes to Q4–Q5):
1. Show descriptions alongside code identifiers while retaining Product Model context where currently useful; show “No description” for missing values.
2. Provide a central Product Model Code management screen, selecting an existing Product and Product Model to add/edit descriptions and create codes; creation of Products or Product Models is not yet requested.
3. Require a nonblank description for newly created codes; retain existing codes with blank descriptions until the owner fills them in, without blocking their existing use.

4. Allow creating a Product Model Code without an ODS workbook. Make it available in applicable selectors under existing eligibility rules, with workbook ownership/associations, Part assignments and costing configuration established only through their respective explicit actions.
5. Where model-code selectors offer search, match description as well as code and existing Product Model search context.

No clarification questions remain from this interview. Implementation authorized by the owner's 16 October instruction to assign R1–R3 to Luna.

## R3 — Usable manufacturing baseline review workspace

Recorded 16 October 2026. Owner-reported problem: manufacturing baseline controls in the Part Mapping side panel are not usable; screenshot shows the Part selector consuming the panel width and the review action clipped. Owner approved the design below and authorized Luna implementation on 16 October.

Visual reference: `C:/Users/ibaro/AppData/Local/Temp/codex-clipboard-6eca33bf-4746-46e8-90e7-22f22ddc0654.png`.

Approved design:
- Add a prominent “Review manufacturing baselines” action in the main Part Mapping toolbar for the selected workbook; open a dedicated full-width workspace with a return to mapping that retains workbook context and unsaved mapping drafts.
- Show a searchable list of mapped Parts with per-Part baseline status. The list provides navigation; review and publication remain explicit per Part.
- Give the selected Part a spacious review area with its number/name, workbook context, MCL/Tool Shop/CNC evidence, mapped-line counts, applicability/completeness confirmations, existing-content warnings and specific blockers.
- Keep the explicit “Establish Part baseline” action visible with a clear explanation when unavailable; show publication success and allow moving to the next Part.
- For an established baseline, offer workbook comparison in the same workspace with room for accepted and incoming values side by side.
- Link the empty Accepted Manufacturing baseline section in Parts to this review workspace with Part/workbook context; if several workbooks are mapped, let the user choose the baseline source explicitly.
- Keep establishment and Rev A finalization separate; preserve all existing validation, explicit confirmations, immutable evidence and comparison behavior. No automatic establishment or bulk finalization proposed.

Owner approved R3 and instructed creation/assignment of R1–R3 on 16 October 2026. Reviewer goal remains tied to independently verified SUCCESS for all three; assignment or implementation submission is not completion. Scheduler setup for the fresh pair is authorized by that instruction.
