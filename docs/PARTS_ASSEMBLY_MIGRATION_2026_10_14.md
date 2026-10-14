# Parts and assembly identity migration — 14 October 2026

## Schema version 15

Schema v15 adds two nullable `Text` columns:

| Table | Column | Use |
| --- | --- | --- |
| `ProductPart` | `IdentityKind` | `assembly` for newly created assemblies; absent/blank is read as `part` for existing and newly created Parts. |
| `PartRegistryRequest` | `IdentityKind` | `assembly` for assembly creation requests; absent/blank remains compatible with existing Part requests. |

The migration is additive. It does not rewrite ProductPart rows, Part numbers, aliases, mappings, configurations, baselines, snapshots, or pending Part request payloads. Existing `SM-P-` identities and compositions retain their current meaning. The application keeps the legacy Part-create fingerprint unchanged so retries written before v15 remain recognizable. New assemblies use a separate `create_assembly` request type and an assembly-specific fingerprint namespace.

The local allocator journal adds `part_registry_settings.next_assembly_number`, defaulting to 1. Startup adds the column to an existing journal when it is missing. Existing `next_part_number` and reservations remain intact. Assemblies and subassemblies share the durable `SM-A-000001` sequence; subassembly is a composition role, not a separate identity kind.

## Safe rollout procedure

1. Take and verify the normal Grist document backup. Record the exact document and workspace IDs and ensure the document is the intended Safari Manufacturing document, not the Costing-New document.
2. Run the schema planner and review that the only changes introduced by v15 are the two `IdentityKind` columns. Resolve other schema differences under the existing schema governance process.
3. Apply the reviewed plan to a new isolated nonproduction document first. Read back the columns and run the identity, retry, composition, and restart checks there.
4. Have the owner and independent reviewer authorize a production rollout separately. This implementation does not authorize production schema or business writes.
5. After any approved schema rollout, restart the application so its read-only schema checks observe the new columns. Assembly creation fails closed until both columns are present. Part reads continue to treat blank classification as `part`.
6. Do not manually seed or advance either sequence. The writer coordinator, Grist prefix scan, local SQLite reservation transaction, and request journal jointly protect permanent numbers and retries.

## Compatibility and deferred scope

- Existing `SM-P-` numbers and legacy compositions are not converted or renumbered.
- A Part Mapping or configuration assignment containing an `SM-A-` identity remains rejected. Assembly mapping, Model Code assignment, configuration selection, replacement/propagation, and assembly costing are deferred.
- Purchased/store Part children are a future requirement. This migration adds no store import, purchase workflow, vendor expansion, or automatic baseline establishment.
- Assembly composition may contain managed Parts or assemblies with explicit quantity and unit. The child engineering revision is pinned by the existing `PartComponentRevision.ChildRevision` reference; no child definition is rewritten or silently finalized.
- Roll-up of workbook requirements onto an assembly is deferred. Viewing existing accepted Part baselines does not establish or alter a baseline.
