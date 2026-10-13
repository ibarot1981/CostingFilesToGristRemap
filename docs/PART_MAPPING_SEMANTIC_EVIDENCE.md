# Part mapping semantic evidence

Part Mapping compares the manufacturing lines that define a canonical Part independently from workbook provenance and costing inputs. A whole-file hash remains attached to every immutable review row and is still checked immediately before a save. Changing that hash alone no longer removes an unchanged mapping.

## Version 1 comparison fields

`part-mapping-semantic-v1` fingerprints a normalized multiset of source lines within one existing sheet-and-label group. A line's sheet row/cell address, workbook formatting and document properties are retained by the existing source evidence, but do not form its semantic identity.

| Sheet | Included manufacturing and assignment fields |
| --- | --- |
| 5. Material Cut List Price | `product_part_name`, `material_to_cut`, `dimension_to_cut_mm`, `dimension_to_cut_inches`, `qty`, `optional_item_group_1`, `in_use` |
| Tool Shop Items | `product_part_name`, `item_name`, `material_to_cut`, `dimension_to_cut_mm`, `weight_per_meter_kg`, `total_weight_kg`, `qty`, `optional_item_group_1`, `in_use`, `toolshop_part_name`, `toolshop_remarks`, `job_remarks` |
| CNC Cut List | `part_category`, `product_part_name`, `material_to_cut`, `length`, `width`, `thickness`, `part_weight_kg`, `total_weight_kg`, `qty`, `optional_item_group_1`, `in_use` |

Quantity is compared in the sheet convention `Nos`. MCL dimension columns retain their named mm/inch units; Tool Shop dimensions use mm and its weight columns use kg; CNC length, width and thickness use mm and weight columns use kg. If a source column is unavailable, evidence records state `unavailable`; a present but empty cell is `blank`. This keeps a missing header distinct from an intentionally empty value. Blank `in_use` is normalized to the sheet's effective active default, while the original blank/value representation is retained for audit display.

Text is Unicode NFKC normalized, whitespace-collapsed and case-folded for comparison. Numeric values use finite decimal normalization, so values such as `2`, `2.0` and `2.00` compare equally. Known active/inactive spellings are normalized; unexpected values remain explicit. The group fingerprint hashes sorted line fingerprints and excludes source coordinates, rates, prices, calculated costing totals, formatting and workbook/document metadata. Manufacturing weights (including `total_weight_kg`) are included because they describe the source Part requirement rather than a costing extension.

## Grist evidence and review behavior

Each append-only `PartMappingReview` row stores:

- `SemanticPolicyVersion`: comparison policy version;
- `SemanticFingerprint`: the fingerprint for the complete current group, repeated on each source-row mapping record;
- `SemanticEvidence`: the normalized fields and fingerprint for that exact source line.

Together with `SheetName`, `SourceRow`, `SourceHash`, association identity/version, Part identity, actor, reason and request keys, this provides a verifiable snapshot after reread and supports idempotent retry. Row coordinates remain provenance. Unchanged fingerprints preserve a canonical Part across moved/reordered lines when correspondence is unambiguous. Changed lines produce field-level proposals. Duplicate or blank-label ambiguity and assignment disagreement require explicit review. Prior mappings without a complete, valid versioned snapshot are reported as requiring one-time confirmation; current workbooks are never used to invent historical evidence.

A changed Product/Model association or grouping policy leaves the previous Part as a proposal for explicit review. It does not change Part metadata, the manufacturing baseline, or engineering revision. Manufacturing changes continue through the existing comparison flow; Parts remain Rev A until an authorized engineering-change flow exists. Rate/price or workbook-only changes never mutate Part mapping history during reads.

## Schema rollout

The reviewed schema planner prepares additive v14 columns for `PartMappingReview`. The implementation performs read-only schema introspection and blocks semantic mapping saves if those columns are absent or cannot be confirmed, while allowing existing history to remain readable. An isolated Grist fixture exercises append, fresh-registry reread, retry and pre-v14 write blocking. No production Grist data or schema was written as part of this task; production use of semantic mapping saves requires applying and verifying the reviewed v14 plan separately.
