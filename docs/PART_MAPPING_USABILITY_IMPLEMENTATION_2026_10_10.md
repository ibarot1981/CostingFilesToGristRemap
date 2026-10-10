# Part Mapping usability implementation — 10 October 2026

This record completes the four agreed usability improvements from the 10 October Part Mapping prompt, building on the layout and inline-creation design in [the 9 October brief](PART_MAPPING_LAYOUT_AND_INLINE_CREATION_2026_10_09.md), the existing [Part Mapping implementation](PART_MAPPING_IMPLEMENTATION_2026_10_08.md), and the shared [Parts UI design](PARTS_UI_DESIGN.md).

## Interaction and design

- Supporting workbook/review information, extended statistics, assignment history, baseline and comparison controls live in a retractable left context panel. Its preference persists. It stays mounted while hidden so baseline state and exact retry identities remain available. At narrow widths it becomes an overlay drawer with keyboard dismissal, focus return, and a scrim.
- The Mapping title, workbook name, filters and group list stay in the compact main workspace. Groups keep their own evidence disclosure. Their header holds the independent disclosure button, sheet/description/row count, canonical Part selector, creation/view/revert/clear actions, individual Save and mapping state. Replacement and clear reasons sit on a compact adjacent row.
- Search suggestions render in a fixed portal outside the scrolling group list. The page preserves drafts, filters, scroll position, group expansion and the originating group across Part creation.
- Mapping, context-panel and shared Part form fields use stable application-specific names, `autocomplete="off"` where applicable, and appropriate spellcheck/capitalization hints. Searchable Part suggestions remain keyboard accessible. These are supported browser hints; a browser or password manager may override them.

## Shared contextual creation

`ui/src/PartCreationForm.tsx` now owns the creation form and state flow used by both the standalone Parts page and the Mapping dialog. Both paths share scope/anchor selection, shortcodes, server-generated name previews and collision checks, description/variant, intended-sharing selection, validation, Rev A/number display, audit reason, publication and exact-request recovery.

From Mapping, **Create Part** opens a labelled modal with read-only workbook, source-sheet and source-description context. It suggests saved workbook Product/Model/Model Codes and a naming anchor while leaving them editable. It does not require workbook selection. A newly published Part is applied from Grist's returned record to only the captured matching group as an **unsaved mapping draft**. The user must separately Save that mapping. Mapping is not reloaded, and creation does not establish a baseline, configure Model Codes, increment a revision or create costing history.

Creation request key, payload and source-group identity survive an uncertain outcome in `sessionStorage`. The dialog cannot be dismissed while recovery is required; Retry uses the original key and body. Reload restores the attempt with its original source context. The originating path, saved association/version, source hash, group key and evidence fingerprint are checked again before assignment. If they changed, the Part is preserved but not attached to a different group. A same-name selectable canonical Part can be used through the same context guard.

## Persistence and business boundaries

Canonical Part creation and mapping review remain separate Grist publications. The mapping draft is never represented as saved until Grist confirms the explicit individual or batch save. Initial assignments remain reason-free; replacements and clears require a reason. The refactor does not change the schema or business semantics, baseline/configuration state, accepted requirements, or immutable snapshots.

## Verification

- Python `unittest` discovery: 233 tests run; 232 passed and one was skipped.
- UI checks with the bundled Node 24 runtime: 64 Vitest tests passed across 13 files; the standalone mapped-files checks passed (2); TypeScript validation and the production Vite build passed (1,597 modules).
- An isolated browser used a disposable Grist-backed local app. At 1366×900, the collapsed group header, individual Save placement, contextual dialog and searchable selector results were inspected. At 823×900, the overlay context drawer, background scrim, wrapped Mapping controls and Escape dismissal/focus return were inspected. The browser viewport override was reset after the checks.
- Real Grist writes were made only in a disposable full-data copy in workspace 3. A synthetic Part (`SM-P-000004`) was created with metadata/name/revision history and eight normalized intended Model Code links. The original group remained a draft until its individual Save. Fresh Grist reads confirmed the Part and intended-sharing rows first, then three mapping-review rows for the saved source rows and the incremented review version. The test copy used an isolated temporary coordinator journal. No production Part or mapping was created and no production schema was changed.
- Browser hints were inspected in the rendered controls and tests; they cannot prevent password managers or browser extensions from overriding them.

The Grist write/read-back above is the persistence evidence; mocked tests and local SQLite state are not used to claim Grist persistence. The disposable document was moved to recoverable Trash after verification. No merge or deployment was performed.

## User workflow

1. Choose **Hide supporting information**; use **Show supporting information** to restore the left panel later.
2. Assign a Part in the desired group header, even while source evidence is collapsed, then use that header's **Save**. Batch Save remains available for multiple drafts.
3. Use that group's **Create Part** action, confirm or edit the contextual suggestions in the dialog, complete the reason, and select **Create and assign**. Then save the mapping separately.
4. If the response is uncertain, return to Part Mapping and use **Retry same creation request** in the restored dialog. Do not begin a second Part creation for that attempt.
