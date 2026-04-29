"""CSV, JSON, and HTML report writers plus Rich summary tables."""

from __future__ import annotations

import csv
from html import escape
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from app.utils import slugify


class ReportWriter:
    """Writes reports to a report directory."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def report_path(self, prefix: str, suffix: str) -> Path:
        """Build a stable report path that is overwritten on each run."""
        return self.reports_dir / f"{slugify(prefix)}.{suffix}"

    def write_json(self, prefix: str, payload: dict[str, Any]) -> Path:
        """Write a JSON report."""
        path = self.report_path(prefix, "json")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def write_csv(self, prefix: str, rows: list[dict[str, Any]]) -> Path:
        """Write a CSV report."""
        path = self.report_path(prefix, "csv")
        fieldnames = sorted({key for row in rows for key in row.keys()})
        if not fieldnames:
            fieldnames = ["message"]
            rows = [{"message": "No detail rows"}]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_verification_html(self, prefix: str, payload: dict[str, Any]) -> Path:
        """Write a self-contained navigable HTML verification report."""
        path = self.report_path(prefix, "html")
        path.write_text(render_verification_html(payload), encoding="utf-8")
        return path


def report_dir_for_source(base_dir: str | Path, source_path: Path) -> Path:
    """Return the per-ODS report directory for a source workbook."""
    return Path(base_dir) / safe_report_folder_name(source_path.stem)


def safe_report_folder_name(name: str) -> str:
    """Return a Windows-safe folder name while preserving the ODS filename shape."""
    invalid = set('<>:"/\\|?*')
    folder_name = "".join("_" if char in invalid or ord(char) < 32 else char for char in name).strip()
    return folder_name.rstrip(". ") or "source_file"


def show_count_summary(console: Console, title: str, counts: dict[str, int]) -> None:
    """Render a simple summary count table."""
    table = Table(title=title)
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    for category, count in counts.items():
        table.add_row(category, str(count))
    console.print(table)


def render_verification_html(payload: dict[str, Any]) -> str:
    """Render a complete single-file HTML verification report."""
    title = f"Verification Report - {payload.get('sheet', '')}"
    counts = payload.get("counts", {})
    details = payload.get("details", [])
    categories = group_details(details)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    match_band = match_band_summary(payload)
    match_band_html = render_match_band(match_band)
    nav_links = "\n".join(
        f'<a href="#{slugify(category)}">{escape(display_category(category))} '
        f'<span>{len(rows)}</span></a>'
        for category, rows in categories.items()
    )
    count_cards = "\n".join(
        f'<article class="metric {status_class(category, count)}">'
        f"<span>{escape(str(count))}</span><strong>{escape(category)}</strong></article>"
        for category, count in counts.items()
    )
    sections = "\n".join(render_category_section(category, rows) for category, rows in categories.items())
    filters = payload.get("filters", {})
    sheet_stats = payload.get("sheet_stats", {})
    product_parts = filters.get("product_part_names", [])
    product_part_text = ", ".join(product_parts) if product_parts else "Not specified"
    selected_options = filters.get("selected_ods_options", [])
    discovered_options = filters.get("options_discovered", [])
    selected_options_text = ", ".join(selected_options) if selected_options else "None"
    discovered_options_text = ", ".join(discovered_options) if discovered_options else "None"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f7f7f8;
      --panel: #ffffff;
      --ink: #1d252c;
      --muted: #66717d;
      --line: #d9dee5;
      --good: #1d7f43;
      --warn: #a15c00;
      --bad: #b42318;
      --info: #146c94;
      --shadow: 0 10px 22px rgba(20, 31, 43, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.45;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 28px 32px 22px;
    }}
    h1, h2, h3 {{ margin: 0; }}
    h1 {{ font-size: 28px; letter-spacing: 0; }}
    h2 {{ font-size: 20px; margin-bottom: 14px; }}
    p {{ margin: 0; }}
    .meta {{
      margin-top: 12px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px 20px;
      color: var(--muted);
      font-size: 14px;
    }}
    nav {{
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      padding: 10px 32px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
    }}
    nav a {{
      color: var(--ink);
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      background: #fff;
      font-size: 13px;
    }}
    nav span {{
      display: inline-block;
      margin-left: 6px;
      color: var(--muted);
    }}
    main {{ padding: 24px 32px 42px; }}
    .match-band {{
      border-radius: 6px;
      padding: 15px 16px;
      margin-bottom: 18px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      box-shadow: var(--shadow);
    }}
    .match-band strong {{
      display: block;
      font-size: 16px;
      margin-bottom: 3px;
    }}
    .match-band span {{
      display: block;
      font-size: 13px;
    }}
    .match-band b {{
      font-size: 24px;
      white-space: nowrap;
    }}
    .match-band small {{
      display: block;
      font-size: 12px;
      font-weight: 600;
      text-align: right;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 5px solid var(--info);
      border-radius: 6px;
      padding: 14px;
      box-shadow: var(--shadow);
    }}
    .metric span {{
      display: block;
      font-size: 26px;
      font-weight: 700;
    }}
    .metric strong {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
      margin-top: 2px;
    }}
    .metric.good {{ border-left-color: var(--good); }}
    .metric.warn {{ border-left-color: var(--warn); }}
    .metric.bad {{ border-left-color: var(--bad); }}
    .toolbar {{
      margin: 0 0 18px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    input[type="search"] {{
      width: min(520px, 100%);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
      background: #fff;
    }}
    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: #fff;
      cursor: pointer;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      margin: 0 0 18px;
      box-shadow: var(--shadow);
      overflow: hidden;
      scroll-margin-top: 78px;
    }}
    section header {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfbfc;
    }}
    .section-summary {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }}
    .table-shell {{
      border-top: 1px solid var(--line);
      background: #fff;
    }}
    .table-tools {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfbfc;
    }}
    .table-tools button {{
      padding: 7px 10px;
      font-size: 12px;
    }}
    .x-scroll {{
      overflow-x: auto;
      overflow-y: hidden;
      height: 16px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    .x-scroll-spacer {{ height: 1px; }}
    .table-wrap {{
      max-height: min(72vh, 760px);
      overflow: auto;
      scrollbar-gutter: stable both-edges;
    }}
    table {{
      min-width: 100%;
      width: max-content;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    td {{
      min-width: 110px;
      max-width: 340px;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: #f2f4f7;
      color: #3f4a55;
      font-weight: 700;
      white-space: nowrap;
    }}
    th:first-child,
    td:first-child {{
      position: sticky;
      left: 0;
      z-index: 3;
      box-shadow: 1px 0 0 var(--line);
    }}
    th:first-child {{ z-index: 4; }}
    td:first-child {{
      background: #fff;
      font-weight: 600;
    }}
    tbody tr:nth-child(even) td {{ background: #fbfcfd; }}
    tbody tr:nth-child(even) td:first-child {{ background: #fbfcfd; }}
    .table-shell.nowrap td {{
      max-width: none;
      white-space: nowrap;
    }}
    tr.hidden {{ display: none; }}
    .empty {{
      padding: 18px;
      color: var(--muted);
    }}
    .pill {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 7px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      margin-left: 8px;
    }}
    footer {{
      padding: 22px 32px;
      color: var(--muted);
      font-size: 12px;
      border-top: 1px solid var(--line);
      background: #fff;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <div class="meta">
      <p><strong>Generated:</strong> {escape(generated_at)}</p>
      <p><strong>Source file:</strong> {escape(str(payload.get('source_file', '')))}</p>
      <p><strong>Sheet:</strong> {escape(str(payload.get('sheet', '')))}</p>
      <p><strong>Grist table:</strong> {escape(str(payload.get('grist_table', '')))}</p>
      <p><strong>Header row:</strong> {escape(str(sheet_stats.get('header_row', '')))}</p>
      <p><strong>Last row with value:</strong> {escape(str(sheet_stats.get('last_value_row_number', '')))}</p>
      <p><strong>Trailing blank rows skipped:</strong> {escape(str(sheet_stats.get('trailing_blank_rows_after_last_value', '')))}</p>
      <p><strong>Product filter:</strong> {escape(str(filters.get('product_filter', '')))}</p>
      <p><strong>ODS option scope:</strong> {escape(str(filters.get('ods_option_scope_label', '')))}</p>
      <p><strong>ODS option scope mode:</strong> {escape(str(filters.get('ods_option_scope_mode', '')))}</p>
      <p><strong>Selected ODS options:</strong> {escape(selected_options_text)}</p>
      <p><strong>BLANKS included:</strong> {escape(str(filters.get('blanks_included', '')))}</p>
      <p><strong>Options discovered:</strong> {escape(discovered_options_text)}</p>
      <p><strong>Grist exclusions:</strong> Configured deleted/history rows are ignored.</p>
      <p><strong>Product parts:</strong> {escape(product_part_text)}</p>
    </div>
  </header>
  <nav>{nav_links}</nav>
  <main>
    {match_band_html}
    <div class="metrics">{count_cards}</div>
    <div class="toolbar">
      <input id="searchBox" type="search" placeholder="Search every table in this report">
      <button type="button" onclick="clearSearch()">Clear</button>
    </div>
    {sections}
  </main>
  <footer>Self-contained report generated by costing-ods-remap.</footer>
  <script>
    const searchBox = document.getElementById('searchBox');
    const tableShells = Array.from(document.querySelectorAll('.table-shell'));

    function refreshTableScrollbars() {{
      tableShells.forEach(shell => {{
        const wrap = shell.querySelector('.table-wrap');
        const spacer = shell.querySelector('.x-scroll-spacer');
        if (wrap && spacer) {{
          spacer.style.width = `${{wrap.scrollWidth}}px`;
        }}
      }});
    }}

    tableShells.forEach(shell => {{
      const wrap = shell.querySelector('.table-wrap');
      const topScroll = shell.querySelector('.x-scroll');
      if (!wrap || !topScroll) {{
        return;
      }}
      let syncing = false;
      topScroll.addEventListener('scroll', () => {{
        if (syncing) return;
        syncing = true;
        wrap.scrollLeft = topScroll.scrollLeft;
        syncing = false;
      }});
      wrap.addEventListener('scroll', () => {{
        if (syncing) return;
        syncing = true;
        topScroll.scrollLeft = wrap.scrollLeft;
        syncing = false;
      }});
    }});

    searchBox.addEventListener('input', () => {{
      const q = searchBox.value.trim().toLowerCase();
      document.querySelectorAll('tbody tr').forEach(row => {{
        row.classList.toggle('hidden', q && !row.innerText.toLowerCase().includes(q));
      }});
    }});
    function clearSearch() {{
      searchBox.value = '';
      searchBox.dispatchEvent(new Event('input'));
      searchBox.focus();
    }}
    function scrollTable(button, direction) {{
      const wrap = button.closest('.table-shell').querySelector('.table-wrap');
      const distance = Math.max(wrap.clientWidth * 0.8, 260);
      wrap.scrollBy({{ left: direction * distance, behavior: 'smooth' }});
    }}
    function toggleTableWrap(button) {{
      const shell = button.closest('.table-shell');
      shell.classList.toggle('nowrap');
      button.textContent = shell.classList.contains('nowrap') ? 'Wrap cells' : 'No wrap';
      refreshTableScrollbars();
    }}
    refreshTableScrollbars();
    window.addEventListener('resize', refreshTableScrollbars);
  </script>
</body>
</html>
"""


