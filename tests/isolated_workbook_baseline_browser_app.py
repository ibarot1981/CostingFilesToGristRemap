"""Memory-only API fixture for real-browser workbook baseline QA.

This server uses a synthetic ODS directory, InMemorySafariRepository and the
GristPartRegistry against MemoryGrist. It never reads .env or contacts Grist.
Run it only on a dedicated local QA port, not the owner app ports 4320/4321.
"""
from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from pyexcel_ods3 import save_data

from app import web
from app.domain import CostingFile, FileModelAssociation, Product, ProductModel
from app.grist_parts import GristPartRegistry
from app.part_baseline import PartBaselineService
from app.part_mapping import mapping_detail, save_mapping
from app.repository import InMemorySafariRepository, sha256_file
from test_grist_parts_recovery import MemoryGrist


temporary = TemporaryDirectory(prefix="workbook-baseline-browser-")
fixture_root = Path(temporary.name)
baseline_path = fixture_root / "Browser-QA-Baseline.ods"
incoming_path = fixture_root / "Browser-QA-Incoming.ods"

MCL = "5. Material Cut List Price"
TOOLSHOP = "Tool Shop Items"
CNC = "CNC Cut List"


def _workbook(*, incoming: bool) -> OrderedDict[str, list[list[str]]]:
    mcl_rows = [
        ["Bulk MCL ready", "MS Plate", "3 x 25", "2", "Yes", "Synthetic browser fixture"],
        ["Pending MCL review", "Copper", "5 x 20", "1", "Yes", "Must stay excluded"],
        ["Draft MCL pending", "Aluminum", "4 x 30", "1", "Yes", "Unsaved draft preservation"],
    ] if incoming else []
    tool_rows = [["Bulk Tool ready", "Fixture bracket", "Steel", "80", "1", "Yes", "", "QA", "QA-ONLY"]] if incoming else []
    cnc_rows = [
        ["Bulk CNC ready", "Bulk CNC ready", "MS", "120", "60", "3", "4", "Yes"],
        ["Existing CNC baseline", "Existing CNC baseline", "MS", "140" if incoming else "120", "40", "2", "1", "Yes"],
    ]
    return OrderedDict([
        (MCL, [["Machine Piece Description", "Material to Cut", "Dimension to Cut (mm)", "Quantity Nos", "In Use", "Remarks"], *mcl_rows]),
        (TOOLSHOP, [["Machine Piece Description", "Item Name", "Material Used", "Dimension in mm", "Qty", "In Use", "Remarks", "Job Remarks", "CR Log"], *tool_rows]),
        (CNC, [["Plate Part to Cut", "Part Category", "Material used", "Length", "Width", "Thickness", "Qty Of Part Used", "In Use"], *cnc_rows]),
    ])


def _save_workbook(path: Path, *, incoming: bool) -> None:
    save_data(str(path), _workbook(incoming=incoming))


_save_workbook(baseline_path, incoming=False)
_save_workbook(incoming_path, incoming=True)

client = MemoryGrist()
registry = GristPartRegistry(client, fixture_root / "parts.sqlite3")
registry._verify_writer = lambda: None
registry.mapping_evidence_schema_available = lambda: True

part_specs = [
    ("Bulk MCL Part", "bulk-mcl-part"),
    ("Bulk Tool Part", "bulk-tool-part"),
    ("Bulk CNC Part", "bulk-cnc-part"),
    ("Existing CNC Part", "existing-cnc-part"),
]
parts = {}
for description, request_key in part_specs:
    created = registry.create_part(scope_type="product", target_id="17", target_label="QA Product",
        description=description, variant="Standard", expected_name=f"S1K — {description} — Standard",
        actor="isolated browser QA", reason="Memory-only workbook baseline browser fixture",
        request_key=request_key, revision_assertion="A")["part"]
    parts[description] = created

repository = InMemorySafariRepository()
repository.add_product(Product(id="17", name="QA Product"))
repository.add_model(ProductModel(id="qa-model", product_id="17", model_number="BQA", name="Browser QA Model"))


def _register(path: Path, association_id: str) -> FileModelAssociation:
    relative = path.relative_to(fixture_root).as_posix()
    file_id = f"file:{relative.casefold()}"
    repository.add_file(CostingFile(id=file_id, relative_path=relative, normalized_path=relative.casefold(),
        name=path.name, extension=".ods", size_bytes=path.stat().st_size, modified_at="2026-10-17T00:00:00Z",
        file_hash=sha256_file(path), readable=True, mapping_status="mapped", source="isolated-workbook-baseline-browser"))
    association = FileModelAssociation(id=association_id, file_id=file_id, product_id="17", model_id="qa-model",
        actor="isolated browser QA", reason="Memory-only workbook baseline browser fixture",
        created_at="2026-10-17T00:00:00Z", version=1)
    repository.associations[association.id] = association
    return association


baseline_association = _register(baseline_path, "qa-association-baseline")
incoming_association = _register(incoming_path, "qa-association-incoming")
web._repository = repository
web._part_identity_registry = registry
web._costing_root = lambda: fixture_root
web._catalog_path = lambda: fixture_root / "no-catalog.json"
web._cached_part_groups.cache_clear()


