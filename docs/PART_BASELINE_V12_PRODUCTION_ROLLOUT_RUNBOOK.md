# Part baseline schema v12 production rollout runbook

Status: prepared for operator review. No production migration or deployment was performed as part of this verification.

## Reviewed target and change

The configured production document was read by its explicit ID and validated as `Safari Manufacturing` in workspace `3`. The reviewed ID is `bAPdkEDn7brbqTrfVsRmXZ`; the separate legacy `GRIST_DOC_ID` is not the migration target. Revalidate the current configured values at rollout time. The standard `safari-setup` command resolves documents by exact name and currently refuses because multiple documents have that name. Do not resolve this ambiguity by selecting a name match. Use the explicit configured document ID and verify its returned name and workspace metadata.

The exact reviewed schema delta is one additive field: `PartWorkbookComparison.MappingVersion`, type `Numeric`. It records the saved mapping review version captured with each workbook comparison and lets later decisions reject stale mapping assignments. No tables or type changes are part of v12.

## Rollout steps

1. **Schedule and pause writes.** Announce a maintenance window. Stop Part, mapping, baseline, comparison, decision and snapshot publication writes across all application instances, background workers and operator tools. Confirm one supported writer, no in-flight or uncertain publications and no active migration. Keep read-only access available if practical.

2. **Validate the explicit production target.** Confirm `SAFARI_MANUFACTURING_GRIST_DOC_ID` is `bAPdkEDn7brbqTrfVsRmXZ`, the workspace is `3`, the Grist base URL is the approved production URL, and the legacy ID differs. Fetch Grist document metadata by ID and validate exact name/workspace. Refuse the migration if any value or metadata differs.

3. **Take a fresh native backup.** Download the `.grist` archive from the validated explicit target using the repository's `GristClient.download_document_backup()` path. Write it to an access-controlled backup location with enough free space. Do not use a local SQLite application-state file as the Grist backup.

4. **Verify and record the backup.** Run SQLite `PRAGMA integrity_check` on the downloaded native `.grist` file and require `ok`. Recompute SHA-256 from the saved file and compare it with the digest computed from the downloaded bytes. Record the absolute location, UTC timestamp, file size, checksum, target document ID/workspace and integrity result in the change record. Keep the file through rollout acceptance and the recovery period.

5. **Reconfirm the schema plan.** With writes still paused, create a fresh read-only plan against the explicit, revalidated ID after the backup. It must contain no table creation, exactly `PartWorkbookComparison.MappingVersion — Numeric`, and no type updates. If it differs, stop and review the additional delta; do not apply it under this change.

6. **Apply the additive column and verify it.** Apply the reviewed plan through `app.schema.apply_schema` with the same document name, workspace and legacy-ID guard. Re-fetch `PartWorkbookComparison` column metadata and require `MappingVersion` to be Numeric. Run a new schema plan and require all three change lists to be empty. Do not backfill older comparisons from current mappings.

7. **Roll out the compatible application.** Deploy the reviewed PR build only after the schema readback succeeds. Start one writer instance first, confirm it connects to the same explicit target and shared writer coordination store, then enable writes according to the deployment's normal controlled rollout. Keep the previous compatible build available for application rollback.

8. **Run read-only production smoke checks.** Confirm the application health/readiness endpoint, Part explorer and Part Mapping load from Grist. Inspect schema metadata, current table counts and a small sample of existing comparison records without creating a Part, mapping, baseline, comparison, decision, purchase, configuration or snapshot. Confirm comparisons missing `MappingVersion` are displayed as requiring recomparison and are not assigned a guessed version. Verify logs show no unexpected schema plan or write errors.

9. **Close or recover the change.** Record the post-apply empty plan, column type, application build, smoke-check results and end time. Resume normal writes only after the owner accepts those checks. If any step fails, keep writes paused and follow the recovery section below.

## Recovery if migration or rollout fails

- Stop writes and preserve the migration logs, schema plans, application logs and both pre-change and current native backups with SHA-256 checksums.
- Revalidate the explicit document ID/workspace, then inspect the current `MappingVersion` column and obtain a new read-only schema plan. If the column is absent and the exact reviewed addition is still pending, correct the cause and retry the additive apply after a fresh plan check. If it is present and Numeric, verify with a post-apply plan before restarting the application.
- If the column exists with an unexpected type, or unrelated schema changes appeared, do not delete or rewrite the column. Keep writes paused and review a forward-compatible repair or an approved whole-document restore. A whole-document restore is appropriate only while writes remain paused and the owner confirms it will not discard later work; take and verify a current backup first.
- If the application rollout fails after v12 is verified, keep the column and roll back the application only to a build confirmed compatible with the additive field. If compatibility is uncertain, leave the application in maintenance/read-only mode until the reviewed build is restored. Do not attempt to reverse v12 by dropping the field.
- If any Part baseline, mapping or comparison write became uncertain, recover it with its original idempotency key and exact payload before allowing a competing write. Historical comparisons without a saved version remain stale and must be recomputed from verified current evidence; never infer their previous mapping version.

## Evidence boundary

The v12 schema, Grist writes, readback, retry recovery and browser interactions have been verified in disposable Grist copies. Production planning was read-only and showed the exact additive column above. Production schema application, production smoke checks, merge and deployment remain pending.
