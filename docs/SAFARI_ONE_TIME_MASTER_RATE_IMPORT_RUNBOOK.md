# One-time Safari master and rate import runbook

This is the controlled operational procedure for the explicit Costing-New to Safari import. The implementer used it only against disposable QA. Sol performs the separately authorized production operation after review and acceptance.

## Fixed document targets

| Role | Document ID | Workspace | Required name | Access |
| --- | --- | --- | --- | --- |
| Safari target | `bAPdkEDn7brbqTrfVsRmXZ` | `3` | `Safari Manufacturing` | Read and write only after the native backup and reviewed preview |
| Costing source | `cPCjjr9oCzhWC7z8vJbcky` | Resolve from Grist metadata before the run | `Costing-New` | Read only |

Use the already configured application instance. Before starting, confirm that `SAFARI_MANUFACTURING_GRIST_DOC_ID` is exactly the Safari ID above, `SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID` is `3`, and `COSTING_NEW_GRIST_DOC_ID` is exactly the Costing-New ID above. The service also checks both document names/workspaces and rejects a source/target collision. Stop if any check differs; do not substitute a document based on its display name.

Implementation and review must use a separately recorded disposable QA document. A QA target can pass the normal Safari writer guard only when `SAFARI_QA_TARGET_DOC_ID`, `SAFARI_QA_TARGET_WORKSPACE_ID`, and `SAFARI_QA_TARGET_LABEL` all match the configured target, and the remote document name exactly matches a unique label beginning `QA - one-time-safari-master-rates-20261125 - `. Production `bAPdkEDn7brbqTrfVsRmXZ` and Costing-New are always excluded. Record the exact QA document ID, workspace, label, purpose, and coordinator in the phase evidence; a matching name by itself is never a target selector. If the service is unavailable, stop live QA and report that dependency rather than weakening these guards.

## Preflight and backup

1. Record operator, timestamp, application build/commit, target ID/workspace, and source ID. Confirm no other import is applying and that the Safari target is active.
2. Download a native Grist backup of the Safari target. Record filename, byte count, SHA-256, and SQLite `PRAGMA integrity_check` result (`ok`). Keep the backup recoverable outside the application runtime.
3. Confirm the source access is read-only. The importer reads `MasterMaterial`, `MaterialRateLog`, `PurchasePartsMaster`, and `PurchasePartsRateLog`; it never writes Costing-New or ODS.
4. Generate a unique request key for this deliberate import, for example `safari-master-2026-11-25-<UTC timestamp>-<operator suffix>`. A new deliberate import must use a new key; retries of this import must retain its original key.

## Preview and review

Send an authenticated request to the configured application host:

```http
POST /api/admin/safari-master-import/preview
Idempotency-Key: <request-key>
Content-Type: application/json

{"requestKey":"<request-key>","reason":"Owner-authorized one-time Safari master and current price import"}
```

The response is a dry preview. Review and save the full response, especially the source document/table counts and fingerprint, captured material/steel/purchase counts, proposed new identities, phase-outs, rate/conversion evidence, duplicate identity conflicts, and unmapped or ambiguous purchase identities. Confirm the preview source is Costing-New and every affected ID remains inside the fixed targets above. Resolve only explicit identity exceptions through the reviewed mapping workflow; never match by a similar description or dimension. A Rs-denominated `SparesPartCostRs` value is recorded as INR with its source-field evidence. The source selector's existing related-row order and formula outputs are retained.

If preview reports source drift, discard that preview for application purposes, preserve its audit record, and start a new deliberate preview with a new key. Do not edit or repair its captured facts.

## Apply and verify

Immediately before apply, recheck the preview fingerprint and source/target configuration. The service re-reads source facts once and rejects a drifted preview. If it rejects for drift, do not force it; repeat preview under a new key and review again.

```http
POST /api/admin/safari-master-import/apply
Idempotency-Key: <request-key>
Content-Type: application/json

{"requestKey":"<request-key>","previewFingerprint":"<exact preview fingerprint>","reason":"Reviewed preview; apply the captured local version"}
```

Verify the result reports `published`, the intended source fingerprint, one new active local version and complete staged material and purchase row counts. Confirm the Safari import control points to that version and that its manifest fingerprint matches the published rows. Run ordinary material options, baseline factor resolution and eligible live cost against Safari while access to Costing-New is blocked; they must use one local version and must not issue source table reads. Review unresolved-rate/conversion results as incomplete/null. Do not turn missing evidence into zero or complete cost.

For purchased Nos/kg conversions, `PerNos_FromKg` is the source Bool formula flag, not a physical factor. With `Unit=Per Nos`, `Nos_Per_KG=10`, and an authoritative `TotalSparesPartCost` of ₹92.70 per piece, the imported ₹/kg is ₹927 (multiply once by 10). A `Per Kg` source with the false flag and the same positive conversion may resolve to ₹92.70/Nos. Same-unit prices remain unchanged. Freight and handling are already included only when the source exposes the base plus both charge values and the authoritative total; never add the charges again.

Download a second native Safari backup and record its SHA-256, byte count, and SQLite integrity result. Compare the before/after `CostSnapshot`, `CostSnapshotPart`, `CostSnapshotLine`, `CostSnapshotRateEvidence`, and `CostSnapshotPublication` table hashes; all five must be identical. Record the import audit and exact verification results with the operation record.

## Retry and recovery

- For a lost response or partial application, call `GET /api/admin/safari-master-import/<request-key>/recovery`, inspect its stored request/capture status, and retry the same apply with the exact original key and preview fingerprint. Once a request is applying, recovery uses its persisted compressed capture and does not fetch mutable source tables again.
- Every preview freezes its predecessor pointer and reserved version. If a separate deliberate import has advanced that pointer, retrying an older key returns `superseded`; it does not publish the old capture at a newly incremented version. Keep that request's immutable capture and outcome as history. Start a new preview/key for new source facts.
- Never use a second key to continue a partial generation. The active pointer advances only after every version row is staged and verified.
- A stale preview is not a recoverable apply. Preserve it, then run a fresh preview with a new key after source drift is reviewed.
- If a target write is ambiguous, stop and preserve both native backups and the request/audit rows for review. Do not delete staged rows or manually advance the active pointer.
- No automatic refresh, webhook, polling, worker, monitoring, snapshot scheduling, merge, or deployment is part of this procedure.
