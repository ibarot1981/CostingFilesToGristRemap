# Parts implementation review — 7 October 2026

Reviewed commit: `8d6a03a` on `codex/part-identity-foundation`.
Pull request: https://github.com/ibarot1981/CostingFilesToGristRemap/pull/3

This review records the findings against the original PR commit. The 7 October completion update below supersedes its original verdict and required-next-slice list; the original findings remain as review history.

## Verdict

The implementation provides a working local Part identity and mapping foundation. It does **not** yet satisfy the requirement that canonical Parts, permanent numbers, names, engineering revisions and their histories ultimately be stored in Safari Manufacturing in Grist. Do not treat the current PR as completion of that requirement.

The review inspected the application write paths, schema, UI, tests and the live Safari Manufacturing document using read-only requests. No live business records or schema were changed. Existing uncommitted design documents were preserved.

## Findings

### P1 — Canonical Part data and new mappings never reach Grist

`app/part_identity.py:92` declares SQLite the source of truth. Creation writes `parts`, `part_metadata_version`, `part_name_index` and `part_request_log` in a local transaction. The default database is `state/safari_parts.sqlite3`, overridable with `SAFARI_PART_DATABASE_PATH`. Metadata, shortcode and retirement histories also live there.

`app/part_mapping.py:69` explicitly rejects Grist Part writes. `PartRegistryMappingStore.append` sends new mapping reviews to SQLite; managed mappings set the Grist `ProductPart` reference to null and store a local `PartIdentity` UUID instead. `canonical_part_records` produces Grist-shaped dictionaries for the application; it does not publish records to Grist. No Part replication/migration path was found.

A local transactional number allocator can remain an implementation component, but that is different from making all Part business data local. The correction needs durable request/number reservations, recoverable Grist publication, reconciliation after response loss and globally coordinated creation. Separate databases on different application hosts would each allocate `SM-P-000001`; same-host concurrency tests do not establish multi-host uniqueness.

### P1 — Managed Rev A is not a Grist engineering revision record

Managed Parts are correctly constrained to A in SQLite and server-side creation. Scope/name metadata updates preserve UUID, number and A. However, no Grist `PartRevision` baseline is created for them. The live table still has numeric `Revision`, with a legacy value of 1. That source/pilot revision must not be relabelled as CR-approved engineering Rev A without a reviewed migration.

The target model should explicitly store engineering `RevisionLabel = A` with its Part reference. A is the initial baseline, not evidence of a completed CR. Rev B and later remain unavailable until the approved CR flow is implemented. Name/scope metadata versions and source/line versions remain separate.

### P2 — Parts explorer is not collapsible

`ui/src/PartsView.tsx:254` renders group headings followed by all Part buttons. There is no tree expansion state, expand/collapse control or nested Product → Model → Code explorer. The dedicated tab and two-pane layout are present, but the explicit collapsible explorer requirement remains open.

### P2 — Detailed relationships are not connected

`app/web.py:515` returns reviewed workbook coordinates as partial process-line evidence, rather than joined normalized quantities, weights and line revisions. Drawings and explicit per-code configuration are returned as unavailable. This is honestly labelled in the UI, but the complete detail page is not implemented. Mapping a source group does not establish an accepted normalized line ownership relationship or an actual per-code BOM assignment.

Use the existing `LineMaster`, `LineRevision`, `LineDetail`, `SourceLineObservation` and `SourceLineMapping` foundation when completing these links. Drawings need a revision-specific link store. Actual usage belongs in explicit configuration selections; naming scope never auto-assigns codes.

### P2 — New Parts UI lacks automated interaction coverage

The suite covers identity creation and metadata through Python, and mapping interactions through UI tests. No automated UI test imports `PartsView`. Passing checks therefore do not verify guided creation, edit prepopulation, detail navigation or explorer expansion. Add meaningful interaction coverage when correcting those flows.

## Verified behavior

- Stable UUID identity and permanent `SM-P-000001` numbering; transactionally unique across processes sharing one local SQLite database, with restart and retry coverage.
- Generated names from maintained scope shortcode, description and optional meaningful design variant, for all four scopes.
- Normalized current-name and historical-alias collision checks, including locally indexed legacy Grist names.
- Audited name/scope changes retaining number, identity and Rev A; retired numbers remain reserved.
- Advisory out-of-scope warnings with saveable assignments; broad scope does not create configuration usage.
- Separate creation and mapping Save, source/association/version checks, partial mapping and response-loss retry protection.
- Dedicated Parts tab, guided panel, stable `#parts/<uuid>` detail route and preserved mapping return context.

