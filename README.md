# Costing ODS Remap CLI

Windows-friendly Python CLI for processing LibreOffice Calc `.ods` product costing files.

> **Safari Manufacturing work:** the CLI remains supported while the manufacturing ERP frontend is developed. Start with the [requirements baseline](docs/MANUFACTURING_ERP_REQUIREMENTS.md), [implementation roadmap](docs/IMPLEMENTATION_ROADMAP.md), [decision log](docs/DECISION_LOG.md), and the browser-friendly [requirements status register](docs/requirements-status.html). The ready-to-copy first implementation brief is in [MILESTONE_1_IMPLEMENTATION_PROMPT.md](docs/MILESTONE_1_IMPLEMENTATION_PROMPT.md).

The app reads configured sheets, detects columns by header names and aliases, filters inactive rows from YAML rules, verifies Material Cut List data against Grist, and creates new versioned `.ods` files without overwriting the original workbook.

## Requirements

- Python 3.10.6 or newer
- LibreOffice Calc `.ods` costing files
- Optional: Grist API key and document ID for verification

## Setup

```powershell
.\setup.ps1
```

`setup.ps1` creates `.venv`, upgrades pip inside that virtual environment, and installs `requirements.txt` using `.venv\Scripts\python.exe`. It prefers the Windows launcher `py -3.10` when available and otherwise accepts any `python` on `PATH` that is Python 3.10 or newer.

Command Prompt users can run:

```bat
setup.bat
```

For Grist verification, copy `.env.example` to `.env` or set the variables in your shell:

```powershell
$env:GRIST_BASE_URL = "https://docs.getgrist.com"
$env:GRIST_DOC_ID = "your-doc-id"
$env:GRIST_API_KEY = "your-api-key"
```

Safari Manufacturing must use a separate Grist document. Its Phase 0 setup
will discover the selected writable workspace, create or validate the document
named `Safari Manufacturing`, and then store its returned ID in the ignored
local/deployment configuration as `SAFARI_MANUFACTURING_GRIST_DOC_ID`. It must
never reuse the legacy `GRIST_DOC_ID` as its write target.

The Safari Manufacturing document has been validated and its foundation schema
applied in the explicitly selected workspace. Its private document/workspace
settings are stored in ignored `config/safari_manufacturing.local.json`; copy
the required `SAFARI_MANUFACTURING_GRIST_DOC_ID` and
`SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID` values into the intended runtime
environment before selecting `SAFARI_REPOSITORY=grist`. Confirm the deployment
base URL and any remaining optional `ProductPartMSList` field mappings before
production use. The approved catalog import is live (24 Products, 44 stored
ProductModel rows, 173 Codes, 6 aliases, 68 reconciliation issues, and two
applied batches). The source catalog has 42 Model identities; two active stored
ProductModel rows do not match a current source identity, own no active Codes,
and remain for owner reconciliation. `catalog-0.2` updated 17 existing Code
rows; the verified repeat plan returned zero creates and zero updates, and the
source ODS hash is unchanged. Three owner-provided S1KHF Bearing Type mappings
are now live for `Safari 1000 HF`: the standard Local workbook has eight codes,
the Export workbook has three requested codes, and the HF-MS Drum workbook has
eight `-MS` codes. Export source spellings match the catalog display values
case-insensitively and exactly; catalog capitalization is preserved. All three
source hashes still match their ODS files; Grist contains three active model
associations, 19 unique active code owners, audit events, and queued import
batches. The latest mapping was validated and saved through the browser UI;
Mapped Files shows its codes, actor/reason, queued status, and history. Its
read-only preview reports 61,125 external references, shown as a review warning.
No source ODS or `Costing-New` data was changed. The several-file S1KHF pilot
gate is now satisfied; conflict/supersede review and owner acceptance of the UI
remain open.
The versioned `catalog-0.2` importer keeps unresolved repeated canonical Model
Codes inactive and records an error-level reconciliation issue; approved GC
rating variants remain distinct and active.

## Safari Manufacturing Phase 0

Run the API and UI as described in [`ui/README.md`](ui/README.md). The API
uses the in-memory adapter by default, which is visibly labelled in the UI and
does not mutate Grist. Set `SAFARI_REPOSITORY=grist` only after the separate
Safari document has been validated.
The API contracts and mapped-files review fields are documented in
[`docs/API_SCHEMA.md`](docs/API_SCHEMA.md); Mapped Files distinguishes
repository observation timestamps from filesystem modification-time fallbacks.

Plan guarded document creation (no Grist mutation):

```powershell
$env:GRIST_API_KEY = "..."
$env:GRIST_BASE_URL = "https://docs.getgrist.com"
$env:SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID = "workspace-id"
.\.venv\Scripts\python.exe main.py safari-setup
```

