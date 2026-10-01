# Safari Manufacturing ERP UI

Safari Manufacturing workbench: a responsive Costing Explorer (lazy
filesystem tree, association workbench, and bounded workbook preview), a
read-only Milestone 2 Costing Review, a Mapped Files view, and a governed
Reconciliation view. Association writes use the in-memory
repository by default for local development and are visibly labelled; select
the explicit Safari Grist adapter only after guarded document/schema setup.
The required association rules and milestone acceptance tests are defined in
[`docs/MANUFACTURING_ERP_REQUIREMENTS.md`](../docs/MANUFACTURING_ERP_REQUIREMENTS.md)
and [`docs/IMPLEMENTATION_ROADMAP.md`](../docs/IMPLEMENTATION_ROADMAP.md).

Run the API from the repository root:

```powershell
.\run-web.ps1
```

Run the UI in a second terminal:

```powershell
cd ui
npm install
npm run dev
```

Use Node.js `^20.0.0`, `^22.0.0`, or `>=24.0.0` for frontend install, test,
and build commands (Vitest 4.1.11's supported engine range). The system Node
18.8.0 is incompatible; verification used the bundled Node.js 24.19.0 runtime.

Explorer search traverses ODS paths recursively without inspecting workbook
contents or hashing files; matching files are shown with their folder ancestry.
Search results are capped at 1,000 and the UI prompts you to refine broader
queries.

Build/type-check the UI:

```powershell
npm run build
```

Run the mapped-files view-model and workbench component tests:

```powershell
npm test
```

The API reads `COSTING_ROOT` (default `C:\Irshad\Safari\DRWGD\Products Costing`)
and optionally `CATALOG_ODS_PATH`. The browser never receives Grist
credentials. The Grist adapter requires `GRIST_API_KEY`,
`SAFARI_MANUFACTURING_GRIST_DOC_ID`, `SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID`,
and `SAFARI_REPOSITORY=grist`; it rejects the legacy `GRIST_DOC_ID` as a write
target. The workspace ID is required for guarded setup/discovery and document
identity validation; choose it explicitly because discovery found multiple
writable workspaces. Mapped Files includes both
registered associations and currently discovered ODS files; the table and
details drawer are keyboard-operable, and explorer rows expose unreadable and
external-link health badges.
Mapped Files groups by Product/Model, filters mapped, unmapped, conflict, and
needs-review states, exposes known ownership/catalog/file-health discrepancies, and
labels repository observation times separately from filesystem mtime fallbacks,
and verifies a uniquely matching missing-file move candidate by hash. Its
details drawer displays persisted association codes, audit events, and queued
processing status. The read-only `/api/processing-queue` endpoint supports a
status filter and a bounded result limit; processing remains a visible safe
stub and does not run a costing parser. The view does not auto-resolve issues
or scan/hash every workbook. Its API projection is specified in
[`docs/API_SCHEMA.md`](../docs/API_SCHEMA.md).

Reconciliation starts with a dry-run scan of at most 50 registered ODS files.
The issue queue filters by status, type, severity, owner, Product, Model, and
search text; issue details show evidence, immutable observation history,
association history, and audit events. Lifecycle decisions require reasons.
Source revision and corrupted-identity actions require a preview and an
explicit Irshad review. Directory Product mappings remain proposals until
approval and never create file/model/code associations. CSV and JSON exports
respect the active issue filters. Governance writes are blocked until the v3
schema diff has been applied through the guarded setup command.

The responsive review surfaces use labelled controls, keyboard reachable issue
rows, focus transfer into issue details, explicit text status, and visible
stale/permission/API errors. Automated UI tests cover keyboard issue selection,
required reasons, and the Mapped Files projections; owner visual acceptance is
still a separate checklist item.

To review costing, select the product workbook in **Costing Explorer**, open
**Costing Review**, and choose **Refresh and calculate**. Each review refreshes
the selected workbook's local links on a temporary copy and reads the current
`Template DB` RawSteel and SteelRateLog snapshots. It shows active and historical
Material Cut List rows, exact calculations, saved/refreshed cache evidence,
rate changes, and composite-key row comparisons. Invalid rates or unresolved
inputs block affected line values and withhold the overall total. The view is
read-only and does not persist semantic changes to Grist yet.