## Live read-only evidence

Document identity was validated as Safari Manufacturing in the configured workspace, separate from the legacy target.

| Live Grist table | Records | Evidence |
| --- | ---: | --- |
| `ProductPart` | 1 | `temporary_unallocated`; no `PartNumber`, stable UUID, scope or metadata-version columns |
| `PartRevision` | 1 | `Revision` is Numeric; existing value is 1 |
| `PartMappingReview` | 0 | Existing reference is `ProductPart`; no managed Part revision reference |
| `LineMaster` | 313 | Existing Part ownership uses `Ref:ProductPart`; this count includes all statuses |

The configured local registry exists but contains zero Parts, mappings, metadata versions and scope shortcodes at review time. Passing tests used isolated temporary registries; they did not create production Parts.

## Checks rerun

- Python unittest suite: 159 tests, 158 passed and one platform skip.
- Vitest: 23 tests passed in seven files.
- Mapped-file view-model checks: two passed.
- TypeScript type check and Vite production build: passed.
- `git diff --check`: passed, with line-ending notices on the pre-existing design edits.

## Required next slice

1. Extend the existing Grist `ProductPart` entity rather than introducing a competing Part master. Implement persistent typed metadata, aliases, shortcodes, engineering baseline and mapping references as described in `PARTS_ENTITY_DIAGRAM.md`.
2. Make Grist canonical for business records. Retain a coordinated allocator/reservation mechanism if needed, with recoverable publication and migration preserving UUIDs, numbers and history. Save must not report success before the durable Grist result is established.
3. Review legacy pilot identities and numeric revisions without destroying source evidence or assuming CR approval. Preserve all normalized lines and their provenance.
4. Implement the collapsible explorer and verify the real Parts creation/edit/navigation flows. Keep unavailable drawings/configuration/typed-line links explicitly labelled until implemented.

## Completion update — 7 October 2026

The implementation on the current PR branch addresses the reviewed Part slice. Schema v8 is applied to the configured Safari Manufacturing document, and Grist is canonical for managed Part business data. SQLite is now a single-host allocation/coordinator and retry journal only. The current schema and relationships are in `PARTS_ENTITY_DIAGRAM.md`; that file reports the deployed schema separately from deferred per-code configuration.

- **P1 canonical persistence — resolved in code and isolated Grist verification.** The real Grist REST adapter persisted Part, Rev A, metadata and alias history, mapping, composition, vendors, purchase specifications/mappings/records and historical cost evidence. A new registry instance re-read those records after restart. A commit-then-timeout recovery test confirmed a same-key retry completes without duplicating records. No managed test Part or purchase was added to production.
- **P1 managed engineering baseline — resolved for newly managed Parts.** Grist stores an explicit Rev A `PartRevision`; finalization locks its physical definition. The legacy numeric `PartRevision` remains unchanged and unclassified.
- **P2 collapsible explorer and details — resolved for the implemented UI.** Browser verification expanded the hierarchical scope/model tree and opened Part details. Mixed MCL, Toolshop and CNC links and a child Part quantity were visible. Purchased-Part detail displayed latest actual rate/history and the capture form. Existing Part UI tests now exercise expansion and avoid false unmatched-target rows.
- **P2 relationships — partially resolved.** Exact process-line revisions, child revisions/quantities, drawings, purchase details and mapping references are stored in Grist. `Used in` honestly reports that explicit per-code configuration is deferred; Summary import/cost-run integration is not complete.
- **Production migration — additive, no business relabeling.** A native `.grist` backup passed SHA-256 and SQLite integrity validation before schema v8 was applied. Production readback showed one legacy ProductPart, one legacy numeric revision, 313 LineMaster records, no PartMappingReview or managed business fixtures, and one writer-coordinator row. Legacy ownership/revision were not rewritten. Isolated synthetic data remained in a temporary Grist document and that test document was moved to Trash.

Updated verification: 174 Python tests passed with one platform skip; 25 Vitest tests passed; TypeScript check and production Vite build passed. The additional isolated browser check used only local mock records. The PR remains subject to review and is not merged by this implementation.
