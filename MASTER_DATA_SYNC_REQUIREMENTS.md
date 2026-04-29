# Master Data Sync Requirements

This document captures the planned requirements for a future workflow that edits the master costing `.ods` file and Grist together. It is intentionally separate from the current implementation notes so this can be revisited later without re-reading the chat history.

## Purpose

The costing `.ods` file is a master source of product costing data and may be linked from other workbooks or systems. For master-data correction workflows, the utility must update the existing `.ods` file in place instead of writing a new `.ods` file.

This is different from the existing "Create New Version" workflow, which should continue to write a new `.ods` file and preserve the original source file.

## Main Goals

- Provide an editable review workflow for verified ODS/Grist rows.
- Allow safe updates to both the current `.ods` file and the matching Grist row.
- Preserve the original `.ods` path and filename so external links remain valid.
- Create automatic backups before any in-place `.ods` changes.
- Keep a per-file audit history beside the original `.ods` file.
- Provide an HTML audit viewer for reviewing all changes over time.
- Support cleanup of rows that are no longer required in ODS or Grist.
- Detect and handle manual Grist edits made outside this utility.

## Editing Existing ODS Files

The future workflow should update the current `.ods` file in place, but only after creating a backup.

Even if edits are limited to non-formula cells, the choice of writer matters:

- `pyexcel-ods3` may rewrite the workbook package and could affect formatting, formulas, styles, external links, merged cells, print settings, or other ODS internals.
- `odfpy` targeted XML editing may preserve more structure but must handle repeated rows/cells carefully.
- LibreOffice UNO/headless automation is preferred for master files because it edits and saves the workbook through LibreOffice itself.

Preferred approach for in-place master edits:

1. Use LibreOffice UNO/headless automation where practical.
2. Edit only configured non-formula cells.
3. Check whether the file is already open before applying changes.
4. Create an automatic backup before saving.
5. Write an audit record for every attempted change.
6. Re-run verification after changes are applied.

## Proposed Local Web App Workflow

A static HTML report should remain a review artifact only. It should not directly update Grist or local `.ods` files because it cannot safely access local files and must not expose Grist API credentials.

Preferred future workflow:

1. Run Verify with Grist.
2. Start a local Python web app, for example `http://localhost:8765`.
3. Show an editable version of the verification report.
4. User edits allowed fields in the local web UI.
5. Backend validates all pending changes.
6. Backend shows a batch preview.
7. User confirms apply.
8. Backend creates an ODS backup.
9. Backend updates the current `.ods` file in place.
10. Backend updates Grist by `grist_row_id`.
11. Backend writes per-file audit records.
12. Backend re-runs verification.
13. Backend shows before/after results.

Initial editable fields:

- `product_part_name`
- `optional_item_group_1`
- `remarks`
- `cr_log`, once the correct Grist field is confirmed

Higher-risk key/costing fields such as material, dimension, quantity, rate, and cost should be introduced later with stricter validation.

## Identity and Matching

Edits must not rely only on the current match key because the user may change a key field.

Use stable identities:

- ODS target identity: `sheet_name + ods_row_number + configured field name`
- Grist target identity: `grist_row_id + configured Grist field name`

Keep the original match key in the audit log for review and validation.

## Per-File Backup and Audit Store

Each source `.ods` file should have its own backup and audit store located beside the original file, not in one global project-level audit store.

For a file like:

```text
C:\...\S1KHF\Local\Safari 1000 HF Local V 4.2.ods
```

Create a folder like:

```text
C:\...\S1KHF\Local\.costing_audit\Safari 1000 HF Local V 4.2\
```

Suggested structure:

```text
.costing_audit\
  Safari 1000 HF Local V 4.2\
    backups\
      Safari 1000 HF Local V 4.2_20260414_183000_before_apply_changes.ods
    audit.sqlite
    audit_viewer.html
    exports\
      audit_20260414_183000.csv
      audit_20260414_183000.json
    change_batches\
      batch_20260414_183000.json
```

Use SQLite for the per-file audit store because it is local, robust, queryable, and suitable for long-term change history.

## Audit Data to Store

Audit records should capture enough detail to understand, filter, and recover from changes.

Suggested audit fields:

- `batch_id`
- `timestamp`
- `user_or_machine`
- `source_file_path`
- `source_file_hash_before`
- `source_file_hash_after`
- `backup_path`
- `sheet_name`
- `ods_row_number`
- `grist_table`
- `grist_row_id`
- `field_name`
- `ods_column_header`
- `grist_column_id`
- `old_ods_value`
- `new_ods_value`
- `old_grist_value`
- `new_grist_value`
- `action`
- `status`
- `error_message`
- `verification_status_after_apply`

Suggested audit tables:

- `change_batch`
- `change_event`
- `row_snapshot`
- `verification_run`
- `grist_sync_event`
- `removed_ods_row`
- `archived_grist_row`

## Audit HTML Viewer

Each per-file audit store should include an HTML viewer for quick review of historical changes.

Useful filters:

- Date range
- Action type: edit, mark inactive, delete, archive, restore, Grist update, ODS update
- Sheet
- Product part name
- Material
- Optional item group
- ODS row number
- Grist row ID
- User or machine
- Status: applied, failed, partial, reverted
- Field changed

Useful viewer features:

- Before/after diff view
- Batch view
- Failed/partial operations view
- Removed ODS rows view
- Archived Grist rows view
- Export filtered results to CSV
- Link from audit batch to backup file path

## Removing Rows From ODS

