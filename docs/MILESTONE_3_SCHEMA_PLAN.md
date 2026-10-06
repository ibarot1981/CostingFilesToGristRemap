# Safari v5 normalized pilot schema plan

Delivery Milestone 3 / Phase 1 package 1.1. The schema diff was fetched
read-only from the validated Safari Manufacturing document in the Work
workspace using `main.py safari-normalized --safari-plan`. Normal startup does
not migrate.

| Table | Natural key | Parent Ref and cardinality |
|---|---|---|
| ProductPart | PartKey | One Part has many PartRevision rows |
| PartRevision | RevisionKey | One ProductPart; immutable revision |
| PartComponentRevision | ComponentKey | One parent and one child PartRevision; zero pilot rows until confirmed |
| Material | MaterialKey | Shared reference; never a usage-line identity |
| PurchaseItem | ItemKey | Shared candidate for Store/Paint rows |
| ProcessOperation | OperationKey | Shared process type |
| WorkCenter | CenterKey | Shared source candidate |
| LineMaster | LineKey | One ProductPart; many immutable LineRevision rows |
| LineRevision | RevisionKey | One LineMaster; optional predecessor Ref; one typed LineDetail |
| LineDetail | DetailKey | One LineRevision; optional Material, PurchaseItem, ProcessOperation, WorkCenter Refs |
| SourceLineObservation | ObservationKey | One accepted CostingSnapshot; exact workbook hash/sheet/row/cells |
| SourceLineMapping | MappingKey | One SourceLineObservation; optional LineMaster while ambiguous |
| LineAuditItem | AuditKey | One LineMaster/LineRevision and optional predecessor/observation |

The pilot uses one `LineDetail` table with a `ProcessType` discriminator and
typed nullable columns for quantity/UOM, dimensions, weight, material, item,
rates and cached cost, operation/center, Store issue route, Tool Shop source
department and costs, and activity classification. Formula and cell-address
maps remain in `SourceLineObservation.Cells`; full semantic JSON is held once
in `CostingSnapshot`. The projection models rate-only changes as new
observations rather than physical revisions; the live subsequent-revision
import path still needs that behavior implemented. Removal creates a retired revision in the projection. Historical `In Use = No` rows
retain child records but are excluded from active totals.

The D-034 five-field MCL composite is the exact workbook-scoped identity.
Changed-key matching uses unique strong evidence; duplicate or multiply
claimable candidates remain unresolved. Current source yields 10 such rows.
Only a temporary `part:s1khf-unallocated` owner is created until owner-reviewed
Part evidence is available. No description similarity or CR reference merges
identity. A Store issue row and Tool Shop manufacturing row remain separate
even when an item name resembles another source row.

Grist's table API does not declare unique indexes. The importer checks each
key column for duplicate values before writes, rejects immutable-key content
changes, and rereads the accepted snapshot and source hash during staged
creation. Existing keys are reused; interrupted writes resume missing rows.
Queryable search keys are `Snapshot`, `SheetName`, `Status`, `LineMaster`,
`Material`, `ProductPart`, and the deterministic text keys. A later PostgreSQL
adapter should enforce unique indexes on every natural key plus
`(Snapshot, SheetName, SourceRow)` for observations, and index these Ref/search
columns. The UI consumes API DTOs and has no direct Grist requests.

The reviewed live diff was 13 created tables, zero added columns, and zero
updated columns. Schema v5 was applied only to the validated Safari
Manufacturing document; the post-apply diff is empty. The accepted snapshot
source hash differs from the current workbook,
but the current MCL physical signatures and all previously covered non-cost
fields across the five sheets match. Current active plus historical cached MCL
line values reconstruct workbook C7 within floating-point noise. The plan
prints the newly covered parser fields, both hashes, and its digest. The
post-schema row plan proposed 2,043 creates, and the guarded import created
exactly those rows. A repeat plan and import proposed/created zero rows. Live
queries verify 313 observations, mappings, masters, revisions, details, and
audits, with 303 proposed-exact and 10 ambiguous mappings. No schema or data
change was applied to Costing-New.
