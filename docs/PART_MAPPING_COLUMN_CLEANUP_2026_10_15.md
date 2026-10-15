# Part Mapping source column cleanup

Owner request 15 October 2026: Tool Shop displays Material Used twice. Since In Use=No rows are filtered, the In Use column need not be shown.

Diagnosis at b420c82: Tool Shop item_name may fall back to Material Used and material_to_cut also maps to Material Used. SourceDetails renders both semantic fields using their physical source headers, duplicating the same physical source column. _configured_active_status classifies No as historical and source_groups includes active rows only; blank In Use is active, unexpected values are excluded. Preserve these rules and source evidence.

## Criteria

### c1
In compact and expanded Part Mapping source tables, a Tool Shop physical source column aliased to multiple semantic fields is displayed once. Specifically Material Used appears once when item_name and material_to_cut resolve to the same physical header cell/column. Preserve separate Item Name and Material Used when the workbook has distinct source columns, including distinct physical columns with matching header text. Do not deduplicate based solely on equal values/header labels or change extraction/mapping semantics. Retain blank/missing source states and provenance. Handle incomplete legacy provenance conservatively without hiding distinct data.

### c2
Hide the In Use column in both compact and expanded Part Mapping tables for MCL, Tool Shop and CNC. Preserve existing active-row filtering: No/inactive rows excluded, blank remains active, unexpected flags follow existing rules. Retain in_use in backend source evidence, semantic comparisons and audit records. Keep Canonical Part visible on every displayed row in both assignment modes, and preserve assignment selection/save and source row references.

### c3
Submit finished exact commit on existing PR4 and codex/parts-assembly-manufacturing with truthful verification. Preserve unrelated changes, production records/configuration and owner running servers. No schema/write migration, production test fixtures, merge/deploy or schedule changes. Five-round workflow limit; Luna alone changes application source, Sol reviews exact SHA.

## Mandatory checks

- ui-suite: full UI tests including standalone models; meaningful coverage of duplicate physical Tool Shop source versus distinct columns and hidden In Use, compact/expanded and assignment modes.
- ui-build: TypeScript check and production Vite build.
- source-filter-regressions: focused existing or meaningful synthetic extraction/mapping checks demonstrate Yes/No/blank/unexpected handling for the three source families and unchanged backend evidence.
- isolated-browser-flows: inspect actual isolated browser compact/expanded Tool Shop tables with duplicate-alias and distinct-column cases; verify hidden In Use and visible Canonical Part. Clearly label mocks; no production writes or stopping owner sessions. Apply QA document cleanup protocol if a Grist test document is created.

## Implementation notes

- Tool Shop source details suppress an aliased semantic field only when header or row-cell provenance identifies the same physical worksheet column. Matching header text or cell values alone do not suppress a distinct column; missing provenance remains visible.
- The In Use source column is omitted from compact and expanded MCL, Tool Shop, and CNC Part Mapping tables. Extraction, active-row filtering, source evidence, semantic comparisons, and assignment behavior remain unchanged.
- Canonical Part and source-row references stay visible, including during line assignment.

## Verification recorded for this implementation

- `ui-suite`: standalone mapped-files view-model tests 2/2; Vitest UI suite 108/108.
- `ui-build`: TypeScript `--noEmit` check and Vite production build passed.
- `source-filter-regressions`: extraction suite 4/4, including Yes, No, blank, and unexpected In Use flags across MCL, Tool Shop, and CNC, with source evidence preserved.
- `isolated-browser-flows`: mock-only alias and distinct-column cases inspected in compact and expanded views; assignment selection retained Canonical Part; fixture reported zero Grist writes.