def match_band_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return match band data, falling back for older payloads when needed."""
    band = payload.get("match_band")
    if isinstance(band, dict):
        return {
            "active_ods_entries": int(band.get("active_ods_entries", 0) or 0),
            "active_grist_entries": int(band.get("active_grist_entries", 0) or 0),
            "total_entries": int(band.get("total_entries", 0) or 0),
            "matched_pairs": int(band.get("matched_pairs", 0) or 0),
            "matched_entries": int(band.get("matched_entries", 0) or 0),
            "mismatched_entries": int(band.get("mismatched_entries", 0) or 0),
            "mismatch_percent": float(band.get("mismatch_percent", 0) or 0),
        }

    counts = payload.get("counts", {})
    matched_pairs = int(counts.get("matched", 0) or 0)
    active_ods_entries = matched_pairs + int(counts.get("only in ODS", 0) or 0)
    active_grist_entries = matched_pairs + int(counts.get("only in Grist", 0) or 0)
    total_entries = active_ods_entries + active_grist_entries
    matched_entries = min(matched_pairs * 2, total_entries)
    mismatched_entries = max(total_entries - matched_entries, 0)
    mismatch_percent = 0.0 if total_entries == 0 else mismatched_entries / total_entries * 100
    return {
        "active_ods_entries": active_ods_entries,
        "active_grist_entries": active_grist_entries,
        "total_entries": total_entries,
        "matched_pairs": matched_pairs,
        "matched_entries": matched_entries,
        "mismatched_entries": mismatched_entries,
        "mismatch_percent": round(mismatch_percent, 2),
    }


def render_match_band(summary: dict[str, Any]) -> str:
    """Render the top source-entry match band."""
    mismatch_percent = float(summary.get("mismatch_percent", 0) or 0)
    mismatched_entries = int(summary.get("mismatched_entries", 0) or 0)
    total_entries = int(summary.get("total_entries", 0) or 0)
    active_ods_entries = int(summary.get("active_ods_entries", 0) or 0)
    active_grist_entries = int(summary.get("active_grist_entries", 0) or 0)
    matched_pairs = int(summary.get("matched_pairs", 0) or 0)
    matched_entries = int(summary.get("matched_entries", 0) or 0)
    match_percent = 0.0 if total_entries == 0 else matched_entries / total_entries * 100
    background, text_color = match_band_colors(mismatch_percent)
    percent_text = format_percent(match_percent)
    if total_entries == 0:
        title = "No active ODS or Grist entries found"
        detail = "Active entries exclude ODS rows not in use, blank or zero quantity rows, and Grist rows marked Delete."
        status = "No active entries"
    elif mismatch_percent == 0:
        title = "All active ODS entries match active Grist entries"
        detail = (
            f"{matched_pairs} matched pairs across {active_ods_entries} active ODS entries "
            f"and {active_grist_entries} active Grist entries."
        )
        status = "Fully matched"
    elif mismatch_percent >= 100:
        title = "0% matched between active ODS and Grist entries"
        detail = (
            f"{mismatched_entries} of {total_entries} active source entries are unmatched "
            f"across ODS and Grist."
        )
        status = "Not matched"
    else:
        title = f"{percent_text} matched between active ODS and Grist entries"
        detail = (
            f"{matched_pairs} pairs matched; {mismatched_entries} of {total_entries} "
            f"active source entries remain unmatched."
        )
        status = "Partial match"

    return (
        f'<div class="match-band" style="background:{background}; color:{text_color};">'
        f"<div><strong>{escape(title)}</strong><span>{escape(detail)}</span></div>"
        f"<div><b>{escape(percent_text)}</b><small>{escape(status)}</small></div>"
        "</div>"
    )


def match_band_colors(percent: float) -> tuple[str, str]:
    """Return background and text colors for the mismatch percentage."""
    if percent <= 0:
        return "#1d7f43", "#ffffff"
    if percent >= 100:
        return "#b42318", "#ffffff"
    yellow = (245, 197, 66)
    orange = (249, 115, 22)
    ratio = max(min((percent - 1) / 98, 1), 0)
    red = round(yellow[0] + (orange[0] - yellow[0]) * ratio)
    green = round(yellow[1] + (orange[1] - yellow[1]) * ratio)
    blue = round(yellow[2] + (orange[2] - yellow[2]) * ratio)
    return f"#{red:02x}{green:02x}{blue:02x}", "#1d252c"


def format_percent(percent: float) -> str:
    """Format a report percentage without noisy decimals."""
    if percent == int(percent):
        return f"{int(percent)}%"
    return f"{percent:.2f}".rstrip("0").rstrip(".") + "%"


def group_details(details: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group flattened detail rows by category."""
    preferred = [
        "matched",
        "only_in_ods",
        "only_in_grist",
        "mismatched",
        "missing_material_mapping",
        "ignored_option_scope_rows",
        "ignored_inactive_rows",
        "duplicate_ods_keys",
        "duplicate_grist_keys",
        "ods_row_accounting",
        "grist_tally_updates",
    ]
    grouped = {category: [] for category in preferred}
    for row in details:
        category = str(row.get("category", "uncategorized"))
        grouped.setdefault(category, []).append(row)
    return grouped


