# Part Mapping implementation — 8 October 2026

## Behavior

Part Mapping now groups active rows by **source sheet plus the configured mapping label**. Material Cut List (`5. Material Cut List Price`) and Tool Shop Items use `Machine Piece Description`; CNC Cut List uses `Part Category`. CNC `Plate Part to Cut` remains line-level evidence and is shown in the source detail table. A blank mapping label remains its own row. Missing or ambiguous label headers are diagnosed and block assignment saves until the workbook header is resolved.

The v2 group identity does not carry an old assignment forward. Existing `PartMappingReview` rows stay immutable and appear as previous-assignment evidence when their sheet/row coordinates still match. This is especially important when CNC moves from plate description to category, or a former cross-sheet description group splits. Each current row resolves its own latest previous evidence; the user must explicitly select and save a new assignment. Records without matching source coordinates cannot be safely associated with a current group and remain history only.

The review displays source values from the saved workbook, including exact source header and cell coordinates. Material Cut List shows Material to Cut, Dimension to Cut (mm), Quantity Nos, Item Group 1 and In Use by default; expanded details add Remarks and CR Log. Toolshop and CNC show their parsed fields, with CNC plate detail and dimensions retained. Missing and blank source values remain distinguishable; values are not recalculated or converted to zero. Each group has a bounded, scrollable details block.

Source filters (All, MS List, Toolshop, CNC), review-state filters and source-text search only change visibility. Draft choices, reasons and browse context remain local until an explicit Save. Single-group Save writes only that group. Batch Save previews every pending group, including filtered-out groups. Explicit clears are saved as audited rows; prior decisions remain in history. A successful save refreshes Grist state and retains other drafts only when workbook, association and group evidence are unchanged. Failed attempts preserve the exact idempotency key and payload for retry. Changed evidence holds local choices aside for explicit review rather than silently reapplying them.

The Part combobox uses bounded server search over permanent number, current name, description, variant, stable ID and supported aliases. It excludes retired, unpublished and ambiguous-name records. Scope warnings are advisory and selection does not edit intended sharing. `View Part` opens the selected Part. `Browse / create Part` carries the source group/workbook context into Parts and returns a chosen Part as an unsaved mapping draft; it never saves the mapping automatically.

## Persistence and compatibility

Confirmed assignments are append-only `PartMappingReview` rows in the Safari Manufacturing Grist document. Rows reference `ProductPart` and stable Part identity where available, and freeze the workbook key/hash, association key/version, mapping group, source sheet/row/description, Part number/name/revision/metadata used, reviewer, reason, time, request key/fingerprint and batch version. Partial batches are supported: unresolved groups have no new review rows. This work required no Grist schema migration. The local Parts journal serializes the supported writer; it is not business-data storage.

After a Grist save, the application reads back the review rows before showing `Saved`. Reloading the same workbook restores the current sheet-scoped assignments from Grist. Older description-only groups remain review evidence, not a hidden migration. An audited clear records a null Part decision without deleting the previous assignment history.

## Verification

- Full Python suite: 207 tests run, 206 passed, 1 platform-specific skip.
- UI suite: 36 tests passed across 9 files. TypeScript check and production UI build passed.
- A read-only validated Safari source document was copied with Grist's `asTemplate` option into a disposable document. A synthetic canonical Part and one mapping row were written only in that copy. A fresh Grist client/registry recovered the saved Part Mapping row; an exact retry returned idempotently without a duplicate; the same label in a second sheet stayed unresolved. The copy was moved to Grist Trash. No production Part Mapping assignment was created.
- An isolated local browser/mock session exercised the single-group save and visible saved state. Part Mapping was inspected at default desktop width and 840 px; Files and Parts were also inspected after the shared masthead change. Automated UI tests cover failure/retry, draft preservation, filters, stale evidence, keyboard selection and Parts return flow.

The existing production mapping table remains unchanged by this verification. As of the latest read captured before this work it contained no mapping assignments, so the owner’s live Chassis was not modified. Production write behavior is not inferred from local SQLite or the browser mock: the persistence evidence above comes from the separate real Grist test copy.

## Remaining boundaries

- Older mapping rows without source sheet/row evidence cannot be matched safely and require a manual review from the immutable history.
- This change does not create manufacturing composition, Model Code configuration or costing snapshots from a mapping. Those remain separate governed actions.
- Existing project gates remain: automatic Summary import/authority integration and the approved Change Request flow are not implemented by Part Mapping.