The web app should allow users to handle `only in ODS` rows.

Possible actions:

- Keep row
- Mark row inactive
- Clear configured data cells
- Delete row from ODS
- Map row to an existing Grist row
- Create a new Grist row
- Ignore for now

Recommended default:

- Mark as inactive by setting the configured inactive flag, for example `In Use = No`.
- Add a remark or audit note explaining that the row was removed by the utility.

Deleting rows from ODS should be an advanced option because row deletion can affect formulas, references, formatting, and external links.

Before removing or deleting rows:

1. Create an ODS backup.
2. Capture a full row snapshot in the audit store.
3. Show a pending-changes preview.
4. Require explicit user confirmation.
5. Re-run verification after applying.

## Removing or Archiving Rows From Grist

Avoid direct deletion from `ProductPartMSList` as the default behavior.

Preferred pattern:

1. Copy the full Grist row snapshot to an archive table.
2. Mark the original row as archived/inactive.
3. Optionally delete from the main table only after the archive write succeeds.

Suggested main-table fields:

- `Archived`
- `ArchivedAt`
- `ArchiveReason`
- `ArchiveBatchId`
- `ODSFileKey`
- `ODSFileName`
- `ODSSheetName`
- `ODSRowNumber`
- `ODSMatchKey`
- `ODSLastVerifiedAt`
- `ODSMatchStatus`
- `AppManagedHash`
- `AppLastSyncedAt`
- `AppLastSyncedBatchId`

Suggested archive table:

```text
ProductPartMSListArchive
```

Suggested archive fields:

- `OriginalRecordId`
- `ArchivedAt`
- `ArchivedBy`
- `ArchiveReason`
- `ArchiveBatchId`
- `SourceODSFileName`
- `SourceODSSheetName`
- `SourceODSRowNumber`
- `OriginalRowJson`
- `MaterialToCut`
- `Length_mm`
- `QtyNos`
- `OptionGroup1_TEMP`
- `ProductPartName`
- `MachinePieceDesc`
- `Remarks`
- `Verified`
- `AppManagedHash`

The `OriginalRowJson` field is important because it preserves the full row snapshot even if the Grist schema changes later.

## Manual Grist Edits

The utility must work even when Grist rows are manually edited outside the app.

Recommended mechanism:

1. During verification, compute a fingerprint/hash for important Grist fields.
2. Store the hash in the per-file audit DB.
3. Optionally write the hash to Grist in an `AppManagedHash` field.
4. On later scans, compare:
   - Previous known Grist hash
   - Current Grist hash
   - Current ODS hash

Possible classifications:

- Unchanged
- Changed in ODS
- Changed in Grist
- Changed in both
- Conflict
- Archived manually
- Deleted manually
- New manual Grist row

If users manually mark a Grist row as archived, the utility should detect it on the next scan and ask whether the corresponding ODS row should be marked inactive or removed.

If users manually delete a Grist row, the utility may not be able to recover full deleted-row details unless Grist history or an archive table captured it earlier. Therefore, team practice should prefer "archive, do not delete".

## Marking Matched Grist Rows

The verification workflow should optionally update matched Grist rows with ODS metadata.

Suggested fields:

- `ODSRowNumber`
- `ODSSheetName`
- `ODSFileName`
- `ODSMatchKey`
- `ODSLastVerifiedAt`
- `ODSMatchStatus`

Suggested prompt:

```text
Update matched Grist rows with ODS row numbers? [y/N]
```

Before writing:

```text
34 Grist rows will be updated with ODS row numbers. Proceed?
```

## Failure Handling

There is no true transaction across a local ODS file and Grist. The workflow must handle partial failure clearly.

Recommended apply sequence:

1. Validate all pending changes.
2. Create ODS backup.
3. Apply ODS changes.
4. Save ODS.
5. Apply Grist changes.
6. Write audit result for every change.
7. Re-run verification.

If Grist update fails after ODS save succeeds:

- Keep the ODS backup.
- Mark the batch as partial failure.
- Show exactly which Grist updates failed.
- Allow retry using the saved change batch.

If ODS update fails before Grist update:

- Do not update Grist.
- Mark the batch as failed.
- Keep the attempted-change audit details.

## Suggested Implementation Stages

Stage 1:

- Per-file audit folder and SQLite audit store.
- Automatic ODS backup before in-place operations.
- Optional update of matched Grist rows with ODS row numbers.

Stage 2:

- Apply safe field edits to both ODS and Grist:
  - Product part name
  - Optional item group
  - Remarks
  - CR log after Grist field confirmation

Stage 3:

- Web app review UI for matched, only-in-ODS, only-in-Grist, duplicate, and ignored rows.
- Batch preview and confirmation.

Stage 4:

- ODS row cleanup actions:
  - Mark inactive
  - Clear row
  - Delete row as advanced action

Stage 5:

- Grist archive workflow using `ProductPartMSListArchive`.
- Manual Grist edit detection using row hashes.

Stage 6:

- Full audit HTML viewer with filtering, diff review, and export.

## Open Questions

- Confirm the correct Grist field equivalent for ODS `CR Log`.
- Decide whether ODS in-place editing should use LibreOffice UNO/headless automation.
- Decide whether row removal from ODS should default to mark-inactive rather than physical deletion.
- Decide whether Grist rows should be archived and retained in the main table, or copied to archive and deleted from the main table.
- Confirm exact Grist columns to add for ODS metadata and app-managed hashes.
- Confirm backup retention policy per source file.
