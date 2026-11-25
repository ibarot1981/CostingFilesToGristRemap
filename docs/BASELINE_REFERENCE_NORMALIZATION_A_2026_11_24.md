# Baseline reference normalization · Package A

Package A repairs baseline establishment and current accepted baseline reads without changing source ODS/Costing-New data or historical physical rows.

## Establishment

The baseline service resolves exact canonical names and configured aliases against existing Safari `Material` identities. Existing local aliases are eligible only when their row records `MappingStatus=reviewed_alias`. Ambiguous or missing identities are returned as source-row exceptions; no similar-name matching or duplicate material creation occurs. Each baseline request freezes its resolved identities, per-piece quantities, factor evidence and exceptions in its original keyed request journal before writing normalized requirement rows.

Material Cut List quantity is stored as validated kg per piece (`Dimension to Cut / 1000 × kg/m`); the part link stores the source piece count once. CNC stores the entered per-part kg; Tool Shop stores one process unit per piece. A missing factor or cut length remains incomplete. The Costing-New provider remains read-only. Package A does not import masters/prices, change local costing source policy, add automation, or change commercial rate policy.

## Existing accepted baselines

`GET /api/parts/{part_id}/manufacturing-baseline/normalization-preview` returns a read-only plan bound to the exact current accepted revision. `POST /api/parts/{part_id}/manufacturing-baseline/normalize` requires its preview fingerprint, an actor, a reason and an idempotency key. It records an immutable request result and audit event. The saved physical LineDetail, PartRevisionLine and snapshot rows are not rewritten. Part-definition reads and live-cost calculations overlay the completed correction only for that exact Part revision and detail. Normal reads perform no business writes.

Conflicting, stale or ambiguous preview data fails closed. A later engineering substitution still follows the existing Change Request process.
