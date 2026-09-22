# Safari Manufacturing — Decision Log

Updated: 21 September 2026  
States: **Accepted**, **Provisional**, **Open**, **Superseded**

| ID | State | Decision | Reason and consequence |
|---|---|---|---|
| D-001 | Accepted | Product costing ODS files and approved ODS masters remain authoritative during transition. | Existing costing is live and trusted. Phase 0 reads and records; it does not rewrite them. |
| D-002 | Accepted | Create a separate Grist document named Safari Manufacturing. | Avoids destabilizing `Costing-New` and provides a clean schema. New-schema writes must reject the legacy document ID. |
| D-003 | Accepted | Safari Manufacturing is the application store before any backend migration. | Five to six users already work in Grist. Moving to PostgreSQL before workflow validation adds risk and another live data point. |
| D-004 | Accepted | One costing file belongs to one Product Model; a Product Model may have many files; a Model Code has one active file. | Supports gradual mapping while preventing ambiguous costs for a sellable code. |
| D-005 | Accepted | The supplied product/model/code ODS is the canonical import list. | It is more complete than current Grist. Source text is preserved and exceptions are reconciled explicitly. |
| D-006 | Accepted | Model Codes are explicit configurations; do not depend on a universal code parser. | Existing fragments are meaningful but not standardized across all families. Parsers may propose metadata, never silently define it. |
| D-007 | Accepted | Bush variants are legacy-spares-only. | They must remain searchable for service but not appear as active selling/manufacturing choices. |
| D-008 | Accepted | Persist the duplicate GC variants as `GCMC-7.5`, `GCMC-10`, `GCMC18-7.5`, and `GCMC18-10`. | Keeps the existing family codes while making their 7.5 and 10 ratings unique and explicit. Original source values remain in provenance. |
| D-009 | Accepted | Costing, manufacturing, and spare-parts configurations are separate versioned concepts. | What is sold, how it is made, and how it is serviced do not always share the same structure. |
| D-010 | Accepted | Store Issue records preserve both manufacture source and issue route. | Tool Shop parts may be issued through Stores. Modeling both prevents loss of workflow meaning and double costing. |
| D-011 | Accepted | Latest observed purchase rate is not automatically the approved costing rate. | Current master updates involve discretion and usually react to increases. Future automation requires an explicit approval policy. |
| D-012 | Accepted | Existing CLI mapping logic becomes shared domain services. | Material aliases and normalization already contain valuable knowledge; duplicate implementations will drift. |
| D-013 | Accepted | Authentication and full role enforcement follow workflow validation. | Phase 0 can establish correct processes first, while service boundaries retain actor/policy hooks for later Authentik integration. |
| D-014 | Accepted | Requirements, architecture, decisions, and status change together with implementation. | Prevents the application and its specification from diverging. `requirements-status.html` is the visible register. |
| D-015 | Accepted | Imported source observations and curated application records are separate. | Provenance and reproducibility require preserving what was read instead of overwriting it with interpreted values. |
| D-016 | Accepted | All synchronization is dry-run/proposed-change first and idempotent. | Protects live ODS and Grist data and makes each change reviewable. |
| D-017 | Accepted | UI and business logic depend on repository interfaces, not Grist tables. | Allows a future PostgreSQL adapter and keeps schema details out of the browser. |
| D-018 | Accepted | Navy, copper, and warm-sand will distinguish Safari Manufacturing while retaining the Seey UI feel. | Provides continuity without making the applications visually identical. |
| D-019 | Accepted | Exact Model Code display values come from the supplied catalog ODS. | Examples, filenames, or historical spellings that differ—such as alternate S1KHF segment ordering—become alias candidates or reconciliation issues, not replacements for the catalog value. |
| D-020 | Accepted | The application setup must create or validate the Safari Manufacturing Grist document before schema bootstrap. | Document creation is part of implementation, not a manual prerequisite. It must be idempotent, detect duplicate names, preserve the returned ID outside source control, and reject the legacy document. |
| D-021 | Accepted | Irshad is the sole approver during the Phase 0 pilot. | Directory inheritance and Model Code reassignment require Irshad's approval and an audit reason until the production role matrix is introduced. |
| D-022 | Accepted | The API defaults to an in-memory Safari repository and requires an explicit `SAFARI_REPOSITORY=grist` selection for the Grist adapter. | Local development and automated tests must not accidentally connect to or mutate either Grist document. The UI labels the active adapter. |
| D-023 | Accepted | Filesystem tree requests are lazy and hash/inspect a workbook only when selected. | A full recursive tree must remain responsive and must not read every ODS package on every request; the selected-file observation supplies the stronger identity token. |
| D-024 | Accepted | Schema plans are bound to a document ID, exact name, and selected workspace, and remote metadata is re-read immediately before apply. | A stale or altered plan must not be retargeted to another Grist document; mismatch fails closed before table writes. |
| D-025 | Accepted | Mapped Files is a read-only reconciliation projection; list requests use repository observations and filesystem metadata, not full workbook scans. | Surface duplicate owners and known file/catalog discrepancies without hashing every ODS file or silently resolving conflicts. Record observation time separately from filesystem modification time. |
| D-026 | Accepted | Grist adapter conversions follow its normal cell representation, and multi-table association writes carry a durable request key/fingerprint for recovery. | Ref IDs and DateTime epochs are handled only at the adapter boundary; retries resume incomplete child/audit/queue records and reject changed payloads under a reused key. |
| D-027 | Accepted | Association actor attribution comes from trusted hosting-proxy identity headers, never from the save request body. | Keeps the browser from self-assigning an audit actor; the proxy must overwrite forwarded headers. This is not authentication or role enforcement, which remain deferred by D-013. |
| D-028 | Accepted | A first-time file association may omit a reason; superseding or revising an existing association requires one. | Keeps the initial mapping flow lightweight while ensuring every reassignment/change has an explicit explanation and audit context. |

## Open decisions

| ID | Needed by | Question | Safe interim treatment |
|---|---|---|---|
| O-005 | Phase 1 gate | What cost tolerance and rounding rules define parity? | Report exact and rounded differences without declaring parity. |
| O-006 | Phase 4 | What precise rule promotes observed purchase rates to approved costing rates? | Never auto-promote. |
| O-007 | Phase 5 | Which ODS cells/sheets may be written, and must external links be retained? | Read-only. |

## Resolved inputs

| ID | Resolution | Evidence |
|---|---|---|
| O-003 | Resolved 19 September 2026: the owner explicitly selected the Work workspace. | Guarded setup revalidated the exact Safari Manufacturing document, applied the schema, and verified the final table/column inventory. The private document ID is kept only in ignored local configuration. |

## How to update this log

Add a new decision when a business rule, source boundary, workflow, schema boundary, or irreversible technical choice changes. Do not edit history to make an old decision look current: mark it Superseded and add the replacement ID. Link implementation work and requirement IDs in the commit or pull request.
