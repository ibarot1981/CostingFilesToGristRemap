"""Read-only live Model Code costing and explicit normalized Grist snapshots."""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone
import json
import math
import os
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain import utc_now
from app.grist_types import datetime_text, grist_datetime
from app.part_identity import PartIdentityError, request_fingerprint


INPUT_TABLES = (
    "ProductModelCode", "ProductModel", "CostingConfiguration", "CostingConfigurationRevision",
    "ConfigurationPartSelection", "ProductPart", "PartRevision", "PartMetadataVersion",
    "PartComponentRevision", "PartRevisionLine", "LineMaster", "LineRevision", "LineDetail",
    "PartPurchaseSpecification", "VendorPartMapping", "PartPurchaseRecord", "PartPurchaseUnitConversion",
    "PartPurchaseCurrencyConversion", "Vendor", "Material", "PurchaseItem", "CostingProcessRate",
)
POLICY_VERSION = "live-cost-v1"
RATE_POLICY = "net merchandise less explicit discount; tax, freight and other charges excluded"
WRITE_BATCH_SIZE = 400


def ref_id(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (list, tuple)) and len(value) > 2 and value[0] == "R":
        value = value[2]
    try:
        result = int(value)
        return result or None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    normalized = datetime_text(value)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _fields(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return row.get("fields", row)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row.get("id"), **_fields(row)}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class GristLiveCostService:
    """Uses Safari Grist as the only configuration, Part, snapshot and policy store."""

    def __init__(self, registry: Any, material_rate_loader: Callable[[list[str]], dict[str, Any]] | None = None):
        self.registry = registry
        self.client = registry.client
        self.material_rate_loader = material_rate_loader or self._load_material_rates

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return self.registry._rows(table)

    def _read_inputs(self) -> dict[str, list[dict[str, Any]]]:
        return {table: self._rows(table) for table in INPUT_TABLES}

    @staticmethod
    def _tables_fingerprint(tables: dict[str, list[dict[str, Any]]]) -> str:
        return request_fingerprint({name: rows for name, rows in tables.items()})

    def _load_material_rates(self, names: list[str]) -> dict[str, Any]:
        if not names:
            return {"status": "available", "source": "Costing-New", "readOnly": True, "rows": []}
        from app.interim_rates import load_interim_rates
        result = load_interim_rates({name: None for name in sorted(set(names))})
        result["sourceDocumentId"] = os.getenv("GRIST_DOC_ID", "")
        return result

    def _code(self, tables: dict[str, list[dict[str, Any]]], code_id: str | int) -> dict[str, Any]:
        target = int(code_id)
        row = next((item for item in tables["ProductModelCode"] if int(item.get("id", -1)) == target), None)
        if not row or not _fields(row).get("Active", True):
            raise PartIdentityError("MODEL_CODE_NOT_FOUND", "Choose an active Model Code stored in Safari Grist.")
        return row

    def _configuration(self, tables: dict[str, list[dict[str, Any]]], code_record_id: int) -> dict[str, Any] | None:
        matches = [row for row in tables["CostingConfiguration"]
                   if ref_id(_fields(row).get("ProductModelCode")) == code_record_id
                   and str(_fields(row).get("Status") or "active").casefold() == "active"]
        if len(matches) > 1:
            raise PartIdentityError("COST_CONFIGURATION_CONFLICT", "This Model Code has more than one active costing configuration; review the Grist records.")
        if not matches:
            return None
        config = matches[0]
        revision_id = ref_id(_fields(config).get("CurrentRevision"))
        revision = next((row for row in tables["CostingConfigurationRevision"] if int(row.get("id", -1)) == revision_id), None)
        if not revision or str(_fields(revision).get("Status") or "").casefold() != "published":
            return None
        selections = [row for row in tables["ConfigurationPartSelection"]
                      if ref_id(_fields(row).get("ConfigurationRevision")) == int(revision["id"])
                      and str(_fields(row).get("Status") or "active").casefold() == "active"]
        return {"record": config, "revision": revision, "selections": selections}

    def get_configuration(self, code_id: str | int) -> dict[str, Any]:
        tables = self._read_inputs()
        code = self._code(tables, code_id)
        config = self._configuration(tables, int(code["id"]))
        if not config:
            return {"status": "missing", "modelCode": _public(code), "configuration": None,
                    "message": "No explicit costing configuration exists for this Model Code. Source mappings and workbook baselines are not used as a BOM."}
        part_rows = {int(row["id"]): _fields(row) for row in tables["ProductPart"]}
        metadata_rows = {int(row["id"]): _fields(row) for row in tables["PartMetadataVersion"]}
        return {"status": "configured", "modelCode": _public(code), "configuration": {
            "id": config["record"]["id"], "key": _fields(config["record"]).get("ConfigurationKey"),
            "version": _fields(config["record"]).get("Version"), "currency": _fields(config["revision"]).get("Currency"),
            "revisionId": config["revision"]["id"], "revisionKey": _fields(config["revision"]).get("ConfigurationRevisionKey"),
            "selections": [{"selectionIdentity": _fields(row).get("SelectionIdentity") or _fields(row).get("SelectionKey"),
                "partId": _fields(row).get("StablePartId") or _fields(row).get("ProductPart"),
                "partName": metadata_rows.get(ref_id(part_rows.get(ref_id(_fields(row).get("ProductPart")), {}).get("CurrentMetadataVersion")) or -1, {}).get("DisplayName")
                    or part_rows.get(ref_id(_fields(row).get("ProductPart")) or -1, {}).get("DisplayName") or "",
                "productPartRecordId": ref_id(_fields(row).get("ProductPart")), "quantity": _fields(row).get("Quantity"),
                "uom": _fields(row).get("QuantityUOM"), "sourcingRoute": _fields(row).get("SourcingRoute") or "auto",
                "label": _fields(row).get("OccurrenceLabel") or "", "optionGroup": _fields(row).get("OptionGroup") or ""} for row in config["selections"]]}}

    def save_configuration(self, code_id: str | int, payload: dict[str, Any], *, actor: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        try:
            code_record_id = int(code_id)
        except (TypeError, ValueError):
            raise PartIdentityError("MODEL_CODE_NOT_FOUND", "Choose an active Model Code stored in Safari Grist.")
        code = next((row for row in self._rows("ProductModelCode") if int(row.get("id", -1)) == code_record_id), None)
        if not code or not _fields(code).get("Active", True):
            raise PartIdentityError("MODEL_CODE_NOT_FOUND", "Choose an active Model Code stored in Safari Grist.")
        reason = str(payload.get("reason") or "").strip()
        currency = str(payload.get("currency") or "INR").strip().upper()
        raw = payload.get("selections")
        if not request_key.strip() or not actor.strip() or not reason or not currency or not isinstance(raw, list) or not raw:
            raise PartIdentityError("COST_CONFIGURATION_INPUT_INVALID", "At least one explicit Part selection, currency, reason and request key are required.")
        part_rows = self._rows("ProductPart")
        parts_by_stable = {str(_fields(row).get("StablePartId") or ""): row for row in part_rows if _fields(row).get("StablePartId")}
        selections = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise PartIdentityError("COST_CONFIGURATION_INPUT_INVALID", "Each selection must identify a canonical Part and quantity.")
            stable_id = str(item.get("partId") or "").strip()
            identity = str(item.get("selectionIdentity") or "").strip()
            quantity, uom = _num(item.get("quantity")), str(item.get("uom") or "").strip()
            route = str(item.get("sourcingRoute") or "auto").casefold()
            if not stable_id or stable_id not in parts_by_stable or not identity or identity in seen or quantity is None or quantity <= 0 or not uom or route not in {"auto", "make", "buy"}:
                raise PartIdentityError("COST_CONFIGURATION_INPUT_INVALID", "Every unique occurrence needs a saved canonical Part, positive quantity, unit, and auto/make/buy route.")
            seen.add(identity)
            part = parts_by_stable[stable_id]
            if str(_fields(part).get("Status") or "active").casefold() != "active":
                raise PartIdentityError("COST_CONFIGURATION_PART_INACTIVE", "Retired Parts cannot be added to a current Model Code configuration.")
            selections.append({"selectionIdentity": identity, "partRecordId": int(part["id"]), "partId": stable_id,
                "quantity": quantity, "uom": uom, "sourcingRoute": route,
                "label": str(item.get("label") or "").strip(), "optionGroup": str(item.get("optionGroup") or "").strip()})
        fingerprint = request_fingerprint([code_record_id, currency, selections, actor.strip(), reason])
        with self.registry.lock:
            existing = next((row for row in self._rows("CostingConfigurationRevision") if _fields(row).get("RequestKey") == request_key), None)
            if existing:
                if _fields(existing).get("RequestFingerprint") != fingerprint:
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "This configuration request key was already used with different data.")
            configs = self._rows("CostingConfiguration")
            config = next((row for row in configs if ref_id(_fields(row).get("ProductModelCode")) == code_record_id), None)
            if not config:
                config = self.registry._ensure_keyed("CostingConfiguration", "ConfigurationKey", f"code:{code_record_id}", {
                    "ConfigurationKey": f"code:{code_record_id}", "ProductModelCode": code_record_id, "Version": 0, "Status": "active"})
            version = int(_fields(existing).get("Revision") or 0) if existing else int(_fields(config).get("Version") or 0) + 1
            revision_key = str(_fields(existing).get("ConfigurationRevisionKey")) if existing else f"code:{code_record_id}:configuration:{version}"
            revision = existing or self.registry._ensure_keyed("CostingConfigurationRevision", "ConfigurationRevisionKey", revision_key, {
                "ConfigurationRevisionKey": revision_key, "Configuration": int(config["id"]), "Revision": version,
                "Status": "publishing", "Currency": currency, "Actor": actor.strip(), "Reason": reason,
                "CreatedAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fingerprint})
            if str(_fields(revision).get("Status") or "").casefold() == "published":
                current_revision_id = ref_id(_fields(config).get("CurrentRevision"))
                current_version = int(_fields(config).get("Version") or 0)
                if current_revision_id == int(revision["id"]):
                    return self.get_configuration(code_record_id)
                if current_version >= version:
                    return {**self.get_configuration(code_record_id), "idempotent": True,
                        "requestRevisionKey": _fields(revision).get("ConfigurationRevisionKey")}
            rows = []
            for selection in selections:
                item_fingerprint = request_fingerprint([fingerprint, selection])
                selection_key = f"{revision_key}:{selection['selectionIdentity']}"
                fields = {"SelectionKey": selection_key, "SelectionIdentity": selection["selectionIdentity"],
                    "ConfigurationRevision": int(revision["id"]), "ProductPart": selection["partRecordId"],
                    "StablePartId": selection["partId"], "Quantity": selection["quantity"], "QuantityUOM": selection["uom"],
                    "SourcingRoute": selection["sourcingRoute"], "OccurrenceLabel": selection["label"],
                    "OptionGroup": selection["optionGroup"], "Status": "active", "Actor": actor.strip(),
                    "Reason": reason, "RequestKey": request_key, "RequestFingerprint": item_fingerprint}
                rows.append(("SelectionKey", selection_key, fields))
            self._upsert_rows("ConfigurationPartSelection", rows)
            self.client.update_table_records("CostingConfigurationRevision", [{"id": int(revision["id"]), "fields": {"Status": "published"}}])
            self.client.update_table_records("CostingConfiguration", [{"id": int(config["id"]), "fields": {
                "CurrentRevision": int(revision["id"]), "Version": version, "Currency": currency, "UpdatedAt": grist_datetime(utc_now()),
                "Actor": actor.strip(), "Reason": reason, "RequestKey": request_key, "RequestFingerprint": fingerprint}}])
            result = self.get_configuration(code_record_id)
            if result["status"] != "configured" or result["configuration"]["revisionId"] != revision["id"] or len(result["configuration"]["selections"]) != len(selections):
                raise PartIdentityError("COST_CONFIGURATION_WRITE_UNCONFIRMED", "Grist did not confirm the complete Model Code configuration revision.")
            return result

    def record_process_rate(self, line_master_id: int, payload: dict[str, Any], *, actor: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        line = next((row for row in self._rows("LineMaster") if int(row.get("id", -1)) == int(line_master_id)), None)
        rate, uom = _num(payload.get("rate")), str(payload.get("uom") or "").strip()
        currency, source = str(payload.get("currency") or "").strip().upper(), str(payload.get("sourceReference") or "").strip()
        effective = _date(payload.get("effectiveAt") or utc_now())
        reason = str(payload.get("reason") or "").strip()
        if not line or rate is None or rate < 0 or not uom or not currency or not source or not effective or not actor.strip() or not reason or not request_key.strip():
            raise PartIdentityError("PROCESS_RATE_INPUT_INVALID", "Choose a process line and enter a nonnegative rate, unit, currency, effective time, source reference and reason.")
        fingerprint = request_fingerprint([line_master_id, rate, uom, currency, effective.isoformat(), source, actor.strip(), reason])
        key = f"process-rate:{line_master_id}:{request_key}"
        with self.registry.lock:
            existing = next((row for row in self._rows("CostingProcessRate") if _fields(row).get("ProcessRateKey") == key), None)
            if existing:
                if _fields(existing).get("RequestFingerprint") != fingerprint:
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "This process-rate request key was already used with different data.")
                return {"rate": _public(existing), "idempotent": True}
            row = self.registry._ensure_keyed("CostingProcessRate", "ProcessRateKey", key, {"ProcessRateKey": key,
                "LineMaster": int(line_master_id), "Rate": rate, "RateUOM": uom, "Currency": currency,
                "EffectiveAt": grist_datetime(effective), "SourceReference": source, "Status": "approved",
                "Actor": actor.strip(), "Reason": reason, "RecordedAt": grist_datetime(utc_now()),
                "RequestKey": request_key, "RequestFingerprint": fingerprint})
            return {"rate": _public(row), "idempotent": False}

    def _rate_map(self, names: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        if not names:
            return {}, {"status": "available", "rows": [], "sourceDocumentId": os.getenv("GRIST_DOC_ID", "")}
        try:
            response = self.material_rate_loader(names)
        except Exception as exc:
            return {}, {"status": "unavailable", "rows": [], "error": str(exc), "sourceDocumentId": os.getenv("GRIST_DOC_ID", "")}
        return {str(row.get("material") or ""): row for row in response.get("rows", [])}, response

    @staticmethod
    def _part_reference_map(tables: dict[str, list[dict[str, Any]]]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        parts = {int(row["id"]): _fields(row) for row in tables["ProductPart"]}
        revisions = {int(row["id"]): _fields(row) for row in tables["PartRevision"]}
        return parts, revisions

    def live_cost(self, code_id: str | int, *, check_consistency: bool = True) -> dict[str, Any]:
        tables = self._read_inputs()
        start_fingerprint = self._tables_fingerprint(tables)
        code = self._code(tables, code_id)
        config = self._configuration(tables, int(code["id"]))
        if not config or not config["selections"]:
            return {"status": "incomplete", "generation": request_fingerprint([int(code["id"]), utc_now()]),
                "modelCode": _public(code), "configuration": None, "currency": None, "totalCost": None,
                "parts": [], "lines": [], "rateEvidence": [], "warnings": [{"code": "COST_CONFIGURATION_REQUIRED",
                "message": "An explicit current Model Code configuration is required. Source mappings and shared workbook rows do not create one."}],
                "inputFingerprint": start_fingerprint, "inputManifest": {"configuration": None, "tableFingerprint": start_fingerprint}}

        parts_by_id, revisions_by_id = self._part_reference_map(tables)
        stable_to_part = {str(fields.get("StablePartId")): (record_id, fields) for record_id, fields in parts_by_id.items() if fields.get("StablePartId")}
        metadata_by_id = {int(row["id"]): _fields(row) for row in tables["PartMetadataVersion"]}
        components_by_parent: dict[int, list[dict[str, Any]]] = {}
        for row in tables["PartComponentRevision"]:
            fields = _fields(row)
            if str(fields.get("ComponentStatus") or fields.get("Status") or "active").casefold() not in {"void", "removed", "inactive"}:
                parent_id = ref_id(fields.get("ParentRevision"))
                if parent_id:
                    components_by_parent.setdefault(parent_id, []).append({"id": row.get("id"), "fields": fields})
        lines_by_revision: dict[int, list[dict[str, Any]]] = {}
        for row in tables["PartRevisionLine"]:
            fields = _fields(row)
            if str(fields.get("Status") or "active").casefold() not in {"void", "removed", "inactive"}:
                revision_id = ref_id(fields.get("PartRevision"))
                if revision_id:
                    lines_by_revision.setdefault(revision_id, []).append({"id": row.get("id"), "fields": fields})
        line_masters = {int(row["id"]): _fields(row) for row in tables["LineMaster"]}
        line_revisions = {int(row["id"]): _fields(row) for row in tables["LineRevision"]}
        details_by_revision: dict[int, list[dict[str, Any]]] = {}
        for row in tables["LineDetail"]:
            revision_id = ref_id(_fields(row).get("LineRevision"))
            if revision_id:
                details_by_revision.setdefault(revision_id, []).append({"id": row.get("id"), "fields": _fields(row)})
        specs_by_revision: dict[int, list[dict[str, Any]]] = {}
        for row in tables["PartPurchaseSpecification"]:
            fields = _fields(row)
            if str(fields.get("Status") or "active").casefold() == "active":
                revision_id = ref_id(fields.get("PartRevision"))
                if revision_id:
                    specs_by_revision.setdefault(revision_id, []).append({"id": row.get("id"), "fields": fields})
        material_by_id = {int(row["id"]): _fields(row) for row in tables["Material"]}
        purchase_item_by_id = {int(row["id"]): _fields(row) for row in tables["PurchaseItem"]}
        process_rates = tables["CostingProcessRate"]
        selections = config["selections"]
        selection_by_identity = {str(_fields(item).get("SelectionIdentity") or _fields(item).get("SelectionKey")): item for item in selections}
        parts_out: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        current_time = datetime.now(timezone.utc)
        visiting: set[int] = set()

        def warn(code_value: str, message: str, path: str, *, blocker: bool = True):
            warnings.append({"code": code_value, "message": message, "occurrencePath": path, "blocking": blocker})

        def add_purchase_candidate(path: str, part_id: int, part_fields: dict[str, Any], revision_id: int,
                                   effective_quantity: float, quantity_per_parent: float, uom: str, spec_row: dict[str, Any], selection_row: dict[str, Any],
                                   parent_path: str | None):
            sf = _fields(spec_row)
            parts_out.append(self._part_occurrence(path, parent_path, part_id, part_fields, revision_id,
                revisions_by_id.get(revision_id, {}), metadata_by_id, effective_quantity, quantity_per_parent,
                uom, "buy", selection_row))
            candidates.append({"kind": "purchase", "lineKey": f"{path}|purchase:{sf.get('SpecificationKey') or spec_row['id']}",
                "occurrencePath": path, "partRecordId": part_id, "partRevisionId": revision_id,
                "selectionRow": selection_row, "quantity": effective_quantity, "uom": uom or str(sf.get("CostingUOM") or ""),
                "currency": str(sf.get("CostingCurrency") or "").upper(), "description": str(sf.get("Description") or sf.get("SpecificationCode") or "Purchased Part"),
                "specification": spec_row, "purchaseItem": ref_id(sf.get("PurchaseItem"))})

        def expand(part_id: int, revision_id: int, path: str, effective_quantity: float, quantity_per_parent: float,
                   quantity_uom: str, selection_row: dict[str, Any], parent_path: str | None, forced_route: str = "auto"):
            if revision_id in visiting:
                warn("PART_COMPOSITION_CYCLE", "A circular Part composition prevents a valid live total.", path)
                return
            part_fields = parts_by_id.get(part_id)
            revision_fields = revisions_by_id.get(revision_id)
            if not part_fields or not revision_fields:
                warn("PART_REVISION_UNAVAILABLE", "The selected Part or physical revision is missing from Grist.", path)
                return
            if ref_id(revision_fields.get("ProductPart")) != part_id:
                warn("PART_REVISION_MISMATCH", "The selected physical revision belongs to a different canonical Part.", path)
                return
            if str(revision_fields.get("BaselineStatus") or revision_fields.get("Status") or "draft").casefold() != "finalized":
                warn("PART_DEFINITION_DRAFT", "This Part's Rev A physical baseline is still draft; review/finalize its child closure before saving a historical snapshot.", path)
            visiting.add(revision_id)
            child_rows = components_by_parent.get(revision_id, [])
            line_links = lines_by_revision.get(revision_id, [])
            spec_rows = specs_by_revision.get(revision_id, [])
            direct_material_names = []
            detail_candidates = []
            for link in line_links:
                link_fields = _fields(link)
                line_revision_id = ref_id(link_fields.get("LineRevision"))
                line_revision = line_revisions.get(line_revision_id or -1, {})
                master_id = ref_id(link_fields.get("LineMaster"))
                master = line_masters.get(master_id or -1, {})
                process = str(link_fields.get("ProcessType") or line_revision.get("ProcessType") or master.get("ProcessType") or "unknown")
                details = details_by_revision.get(line_revision_id or -1, [])
                if not details:
                    warn("PROCESS_DETAIL_REQUIRED", f"{process} line has no normalized detail/rate basis.", path)
                for detail in details:
                    detail_fields = _fields(detail)
                    material_id = ref_id(detail_fields.get("Material"))
                    material_name = str(detail_fields.get("MaterialDisplayName") or material_by_id.get(material_id or -1, {}).get("CanonicalName") or "").strip()
                    amount_per_line = _num(detail_fields.get("Quantity"))
                    link_quantity = _num(link_fields.get("QuantityPerPart"))
                    if amount_per_line is None or link_quantity is None or amount_per_line < 0 or link_quantity <= 0:
                        warn("LINE_QUANTITY_INVALID", "A normalized line has missing or invalid quantity evidence.", path)
                        continue
                    total_quantity = effective_quantity * link_quantity * amount_per_line
                    detail_candidates.append({"kind": "material" if process.casefold() in {"mcl", "material", "material cut list"} else "process",
                        "lineKey": f"{path}|{link_fields.get('RevisionLineKey') or link['id']}|{_fields(detail).get('DetailKey') or detail['id']}",
                        "occurrencePath": path, "partRecordId": part_id, "partRevisionId": revision_id, "selectionRow": selection_row,
                        "lineLink": link, "lineMasterId": master_id, "lineRevisionId": line_revision_id,
                        "detail": detail, "detailFields": detail_fields, "process": process, "materialId": material_id,
                        "materialName": material_name, "quantity": total_quantity,
                        "uom": str(detail_fields.get("QuantityUOM") or link_fields.get("QuantityUOM") or "").strip(),
                        "description": str(detail_fields.get("ItemName") or detail_fields.get("MaterialDisplayName") or master.get("SourcePartName") or process).strip(),
                        "currency": str(_fields(config["revision"]).get("Currency") or "").upper(),
                        "physicalSignature": str(line_revision.get("PhysicalSignature") or "")})
                    if material_name and process.casefold() in {"mcl", "material", "material cut list"}:
                        direct_material_names.append(material_name)
            route = forced_route.casefold()
            if route == "auto":
                if spec_rows and (child_rows or line_links):
                    warn("SOURCING_ROUTE_REQUIRED", "This Part has both a purchase specification and a make definition; select buy or make for this occurrence.", path)
                    visiting.remove(revision_id)
                    return
                route = "buy" if spec_rows else "make"
            if route == "buy":
                if len(spec_rows) != 1:
                    warn("PURCHASE_SPECIFICATION_REQUIRED", "A buy route requires exactly one active physical purchase specification on the selected Part revision.", path)
                    visiting.remove(revision_id)
                    return
                add_purchase_candidate(path, part_id, part_fields, revision_id, effective_quantity, quantity_per_parent,
                    quantity_uom, spec_rows[0], selection_row, parent_path)
                visiting.remove(revision_id)
                return
            metadata_id = ref_id(part_fields.get("CurrentMetadataVersion"))
            metadata = metadata_by_id.get(metadata_id or -1, {})
            parts_out.append({"occurrencePath": path, "parentOccurrencePath": parent_path,
                "productPartId": str(part_fields.get("StablePartId") or ""), "productPartRecordId": part_id,
                "partRevisionId": revision_id, "metadataVersionId": metadata_id, "metadataVersion": metadata.get("Version"),
                "partNumber": part_fields.get("PartNumber"), "name": metadata.get("DisplayName") or part_fields.get("DisplayName") or "",
                "description": metadata.get("Description") or part_fields.get("Description") or "",
                "engineeringRevision": part_fields.get("EngineeringRevision") or revision_fields.get("RevisionLabel") or "A",
                "quantityPerParent": quantity_per_parent, "effectiveQuantity": effective_quantity,
                "quantityUOM": quantity_uom, "sourcingRoute": "make",
                "configurationSelectionIdentity": str(_fields(selection_row).get("SelectionIdentity") or _fields(selection_row).get("SelectionKey") or "")})
            candidates.extend(detail_candidates)
            for component in sorted(child_rows, key=lambda row: str(_fields(row).get("ComponentKey") or row.get("id"))):
                cf = _fields(component)
                child_revision_id = ref_id(cf.get("ChildRevision"))
                child_revision = revisions_by_id.get(child_revision_id or -1, {})
                child_part_id = ref_id(child_revision.get("ProductPart"))
                q = _num(cf.get("Quantity"))
                if not child_revision_id or not child_part_id or q is None or q <= 0:
                    warn("COMPONENT_INVALID", "A component reference or quantity is invalid.", path)
                    continue
                child_path = f"{path}/component:{cf.get('ComponentKey') or component['id']}"
                expand(child_part_id, child_revision_id, child_path, effective_quantity * q, q,
                    str(cf.get("QuantityUOM") or "").strip(), selection_row, path, str(cf.get("SourcingRoute") or "auto"))
            if not child_rows and not line_links:
                warn("PART_PHYSICAL_DEFINITION_EMPTY", "This make route has no child Parts or normalized process lines.", path)
            visiting.remove(revision_id)

        for selection in config["selections"]:
            sf = _fields(selection)
            stable_id = str(sf.get("StablePartId") or "")
            part_ref, part_fields = stable_to_part.get(stable_id, (None, None))
            if part_ref is None:
                warn("CONFIGURATION_PART_UNAVAILABLE", "A configured canonical Part no longer resolves.", str(sf.get("SelectionIdentity") or sf.get("SelectionKey")))
                continue
            rev_id = ref_id(part_fields.get("CurrentPartRevision"))
            qty, uom = _num(sf.get("Quantity")), str(sf.get("QuantityUOM") or "").strip()
            if not rev_id or qty is None or qty <= 0 or not uom:
                warn("CONFIGURATION_QUANTITY_INVALID", "A configured Part quantity, unit or current engineering revision is missing.", str(sf.get("SelectionIdentity") or sf.get("SelectionKey")))
                continue
            occurrence = f"selection:{sf.get('SelectionIdentity') or sf.get('SelectionKey')}"
            expand(part_ref, rev_id, occurrence, qty, qty, uom, selection, None, str(sf.get("SourcingRoute") or "auto"))

        direct_material_names = [candidate.get("materialName") for candidate in candidates if candidate["kind"] == "material" and candidate.get("materialName")]
        material_rates, material_response = self._rate_map(direct_material_names)
        evidence_rows: list[dict[str, Any]] = []
        lines_out: list[dict[str, Any]] = []
        code_currency = str(_fields(config["revision"]).get("Currency") or "").upper()
        process_by_master: dict[int, list[dict[str, Any]]] = {}
        for row in process_rates:
            rf = _fields(row)
            master_id = ref_id(rf.get("LineMaster"))
            if master_id:
                process_by_master.setdefault(master_id, []).append({"id": row.get("id"), "fields": rf})
        for candidate in candidates:
            if candidate["kind"] == "purchase":
                sf = _fields(candidate["specification"])
                purchase_rows = [row for row in tables["PartPurchaseRecord"] if ref_id(_fields(row).get("PartPurchaseSpecification")) == int(candidate["specification"]["id"])]
                unit_rows = [_fields(row) for row in tables["PartPurchaseUnitConversion"] if ref_id(_fields(row).get("PartPurchaseSpecification")) == int(candidate["specification"]["id"])]
                currency_rows = [_fields(row) for row in tables["PartPurchaseCurrencyConversion"] if ref_id(_fields(row).get("PartPurchaseSpecification")) == int(candidate["specification"]["id"])]
                vendor_maps = {int(row["id"]): _fields(row) for row in tables["VendorPartMapping"]}
                valid_records = []
                for row in purchase_rows:
                    pf = _fields(row)
                    mapping = vendor_maps.get(ref_id(pf.get("VendorPartMapping")) or -1, {})
                    if str(mapping.get("Status") or "").casefold() == "reviewed":
                        valid_records.append(row)
                from app.purchased_parts import resolve_purchased_part_rate
                rate = resolve_purchased_part_rate({**sf, "recordId": str(candidate["specification"]["id"])}, valid_records,
                    unit_conversions=unit_rows, currency_conversions=currency_rows)
                if rate.get("status") != "available":
                    warn("PURCHASE_RATE_" + str(rate.get("status", "unavailable")).upper(), str(rate.get("reason") or "Purchased-Part rate is unavailable."), candidate["occurrencePath"])
                    lines_out.append(self._unpriced_line(candidate, rate.get("reason") or "Purchased-Part rate unavailable", "purchased_part"))
                    continue
                if rate.get("currency") != code_currency:
                    warn("CURRENCY_BASIS_MISMATCH", f"Purchased Part rate is {rate.get('currency')} while this configuration uses {code_currency}; add an explicit reviewed configuration conversion.", candidate["occurrencePath"])
                    lines_out.append(self._unpriced_line(candidate, "Currency conversion to configuration basis is unavailable.", "purchased_part"))
                    continue
                amount = candidate["quantity"] * float(rate["rate"])
                line = {"lineKey": candidate["lineKey"], "stableOccurrencePath": candidate["occurrencePath"],
                    "partOccurrencePath": candidate["occurrencePath"], "configurationSelectionIdentity": _fields(candidate["selectionRow"]).get("SelectionIdentity"),
                    "sourceType": "purchased_part", "sourceKey": f"purchase-spec:{sf.get('SpecificationKey') or candidate['specification']['id']}", "sourceRecordId": str(candidate["specification"]["id"]),
                    "sourceRevisionId": str(candidate["partRevisionId"]), "description": candidate["description"], "costCategory": "purchased_part",
                    "quantity": candidate["quantity"], "quantityUOM": candidate["uom"], "rate": rate["rate"], "rateUOM": rate["uom"],
                    "currency": rate["currency"], "grossCost": amount, "recoveryCost": 0, "adjustmentCost": 0, "netCost": amount,
                    "calculationBasis": "effective_part_quantity × latest eligible actual purchase rate", "costPolicy": RATE_POLICY,
                    "status": "available", "linear": True, "purchaseItemId": ref_id(sf.get("PurchaseItem"))}
                lines_out.append(line)
                evidence_rows.append({"lineKey": candidate["lineKey"], "sourceType": "purchased_part", "sourceTable": "PartPurchaseRecord",
                    "sourceDocumentId": getattr(self.client, "doc_id", ""), "sourceRecordId": str(rate.get("purchaseRecordId") or ""),
                    "sourceVersion": str(rate.get("transactionAt") or ""), "transactionAt": rate.get("transactionAt"),
                    "vendorId": ref_id(rate.get("vendorId")), "purchaseRecordId": ref_id(rate.get("purchaseRecordId")),
                    "rawUnitRate": rate.get("rate"), "normalizedUnitRate": rate.get("rate"), "sourceCurrency": rate.get("sourceCurrency"),
                    "sourceUOM": rate.get("purchaseUOM"), "normalizedCurrency": rate.get("currency"), "normalizedUOM": rate.get("uom"),
                    "conversionFactor": None, "baseUnitPrice": rate.get("baseUnitPrice"), "discountPerUnit": rate.get("discountPerUnit"),
                    "taxExcluded": rate.get("excludedCharges", {}).get("tax", 0), "freightExcluded": rate.get("excludedCharges", {}).get("freight", 0),
                    "otherChargesExcluded": rate.get("excludedCharges", {}).get("other", 0), "ratePolicy": rate.get("ratePolicy"),
                    "evidenceReference": rate.get("purchaseRecordId"), "resolutionStatus": "available", "reason": rate.get("reason"),
                    "purchaseSpecificationId": int(candidate["specification"]["id"])})
                continue
            if candidate["kind"] == "material":
                rate = material_rates.get(candidate.get("materialName") or "", {})
                if rate.get("status") != "available" or _num(rate.get("costingNewRate")) is None:
                    warn("MATERIAL_RATE_UNAVAILABLE", f"Material rate is {rate.get('status') or material_response.get('status') or 'unavailable'} in read-only Costing-New.", candidate["occurrencePath"])
                    lines_out.append(self._unpriced_line(candidate, "Material rate is unavailable or unmapped.", "material"))
                    continue
                unit_rate = float(rate["costingNewRate"])
                amount = candidate["quantity"] * unit_rate
                lines_out.append({"lineKey": candidate["lineKey"], "stableOccurrencePath": candidate["occurrencePath"],
                    "partOccurrencePath": candidate["occurrencePath"], "configurationSelectionIdentity": _fields(candidate["selectionRow"]).get("SelectionIdentity"),
                    "sourceType": "material", "sourceKey": str(candidate["lineKey"].split("|", 1)[-1]), "sourceRecordId": str(rate.get("masterRecordId") or ""),
                    "sourceRevisionId": str(candidate["physicalSignature"] or candidate["lineRevisionId"]), "materialId": candidate.get("materialId"),
                    "description": candidate["description"], "costCategory": "material", "quantity": candidate["quantity"],
                    "quantityUOM": candidate["uom"], "rate": unit_rate, "rateUOM": candidate["uom"], "currency": code_currency,
                    "grossCost": amount, "recoveryCost": 0, "adjustmentCost": 0, "netCost": amount,
                    "calculationBasis": "effective Part quantity × normalized material quantity × current Costing-New rate",
                    "costPolicy": str(rate.get("basis") or "MaterialLatestRate / Default_MaterialRate"), "status": "available", "linear": True})
                evidence_rows.append({"lineKey": candidate["lineKey"], "sourceType": "material", "sourceTable": str(rate.get("basis") or "MasterMaterial"),
                    "sourceDocumentId": str(material_response.get("sourceDocumentId") or ""),
                    "sourceRecordId": ",".join(str(value) for value in rate.get("rateLogRecordIds", [])) or str(rate.get("masterRecordId") or ""),
                    "sourceVersion": str(rate.get("basis") or ""), "effectiveAt": None,
                    "rawUnitRate": unit_rate, "normalizedUnitRate": unit_rate, "sourceCurrency": code_currency, "sourceUOM": candidate["uom"],
                    "normalizedCurrency": code_currency, "normalizedUOM": candidate["uom"], "conversionFactor": 1,
                    "ratePolicy": str(rate.get("basis") or "MaterialLatestRate / Default_MaterialRate"),
                    "evidenceReference": f"Costing-New/{rate.get('basis')} record {rate.get('masterRecordId')}",
                    "resolutionStatus": "available", "reason": "Current read-only MaterialRateLog/default material selection.",
                    "materialId": candidate.get("materialId")})
                continue
            master_rates = process_by_master.get(int(candidate.get("lineMasterId") or -1), [])
            as_of_rates = [(row, _date(_fields(row).get("EffectiveAt"))) for row in master_rates]
            as_of_rates = [(row, dt) for row, dt in as_of_rates if dt and dt <= current_time and str(_fields(row).get("Status") or "").casefold() == "approved"]
            if not as_of_rates:
                warn("PROCESS_RATE_UNAVAILABLE", f"No approved current rate with source evidence exists for {candidate.get('process')}.", candidate["occurrencePath"])
                lines_out.append(self._unpriced_line(candidate, "No approved current process-rate source is recorded.", "process"))
                continue
            latest_at = max(dt for _, dt in as_of_rates)
            latest = [row for row, dt in as_of_rates if dt == latest_at]
            values = {(_num(_fields(row).get("Rate")), str(_fields(row).get("RateUOM") or ""), str(_fields(row).get("Currency") or "").upper()) for row in latest}
            if len(values) != 1:
                warn("PROCESS_RATE_CONFLICT", "Different approved process rates share the latest effective time.", candidate["occurrencePath"])
                lines_out.append(self._unpriced_line(candidate, "Conflicting process rates require review.", "process"))
                continue
            unit_rate, rate_uom, currency = next(iter(values))
            row = sorted(latest, key=lambda item: int(item["id"]))[0]
            if unit_rate is None or unit_rate < 0 or rate_uom.casefold() != str(candidate["uom"]).casefold() or currency != code_currency:
                warn("PROCESS_RATE_BASIS_MISMATCH", "The current process rate has a different UOM/currency or invalid amount.", candidate["occurrencePath"])
                lines_out.append(self._unpriced_line(candidate, "Process-rate basis does not match this line/configuration.", "process"))
                continue
            amount = candidate["quantity"] * unit_rate
            lines_out.append({"lineKey": candidate["lineKey"], "stableOccurrencePath": candidate["occurrencePath"],
                "partOccurrencePath": candidate["occurrencePath"], "configurationSelectionIdentity": _fields(candidate["selectionRow"]).get("SelectionIdentity"),
                "sourceType": "process", "sourceKey": str(candidate["lineKey"].split("|", 1)[-1]), "sourceRecordId": str(row["id"]),
                "sourceRevisionId": str(candidate["physicalSignature"] or candidate["lineRevisionId"]), "description": candidate["description"],
                "costCategory": str(candidate.get("process") or "process"), "quantity": candidate["quantity"], "quantityUOM": candidate["uom"],
                "rate": unit_rate, "rateUOM": rate_uom, "currency": currency, "grossCost": amount, "recoveryCost": 0,
                "adjustmentCost": 0, "netCost": amount, "calculationBasis": "effective Part quantity × normalized process line quantity × approved process rate",
                "costPolicy": "latest effective approved CostingProcessRate", "status": "available", "linear": True})
            evidence_rows.append({"lineKey": candidate["lineKey"], "sourceType": "process", "sourceTable": "CostingProcessRate",
                "sourceDocumentId": str(getattr(self.client, "doc_id", "")), "sourceRecordId": str(row["id"]),
                "sourceVersion": str(_fields(row).get("EffectiveAt") or ""), "effectiveAt": datetime_text(_fields(row).get("EffectiveAt")),
                "rawUnitRate": unit_rate, "normalizedUnitRate": unit_rate, "sourceCurrency": currency, "sourceUOM": rate_uom,
                "normalizedCurrency": currency, "normalizedUOM": rate_uom, "conversionFactor": 1,
                "ratePolicy": "latest effective approved CostingProcessRate", "evidenceReference": _fields(row).get("SourceReference"),
                "resolutionStatus": "available", "reason": _fields(row).get("Reason"), "processRateId": int(row["id"])})

        check_fingerprint = self._tables_fingerprint(self._read_inputs()) if check_consistency else start_fingerprint
        if check_fingerprint != start_fingerprint:
            warn("LIVE_INPUTS_CHANGED_DURING_READ", "A Grist costing input changed while this Live Cost was being assembled. Refresh before saving a snapshot.", "", blocker=True)
        # Re-read the external material source once and reject a torn read if its
        # selected rate facts changed during this calculation.
        if check_consistency and direct_material_names:
            verify_rates, verify_meta = self._rate_map(direct_material_names)
            if request_fingerprint({"rates": material_rates, "source": material_response.get("source"), "document": material_response.get("sourceDocumentId")}) != request_fingerprint({"rates": verify_rates, "source": verify_meta.get("source"), "document": verify_meta.get("sourceDocumentId")}):
                warn("LIVE_RATE_SOURCE_CHANGED_DURING_READ", "Costing-New material rates changed while this Live Cost was being assembled. Refresh before saving a snapshot.", "", blocker=True)
        available_lines = [line for line in lines_out if line.get("status") == "available"]
        subtotal = sum(float(line.get("netCost") or 0) for line in available_lines)
        blockers = [item for item in warnings if item.get("blocking", True)]
        manifest = {"configurationRevisionId": int(config["revision"]["id"]), "configurationRevisionKey": _fields(config["revision"]).get("ConfigurationRevisionKey"),
            "tableCounts": {name: len(rows) for name, rows in tables.items()}, "tableFingerprint": start_fingerprint,
            "materialRateSource": material_response.get("source"), "policyVersion": POLICY_VERSION}
        input_fingerprint = request_fingerprint({"manifest": manifest, "parts": parts_out, "lines": lines_out, "rateEvidence": evidence_rows,
            "currency": code_currency, "configurationSelections": [row.get("SelectionIdentity") for row in selections]})
        return {"status": "complete" if not blockers and bool(lines_out) else "incomplete", "generation": request_fingerprint([int(code["id"]), utc_now(), input_fingerprint]),
            "modelCode": _public(code), "configuration": {"id": int(config["record"]["id"]), "revisionId": int(config["revision"]["id"]),
                "revisionKey": _fields(config["revision"]).get("ConfigurationRevisionKey"), "version": _fields(config["revision"]).get("Revision")},
            "currency": code_currency, "costBasis": "current per-code configured physical occurrences and current eligible rates",
            "totalCost": round(subtotal, 6) if not blockers else None, "knownSubtotal": round(subtotal, 6),
            "parts": parts_out, "lines": lines_out, "rateEvidence": evidence_rows, "warnings": warnings,
            "inputFingerprint": input_fingerprint, "inputManifest": manifest,
            "lineCount": len(lines_out), "availableLineCount": len(available_lines)}

    @staticmethod
    def _unpriced_line(candidate: dict[str, Any], reason: str, category: str) -> dict[str, Any]:
        return {"lineKey": candidate["lineKey"], "stableOccurrencePath": candidate["occurrencePath"],
            "partOccurrencePath": candidate["occurrencePath"], "configurationSelectionIdentity": _fields(candidate["selectionRow"]).get("SelectionIdentity"),
            "sourceType": category, "sourceKey": str(candidate.get("sourceKey") or candidate.get("materialName") or candidate.get("lineMasterId") or ""),
            "sourceRecordId": str(candidate.get("sourceRecordId") or candidate.get("lineMasterId") or ""),
            "sourceRevisionId": str(candidate.get("physicalSignature") or candidate.get("lineRevisionId") or ""),
            "description": str(candidate.get("description") or reason), "costCategory": category,
            "lineMasterId": candidate.get("lineMasterId"), "process": candidate.get("process"),
            "quantity": candidate.get("quantity"), "quantityUOM": candidate.get("uom"), "rate": None,
            "rateUOM": None, "currency": candidate.get("currency"), "grossCost": None, "recoveryCost": None,
            "adjustmentCost": None, "netCost": None, "calculationBasis": "unavailable", "costPolicy": None,
            "status": "unavailable", "reason": reason, "linear": True}

    @staticmethod
    def _part_occurrence(path: str, parent: str | None, part_record_id: int, part_fields: dict[str, Any], revision_id: int,
                         revision_fields: dict[str, Any], metadata: dict[int, dict[str, Any]], effective: float,
                         quantity_per_parent: float | None, uom: str, route: str, selection: dict[str, Any]) -> dict[str, Any]:
        metadata_id = ref_id(part_fields.get("CurrentMetadataVersion"))
        fields = metadata.get(metadata_id or -1, {})
        return {"occurrencePath": path, "parentOccurrencePath": parent, "productPartId": str(part_fields.get("StablePartId") or ""),
            "productPartRecordId": part_record_id, "partRevisionId": revision_id, "metadataVersionId": metadata_id,
            "metadataVersion": fields.get("Version"), "partNumber": part_fields.get("PartNumber"),
            "name": fields.get("DisplayName") or part_fields.get("DisplayName") or "", "description": fields.get("Description") or part_fields.get("Description") or "",
            "engineeringRevision": part_fields.get("EngineeringRevision") or revision_fields.get("RevisionLabel") or "A",
            "quantityPerParent": quantity_per_parent, "effectiveQuantity": effective, "quantityUOM": uom,
            "sourcingRoute": route, "configurationSelectionIdentity": _fields(selection).get("SelectionIdentity") or _fields(selection).get("SelectionKey")}

    @staticmethod
    def _result_content_fingerprint(live: dict[str, Any]) -> str:
        return request_fingerprint({key: live.get(key) for key in ("modelCode", "configuration", "currency", "costBasis", "totalCost", "knownSubtotal", "parts", "lines", "rateEvidence", "inputFingerprint")})

    def save_snapshot(self, code_id: str | int, payload: dict[str, Any], *, actor: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        live = payload.get("liveResult")
        if not isinstance(live, dict) or not request_key.strip() or not actor.strip() or not live.get("generation"):
            raise PartIdentityError("COST_SNAPSHOT_INPUT_INVALID", "Save Cost Snapshot requires the displayed Live Cost result and a request key.")
        if str(live.get("status") or "").casefold() != "complete" or not live.get("inputFingerprint"):
            raise PartIdentityError("COST_SNAPSHOT_INCOMPLETE", "Only a complete, fingerprinted Live Cost result can be saved as historical cost.")
        reason, label = str(payload.get("reason") or "").strip(), str(payload.get("label") or "").strip()
        if not reason:
            raise PartIdentityError("COST_SNAPSHOT_REASON_REQUIRED", "Enter a reason before saving a Cost Snapshot.")
        content_fingerprint = self._result_content_fingerprint(live)
        request_fp = request_fingerprint([int(code_id), actor.strip(), reason, label, live.get("generation"), live.get("inputFingerprint"), content_fingerprint])
        with self.registry.lock:
            publication = next((row for row in self._rows("CostSnapshotPublication") if _fields(row).get("PublicationKey") == request_key), None)
            if publication:
                if _fields(publication).get("RequestFingerprint") != request_fp:
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "This snapshot request key was already used with different frozen facts.")
                if str(_fields(publication).get("Status") or "").casefold() == "complete":
                    return self.get_snapshot(str(_fields(publication).get("SnapshotKey")))
            else:
                current = self.live_cost(code_id)
                if current.get("status") != "complete":
                    raise PartIdentityError("COST_SNAPSHOT_INCOMPLETE", "Current configuration, physical Part closure and every cost line must resolve before saving a completed snapshot. Review the Live Cost warnings.")
                if str(current.get("inputFingerprint")) != str(live.get("inputFingerprint")):
                    raise PartIdentityError("COST_LIVE_STALE", "Costing inputs changed after this Live Cost was displayed. Refresh, review the new result and save again.")
                if self._result_content_fingerprint(current) != content_fingerprint:
                    raise PartIdentityError("COST_LIVE_CONTENT_MISMATCH", "The displayed Live Cost facts do not match the current Grist calculation. Refresh before saving.")
            frozen = live
            expected_parts, expected_lines, expected_evidence = (len(frozen.get("parts", [])), len(frozen.get("lines", [])), len(frozen.get("rateEvidence", [])))
            snapshot_key = str(uuid5(NAMESPACE_URL, "safari-cost-snapshot:" + request_key))
            captured_at = _fields(publication).get("CreatedAt") if publication else grist_datetime(utc_now())
            header = {"SnapshotKey": snapshot_key, "ProductModelCode": int(code_id),
                "Configuration": int(frozen["configuration"]["id"]), "ConfigurationRevision": int(frozen["configuration"]["revisionId"]),
                "CapturedAt": captured_at, "CostingAsOf": captured_at, "CreatedBy": actor.strip(), "Label": label, "Notes": reason,
                "Currency": frozen["currency"], "CostBasis": frozen["costBasis"], "TotalCost": frozen["totalCost"],
                "CalculationPolicyVersion": POLICY_VERSION, "PublicationStatus": "publishing", "InputFingerprint": frozen["inputFingerprint"],
                "ManifestFingerprint": request_fingerprint(frozen.get("inputManifest") or {}), "PartRowCount": expected_parts,
                "LineRowCount": expected_lines, "EvidenceRowCount": expected_evidence,
                "ContentChecksum": content_fingerprint, "RequestKey": request_key, "RequestFingerprint": request_fp}
            if not publication:
                publication = self.registry._ensure_keyed("CostSnapshotPublication", "PublicationKey", request_key, {
                    "PublicationKey": request_key, "SnapshotKey": snapshot_key, "ProductModelCode": int(code_id), "Status": "publishing",
                    "RequestFingerprint": request_fp, "InputFingerprint": frozen["inputFingerprint"],
                    "ManifestFingerprint": header["ManifestFingerprint"], "ExpectedPartCount": expected_parts,
                    "ExpectedLineCount": expected_lines, "ExpectedEvidenceCount": expected_evidence,
                    "CreatedAt": captured_at, "UpdatedAt": captured_at, "Actor": actor.strip(), "Reason": reason})
            elif str(_fields(publication).get("SnapshotKey") or "") != snapshot_key:
                raise PartIdentityError("COST_SNAPSHOT_PUBLICATION_CONFLICT", "Snapshot request journal points to a different frozen snapshot identity.")
            self.registry._ensure_keyed("CostSnapshot", "SnapshotKey", snapshot_key, header)

            part_key_ids = self._publish_snapshot_parts(snapshot_key, request_key, request_fp, frozen.get("parts", []))
            line_key_ids = self._publish_snapshot_lines(snapshot_key, request_key, request_fp, frozen.get("lines", []), part_key_ids, frozen.get("rateEvidence", []))
            self._publish_snapshot_evidence(snapshot_key, request_key, request_fp, frozen.get("rateEvidence", []), line_key_ids)
            header_row = next(row for row in self._rows("CostSnapshot") if _fields(row).get("SnapshotKey") == snapshot_key)
            actual_parts = [row for row in self._rows("CostSnapshotPart") if ref_id(_fields(row).get("Snapshot")) == int(header_row["id"])]
            actual_lines = [row for row in self._rows("CostSnapshotLine") if ref_id(_fields(row).get("Snapshot")) == int(header_row["id"])]
            actual_evidence = [row for row in self._rows("CostSnapshotRateEvidence") if ref_id(_fields(row).get("Snapshot")) == int(header_row["id"])]
            if (len(actual_parts) != expected_parts or len(actual_lines) != expected_lines or len(actual_evidence) != expected_evidence
                    or any(ref_id(_fields(row).get("PartOccurrence")) not in {int(item["id"]) for item in actual_parts} for row in actual_lines)
                    or any(ref_id(_fields(row).get("SnapshotLine")) not in {int(item["id"]) for item in actual_lines} for row in actual_evidence)):
                raise PartIdentityError("COST_SNAPSHOT_PUBLICATION_INCOMPLETE", "Grist has not confirmed every normalized snapshot row and relationship; retry this same request key to resume publication.")
            checksum = self._snapshot_content_checksum(actual_parts, actual_lines, actual_evidence)
            self.client.update_table_records("CostSnapshot", [{"id": int(header_row["id"]), "fields": {"PublicationStatus": "complete", "ContentChecksum": checksum}}])
            publication_row = next(row for row in self._rows("CostSnapshotPublication") if _fields(row).get("PublicationKey") == request_key)
            self.client.update_table_records("CostSnapshotPublication", [{"id": int(publication_row["id"]), "fields": {"Status": "complete", "CompletedAt": grist_datetime(utc_now()), "UpdatedAt": grist_datetime(utc_now())}}])
            result = self.get_snapshot(snapshot_key)
            if result.get("snapshot", {}).get("PublicationStatus") != "complete":
                raise PartIdentityError("COST_SNAPSHOT_WRITE_UNCONFIRMED", "Grist did not confirm the completed immutable Cost Snapshot.")
            return result

    def _publish_snapshot_parts(self, snapshot_key: str, request_key: str, request_fp: str, parts: list[dict[str, Any]]) -> dict[str, int]:
        header = next(row for row in self._rows("CostSnapshot") if _fields(row).get("SnapshotKey") == snapshot_key)
        config_revision_id = ref_id(_fields(header).get("ConfigurationRevision"))
        selection_rows = [row for row in self._rows("ConfigurationPartSelection")
                          if ref_id(_fields(row).get("ConfigurationRevision")) == config_revision_id]
        selection_ids = {_fields(row).get("SelectionIdentity"): int(row["id"]) for row in selection_rows}
        pending = []
        for item in parts:
            key = f"{snapshot_key}:{item['occurrencePath']}"
            fields = {"PartOccurrenceKey": key, "Snapshot": int(header["id"]), "StableOccurrencePath": item["occurrencePath"],
                "ProductPart": int(item["productPartRecordId"]), "PartRevision": int(item["partRevisionId"]),
                "MetadataVersion": int(item["metadataVersionId"]) if item.get("metadataVersionId") else None,
                "ParentPartOccurrence": None, "ParentOccurrencePath": item.get("parentOccurrencePath"),
                "ConfigurationSelection": selection_ids.get(item.get("configurationSelectionIdentity")),
                "PartNumber": item.get("partNumber"), "FrozenName": item.get("name"), "Description": item.get("description"),
                "EngineeringRevision": item.get("engineeringRevision"), "MetadataVersionNumber": item.get("metadataVersion"),
                "QuantityPerParent": item.get("quantityPerParent"), "EffectiveQuantity": item.get("effectiveQuantity"),
                "QuantityUOM": item.get("quantityUOM"), "SourcingRoute": item.get("sourcingRoute"),
                "RequestKey": request_key, "RequestFingerprint": request_fingerprint([request_fp, item])}
            pending.append(("PartOccurrenceKey", key, fields))
        self._upsert_rows("CostSnapshotPart", pending)
        rows = self._rows("CostSnapshotPart")
        by_path = {_fields(row).get("StableOccurrencePath"): int(row["id"]) for row in rows if ref_id(_fields(row).get("Snapshot")) == int(header["id"])}
        updates = []
        for item in parts:
            parent_path = item.get("parentOccurrencePath")
            row = next((row for row in rows if _fields(row).get("PartOccurrenceKey") == f"{snapshot_key}:{item['occurrencePath']}"), None)
            if row and parent_path and by_path.get(parent_path) and ref_id(_fields(row).get("ParentPartOccurrence")) != by_path[parent_path]:
                updates.append({"id": int(row["id"]), "fields": {"ParentPartOccurrence": by_path[parent_path]}})
        if updates:
            self.client.update_table_records("CostSnapshotPart", updates)
        return by_path

    def _publish_snapshot_lines(self, snapshot_key: str, request_key: str, request_fp: str, lines: list[dict[str, Any]],
                                part_ids: dict[str, int], evidence: list[dict[str, Any]]) -> dict[str, int]:
        header = next(row for row in self._rows("CostSnapshot") if _fields(row).get("SnapshotKey") == snapshot_key)
        materials = {str(_fields(row).get("CanonicalName") or "").casefold(): int(row["id"]) for row in self._rows("Material")}
        purchase_items = {int(row["id"]): int(row["id"]) for row in self._rows("PurchaseItem")}
        line_evidence = {str(item.get("lineKey")): item for item in evidence}
        config_revision_id = ref_id(_fields(header).get("ConfigurationRevision"))
        selection_rows = [row for row in self._rows("ConfigurationPartSelection")
                          if ref_id(_fields(row).get("ConfigurationRevision")) == config_revision_id]
        selection_ids = {_fields(row).get("SelectionIdentity"): int(row["id"]) for row in selection_rows}
        pending = []
        for item in lines:
            key = f"{snapshot_key}:{item['lineKey']}"
            evidence_item = line_evidence.get(str(item.get("lineKey")), {})
            fields = {"SnapshotLineKey": key, "Snapshot": int(header["id"]),
                "PartOccurrence": part_ids.get(item.get("partOccurrencePath")),
                "ConfigurationSelection": selection_ids.get(item.get("configurationSelectionIdentity")), "StableOccurrencePath": item.get("stableOccurrencePath"), "SourceType": item.get("sourceType"), "SourceKey": item.get("sourceKey"),
                "SourceRecordId": item.get("sourceRecordId"), "SourceRevisionId": item.get("sourceRevisionId"),
                "Material": int(item["materialId"]) if item.get("materialId") and int(item["materialId"]) in materials.values() else None,
                "PurchaseItem": int(item["purchaseItemId"]) if item.get("purchaseItemId") and int(item["purchaseItemId"]) in purchase_items else None,
                "Description": item.get("description"), "CostCategory": item.get("costCategory"), "Quantity": item.get("quantity"),
                "QuantityUOM": item.get("quantityUOM"), "Rate": item.get("rate"), "RateUOM": item.get("rateUOM"),
                "Currency": item.get("currency"), "GrossCost": item.get("grossCost"), "RecoveryCost": item.get("recoveryCost"),
                "AdjustmentCost": item.get("adjustmentCost"), "NetCost": item.get("netCost"), "CalculationBasis": item.get("calculationBasis"),
                "CostPolicy": item.get("costPolicy"), "Status": item.get("status"), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([request_fp, item, evidence_item])}
            pending.append(("SnapshotLineKey", key, fields))
        self._upsert_rows("CostSnapshotLine", pending)
        return {_fields(row).get("SnapshotLineKey")[len(snapshot_key) + 1:]: int(row["id"])
                for row in self._rows("CostSnapshotLine") if ref_id(_fields(row).get("Snapshot")) == int(header["id"])}

    def _publish_snapshot_evidence(self, snapshot_key: str, request_key: str, request_fp: str, evidence: list[dict[str, Any]], line_ids: dict[str, int]) -> None:
        header = next(row for row in self._rows("CostSnapshot") if _fields(row).get("SnapshotKey") == snapshot_key)
        pending = []
        for item in evidence:
            key = f"{snapshot_key}:{item['lineKey']}:{item['sourceType']}:{item.get('sourceRecordId') or item.get('evidenceReference') or 'source'}"
            fields = {"EvidenceKey": key, "Snapshot": int(header["id"]), "SnapshotLine": line_ids.get(str(item["lineKey"])),
                "CostingProcessRate": int(item["processRateId"]) if item.get("processRateId") else None,
                "SourceType": item.get("sourceType"), "SourceDocumentId": item.get("sourceDocumentId"),
                "SourceTable": item.get("sourceTable"), "SourceRecordId": item.get("sourceRecordId"),
                "SourceVersion": item.get("sourceVersion"), "EffectiveAt": grist_datetime(item.get("effectiveAt")) if item.get("effectiveAt") else None,
                "TransactionAt": grist_datetime(item.get("transactionAt")) if item.get("transactionAt") else None,
                "Vendor": item.get("vendorId"), "PurchaseRecord": item.get("purchaseRecordId"),
                "RawUnitRate": item.get("rawUnitRate"), "NormalizedUnitRate": item.get("normalizedUnitRate"),
                "SourceCurrency": item.get("sourceCurrency"), "SourceUOM": item.get("sourceUOM"),
                "NormalizedCurrency": item.get("normalizedCurrency"), "NormalizedUOM": item.get("normalizedUOM"),
                "ConversionFactor": item.get("conversionFactor"), "BaseUnitPrice": item.get("baseUnitPrice"),
                "DiscountPerUnit": item.get("discountPerUnit"), "TaxExcluded": item.get("taxExcluded"),
                "FreightExcluded": item.get("freightExcluded"), "OtherChargesExcluded": item.get("otherChargesExcluded"),
                "RatePolicy": item.get("ratePolicy"), "EvidenceReference": item.get("evidenceReference"),
                "ResolutionStatus": item.get("resolutionStatus"), "Reason": item.get("reason"),
                "RequestKey": request_key, "RequestFingerprint": request_fingerprint([request_fp, item])}
            pending.append(("EvidenceKey", key, fields))
        self._upsert_rows("CostSnapshotRateEvidence", pending)

    def _upsert_rows(self, table: str, rows: list[tuple[str, str, dict[str, Any]]]) -> None:
        if not rows:
            return
        current = self._rows(table)
        by_key: dict[str, dict[str, Any]] = {}
        for row in current:
            key_field_value = str(_fields(row).get(rows[0][0]) or "")
            if key_field_value:
                if key_field_value in by_key:
                    raise PartIdentityError("COST_SNAPSHOT_DUPLICATE_KEY", f"Grist contains duplicate {rows[0][0]} values in {table}.")
                by_key[key_field_value] = row
        missing = []
        for key_field, key, fields in rows:
            existing = by_key.get(key)
            if existing:
                if (_fields(existing).get("RequestFingerprint") != fields.get("RequestFingerprint")
                        or _fields(existing).get("RequestKey") != fields.get("RequestKey")):
                    raise PartIdentityError("COST_SNAPSHOT_ROW_CONFLICT", f"Frozen {table} row {key} differs from the original publication request.")
            else:
                missing.append({"fields": fields})
        for offset in range(0, len(missing), WRITE_BATCH_SIZE):
            self.client.create_table_records(table, missing[offset:offset + WRITE_BATCH_SIZE])
        confirmed = self._rows(table)
        confirmed_by_key = {str(_fields(row).get(rows[0][0]) or ""): row for row in confirmed}
        if any(key not in confirmed_by_key or _fields(confirmed_by_key[key]).get("RequestFingerprint") != fields.get("RequestFingerprint")
               for _, key, fields in rows):
            raise PartIdentityError("COST_SNAPSHOT_WRITE_UNCONFIRMED", f"Grist did not confirm every {table} row after Save.")

    def set_policy(self, scope_type: str, scope_id: str | int | None, payload: dict[str, Any], *, actor: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        scope = str(scope_type).casefold()
        policy = str(payload.get("policy") or "").casefold()
        if scope not in {"system", "product_model", "model_code"} or policy not in {"weekly", "monthly", "manual", "inherit"}:
            raise PartIdentityError("COST_POLICY_INVALID", "Choose System/Product Model/Model Code and Weekly, Monthly, Manual or Inherit.")
        target_id = int(scope_id) if scope_id not in (None, "") else None
        if (scope == "system") != (target_id is None):
            raise PartIdentityError("COST_POLICY_SCOPE_INVALID", "System policy has no target; Model and Model Code policies require an exact Grist record ID.")
        if target_id is not None:
            table = "ProductModel" if scope == "product_model" else "ProductModelCode"
            if not any(int(row.get("id", -1)) == target_id for row in self._rows(table)):
                raise PartIdentityError("COST_POLICY_SCOPE_INVALID", "The selected policy target does not exist in Safari Grist.")
        zone_name = str(payload.get("timeZone") or "Asia/Kolkata")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            raise PartIdentityError("COST_POLICY_TIMEZONE_INVALID", "Enter a recognized timezone such as Asia/Kolkata.")
        anchor = _date(payload.get("scheduleAnchorAt") or utc_now())
        if not anchor:
            raise PartIdentityError("COST_POLICY_ANCHOR_INVALID", "The schedule anchor must be a valid date and time.")
        reason = str(payload.get("reason") or "").strip()
        if not actor.strip() or not request_key.strip() or not reason:
            raise PartIdentityError("COST_POLICY_INPUT_REQUIRED", "Policy changes require an actor, reason and request key.")
        day = int(payload.get("anchorDay") or anchor.astimezone(zone).day)
        if day < 1 or day > 31:
            raise PartIdentityError("COST_POLICY_ANCHOR_INVALID", "Monthly anchor day must be between 1 and 31; short months clamp to their last day.")
        target_col = "ProductModel" if scope == "product_model" else "ProductModelCode" if scope == "model_code" else None
        existing = [row for row in self._rows("CostSnapshotPolicy") if str(_fields(row).get("ScopeType") or "").casefold() == scope
                    and (ref_id(_fields(row).get(target_col)) == target_id if target_col else True)]
        version = max([int(_fields(row).get("Version") or 0) for row in existing] or [0]) + 1
        fingerprint = request_fingerprint([scope, target_id, policy, zone_name, anchor.isoformat(), day, actor.strip(), reason])
        existing_request = next((row for row in existing if _fields(row).get("RequestKey") == request_key), None)
        if existing_request:
            if _fields(existing_request).get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This policy request key was already used with different data.")
            return self.policy_state(target_id if scope == "model_code" else self._code_for_model(target_id) if scope == "product_model" else self._first_code_id(), now=datetime.now(timezone.utc))
        key = f"{scope}:{target_id or 'default'}:v{version}"
        fields = {"PolicyKey": key, "ScopeType": scope, "ProductModel": target_id if scope == "product_model" else None,
            "ProductModelCode": target_id if scope == "model_code" else None, "Policy": policy, "TimeZone": zone_name,
            "ScheduleAnchorAt": grist_datetime(anchor), "AnchorDay": day, "Version": version, "Status": "active",
            "Actor": actor.strip(), "Reason": reason, "EffectiveAt": grist_datetime(utc_now()), "RequestKey": request_key,
            "RequestFingerprint": fingerprint}
        with self.registry.lock:
            # A new immutable policy version supersedes prior rows in this exact scope.
            for old in existing:
                if str(_fields(old).get("Status") or "").casefold() == "active":
                    self.client.update_table_records("CostSnapshotPolicy", [{"id": int(old["id"]), "fields": {"Status": "superseded"}}])
            self.registry._ensure_keyed("CostSnapshotPolicy", "PolicyKey", key, fields)
        if scope == "model_code":
            code_id = target_id
        elif scope == "product_model":
            code_id = self._code_for_model(target_id)
        else:
            code_id = self._first_code_id()
        return self.policy_state(code_id, now=datetime.now(timezone.utc))

    def _first_code_id(self) -> int:
        codes = self._rows("ProductModelCode")
        if not codes:
            return 0
        return int(codes[0]["id"])

    def _code_for_model(self, model_id: int | None) -> int:
        return next((int(row["id"]) for row in self._rows("ProductModelCode") if ref_id(_fields(row).get("ProductModel")) == model_id), 0)

    def policy_state(self, code_id: int | str, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        codes = self._rows("ProductModelCode")
        code = next((row for row in codes if int(row.get("id", -1)) == int(code_id)), None)
        model_id = ref_id(_fields(code).get("ProductModel")) if code else None
        rows = self._rows("CostSnapshotPolicy")
        def latest(scope: str, target: int | None):
            target_col = "ProductModel" if scope == "product_model" else "ProductModelCode" if scope == "model_code" else None
            candidates = [row for row in rows if str(_fields(row).get("ScopeType") or "").casefold() == scope
                          and str(_fields(row).get("Status") or "active").casefold() == "active"
                          and (ref_id(_fields(row).get(target_col)) == target if target_col else True)]
            return max(candidates, key=lambda row: int(_fields(row).get("Version") or 0), default=None)
        selected = None
        source = "system default"
        for scope, target in (("model_code", int(code_id)), ("product_model", model_id), ("system", None)):
            row = latest(scope, target)
            if row and str(_fields(row).get("Policy") or "").casefold() != "inherit":
                selected, source = row, scope
                break
        policy_fields = _fields(selected)
        policy = str(policy_fields.get("Policy") or "manual").casefold()
        zone_name = str(policy_fields.get("TimeZone") or "Asia/Kolkata")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            zone_name = "Asia/Kolkata"
            zone = ZoneInfo(zone_name)
        completed = [row for row in self._rows("CostSnapshot") if ref_id(_fields(row).get("ProductModelCode")) == int(code_id)
                     and str(_fields(row).get("PublicationStatus") or "").casefold() == "complete"]
        last = max(completed, key=lambda row: _date(_fields(row).get("CapturedAt")) or datetime.min.replace(tzinfo=timezone.utc), default=None)
        anchor = _date(policy_fields.get("ScheduleAnchorAt")) or now
        next_due = None
        if policy in {"weekly", "monthly"}:
            if not last:
                next_due = anchor
            else:
                saved = (_date(_fields(last).get("CapturedAt")) or now).astimezone(zone)
                if policy == "weekly":
                    anchor_local = anchor.astimezone(zone)
                    due_date = saved + timedelta(days=7)
                    next_due = due_date.replace(hour=anchor_local.hour, minute=anchor_local.minute, second=anchor_local.second,
                        microsecond=anchor_local.microsecond).astimezone(timezone.utc)
                else:
                    anchor_day = int(policy_fields.get("AnchorDay") or anchor.astimezone(zone).day)
                    next_month = saved.month + 1
                    year = saved.year + (1 if next_month > 12 else 0)
                    month = 1 if next_month > 12 else next_month
                    day = min(anchor_day, monthrange(year, month)[1])
                    anchor_local = anchor.astimezone(zone)
                    next_due = saved.replace(year=year, month=month, day=day, hour=anchor_local.hour, minute=anchor_local.minute,
                        second=anchor_local.second, microsecond=anchor_local.microsecond).astimezone(timezone.utc)
        due = bool(policy in {"weekly", "monthly"} and next_due and now >= next_due)
        return {"policy": policy, "source": source, "scopePolicyKey": policy_fields.get("PolicyKey"),
            "timeZone": zone_name, "scheduleAnchorAt": datetime_text(policy_fields.get("ScheduleAnchorAt")) if selected else None,
            "anchorDay": policy_fields.get("AnchorDay"), "lastSnapshotAt": datetime_text(_fields(last).get("CapturedAt")) if last else None,
            "nextDueAt": next_due.isoformat() if next_due else None, "due": due,
            "firstSnapshotDueImmediately": policy in {"weekly", "monthly"} and not bool(last),
            "snapshotNowAvailable": True, "automaticSave": False}

    def snapshot_history(self, code_id: int | str, *, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 100)
        offset = max(int(offset), 0)
        rows = [row for row in self._rows("CostSnapshot") if ref_id(_fields(row).get("ProductModelCode")) == int(code_id)
                and str(_fields(row).get("PublicationStatus") or "").casefold() == "complete"]
        rows.sort(key=lambda row: _date(_fields(row).get("CapturedAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        items = []
        for row in rows[offset:offset + limit]:
            fields = _fields(row)
            items.append({"snapshotKey": fields.get("SnapshotKey"), "capturedAt": datetime_text(fields.get("CapturedAt")),
                "createdBy": fields.get("CreatedBy"), "label": fields.get("Label"), "notes": fields.get("Notes"),
                "currency": fields.get("Currency"), "totalCost": fields.get("TotalCost"), "costBasis": fields.get("CostBasis"),
                "configurationRevision": ref_id(fields.get("ConfigurationRevision")), "partRowCount": fields.get("PartRowCount"),
                "lineRowCount": fields.get("LineRowCount"), "requestKey": fields.get("RequestKey")})
        return {"items": items, "total": len(rows), "offset": offset, "limit": limit}

    def get_snapshot(self, snapshot_key: str, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        header = next((row for row in self._rows("CostSnapshot") if _fields(row).get("SnapshotKey") == snapshot_key), None)
        if not header or str(_fields(header).get("PublicationStatus") or "").casefold() != "complete":
            raise PartIdentityError("COST_SNAPSHOT_NOT_FOUND", "The requested completed Cost Snapshot was not found.")
        hid = int(header["id"])
        part_rows = [row for row in self._rows("CostSnapshotPart") if ref_id(_fields(row).get("Snapshot")) == hid]
        line_rows = [row for row in self._rows("CostSnapshotLine") if ref_id(_fields(row).get("Snapshot")) == hid]
        evidence_rows = [row for row in self._rows("CostSnapshotRateEvidence") if ref_id(_fields(row).get("Snapshot")) == hid]
        expected_checksum = str(_fields(header).get("ContentChecksum") or "")
        if not expected_checksum or self._snapshot_content_checksum(part_rows, line_rows, evidence_rows) != expected_checksum:
            raise PartIdentityError("COST_SNAPSHOT_INTEGRITY_FAILED", "Frozen snapshot rows no longer match their publication checksum; review the Grist records before using this history.")
        parts = [_public(row) for row in part_rows]
        all_lines = [_public(row) for row in line_rows]
        evidence = [_public(row) for row in evidence_rows]
        limit, offset = min(max(int(limit), 1), 10000), max(int(offset), 0)
        lines = all_lines[offset:offset + limit]
        line_ids = {int(row["id"]) for row in lines}
        return {"snapshot": _public(header), "parts": parts, "lines": lines,
            "rateEvidence": [row for row in evidence if ref_id(_fields(row).get("SnapshotLine")) in line_ids],
            "lineCount": len(all_lines), "lineOffset": offset, "lineLimit": limit,
            "evidenceCount": len(evidence), "historicalSource": "frozen CostSnapshot records"}

    @staticmethod
    def _snapshot_content_checksum(parts: list[dict[str, Any]], lines: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
        def ordered(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
            return sorted((_fields(row) for row in rows), key=lambda fields: str(fields.get(key) or ""))
        return request_fingerprint({"parts": ordered(parts, "PartOccurrenceKey"),
            "lines": ordered(lines, "SnapshotLineKey"), "evidence": ordered(evidence, "EvidenceKey")})

    def compare(self, code_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "").casefold()
        if mode not in {"live_last", "live_snapshot", "snapshot_snapshot"}:
            raise PartIdentityError("COST_COMPARISON_MODE_INVALID", "Choose Live vs last snapshot, Live vs selected snapshot or snapshot vs snapshot.")
        if mode in {"live_last", "live_snapshot"}:
            live = payload.get("liveResult") if isinstance(payload.get("liveResult"), dict) else self.live_cost(code_id)
            if live.get("status") != "complete":
                raise PartIdentityError("COST_COMPARISON_INCOMPLETE", "An incomplete Live Cost cannot be compared as a complete total.")
            if mode == "live_last":
                history = self.snapshot_history(code_id, limit=1)
                if not history["items"]:
                    return {"status": "no_snapshot", "message": "This Model Code has no completed Cost Snapshot yet."}
                old = self.get_snapshot(history["items"][0]["snapshotKey"], limit=10000)
                left = self._snapshot_comparison_data(old)
                right = self._live_comparison_data(live)
                labels = {"left": history["items"][0]["snapshotKey"], "right": "Live"}
            else:
                old = self.get_snapshot(str(payload.get("snapshotKey") or ""), limit=10000)
                self._require_snapshot_code(old, code_id)
                left, right = self._snapshot_comparison_data(old), self._live_comparison_data(live)
                labels = {"left": str(payload.get("snapshotKey")), "right": "Live"}
        else:
            left_snapshot = self.get_snapshot(str(payload.get("leftSnapshotKey") or ""), limit=10000)
            right_snapshot = self.get_snapshot(str(payload.get("rightSnapshotKey") or ""), limit=10000)
            self._require_snapshot_code(left_snapshot, code_id)
            self._require_snapshot_code(right_snapshot, code_id)
            left, right = self._snapshot_comparison_data(left_snapshot), self._snapshot_comparison_data(right_snapshot)
            labels = {"left": str(payload.get("leftSnapshotKey")), "right": str(payload.get("rightSnapshotKey"))}
        if left.get("currency") != right.get("currency") or left.get("costBasis") != right.get("costBasis"):
            return {"status": "incomparable", "left": labels["left"], "right": labels["right"],
                "reason": "Currency or cost basis differs. Apply a reviewed conversion/policy before comparing totals."}
        return self._comparison_result(left, right, labels)

    @staticmethod
    def _require_snapshot_code(snapshot: dict[str, Any], code_id: int | str) -> None:
        if ref_id(_fields(snapshot.get("snapshot")).get("ProductModelCode")) != int(code_id):
            raise PartIdentityError("COST_SNAPSHOT_CODE_MISMATCH", "Cost comparisons can only use completed snapshots for the selected Model Code.")

    @staticmethod
    def _live_comparison_data(live: dict[str, Any]) -> dict[str, Any]:
        parts = {str(item.get("occurrencePath")): item for item in live.get("parts", [])}
        lines = []
        for item in live.get("lines", []):
            part = parts.get(str(item.get("partOccurrencePath")), {})
            lines.append({**item, "matchKey": f"{item.get('stableOccurrencePath')}|{item.get('sourceKey') or item.get('lineKey')}",
                "partNumber": part.get("partNumber"), "partName": part.get("name"), "engineeringRevision": part.get("engineeringRevision")})
        return {"total": live.get("totalCost"), "currency": live.get("currency"), "costBasis": live.get("costBasis"), "lines": lines}

    @staticmethod
    def _snapshot_comparison_data(snapshot: dict[str, Any]) -> dict[str, Any]:
        header = snapshot["snapshot"]
        parts = {str(_fields(row).get("StableOccurrencePath")): _fields(row) for row in snapshot.get("parts", [])}
        lines = []
        for row in snapshot.get("lines", []):
            fields = _fields(row)
            part = parts.get(str(fields.get("StableOccurrencePath") or ""), {})
            lines.append({"matchKey": f"{fields.get('StableOccurrencePath')}|{fields.get('SourceKey') or fields.get('SnapshotLineKey')}",
                "stableOccurrencePath": fields.get("StableOccurrencePath"), "sourceKey": fields.get("SourceKey"),
                "sourceType": fields.get("SourceType"), "sourceRevisionId": fields.get("SourceRevisionId"),
                "description": fields.get("Description"), "quantity": fields.get("Quantity"), "rate": fields.get("Rate"),
                "netCost": fields.get("NetCost"), "currency": fields.get("Currency"), "costCategory": fields.get("CostCategory"),
                "linear": fields.get("Rate") is not None and fields.get("Quantity") is not None,
                "partNumber": part.get("PartNumber"), "partName": part.get("FrozenName"), "engineeringRevision": part.get("EngineeringRevision")})
        return {"total": _fields(header).get("TotalCost"), "currency": _fields(header).get("Currency"),
            "costBasis": _fields(header).get("CostBasis"), "lines": lines}

    @staticmethod
    def _comparison_result(old: dict[str, Any], new: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
        old_total, new_total = _num(old.get("total")), _num(new.get("total"))
        if old_total is None or new_total is None:
            return {"status": "incomparable", "reason": "A complete numeric total is unavailable."}
        delta = new_total - old_total
        old_lines = {str(line.get("matchKey")): line for line in old.get("lines", [])}
        new_lines = {str(line.get("matchKey")): line for line in new.get("lines", [])}
        rows, quantity_impact, rate_impact, structural_impact, residual = [], 0.0, 0.0, 0.0, 0.0
        for key in sorted(old_lines.keys() | new_lines.keys()):
            previous, current = old_lines.get(key), new_lines.get(key)
            if previous is None:
                amount = _num(current.get("netCost")) if current else 0.0
                amount = amount or 0.0
                structural_impact += amount
                rows.append({"matchKey": key, "classification": ["addition"], "structuralImpact": amount, "netDelta": amount, "quantityImpact": None, "rateImpact": None, "reconciliationResidual": 0})
                continue
            if current is None:
                amount = -(_num(previous.get("netCost")) or 0.0)
                structural_impact += amount
                rows.append({"matchKey": key, "classification": ["removal"], "structuralImpact": amount, "netDelta": amount, "quantityImpact": None, "rateImpact": None, "reconciliationResidual": 0})
                continue
            old_q, new_q = _num(previous.get("quantity")), _num(current.get("quantity"))
            old_r, new_r = _num(previous.get("rate")), _num(current.get("rate"))
            old_cost, new_cost = _num(previous.get("netCost")), _num(current.get("netCost"))
            line_delta = (new_cost or 0.0) - (old_cost or 0.0)
            classes = []
            if previous.get("sourceRevisionId") != current.get("sourceRevisionId"):
                classes.append("Part revision / process revision")
            if old_q != new_q:
                classes.append("quantity/configuration")
            if old_r != new_r:
                classes.append("rate")
            if previous.get("sourceType") != current.get("sourceType") or previous.get("costCategory") != current.get("costCategory"):
                classes.append("configuration / cost category")
            if previous.get("partName") != current.get("partName"):
                classes.append("metadata rename (no cost identity change)")
            if not classes and abs(line_delta) < 1e-9:
                classes = ["unchanged"]
            qimpact = rimpact = line_residual = 0.0
            if previous.get("linear") and current.get("linear") and None not in (old_q, new_q, old_r, new_r):
                qimpact = (new_q - old_q) * old_r
                rimpact = new_q * (new_r - old_r)
                line_residual = line_delta - qimpact - rimpact
                quantity_impact += qimpact
                rate_impact += rimpact
                residual += line_residual
            else:
                line_residual = line_delta
                residual += line_residual
                if line_delta:
                    classes.append("nonlinear/adjustment residual")
            rows.append({"matchKey": key, "classification": classes, "partName": current.get("partName"),
                "previousQuantity": old_q, "currentQuantity": new_q, "previousRate": old_r, "currentRate": new_r,
                "previousCost": old_cost, "currentCost": new_cost, "netDelta": line_delta,
                "quantityImpact": qimpact, "rateImpact": rimpact, "structuralImpact": 0,
                "reconciliationResidual": line_residual})
        attributed = quantity_impact + rate_impact + structural_impact + residual
        return {"status": "comparable", "leftTotal": old_total, "rightTotal": new_total, "currency": old.get("currency"),
            "absoluteDifference": abs(delta), "difference": delta,
            "percentDifference": None if old_total == 0 else (delta / abs(old_total)) * 100,
            "zeroBaselinePercentUndefined": old_total == 0,
            "left": labels["left"], "right": labels["right"],
            "attributionConvention": "(Qnew−Qold)×Rold + Qnew×(Rnew−Rold); interaction is assigned to rate impact",
            "quantityImpact": quantity_impact, "rateImpact": rate_impact, "structuralImpact": structural_impact,
            "reconciliationResidual": residual, "reconciledDelta": attributed, "lines": rows}