Apply document creation/reuse and the reviewed schema explicitly:

```powershell
.\.venv\Scripts\python.exe main.py safari-setup --apply --schema-apply --yes
```

The command prints the base URL, selected workspace, exact document name, and
legacy `GRIST_DOC_ID` guard. It writes no document ID or secret to source
control; set `SAFARI_MANUFACTURING_GRIST_DOC_ID` in ignored deployment
configuration after validation. The schema plan is versioned in
[`config/safari_manufacturing_schema.json`](config/safari_manufacturing_schema.json)
and implemented in `app/schema.py`; normal API startup never creates Grist
documents or tables.

Read the canonical catalog without writing the source workbook or Grist:

```powershell
.\.venv\Scripts\python.exe main.py safari-catalog --source "C:\path\Product-ProductModelNo-ModelCode.ods" --output reports/catalog-plan.json
```

After schema bootstrap, compare with the explicitly configured Safari document,
then apply the reviewed idempotent upsert only when requested:

```powershell
.\.venv\Scripts\python.exe main.py safari-catalog --source "C:\path\Product-ProductModelNo-ModelCode.ods" --grist-plan
.\.venv\Scripts\python.exe main.py safari-catalog --source "C:\path\Product-ProductModelNo-ModelCode.ods" --apply
```

`--apply` prompts before writing; `--yes` is available for an explicitly
reviewed non-interactive run. The configured Safari document and workspace are
revalidated before writes. The catalog ODS remains read-only.

