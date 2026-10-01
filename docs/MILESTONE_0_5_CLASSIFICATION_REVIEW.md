# Costing Root Classification Review

Review date: 23 September 2026  
Scope: read-only sample of `COSTING_ROOT`; no workbook or support file was changed.

## Root inventory and sampled evidence

The root contained 1,885 files, including 505 ODS workbooks. The filename-based
walk reported 310 ODS costing candidates, 29 archives, 182 masters/templates,
3 generated outputs, and 1,361 unsupported files. These are review buckets, not
business assignments; each selected ODS inspection remains read-only.

| Review category | Representative relative path | Observed evidence and treatment |
|---|---|---|
| Costing candidate | `S1KHF/Local/Safari 1000 HF Local V 4.2.ods` | Readable, 17 sheets, 61,130 external references. This is a pilot candidate; the sheet names and external links remain visible for later interpretation. |
| Archive | `04 InHouseSpares/2021/InHouse Items Consolidated1.0 - Backup 17-2-2021.ods` | Readable, with `Key` and `InHouse Cost Master` sheets. The backup filename/path keeps it out of association eligibility. |
| Master | `06 Spares Pricing/Archives/Spares List Pricing - Archive/Spares List - Master - Live - 22-12-2021.ods` | Readable, 12 sheets and 7,750 external references. The explicit `Master`/`Archive` evidence classifies it as a master/template, not a costing candidate. |
| Generated output | `S2K/labor/Export.xlsx` | Classified as generated output from its name and non-ODS type. Its workbook contents were not opened; it remains ineligible for costing association. |
| Export workbook | `S1KHF/Export/S1KHF-Export 1.0 - Max.ods` and `S1KHF/Export/S1KHF-Export 1.0.ods` | Both readable and contain `Cost Log` and `Total Summary`. Inspection evidence overrides the filename-only `Export` label to `costing_candidate`; the name alone does not make them candidates. |
| Unreadable package | `02 Safari Product List/Safari Product List_Summary v2.ods` | The package is encrypted and cannot be scanned without a password. Inspect now returns classification `unreadable` and read state `encrypted`; it cannot be associated. |
| Support database | `04 InHouseSpares/2022/Thumbs.db` | The root has 74 `.db` files and all are Windows `Thumbs.db` caches. They classify as `unsupported`; no application/business database file was found in this extension sample. |

The first 35 ODS packages in the bounded path-ordered check included 34 readable
packages and the encrypted package above. This is not a root-wide readability
count; unreadable status is determined on inspection and does not require a
full-root workbook scan.

## Classification rule updates

- Inspected encrypted, corrupt, or otherwise unreadable ODS files now receive
  an explicit `unreadable` classification. Their `read_state` still distinguishes
  encrypted from parse errors.
- `.db`, SQLite/Access database, and `.xltx` template types are separated into
  the master/template review bucket. Windows `Thumbs.db` remains unsupported.
- A non-ODS file named `Export` or `Output` is classified as a generated output
  review item and remains ineligible for association.
- An ODS `Export` workbook becomes a costing candidate only when inspected
  workbook evidence contains both `Cost Log` and `Total Summary`.

Focused regression fixtures cover archive/master/output/support categories,
encrypted/unreadable handling, and both sides of the Export workbook rule.
Classification remains a discovery aid: it does not choose a Product, Model,
Model Code, or interpret workbook line meaning.
