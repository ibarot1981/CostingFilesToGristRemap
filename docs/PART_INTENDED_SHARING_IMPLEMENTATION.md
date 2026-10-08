# Part intended sharing and cascaded creation — implementation record

Updated 8 October 2026 for schema `safari-part-intended-sharing-grist-2026-10-08.v10` on PR #3.

## Implemented behavior

Part creation now offers one Product filter, an optional single Product Model under that Product, and explicit multi-select intended Model Codes. Product-level selection can include codes under several Models without selecting multiple Models. The name anchor is a separate control: Product and Model are sensible defaults, while Global remains available after using the cascade. Model Code naming requires an explicit single anchor. Generated names never concatenate the chosen code shortcodes.

The UI shows selected codes as removable chips, a count, search, Select visible and Clear selection. Product/Model filters constrain code choices and the server validates that every submitted code, Model and Product is active and related. Changing filters cannot silently remove already-selected codes. In the Part detail editor, picker context only filters the list; it does not change the saved naming scope.

Selected codes mean intended sharing only. Saving them never creates a configuration selection or mapping, infers a quantity, changes Part composition or Rev A, or writes a costing snapshot. One canonical Part keeps its stable UUID, permanent number and generated name as the sharing set changes. Out-of-scope advisory codes are allowed. Removing every code is valid and does not retire the Part.

The detail page separates **Intended sharing** from **Used in configurations**. The first is editable with a reason and versioned history. The latter comes from active published configuration revisions and reports direct `ConfigurationPartSelection` references only. Component/indirect usage is not expanded; the UI states that limitation. Source mappings and broad naming scope are not treated as actual usage.

## Grist storage and recovery

Schema v10 adds:

| Table/column | Stored meaning |
| --- | --- |
| `PartIntendedModelCode` | One normalized Part/Model Code pair, keyed by stable Part UUID and code record ID. A pair is updated between active and removed and can be reactivated without creating a second active row. Creation/update actor, reason, time and request evidence are retained. |
| `PartIntendedSharingState` | One sharing-set version and fingerprint per Part, plus the last request and actor/reason/time. |
| `PartIntendedSharingEvent` | Append-only per-code add/remove event for each saved sharing-set version. |
| `PartRegistryRequest.Payload` | Durable normalized before/desired sets and operation context so a committed or interrupted Grist write can be retried after restart. |

Writes run through the existing bound `PartRegistryCoordinator` and serialized local journal. Expected versions prevent stale editors from replacing a newer set. The request key and fingerprint must match for a retry; reusing a key with changed data conflicts. Incomplete publication resumes from Grist rows and immutable events. A stale pending request is rejected unless its complete event set proves that its change set was published. Direct table edits are detected as drift; this app path does not claim a native Grist unique constraint.

When Part creation includes codes, the canonical Part and Rev A are first created through the existing journal. Initial intended sharing uses a deterministic request key derived from the original Part-save key. If relationship publication fails after Part creation, the API returns the saved Part identity and a typed pending error. The UI retains the original request in session storage; Retry resumes the same Part and sharing operation instead of allocating a second number.

## Actual usage and naming boundaries

`Name derives from` remains one Global/Product/Product Model/Model Code anchor. Picker context and intended membership are not naming inputs unless the user explicitly changes the name anchor and saves a separate metadata version. Membership edits do not create aliases, change scope or metadata version, or revise engineering A.

`GET /api/parts/{part_id}` now returns both `intendedSharing` and `usedIn`. The actual-use service reads the current active published configuration for each active Model Code and joins direct selections to the canonical Part. It currently does not walk component paths, so it does not claim complete indirect assembly usage.

## Deployment and data boundary

The production Safari Manufacturing document was guarded and validated before applying v10. The additive plan created three tables and added `PartRegistryRequest.Payload`; no existing field type changed. A native Grist backup passed SQLite integrity and SHA-256 validation before apply:

- Backup: `%TEMP%/CostingFilesToGristRemap/safari-grist-backups/safari-manufacturing-bAPdkEDn7brbqTrfVsRmXZ-20261008T073702Z.grist`
- SHA-256: `87434603e7376b7c6f0d9bc05a414fa1449bce93438b27dd0d9d06518d017520`

Production readback confirmed the schema is current and that the three new tables contain zero rows. Existing row counts were preserved: one legacy `ProductPart`, one legacy numeric `PartRevision`, 313 `LineMaster`, zero `PartMappingReview`, zero `PartRegistryRequest`, one `PartRegistryCoordinator`, and one pre-existing workbook `CostingSnapshot`. No synthetic Part or costing/business record was created in production.

## Verification performed

- Registry tests cover normalized active links, immutable add/remove/re-add history, stable Part identity, stale-set conflicts, invalid/inactive codes, exact replay, recovery after a committed relationship/state response loss, and rejection of a stale pending request after a different edit. Test assertions also verify that intended sharing creates no configuration, mapping or snapshot rows.
- Live-cost tests confirm an intended code is not actual usage and that a direct configuration selection is reported separately.
- UI interaction tests cover Product/Model filters, multi-code creation, independent Global naming, removal history, actual-usage separation and retry after partial initial Save.
- An isolated real Grist service test created one Part shared across two codes, recovered after the relationship commit response was lost using a restarted registry, then removed and re-added a code. Readback showed one normalized row per pair, four audit events, unchanged Part number/name/Rev A, and no configuration, mapping or snapshot side effects. A separate configuration selection appeared only after an explicit configuration write.
- An isolated browser test created `SM-P-000001` with the stable Model prefix `QAHF — Browser Chassis — Standard` and two intended codes, then removed one through the detail editor. Grist readback showed version 2 and the audited removal. A second real-Grist pass injected a timeout after the version-3 state row committed, restarted the registry and replayed the same request; readback showed both intended links active, four immutable events, unchanged Part identity/name/Rev A, and zero configuration, mapping or snapshot rows. The browser showed the recovered version-3 set. The disposable Grist test document was moved to Trash after verification.

The production schema apply itself did not create application business rows. All behavioral fixtures remained isolated.

## Remaining limitations

Indirect/component usage is not expanded in **Used in configurations**. Automatic Summary-to-configuration import, costing authority integration, CR approval beyond Rev A, and reviewed migration/classification of the existing legacy Part/numeric revision remain separate open project gates. The app cannot prevent a Grist user with direct table-write access from bypassing coordinated sharing writes; it detects the resulting relation/state drift on read or save.
