# Costing ODS Remap CLI

Windows-friendly Python CLI for processing LibreOffice Calc `.ods` product costing files.

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

TODO: Confirm the production Grist document ID, API base URL, and any remaining optional `ProductPartMSList` field mappings before production use.

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

## Notes

- The app uses `pyexcel-ods3` for ODS read/write. It preserves sheet data, unknown columns, reordered columns, and extra columns, but it is not intended as a high-fidelity formatting engine.
- `pandas` is included for future reporting/data analysis convenience, but the workbook workflows do not depend on dataframe-shaped data.
- Keep a backup workflow for production costing files until your exact sheet headers and Grist fields are validated.
