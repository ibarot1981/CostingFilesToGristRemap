"""Read-only isolated API fixture for the Parts mapped-lines browser flow.

Run only on the documented QA port 4331. It uses a temporary local SQLite Part
registry and a synthetic ODS file; it never reads .env or contacts Grist.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from create_synthetic_parts_browser_fixture import _content_xml, _manifest_xml, MEDIA_TYPE
from app import web
from app.domain import CostingFile, FileModelAssociation, Product, ProductModel
from app.part_identity import PartIdentityStore
from app.part_mapping import mapping_detail, save_mapping
from app.repository import InMemorySafariRepository, sha256_file


_temporary = TemporaryDirectory(prefix="safari-mapped-lines-browser-")
FIXTURE_ROOT = Path(_temporary.name)
WORKBOOK = FIXTURE_ROOT / "Browser-QA.ods"
with ZipFile(WORKBOOK, "w") as package:
    package.writestr("mimetype", MEDIA_TYPE, compress_type=ZIP_STORED)
    package.writestr("content.xml", _content_xml(), compress_type=ZIP_DEFLATED)
    package.writestr("META-INF/manifest.xml", _manifest_xml(), compress_type=ZIP_DEFLATED)

STORE_PATH = FIXTURE_ROOT / "parts.sqlite3"
registry = PartIdentityStore(STORE_PATH)
registry.set_shortcode(scope_type="global", target_id="global", target_label="Safari Manufacturing",
    shortcode="SM", actor="isolated-browser", reason="Temporary browser fixture", request_key="qa-shortcode")
preview = registry.preview(scope_type="global", target_id="global", description="Browser QA Part")
part = registry.create_part(scope_type="global", target_id="global", target_label="Safari Manufacturing",
    description="Browser QA Part", expected_name=preview["name"], actor="isolated-browser",
    reason="Temporary browser fixture", request_key="qa-part-create")["part"]

repository = InMemorySafariRepository()
repository.add_product(Product(id="qa-product", name="Browser QA Product"))
repository.add_model(ProductModel(id="qa-model", product_id="qa-product", model_number="BQA", name="Browser QA Model"))
file_id = "file:browser-qa.ods"
file_hash = sha256_file(WORKBOOK)
repository.add_file(CostingFile(id=file_id, relative_path=WORKBOOK.name,
    normalized_path=WORKBOOK.name.casefold(), name=WORKBOOK.name, extension=".ods",
    size_bytes=WORKBOOK.stat().st_size, modified_at="2026-10-15T00:00:00Z", file_hash=file_hash,
    readable=True, mapping_status="mapped", source="isolated-browser-fixture"))
association = FileModelAssociation(id="qa-association-v1", file_id=file_id,
    product_id="qa-product", model_id="qa-model", actor="isolated-browser",
    reason="Temporary browser fixture", created_at="2026-10-15T00:00:00Z", version=1)
repository.associations[association.id] = association

web._repository = repository
web._part_identity_registry = registry
web._costing_root = lambda: FIXTURE_ROOT
web._catalog_path = lambda: FIXTURE_ROOT / "no-catalog.json"
web._cached_part_groups.cache_clear()
repo, actual_file_id, actual_association, actual_hash, groups = web._part_context(WORKBOOK.name)
before = mapping_detail(web._part_mapping_store(repository), file_id=actual_file_id,
    source_hash=actual_hash, association=actual_association, groups=groups)
line_decisions = {str(line["lineKey"]): part["id"] for group in before["groups"] for line in group["rows"]}
saved = save_mapping(web._part_mapping_store(repository), file_id=actual_file_id, source_hash=actual_hash,
    association=actual_association, groups=groups, line_decisions=line_decisions,
    expected_hash=actual_hash, expected_version=before["version"],
    expected_association=actual_association.id, expected_association_version=actual_association.version,
    actor="isolated-browser", reason="Temporary mapped-lines browser read-back",
    request_key="qa-mapped-lines-assignment")

# Reopen the DB before serving the page so displayed mappings must come back
# from persisted records rather than the initial save response.
registry = PartIdentityStore(STORE_PATH)
web._part_identity_registry = registry
persisted = registry.mapping_records()
if len(persisted) != len(line_decisions) or saved["savedRows"] != len(line_decisions):
    raise RuntimeError("Isolated mapping save did not persist every expected source line")
readback = web.part_details(part["id"])["mappedLines"]
if readback["status"] != "available" or len(readback["items"]) != len(line_decisions):
    raise RuntimeError("Part detail did not read back the persisted mapping rows")
if {item["family"] for item in readback["items"]} != {"MCL", "Tool Shop", "CNC"}:
    raise RuntimeError("The browser fixture must resolve all three source families")

print(json.dumps({"fixture": "isolated local SQLite + synthetic ODS; no Grist access",
    "root": str(FIXTURE_ROOT), "document": None, "partId": part["id"], "partName": part["name"],
    "sourceWorkbook": str(WORKBOOK), "savedRows": saved["savedRows"],
    "persistedRowsAfterStoreReopen": len(persisted), "readbackRows": len(readback["items"]),
    "families": sorted({item["family"] for item in readback["items"]}),
    "baselineStatus": web.part_details(part["id"])["manufacturingBaseline"]["status"],
    "readOnlyProjection": readback["readOnly"], "sha256": hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()}, indent=2))

app = web.app
