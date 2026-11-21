# B2-P1 Part Definition Maintenance — Implementation Notes

## Scope

This package adds direct maintenance of established Part definitions through Safari Grist. It covers saved drafts, validation, review, Irshad-approved engineering change requests, immutable successor revisions, revision history, and automatic successor versions for current assembly and Model Code configuration dependents.

This package does not complete B2-P2 ODS comparison/adoption, B2 spare composition, general role-based access control, or full Model Code costing authority. Unsupported spare composition is reported as unsupported. Part definition authority does not change commercial rate policy or Model Code cost completeness.

## Data and migration

The additive Grist definitions are in `app/schema.py`. The Part definition service uses the existing Grist repository and coordinator journal; `PartDefinitionDraft`, `PartDefinitionVersion`, `PartEngineeringChangeRequest`, and `PartDefinitionPublication` store editable drafts, immutable content versions, approval requests, and durable publication plans/results. Current dependent assembly and configuration versions retain their own pins and carry the publication cause. Historical revisions and configuration selections keep their prior pins.

For any later deployment, first obtain explicit deployment and schema-write authorization. Download and validate a native backup for the exact approved document/workspace, then apply the additive schema through the repository's existing schema-management path. Verify the expected tables/columns and baseline compatibility with a fresh adapter before enabling writes. This implementation did not migrate or write production Grist, ODS, or Costing-New.

## Normal operation

Open an established Part from the Parts view to read its saved requirements, applicability, engineering revision, and baseline provenance. Use **Edit Part** to save a draft in Grist, review its impact and before/after definition, and submit a change request. The configured Irshad approver publishes the request to a successor revision. **Revision history** retains accepted engineering versions; **Compare with ODS** remains an explicit source comparison action. Normal viewing, editing, draft save, and publication use saved Grist content and do not need to open or hash the source workbook.

The relevant HTTP routes are registered in `app/web.py`; the UI calls are in `ui/src/api.ts` and the direct editor is in `ui/src/PartDefinitionMaintenance.tsx`. Existing Part Mapping and costing services remain responsible for source assignments and commercial rates respectively.

## Verification and limitations

The round-1 Python suite passed (342 tests, one skipped), the UI suite passed (140 tests), and the Node 24 TypeScript/Vite production build passed. The build reports the existing large-chunk advisory.

An isolated Grist/browser flow saved and reloaded a draft, published Rev B through an Irshad-approved change request, and confirmed current assembly and Model Code configuration successors reference the new Part revision. The QA document and native SQLite backup are retained with the implementation evidence. A same-key publication retry returned the published result without duplicate successor rows.

The isolated QA fork contains no eligible `CostingProcessRate` or purchase-history input. Its live-cost read therefore remained incomplete; the calculation made no cost-history writes. Costing from eligible rate data and full cost-date aging behavior remain subject to independent verification against suitable rate fixtures. No complete costing claim is made here.
