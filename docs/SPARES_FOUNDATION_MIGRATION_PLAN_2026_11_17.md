# Spares foundation schema migration runbook

This is a future operator runbook for schema `safari-spares-foundation-grist-2026-11-17.v16`. It is not authorization to merge, deploy, or change Grist. No production migration was run for this implementation.

## Preconditions

1. Obtain separate owner approval for the release and production schema change after independent review. A successful review or merge alone does not authorize the migration.
2. Use the approved deployment checkout and its supported single-writer host. Set `GRIST_API_KEY`, `SAFARI_MANUFACTURING_GRIST_DOC_ID`, and `SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID` in ignored deployment configuration. Keep `GRIST_DOC_ID` only as the existing legacy-document guard; the target must not equal it.
3. Resolve the production target by exact document ID and workspace ID. Confirm the document is named `Safari Manufacturing`, is in the approved workspace, and is the existing operational document. If the read-only plan cannot resolve that exact document, stop. Do not proceed on a name match alone or allow setup to create a new document as a substitute.
4. Schedule the approved change window and ensure the normal recovery owner is available. Do not stop or restart an owner service as part of this runbook unless a separate owner-approved deployment procedure requires it.

## Plan and review

From the repository root, run the guarded, read-only plan:

```powershell
.\.venv\Scripts\python.exe main.py safari-setup --workspace-id "APPROVED_WORKSPACE_ID"
```

Confirm the printed document ID, document name, workspace ID, and schema version `safari-spares-foundation-grist-2026-11-17.v16`. Review the complete plan. Against a verified v15 document, the expected delta is only creation of these eight tables: `SpareMaster`, `SpareMasterRequest`, `SpareAlias`, `SpareHistory`, `SpareSourceEvidence`, `SpareCompositionEvidence`, `SpareSourceCost`, and `SpareImportBatch`. There should be no column updates or changes to existing tables. Stop for any unexpected target, table, column update, or non-spares change; refresh the plan and resolve the discrepancy before approval.

## Apply after explicit approval

Only after the preconditions and plan are independently reviewed, run the guarded apply and answer its confirmation prompt:

```powershell
.\.venv\Scripts\python.exe main.py safari-setup --workspace-id "APPROVED_WORKSPACE_ID" --apply --schema-apply
```

The CLI revalidates the target and plan, downloads a native `.grist` backup, verifies its SHA-256 and SQLite integrity, and only then creates missing tables/adds missing columns. Preserve the printed backup path, hash, integrity result, exact target IDs, reviewed plan, and apply output in the deployment record. If backup verification or the refreshed-plan comparison fails, do not retry by bypassing the guard; investigate and review a fresh plan.

## Post-apply verification and recovery

1. Run the read-only plan again against the same exact IDs. It should report no schema differences. Verify the spare tables and expected columns in Grist, and run the approved application smoke checks without importing production source data.
2. Keep the verified native backup and migration record under the approved retention policy. Do not delete additive tables or restore the whole document as an automatic rollback. If recovery is needed, stop and use a separately approved recovery decision with the preserved backup; assess writes made after the backup first.
3. The application does not apply this foundation schema at startup. Existing Part, baseline, costing, and source-workbook records are not rewritten by this additive schema plan.