def render_category_section(category: str, rows: list[dict[str, Any]]) -> str:
    """Render one report category section."""
    section_id = slugify(category)
    title = display_category(category)
    if not rows:
        return (
            f'<section id="{section_id}"><header><h2>{escape(title)} '
            f'<span class="pill">0</span></h2></header><div class="empty">No rows.</div></section>'
        )

    columns = ordered_columns(rows)
    header_html = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body_html = "\n".join(
        "<tr>" + "".join(f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"""<section id="{section_id}">
  <header>
    <h2>{escape(title)} <span class="pill">{len(rows)}</span></h2>
    <p class="section-summary">{escape(section_description(category))}</p>
  </header>
  <div class="table-shell">
    <div class="table-tools">
      <button type="button" onclick="scrollTable(this, -1)">Left</button>
      <button type="button" onclick="scrollTable(this, 1)">Right</button>
      <button type="button" onclick="toggleTableWrap(this)">No wrap</button>
    </div>
    <div class="x-scroll" aria-hidden="true"><div class="x-scroll-spacer"></div></div>
    <div class="table-wrap">
      <table>
        <thead><tr>{header_html}</tr></thead>
        <tbody>{body_html}</tbody>
      </table>
    </div>
  </div>
</section>"""


def ordered_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Return stable, useful report columns with category moved to the end."""
    preferred = [
        "source",
        "sheet",
        "row_number",
        "grist_row_id",
        "key",
        "fallback_key",
        "match_strategy",
        "field",
        "ods_value",
        "grist_value",
        "material_name",
        "ods_material",
        "grist_material",
        "compared_material",
        "material_match_type",
        "outcome",
        "product_part_name",
        "grist_product_part_name",
        "toolshop_part_name",
        "job_remarks",
        "optional_item_group_1",
        "option_value",
        "selected_option_scope",
        "remarks",
        "cr_log",
        "previous_tally_with_ods",
        "new_tally_with_ods",
        "reason",
        "count",
        "rows",
        "grist_row_ids",
        "product_parts",
        "category",
    ]
    available = {key for row in rows for key in row.keys()}
    ordered = [column for column in preferred if column in available]
    ordered.extend(sorted(available - set(ordered)))
    return ordered


def display_category(category: str) -> str:
    """Return a human-readable category label."""
    return category.replace("_", " ").title()


def status_class(category: str, count: int) -> str:
    """Return CSS class for a metric card."""
    if category == "matched":
        return "good"
    if category == "ODS row accounting":
        return ""
    if count == 0:
        return "good"
    if category in {"mismatched", "missing material mapping", "duplicate ODS keys", "duplicate Grist keys"}:
        return "bad"
    return "warn"


def section_description(category: str) -> str:
    """Describe why a category matters."""
    descriptions = {
        "matched": "Rows with a unique matching key in both ODS and Grist. Tool Shop comparable-material rows may use the fallback key when dimensions differ.",
        "only_in_ods": "Active ODS rows whose key was not found in the filtered Grist data.",
        "only_in_grist": "Filtered Grist rows whose key was not found in active ODS rows.",
        "mismatched": "Rows where matching keys were found but compared fields differed.",
        "missing_material_mapping": "Materials with no configured ODS-to-Grist mapping or comparable material class. ODS rows are listed first, followed by Grist rows whose material is not reached by any ODS mapping.",
        "ignored_option_scope_rows": "Active ODS rows ignored because their optional item group is outside the selected ODS option scope.",
        "ignored_inactive_rows": "Rows ignored because they were blank, quantity zero/blank, or marked inactive.",
        "duplicate_ods_keys": "ODS keys that appear more than once and need manual review.",
        "duplicate_grist_keys": "Grist keys that appear more than once and need manual review.",
        "ods_row_accounting": "Every ODS row from the first data row through the last row with a value, with its final verification outcome.",
        "grist_tally_updates": "Grist rows updated in TallyWithODS for the current verification scope.",
    }
    return descriptions.get(category, "")
