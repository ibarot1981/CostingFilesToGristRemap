# Parts and assembly round 2 verification

These instructions cover the implementation commit identified in the matching `.agent-workflow` implementation report. Run the commands from the repository root unless noted. Use Node 24 for UI checks.

## Local mandatory checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_grist_parts_recovery.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_assembly_invariants.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_part_identity_web.py -v
```

From `ui`, run the standalone model tests, the complete Vitest suite, the TypeScript check and production build with the same Node 24 executable:

```powershell
$node = 'C:\path\to\node-24\node.exe'
& $node test/mappedFilesViewModel.test.mjs
& $node .\node_modules\vitest\vitest.mjs run --config vitest.config.ts
& $node .\node_modules\typescript\bin\tsc --noEmit
& $node .\node_modules\vite\bin\vite.js build
```

The focused UI regressions are in `test/partsConfiguration.test.tsx`, `test/liveCost.test.tsx` and `test/parts.test.tsx`. They cover returned assembly rows in both the configuration API catalog and Live Cost selector, Part-to-Assembly and Assembly-to-Part request recovery before and after reservation/reload, delayed baseline/composition details, and cancellation after navigating to another Part. These UI tests use mocked API responses; they are not evidence of Grist persistence.

## Real isolated Grist prerequisite

Use a dedicated nonproduction Grist document named exactly `Safari Manufacturing`, in an explicitly selected test workspace. Set `SAFARI_MANUFACTURING_GRIST_DOC_ID` and `SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID` to that document and workspace, and ensure the document ID differs from the legacy `GRIST_DOC_ID`. `GRIST_BASE_URL` and `GRIST_API_KEY` must address that test service. Do not point these settings at production or `Costing-New`.

The document must already have the complete v15 schema. The verifier plans schema read-only and stops if it finds schema drift. For a fresh-run check, `ProductPart`, `PartRegistryCoordinator` and `PartRegistryRequest` must be empty. Use a new local SQLite journal path for each run:

```powershell
$docId = $env:SAFARI_MANUFACTURING_GRIST_DOC_ID
$workspaceId = $env:SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID
$journal = Join-Path $env:TEMP ("safari-parts-assembly-r2-{0}.sqlite3" -f [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts/verify_isolated_assembly_persistence.py `
  --expected-doc-id $docId `
  --workspace-id $workspaceId `
  --journal-path $journal `
  --allow-isolated-grist-writes
```

The script checks the exact configured document and workspace, rejects the legacy document, reads the v15 schema plan, and refuses a reused registry document before writing. Its writes are synthetic verification records in the named isolated document only.

## Real browser setup

For browser verification, use that same isolated document and journal. Point `SAFARI_REPOSITORY` at `grist`, `SAFARI_PART_DATABASE_PATH` at the journal path above, and `COSTING_ROOT` at a temporary folder containing only synthetic ODS fixtures. Run the FastAPI app on `127.0.0.1:4321` with those process-local environment values, and run the Vite development server on `127.0.0.1:4320` (the Vite proxy targets that API). Open `http://127.0.0.1:4320` in a real browser.

Generate the disposable workbook without copying user costing files:

```powershell
$fixtureRoot = Join-Path $env:TEMP ('parts-assembly-browser-r2-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts/create_synthetic_parts_browser_fixture.py --root $fixtureRoot
$env:COSTING_ROOT = $fixtureRoot
$env:SAFARI_REPOSITORY = 'grist'
$env:SAFARI_PART_DATABASE_PATH = $journal
$env:SAFARI_MANUFACTURING_GRIST_DOC_ID = $docId
$env:SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID = $workspaceId
$api = Start-Process -WindowStyle Hidden -PassThru -WorkingDirectory (Get-Location) `
  -FilePath (Resolve-Path '.\.venv\Scripts\python.exe') `
  -ArgumentList @('-m', 'uvicorn', 'app.web:app', '--host', '127.0.0.1', '--port', '4321')
$node = 'C:\path\to\node-24\node.exe'
$vite = Start-Process -WindowStyle Hidden -PassThru -WorkingDirectory (Resolve-Path 'ui') `
  -FilePath $node -ArgumentList @('.\node_modules\vite\bin\vite.js', '--host', '127.0.0.1', '--port', '4320')
```

Run this only after the persistence verifier succeeds. Keep `GRIST_BASE_URL` and the API credential scoped to the isolated test service. Stop the two local processes after browser verification.

```powershell
Stop-Process -Id $api.Id, $vite.Id
```

With the isolated fixture, verify the line-level Canonical Part controls, the Part baseline empty/accepted-evidence sections, Part and Assembly creation, nested composition and Back navigation, page reload, and the Live Cost Canonical Part options. Confirm that the assembly remains visible in the Parts explorer while it is absent from configuration choices. Reload must read the saved identities and composition from the same Grist document. Do not treat an in-memory adapter, mocked API test, or inspection of a generated bundle as a real persistence/browser pass.

If the test Grist endpoint or isolated document is unavailable, keep both `isolated-grist-persistence` and `isolated-browser-flows` unverified and report the concrete dependency failure. Do not redirect the check to production.