Run focused backend checks and the production UI build with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd ui
npm test
npm run build
```

## Run

```powershell
.\run.ps1
```

`run.ps1` always launches the app with `.venv\Scripts\python.exe`, so it does not depend on whichever global Python is first on `PATH`.

Command Prompt users can run:

```bat
run.bat
```

Main flow:

1. Enter the folder path.
2. Enter the source `.ods` file name.
3. Choose:
   - `Verify with Grist`
   - `Create New Version`
   - `Manage Material Mapping`
   - `Manage Product Part Names`
   - `Exit`

The CLI remembers the last successfully loaded folder and source filename in `config/last_inputs.yaml`.
On the next run it asks whether to reuse them. If you enter a filename without an extension, `.ods` is assumed.

## Supported Sheets

Configured in `config/sheet_config.yaml`:

- `5. Material Cut List Price` with header row `8`
- `Tool Shop Items` with header row `9`
- `Stores and Consumables List` with header row `6`
- `CNC Cut List` with header row `8`
- `Labour - Paint - Packing` with header row `6`

Columns are detected by header name, not by position. You can add aliases in YAML when a source file uses different names.

## Verify with Grist

Verification currently supports:

- `5. Material Cut List Price` against the Grist table `ProductPartMSList`.
- `Tool Shop Items` against the Grist table `ProductPartToolShopList`.

The workflow:

- Reads active rows from the ODS sheet.
- Maps ODS material values to Grist material names using `config/material_mapping.yaml`.
- For casting, profile cutting, template, or other generic ODS materials, the app can compare through `MasterMaterial.ODSComparableMaterial` instead of requiring every detailed Grist material name to exist in the ODS file.
- Asks whether to filter Grist rows by manually entered product part names or by selecting a `ProductModelConfig` model code.
- Asks which ODS option scope to verify. In ODS costing files, `Optional Item Group 1` / `Option Item Group 1` represents product variations inside one costing file. Blank option group rows are common/shared rows.
  - `Only blanks` includes only common/shared ODS rows where the option group is blank.
  - `Specific options` scans all five supported ODS sheets for distinct nonblank option groups, lets you select one or more values, and lets you include `BLANKS/common rows` as an explicit selection.
  - `All options` includes every ODS row regardless of option group.
- Fetches matching Grist line records from the configured table for the selected sheet.
- Uses all matching Grist rows except configured retained-history exclusions such as `Part_Status = Delete`.
- Matches rows by material, dimension to cut in mm, quantity, and optional item group.
- For `Tool Shop Items` only, if the strict dimension-based key does not match and the material is matched through `ODSComparableMaterial`, the app tries a fallback key using compared material, ODS `Remarks` / Grist `ToolShop_Part_Name`, quantity, and optional item group.
- Matching keys still include `optional_item_group_1`; the ODS option scope only decides which ODS rows enter the verification comparison.
- Prints a terminal summary.
- Asks whether to update the Grist toggle field `TallyWithODS` for the current filtered verification scope.
  - Matched Grist rows are set to `Yes`.
  - Filtered Grist rows without a matching ODS row are set to `No`, clearing stale previous tallies.
- Writes one self-contained navigable HTML report plus CSV and JSON reports to a per-file folder under `reports/`.
- Report metadata includes the ODS option scope mode, selected ODS options, whether blanks/common rows are included, and all discovered option values.
- Reports the last sheet row where any column has a value and skips trailing blank rows after that point. Blank rows inside the used range are still reported.
- Includes descriptive fields such as product part name, optional item group, remarks, and CR log where those columns are available.
- Includes material audit fields such as ODS material, Grist material, compared material, and material match type.
- Includes `match_strategy` and `fallback_key` when a Tool Shop comparable-material fallback is used.
- Shows the Grist record ID for matched, mismatched, only-in-Grist, and duplicate Grist rows.
- Lists missing material mappings with ODS rows first, followed by Grist rows whose material is not reached by the current ODS-to-Grist mapping.
- Lists active rows outside the selected ODS option scope as `ignored_option_scope_rows`. Those rows are not reported as only-in-ODS, mismatched, or used for `TallyWithODS` decisions.
- Adds an ODS row accounting section so every ODS row from the first data row through the last row with a value has a visible verification outcome.
  - Rows outside the selected ODS option scope use the outcome `ignored/out of selected option scope`.

Report categories:

- matched
- only in ODS
- only in Grist
- mismatched
- missing material mapping
- ignored option scope rows
- ignored/inactive rows
- duplicate ODS keys
- duplicate Grist keys
- ODS row accounting

The HTML report includes summary cards, sticky navigation, category tables, and a search box that filters all rows in the report.

## TODO / Backlog

### Verification Scope Enhancement: Choose ODS Scope Column and Then Search/List Values

Requested future enhancement for `Verify with Grist`, especially for:

- `5. Material Cut List Price`
- `Tool Shop Items`

Current behavior:

- After selecting the Grist `ProductPartName` filter, the utility goes directly to `ODS Option Scope`.
- That scope is currently based only on the ODS column `Optional Item Group 1`.

Desired behavior:

- After selecting the Grist `ProductPartName`, add one more ODS scope step that asks which ODS column should be used for narrowing rows before verification.
- For `Material Cut List` and `Tool Shop Items`, allow choosing between:
  - `Optional Item Group 1`
  - `Machine Piece Description`
- After choosing the ODS column, allow the user to choose how to find values from that column:
  - `Search`
  - `Show all`
- Then let the user select values from that chosen ODS column for the active selected sheet only.

Expected notes for implementation:

- This should work for both `5. Material Cut List Price` and `Tool Shop Items`.
- ODS value discovery must come only from the currently selected ODS sheet being verified.
- The verification scope/filter object should be generalized so it can work with either:
  - `optional_item_group_1`
  - `product_part_name` / `Machine Piece Description`
- Reports and ignored-row reporting should clearly state which ODS column was used as the verification scope.
- Saved/replay verification state should also remember:
  - selected ODS scope column
  - selected values
  - chosen search/list path if needed

## Create New Version

The app scans all supported sheets for configured part-name fields, asks for replacements one by one, and applies the remapping everywhere those configured fields are found.

It then:

- Removes blank rows.
- Removes inactive/not-in-use rows.
- Removes rows where configured quantity fields are blank or zero.
- Optionally blanks configured costing columns while keeping structural data.
- Writes only the five configured supported sheets.
- Writes static values only; formulas from the source workbook are not preserved.
- Writes a new `.ods` file in `output/`.
- Writes a remap CSV report to a per-file folder under `reports/`.

The original file is never overwritten.

## Configuration

Edit `config/sheet_config.yaml` to tune:

- Header row number per sheet
- Canonical fields and header aliases
- Product part-name fields
- Quantity fields
- Inactive flag fields and inactive values
- Costing columns to blank
- Grist table and field mapping

Edit `config/material_mapping.yaml` to map source ODS material names to exact Grist material names.

For generic ODS material names that represent many detailed Grist materials, such as castings or profile cuttings, set `ODSComparableMaterial` in the `MasterMaterial` Grist table. During verification:

- ODS materials with an exact YAML mapping compare using the mapped Grist material name; if that mapped Grist material has `ODSComparableMaterial`, the comparable value is used for the match key.
- ODS materials without a YAML mapping can still compare if their raw ODS material name matches a value in `MasterMaterial.ODSComparableMaterial`.
- Grist materials with `ODSComparableMaterial` compare using that comparable value while reports still show the original detailed Grist material.
- Reports show `compared_material` and `material_match_type` so you can see whether the match was exact or through a comparable material class.
- Tool Shop comparable-material fallback is only used after the strict key fails. It does not change Material Cut List matching.

## Manage Material Mapping

You can maintain `config/material_mapping.yaml` from inside the utility instead of editing YAML manually.

From the main interactive flow, choose:

```text
Manage Material Mapping
```

Or open the mapping utility directly without loading an ODS file:

```powershell
.\run.ps1 materials
```

The mapping manager can:

- List or search existing mappings.
- Add a new ODS material to Grist material mapping.
- Edit an existing ODS material name or mapped Grist material name.
- Delete a mapping after confirmation.
- Validate mapped Grist material names against `MasterMaterial.MasterMaterial`.

Usage hints shown inside the utility:

- Search before adding to avoid duplicate ODS material names.
- The mapped Grist value should normally match `MasterMaterial.MasterMaterial` exactly.
- If a value matches `MasterMaterial.AlternateSize`, the validator shows the suggested `MasterMaterial` name.
- Blank input keeps the existing value while editing.
- Every add, edit, or delete creates a timestamped backup beside the mapping file, for example:

```text
config/material_mapping.backup_20260417_153000.yaml
```

The manager writes mappings sorted by ODS material name so the YAML remains readable.

## Manage Product Part Names

You can maintain product part master records, model-to-part config links, and MS Cut List part assignments from inside the utility.

From the main interactive flow, choose:

```text
Manage Product Part Names
```

The manager currently provides these options:

- `Add new Product Part Name`
- `Assign part to config`
- `Assign Product Part to MS Cut List`
- `Add New MS Cut List from ODS`

### Assign part to config

This workflow creates a new row in `ProductModelConfig` linking a selected `ProductModelCode` to a selected `ProductPartName`.

- The `ProductModelCode` picker is driven from `ProductModelMaster2`, so you can assign the first part to a new model even if it does not yet have any `ProductModelConfig` rows.
- The utility still shows how many parts are already configured for that model and prevents duplicate model-to-part links.
- After you choose the model and the target `ProductPartMaster` row, it creates the new `ProductModelConfig` entry with `ExistingProduct = Product Part`.

### Assign Product Part to MS Cut List

This workflow updates the `ProductPartName` field on existing rows in `ProductPartMSList`.

- The utility first explains that you are filtering rows from `ProductPartMSList`.
- It asks you to choose a filter option:
  - `Search based on Existing ProductPartName field`
  - `Show all Available ProductPartNames (Sorted)`
- After you pick the current `ProductPartName`, it shows the distinct `MachinePieceDesc` values for that current MS Cut List scope.
- After you choose the `MachinePieceDesc`, it previews the exact `ProductPartMSList` rows that will be updated.
- It then asks you to choose the replacement `ProductPartMaster` entry.
- After a final confirmation, it updates `ProductPartMSList.ProductPartName` on the selected rows.

### Add New MS Cut List from ODS

This workflow reads active rows from the ODS sheet `5. Material Cut List Price` and creates or syncs rows in `ProductPartMSList`.

- The utility first scans the ODS sheet and skips rows whose material cannot be mapped to `MasterMaterial`.
- It shows those skipped rows in an issue table so you can see which ODS materials are still unmapped.
- It then shows the distinct ODS `Machine Piece Description` values and lets you choose one group to import.
- After previewing the selected ODS rows, you choose whether the target should be:
  - a new `ProductPartMaster` row, or
  - an existing `ProductPartMaster` row.
- If you choose a new part, the utility checks for duplicate `ProductPartName` values before creating the new `ProductPartMaster` row.
- After the MS Cut List sync finishes, it can optionally run the existing `Assign part to config` flow for that new part.
- If you choose an existing part, the utility asks whether it should:
  - add only the missing ODS rows, or
  - treat the selected ODS rows as the master list for that `ProductPartName + MachinePieceDesc` scope.
- Matching between ODS and `ProductPartMSList` uses:
  - `MaterialToCut`
  - `Length_mm`
  - `QtyNos`
  - `OptionGroup1_TEMP`
- `Remarks` is intentionally not part of the match key. If the row key matches and only `Remarks` changed, the utility updates the Grist row and marks it as `Part_Status = Modify`.
- In master-list mode, any extra Grist rows in the selected scope are not deleted. They are only updated to `Part_Status = Delete`.
- New rows created from ODS are marked `Part_Status = Active`.
- Every status change writes a `Part_Status_Remark` message that includes the action timestamp and the change reason.

The screens include guidance text so the utility explains what table is being filtered, what the current selection means, and what will be updated before any records are changed.

## Notes

- The app uses `pyexcel-ods3` for ODS read/write. It preserves sheet data, unknown columns, reordered columns, and extra columns, but it is not intended as a high-fidelity formatting engine.
- `pandas` is included for future reporting/data analysis convenience, but the workbook workflows do not depend on dataframe-shaped data.
- Keep a backup workflow for production costing files until your exact sheet headers and Grist fields are validated.