def _save_file_mappings(path: Path, association: FileModelAssociation, target_by_row: dict[tuple[str, int], str], request_key: str) -> tuple[str, object, object]:
    relative = path.relative_to(fixture_root).as_posix()
    repo, file_id, current_association, source_hash, groups = web._part_context(relative)
    store = web._part_mapping_store(repo)
    detail = mapping_detail(store, file_id=file_id, source_hash=source_hash, association=current_association, groups=groups)
    decisions = {}
    for group in detail["groups"]:
        for row in group.get("rows") or []:
            key = (group["sheet"], int(row["row"]))
            if key in target_by_row:
                decisions[str(row["lineKey"])] = target_by_row[key]
    saved = save_mapping(store, file_id=file_id, source_hash=source_hash, association=current_association,
        groups=groups, line_decisions=decisions, expected_hash=source_hash, expected_version=detail["version"],
        expected_association=current_association.id, expected_association_version=current_association.version,
        actor="isolated browser QA", reason="Preload only explicitly mapped fixture rows", request_key=request_key)
    return source_hash, groups, saved


# Seed one accepted Rev A baseline from the old fixture workbook. Its incoming
# CNC length is changed, so the browser can verify that bulk confirmation
# preserves this baseline and keeps the existing comparison route available.
baseline_hash, baseline_groups, baseline_saved = _save_file_mappings(baseline_path, baseline_association,
    {(CNC, 3): parts["Existing CNC Part"]["id"]}, "qa-baseline-mapping")
baseline_detail = mapping_detail(web._part_mapping_store(repository),
    file_id=f"file:{baseline_path.name.casefold()}", source_hash=baseline_hash,
    association=baseline_association, groups=baseline_groups)
preseeded_rows = [row for group in baseline_detail["groups"] for row in group.get("rows") or []
    if (row.get("part") or {}).get("id") == parts["Existing CNC Part"]["id"]]
assert len(preseeded_rows) == 1
assert preseeded_rows[0]["row"] == 3
assert preseeded_rows[0]["fields"]["part_category"] == "Existing CNC baseline"
PartBaselineService(registry).establish(part_id=parts["Existing CNC Part"]["id"],
    file_id=f"file:{baseline_path.name.casefold()}", workbook_name=baseline_path.name,
    workbook_path=baseline_path.relative_to(fixture_root).as_posix(), source_hash=baseline_hash,
    association=baseline_association, groups=baseline_groups,
    mapping_store=web._part_mapping_store(repository), source_families=None,
    actor="isolated browser QA", reason="Seed existing Part baseline for comparison check",
    request_key="qa-existing-cnc-baseline", expected_hash=baseline_hash,
    expected_association=baseline_association.id, expected_association_version=baseline_association.version,
    expected_mapping_version=baseline_detail["version"], workbook_initial=True)

incoming_hash, incoming_groups, incoming_saved = _save_file_mappings(incoming_path, incoming_association, {
    (MCL, 2): parts["Bulk MCL Part"]["id"],
    (TOOLSHOP, 2): parts["Bulk Tool Part"]["id"],
    (CNC, 2): parts["Bulk CNC Part"]["id"],
    (CNC, 3): parts["Existing CNC Part"]["id"],
}, "qa-incoming-mappings")

app = web.app


@app.get("/api/qa/workbook-baseline-state")
def workbook_baseline_state():
    baseline_rows = client.fetch_table_records_with_ids("PartBaselineProcessing")
    revision_lines = client.fetch_table_records_with_ids("PartRevisionLine")
    observations = client.fetch_table_records_with_ids("SourceLineObservation")
    requests = client.fetch_table_records_with_ids("PartRegistryRequest")
    mapping_rows = registry.mapping_records()
    return {"baselineCount": len(baseline_rows), "revisionLineCount": len(revision_lines),
        "observationCount": len(observations), "mappingRecordCount": len(mapping_rows),
        "baselineRequestKeys": sorted(str(row.get("fields", {}).get("RequestKey") or "") for row in requests
            if row.get("fields", {}).get("RequestType") == "establish_part_baseline"),
        "parts": {name: {"id": value["id"], "partNumber": value["partNumber"]} for name, value in parts.items()},
        "incomingPath": incoming_path.name, "incomingHash": incoming_hash,
        "previousPath": baseline_path.name, "previousHash": baseline_hash}


print(json.dumps({"fixture": "synthetic ODS + InMemorySafariRepository + GristPartRegistry over MemoryGrist",
    "gristAccess": False, "fixtureRoot": str(fixture_root), "incomingWorkbook": incoming_path.name,
    "incomingHash": incoming_hash, "previousWorkbook": baseline_path.name, "previousHash": baseline_hash,
    "preseededBaselinePart": parts["Existing CNC Part"]["id"],
    "preseededMappingRows": baseline_saved["savedRows"], "incomingSavedRows": incoming_saved["savedRows"],
    "partIds": {name: value["id"] for name, value in parts.items()}}, indent=2), flush=True)


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=44570)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
