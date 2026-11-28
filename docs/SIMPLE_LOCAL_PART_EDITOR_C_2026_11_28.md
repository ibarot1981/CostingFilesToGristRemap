# Package C — simple local Part editor

Owner explicitly authorized B and C. B was independently accepted at 9151c8debaf0bcbdbf08158e26166f5a558f91f2, round3. This is the immutable C implementation assignment, task simple-local-part-editor-20261128, on codex/safari-local-master-rates, draft PR12. No merge or deployment authorization.

Use gpt-6-luna/high in existing implementor chat, initial and corrections. Sol owns architecture/review. Five rounds maximum; preserve prior tasks and owner verification-method addendum. Do not reintroduce upstream catalogue capture into ordinary editing. Existing approved mappings and local mirror facts are authoritative for normal behavior. No initial-establishment append flow on established screen.

Use one accessible local combobox with saved material text visible before interaction, canonical/alias filtering and stable-ID selection. Avoid retaining the confusing Item Name/Material duplication. Display compact family-specific table columns and appropriately sized numeric/remarks controls, preserving process-specific engineering semantics and existing purchase/component routing. Hide advanced identity/calculation metadata behind a deliberate details action. Preserve stable requirement keys and controlled accepted-definition publication/history/dependency propagation.

Local data and pricing semantics must come from accepted B APIs/providers/version manifest. Do not cache stale per-piece physical mass as current master authority. Actual ODS comparison stays explicit; no automatic overwrite. ModelCode-specific Issue Slip and entry overrides stay in configuration.

Approved bounded acceptance criteria:

**sle-c1**: Edit Part has one prefilled Material To Use editable combobox per applicable row. Typing filters local canonical names and reviewed aliases, displays meaningful disambiguation, retains hidden stable canonical ID, and preserves the original saved value. No upstream catalogue/binding panel or long scrolling select is the normal editing flow.

**sle-c2**: MCL and physical Tool Shop rows show only material, one quantity/unit, cut length/unit, derived weight, remarks and Add/Remove controls; CNC shows plate part, material, quantity/unit and entered per-part kg/derived total; Store uses appropriate existing canonical component/purchase routing. Process-only TS semantics remain distinct. Controls are appropriately sized and irrelevant calculation/identity details stay behind a deliberate details action.

**sle-c3**: Existing rows can be edited; Add creates exactly one usable family row with correct applicability and focus; invalid numeric/unit/dimension values are rejected at form level with clear inline feedback. Removal Cancel preserves the row; confirmed removal hides it in the draft and fresh readback, while historical references/snapshots remain. Preserve keyboard/accessibility/focus behavior.

**sle-c4**: Known established baseline rows display selected saved material and correct current local weight without duplicate-consumption questions or renewed initial applicability confirmations. Genuine missing/ambiguous mappings have one concise explicit local selection path, no guessing or silent substitute, and unrelated edits preserve unresolved evidence rather than dropping it.

**sle-c5**: Accepted view remains read-only by default with Edit Part, Revision history and Compare with ODS. Draft Save/fresh reload, controlled Irshad-approved revision/publication with reason/row-field before-after history and dependency preview/current propagation work; prior accepted revisions, ModelCode-specific issue/entry overrides and snapshots remain distinct/preserved. Terminal validation failures retain authored values; uncertain writes lock/retry original payload safely.

**sle-c6**: All ordinary view/edit/typeahead/weight/eligible costing actions use B local persisted facts and coherent versions with Costing-New and ODS unavailable. No per-keystroke full-source fetch or upstream fallback. MCL quantities/kgm/cut and prices/charges apply once, CNC engineering weight and TS process basis remain correct; fresh-session readback/source-date aging and stale snapshot validation remain intact.

**sle-c7**: Repeatable actual backend/API integration starts from a newly established baseline using normal writer behavior, covers owner Angles2x75mmx5.8=.870kg, alias/unmapped/conflicting materials, duplicate occurrences and MCL/TS/CNC/Store add/edit/remove/save/publication/history/local-source-offline costing. Nonempty snapshot hashes and original-key stale/concurrent/partial/committed-loss/restart recovery verified. Full backend/UI-unit/Node24 build pass; only essential UI smoke, remaining human observations follow owner25Nov addendum.

**sle-c8**: Exact scoped committed/pushed SHA on designated branch/PR; captured unrelated edits/coordinator/owner4320/4321/source/production preserved. Actual tests use recorded fresh disposable QA with native SHA/SQLite-integrity backups and recoverable final lifecycle cleanup. No webhook/automatic sync/B2 composition/full ModelCode cutover/unapproved merge or deployment.

Required checks: python-suite; ui-suite; ui-build; isolated-grist-editor-recovery; isolated-api-offline-editor-costing; quantity-revision-date-history; exact-commit-preservation.

Comprehensive backend/HTTP API/persistence/recovery/unit/build checks. Essential browser smoke only; do not spend rounds on a broad automated browser matrix. Supply a stable test build and concise exact steps/expected values for remaining owner observations. If the owner asks Sol to do a bounded checklist, record that evidence. Pending UI results alone do not require a new correction round; retain immutable submission and use appropriate verification evidence handling.

Fresh disposable QA exact IDs/workspace/purpose and original coordinator/native backup SHA/integrity before/after. No production QA or source writes, owner ports4320/4321 untouched; never restore/reuse trashed QA. Current Part revisions and nonempty monetary snapshots must be tested, not merely empty-equal tables. Final integrated review includes baseline->editor->publication->local costing while source unavailable. Merge/deployment remain separate owner actions. Approved operational one-time import must use accepted guarded tool/preview/backup/runbook, never ad hoc writes.

## Current-data integration contract

# Existing-scope C integration checks to retain when freezing the task

The current UI `cutWeight` prioritizes saved `item.materialWeightEvidence` over the selected current local master. B updates current MCL costing independently. When implementing C, explicitly verify that ordinary calculated-weight display uses the current local version while historical revision/source evidence and frozen CostSnapshots retain captured facts. Avoid turning a commercial master update into a Part engineering revision.

Example: existing accepted material4,2pieces,75mm,old5.8kg/m. A new approved local master version at6kg/m should show0.45kg/piece and0.90kg current total, while historical snapshot0.435/0.870 remains unchanged. A ToolShop physical-stock30mmx2 example should verify current-weight display and its actual eligible process-price basis separately; a fixed per-piece service operation must not be changed to kg, and entered CNC0.25kgx2 remains0.5kg. Record the distinction between current derived display/eligible costing and saved engineering/source facts rather than silently replacing archived evidence.

A phased-out material already referenced by a saved requirement must remain visibly identified and preserved on unrelated edits, even though new-selection suggestions omit it. Verify this with current-version changes and fresh-session readback. These are the already authorized local-current-data/history/phaseout requirements, not new package scope.

Accepted B provider: app.safari_master_import.load_local_material_weight_catalog, injected into baseline/definition services by app.web. Current local reads use current_local_import_rows and its coherent version manifest. Existing definition/options API is GET /api/parts/{part_id}/definition/options. Inspect its committed DTO before wiring the combobox; no upstream fallback. Reuse accepted provider/version/date semantics.

Read .agent-workflow/inputs/baseline-local-sync-series-20261124/OWNER_VERIFICATION_ADDENDUM_2026_11_25.md. Review every criterion, with comprehensive backend/API evidence and only a short essential browser smoke. Supply stable exact-build owner checklist; never claim unperformed UI observations.
