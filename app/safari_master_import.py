"""Explicit, versioned Costing-New master/rate capture into Safari.

The upstream document is read-only. Imported rows are staged under an
immutable version; a single control pointer makes a complete version current.
Ordinary readers never call the upstream loader in this module.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import base64
import gzip
import json
import os
from typing import Any
from uuid import uuid4

from app.domain import utc_now
from app.exceptions import GristValidationError
from app.grist import GristClient
from app.grist_admin import GristAdminClient
from app.grist_types import grist_datetime
from app.part_identity import PartIdentityError, request_fingerprint

SOURCE_DOC_ID = "cPCjjr9oCzhWC7z8vJbcky"
SOURCE_TABLES = ("MasterMaterial", "MaterialRateLog", "PurchasePartsMaster", "PurchasePartsRateLog")
READ_BATCH = 500
WRITE_BATCH = 20
CAPTURE_CHUNK_CHARS = 90_000
CAPTURE_WRITE_BATCH = 6


def _fields(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("fields", row)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str, allow_nan=False)


def _decode_json(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _source_fingerprint(tables: dict[str, list[dict[str, Any]]]) -> str:
    return hashlib.sha256(_json({name: tables[name] for name in SOURCE_TABLES}).encode("utf-8")).hexdigest()


def _record_by_key(rows: list[dict[str, Any]], field: str, key: str) -> dict[str, Any] | None:
    found = [row for row in rows if str(_fields(row).get(field) or "") == key]
    if len(found) > 1:
        raise PartIdentityError("SAFARI_IMPORT_DUPLICATE_KEY", f"Safari has duplicate {field}={key!r} rows.")
    return found[0] if found else None


def _create(client: GristClient, table: str, records: list[dict[str, Any]]) -> None:
    for start in range(0, len(records), WRITE_BATCH):
        client.create_table_records(table, [{"fields": row} for row in records[start:start + WRITE_BATCH]])


def _patch(client: GristClient, table: str, row_id: int, fields: dict[str, Any]) -> None:
    client.update_table_records(table, [{"id": row_id, "fields": fields}])


def _capture_key(request_key: str) -> str:
    return f"safari-master-capture:{request_key}"


def _capture_fingerprint(capture: dict[str, Any]) -> str:
    return request_fingerprint({"sourceFingerprint": capture.get("sourceFingerprint"),
        "materials": capture.get("materials", []), "purchases": capture.get("purchases", [])})


def _store_capture(client: GristClient, request_key: str, capture: dict[str, Any]) -> str:
    """Persist a large upstream observation in small immutable local rows."""
    key = _capture_key(request_key)
    # Compress repeated source columns/fields before sending through Grist's
    # small request limit. Base64 keeps each independently retryable chunk ASCII.
    expected_fingerprint = _capture_fingerprint(capture)
    capture["captureFingerprint"] = expected_fingerprint
    raw = json.dumps(capture, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    wire = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
    chunks = [wire[offset:offset + CAPTURE_CHUNK_CHARS] for offset in range(0, len(wire), CAPTURE_CHUNK_CHARS)] or ["{}"]
    rows = client.fetch_table_records_with_ids("SafariMasterImportCapture")
    existing = [row for row in rows if str(_fields(row).get("RequestKey") or "") == request_key]
    if existing:
        if any(str(_fields(row).get("CaptureFingerprint") or "") != expected_fingerprint
                or str(_fields(row).get("SourceFingerprint") or "") != str(capture.get("sourceFingerprint") or "")
                for row in existing):
            raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CONFLICT", "The original preview key already has different immutable source-capture content.")
        if len(existing) != len(chunks) or {int(_fields(row).get("ChunkIndex") or -1) for row in existing} != set(range(len(chunks))):
            raise PartIdentityError("SAFARI_IMPORT_CAPTURE_INCOMPLETE", "The saved source capture is incomplete. Use a new preview key; the partial capture was preserved.")
        _load_capture(client, request_key, expected_fingerprint=expected_fingerprint)
        return key
    records = []
    for index, chunk in enumerate(chunks):
        records.append({"CaptureChunkKey": f"{key}:{index:05d}", "RequestKey": request_key,
            "ChunkIndex": index, "ChunkCount": len(chunks), "SourceFingerprint": capture["sourceFingerprint"],
            "CaptureFingerprint": expected_fingerprint, "PayloadEncoding": "gzip+base64", "PayloadChunk": chunk,
            "ChunkFingerprint": hashlib.sha256(chunk.encode("ascii")).hexdigest()})
    for offset in range(0, len(records), CAPTURE_WRITE_BATCH):
        _create(client, "SafariMasterImportCapture", records[offset:offset + CAPTURE_WRITE_BATCH])
    _load_capture(client, request_key, expected_fingerprint=expected_fingerprint)
    return key


def _load_capture(client: GristClient, request_key: str, *, expected_fingerprint: str = "") -> dict[str, Any]:
    rows = [row for row in client.fetch_table_records_with_ids("SafariMasterImportCapture")
        if str(_fields(row).get("RequestKey") or "") == request_key]
    if not rows:
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_MISSING", "The persisted import preview has no immutable source capture.")
    fields = [_fields(row) for row in rows]
    counts = {int(item.get("ChunkCount") or 0) for item in fields}
    fingerprints = {str(item.get("CaptureFingerprint") or "") for item in fields}
    source_fingerprints = {str(item.get("SourceFingerprint") or "") for item in fields}
    encodings = {str(item.get("PayloadEncoding") or "json") for item in fields}
    if len(counts) != 1 or len(fingerprints) != 1 or len(source_fingerprints) != 1 or len(encodings) != 1:
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CONFLICT", "The persisted source capture chunks disagree on their immutable manifest.")
    count, = counts
    ordered = sorted(fields, key=lambda item: int(item.get("ChunkIndex") or 0))
    if len(ordered) != count or [int(item.get("ChunkIndex") or 0) for item in ordered] != list(range(count)):
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_INCOMPLETE", "The persisted source capture is missing one or more immutable chunks.")
    for item in ordered:
        chunk = str(item.get("PayloadChunk") or "")
        if hashlib.sha256(chunk.encode("ascii")).hexdigest() != str(item.get("ChunkFingerprint") or ""):
            raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CORRUPT", "A persisted source capture chunk failed its SHA-256 check.")
    try:
        wire = "".join(str(item.get("PayloadChunk") or "") for item in ordered)
        encoding = next(iter(encodings))
        if encoding == "gzip+base64":
            wire = gzip.decompress(base64.b64decode(wire.encode("ascii"), validate=True)).decode("ascii")
        elif encoding != "json":
            raise ValueError("Unsupported capture payload encoding.")
        capture = json.loads(wire)
    except (TypeError, ValueError) as exc:
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CORRUPT", "The persisted source capture is not valid JSON.") from exc
    stored_fingerprint = next(iter(fingerprints))
    if expected_fingerprint and stored_fingerprint != expected_fingerprint:
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CONFLICT", "The request and immutable source capture fingerprints do not match.")
    if _capture_fingerprint(capture) != stored_fingerprint:
        raise PartIdentityError("SAFARI_IMPORT_CAPTURE_CORRUPT", "The reconstructed source capture failed its manifest fingerprint.")
    capture["captureFingerprint"] = stored_fingerprint
    return capture


def _value_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value).strip()


def _rate_evidence(rows: list[dict[str, Any]], master_id: int, foreign_key: str, source_date: Any,
                   amount: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """Preserve the legacy selector: the source table's final related log row.

    The existing interim-rate reader selects ``entries[-1]`` from Grist's
    related-row order and uses the master formula output for price. Keep that
    exact evidence pointer; do not replace it with timestamp ranking or an
    amount/date heuristic during import.
    """
    candidates = []
    for row in rows:
        fields = _fields(row)
        if _int_ref(fields.get(foreign_key)) != master_id:
            continue
        candidates.append({"id": row.get("id"), "fields": deepcopy(fields)})
    selected = str(candidates[-1]["id"]) if candidates else None
    return selected, candidates


def _source_rows(tables: dict[str, list[dict[str, Any]]], source_doc: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    material_logs: dict[str, list[dict[str, Any]]] = {}
    for row in tables["MaterialRateLog"]:
        material_logs.setdefault(str(_int_ref(_fields(row).get("MasterMaterial")) or ""), []).append(row)
    purchase_logs: dict[str, list[dict[str, Any]]] = {}
    for row in tables["PurchasePartsRateLog"]:
        purchase_logs.setdefault(str(_int_ref(_fields(row).get("SparesPartMasterName2")) or ""), []).append(row)
    materials: list[dict[str, Any]] = []
    for source in tables["MasterMaterial"]:
        fields = _fields(source)
        name = str(fields.get("MasterMaterial") or "").strip()
        if not name:
            continue
        selected, evidence = _rate_evidence(material_logs.get(str(source.get("id")), []), int(source["id"]),
            "MasterMaterial", fields.get("LatestRateDate"), fields.get("MaterialLatestRate"))
        materials.append({"sourceRecordId": str(source["id"]), "sourceTable": "MasterMaterial",
            "sourceDocumentId": source_doc, "canonicalName": name,
            "code": str(fields.get("Code") or fields.get("MaterialCode") or "").strip(),
            "inUse": bool(fields.get("InUse")), "kgPerMeter": _num(fields.get("KGsPerMeter")),
            "latestKgPerMeterDate": fields.get("LatestKGsPerMeterDate"),
            "materialLatestRate": _num(fields.get("MaterialLatestRate")),
            "defaultMaterialRate": _num(fields.get("Default_MaterialRate")),
            "transportLatestRate": _num(fields.get("TransportLatestRate")),
            "totalRate": _num(fields.get("Total_Rate")),
            "latestRateDate": fields.get("LatestRateDate"),
            "rateBasis": "MaterialLatestRate" if material_logs.get(str(source.get("id"))) else "Default_MaterialRate",
            "selectedRateLogId": selected, "rateLogEvidence": evidence,
            "masterFingerprint": request_fingerprint({"id": source.get("id"), "fields": fields})})
    purchases: list[dict[str, Any]] = []
    for source in tables["PurchasePartsMaster"]:
        fields = _fields(source)
        name = str(fields.get("SparesPartName") or fields.get("SparesMasterPartName") or "").strip()
        if not name:
            continue
        selected, evidence = _rate_evidence(purchase_logs.get(str(source.get("id")), []), int(source["id"]),
            "SparesPartMasterName2", fields.get("SparesPartLastPurchaseDate"), fields.get("SparesPartCostRs"))
        selected_fields = next((row["fields"] for row in evidence if str(row.get("id")) == str(selected)), {})
        base_rate = _num(fields.get("SparesPartCostRs"))
        freight = _num(fields.get("SparePartsFreight"))
        handling = _num(fields.get("SparesPartHandling"))
        total_rate = _num(fields.get("TotalSparesPartCost"))
        explicit_currency = str(fields.get("Currency") or fields.get("CurrencyCode") or "").strip().upper()
        # The deployed source has no currency column on PurchasePartsMaster;
        # its authoritative price outputs are explicitly named *Rs. Map that
        # documented source denomination to ISO INR and retain the evidence.
        source_currency = explicit_currency or ("INR" if fields.get("SparesPartCostRs") not in (None, "") else "")
        currency_evidence = ("PurchasePartsMaster.Currency" if explicit_currency else
            "PurchasePartsMaster.SparesPartCostRs (source field is denominated in Rs)" if source_currency else "")
        purchases.append({"sourceRecordId": str(source["id"]), "sourceTable": "PurchasePartsMaster",
            "sourceDocumentId": source_doc, "partName": name,
            "partCode": str(fields.get("SparesPartCode") or "").strip(),
            "masterPartName": str(fields.get("SparesMasterPartName") or name).strip(),
            "unit": str(fields.get("Unit") or "").strip(), "nosPerKg": _num(fields.get("Nos_Per_KG")),
            "perNosFromKg": _num(fields.get("PerNos_FromKg")), "defaultRate": _num(fields.get("DefaultRate2")),
            "currentUnitRate": base_rate,
            "freight": freight, "handling": handling,
            "totalCurrentRate": total_rate,
            # Do not claim a complete included-charge total unless the actual
            # source exposes its base and both charge components.
            "freightIncluded": total_rate is not None and base_rate is not None and freight is not None and handling is not None,
            "lastPurchaseDate": fields.get("SparesPartLastPurchaseDate"),
            "selectedRateLogId": selected, "rateLogEvidence": evidence,
            "vendor": str(selected_fields.get("Supplier") or selected_fields.get("Vendor") or ""),
            "currency": source_currency, "currencyEvidence": currency_evidence,
            "masterFingerprint": request_fingerprint({"id": source.get("id"), "fields": fields})})
    return materials, purchases, []


def imported_purchase_rate(row: dict[str, Any], expected_uom: str, target_currency: str) -> dict[str, Any]:
    """Resolve a captured master price without inventing a unit conversion.

    Costing-New's explicit Nos_Per_KG and PerNos_FromKg fields cover the
    source's Nos/kg pair. Other unit changes require an existing approved
    local transaction conversion and are not inferred here.
    """
    fields = _fields(row)
    amount = _num(fields.get("TotalCurrentRate"))
    source_unit_label = str(fields.get("Unit") or "").strip()
    source_unit = source_unit_label[4:].strip() if source_unit_label.casefold().startswith("per ") else source_unit_label
    target_unit = str(expected_uom or "").strip()
    currency = str(fields.get("Currency") or "").strip().upper()
    if amount is None or amount < 0 or not source_unit or not target_unit or not currency:
        return {"status": "review_required", "rate": None,
            "reason": "The imported purchase price lacks a valid amount, unit or currency."}
    if currency != str(target_currency or "").strip().upper():
        return {"status": "review_required", "rate": None,
            "reason": "The imported purchase price currency does not match the Part costing currency."}
    if not bool(fields.get("FreightIncluded")):
        return {"status": "review_required", "rate": None,
            "reason": "The imported purchase price lacks a complete base, freight and handling breakdown; no charge amount was inferred."}

    def unit_kind(value: str) -> str | None:
        normalized = value.strip().casefold().replace(".", "")
        if normalized in {"nos", "no", "each", "unit", "units", "piece", "pieces", "pcs"}:
            return "nos"
        if normalized in {"kg", "kgs", "kilogram", "kilograms"}:
            return "kg"
        return normalized or None

    source_kind, target_kind = unit_kind(source_unit), unit_kind(target_unit)
    factor = 1.0
    if source_kind != target_kind:
        nos_per_kg = _num(fields.get("NosPerKG"))
        kg_per_nos = _num(fields.get("PerNosFromKg"))
        if nos_per_kg is not None and nos_per_kg <= 0 or kg_per_nos is not None and kg_per_nos <= 0:
            return {"status": "review_required", "rate": None, "reason": "The imported Nos/kg conversion is not positive."}
        if nos_per_kg is not None and kg_per_nos is not None and abs(nos_per_kg * kg_per_nos - 1.0) > 0.005:
            return {"status": "review_required", "rate": None,
                "reason": "The imported Nos/kg conversion fields conflict; no conversion was selected."}
        if source_kind == "nos" and target_kind == "kg" and kg_per_nos:
            factor = 1.0 / kg_per_nos
        elif source_kind == "kg" and target_kind == "nos" and nos_per_kg:
            factor = 1.0 / nos_per_kg
        else:
            return {"status": "review_required", "rate": None,
                "reason": f"The imported purchase unit {source_unit} has no approved conversion to {target_unit}."}
    return {"status": "available", "sourceRate": amount, "rate": amount * factor, "currency": currency,
        "uom": target_unit, "sourceUOM": source_unit_label, "sourceCurrency": currency,
        "conversionFactor": factor, "conversionEvidence": {"NosPerKG": fields.get("NosPerKG"),
            "PerNosFromKg": fields.get("PerNosFromKg")},
        "purchaseRecordId": str(fields.get("SourceRecordId") or ""),
        "selectedRateLogId": fields.get("SelectedRateLogId"), "vendor": fields.get("Vendor"),
        "currencyEvidence": fields.get("CurrencyEvidence"), "sourceTable": fields.get("SourceTable"),
        "sourceDocumentId": fields.get("SourceDocumentId"), "sourceFingerprint": fields.get("Fingerprint"),
        "sourceBaseRate": fields.get("CurrentUnitRate"), "sourceFreight": fields.get("Freight"),
        "sourceHandling": fields.get("Handling"),
        "ratePolicy": "Costing-New PurchasePartsMaster TotalSparesPartCost; freight and handling included once"}


class SafariMasterImportService:
    """Preview, capture, apply and recover an explicit one-time local import."""

    def __init__(self, registry: Any, source_client: GristClient | None = None):
        self.registry = registry
        self.client = registry.client
        self.source_client = source_client or self._configured_source()

    def _configured_source(self) -> GristClient:
        source_id = os.getenv("COSTING_NEW_GRIST_DOC_ID", SOURCE_DOC_ID).strip()
        if source_id != SOURCE_DOC_ID:
            raise GristValidationError("The import source must be the reviewed Costing-New document ID.")
        if source_id == str(getattr(self.client, "doc_id", "")):
            raise GristValidationError("Source and Safari import target must be different documents.")
        return GristClient(api_key=self.client.api_key, doc_id=source_id, base_url=self.client.base_url)

    def _guard(self, *, writing: bool = False) -> None:
        if writing:
            self.registry._verify_writer()
        else:
            self.client.validate_safari_write_target()
        target = GristAdminClient(self.client.api_key, self.client.base_url).get_document(self.client.doc_id)
        if target.name != "Safari Manufacturing" or str(getattr(target, "workspace_id", "")) != str(getattr(self.client, "safari_workspace_id", "")):
            raise GristValidationError("Import target is not the explicitly configured Safari Manufacturing document and workspace.")
        source_id = str(self.source_client.doc_id)
        if source_id != SOURCE_DOC_ID or source_id == self.client.doc_id:
            raise GristValidationError("Import source document identity is not the reviewed read-only Costing-New source.")
        source = GristAdminClient(self.source_client.api_key, self.source_client.base_url).get_document(source_id)
        if source.name != "Costing-New":
            raise GristValidationError("Import source must be named exactly Costing-New.")
        self.registry._ensure_schema()

    def _read_capture(self) -> dict[str, Any]:
        self._guard()
        tables = {name: self.source_client.fetch_table_records_with_ids(name) for name in SOURCE_TABLES}
        first = _source_fingerprint(tables)
        materials, purchases, exceptions = _source_rows(tables, self.source_client.doc_id)
        # Re-read all source tables before accepting a capture. A drifted
        # observation is rejected, not partially combined.
        verify = {name: self.source_client.fetch_table_records_with_ids(name) for name in SOURCE_TABLES}
        second = _source_fingerprint(verify)
        if first != second:
            raise PartIdentityError("SAFARI_IMPORT_SOURCE_DRIFT", "Costing-New changed while the import observation was being captured; create a new preview.")
        return {"schema": "safari-master-import-capture-v1", "sourceDocumentId": self.source_client.doc_id,
            "sourceWorkspaceId": str(getattr(GristAdminClient(self.source_client.api_key, self.source_client.base_url).get_document(self.source_client.doc_id), "workspace_id", "")),
            "sourceTables": list(SOURCE_TABLES), "sourceCounts": {name: len(rows) for name, rows in tables.items()},
            "sourceFingerprint": first, "capturedAt": utc_now(), "materials": materials,
            "purchases": purchases, "exceptions": exceptions,
            "captureFingerprint": request_fingerprint({"sourceFingerprint": first, "materials": materials, "purchases": purchases})}

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return self.registry._rows(table)

    def _resolve_materials(self, capture: dict[str, Any]) -> None:
        materials = self._rows("Material")
        aliases = self._rows("IdentityAlias")
        from app.part_definition import GristPartDefinitionService
        binding_service = GristPartDefinitionService(self.registry, material_weight_catalog_provider=lambda: {"status": "unavailable", "records": []})
        reviewed_bindings = {}
        for _, payload in binding_service._identity_alias_bindings():
            if str(payload.get("sourceDocumentId") or "") != self.source_client.doc_id:
                continue
            source_id = _int_ref(payload.get("sourceMasterMaterialId"))
            material_id = _int_ref(payload.get("safariMaterialId"))
            if source_id and material_id:
                reviewed_bindings.setdefault(source_id, set()).add(material_id)
        by_name: dict[str, list[dict[str, Any]]] = {}
        by_reviewed_name: dict[str, list[dict[str, Any]]] = {}
        for row in materials:
            fields = _fields(row)
            name = str(fields.get("CanonicalName") or fields.get("Name") or "").strip()
            if name:
                by_name.setdefault(name.casefold(), []).append(row)
            alias = str(fields.get("ODSDisplayName") or "").strip()
            if alias and str(fields.get("MappingStatus") or "").casefold() == "reviewed_alias":
                by_reviewed_name.setdefault(alias.casefold(), []).append(row)
        by_alias: dict[str, list[dict[str, Any]]] = {}
        for row in aliases:
            fields = _fields(row)
            if str(fields.get("EntityType") or "").casefold() == "material":
                by_alias.setdefault(str(fields.get("Value") or "").strip().casefold(), []).append(fields)
        seen_names: dict[str, int] = {}
        for source in capture["materials"]:
            name = source["canonicalName"]
            seen_names[name.casefold()] = seen_names.get(name.casefold(), 0) + 1
        for source in capture["materials"]:
            source["materialId"] = None
            source["bindingStatus"] = "unmapped"
            explicit = reviewed_bindings.get(_int_ref(source.get("sourceRecordId")) or -1, set())
            if len(explicit) == 1 and any(int(row.get("id", -1)) == next(iter(explicit)) for row in materials):
                source["materialId"] = next(iter(explicit))
                source["bindingStatus"] = "bound"
                continue
            if len(explicit) > 1:
                source["bindingStatus"] = "ambiguous"
                continue
            candidates = by_name.get(source["canonicalName"].casefold(), [])
            if not candidates:
                candidates = by_reviewed_name.get(source["canonicalName"].casefold(), [])
            if not candidates:
                alias_rows = by_alias.get(source["canonicalName"].casefold(), [])
                ids = {int(row.get("EntityId")) for row in alias_rows if str(row.get("EntityId") or "").isdigit()}
                candidates = [row for row in materials if int(row.get("id", -1)) in ids]
            if len(candidates) > 1 or seen_names[source["canonicalName"].casefold()] > 1:
                source["bindingStatus"] = "ambiguous"
            elif len(candidates) == 1:
                source["materialId"] = int(candidates[0]["id"])
                source["bindingStatus"] = "bound"
            else:
                source["bindingStatus"] = "create"

    def _resolve_purchases(self, capture: dict[str, Any]) -> None:
        items = self._rows("PurchaseItem")
        specs = self._rows("PartPurchaseSpecification")
        aliases = self._rows("IdentityAlias")
        by_alias: dict[str, set[int]] = {}
        for row in aliases:
            fields = _fields(row)
            if str(fields.get("EntityType") or "").casefold() == "purchaseitem":
                try:
                    by_alias.setdefault(str(fields.get("Value") or "").strip().casefold(), set()).add(int(fields.get("EntityId")))
                except (TypeError, ValueError):
                    pass
        for source in capture["purchases"]:
            matches = set(by_alias.get(source["partCode"].casefold(), set())) if source["partCode"] else set()
            matches |= set(by_alias.get(source["partName"].casefold(), set()))
            for spec in specs:
                fields = _fields(spec)
                if str(fields.get("Status") or "active").casefold() not in {"active", "approved", "reviewed"}:
                    continue
                if source["partCode"] and source["partCode"].casefold() in {
                    str(fields.get("SpecificationCode") or "").strip().casefold(),
                    str(fields.get("ManufacturerPartNumber") or "").strip().casefold()}:
                    item_id = _int_ref(fields.get("PurchaseItem"))
                    if item_id:
                        matches.add(item_id)
            source["purchaseItemId"] = None
            source["resolutionStatus"] = "unmapped"
            if len(matches) == 1 and any(int(row.get("id", -1)) == next(iter(matches)) for row in items):
                source["purchaseItemId"] = next(iter(matches))
                source["resolutionStatus"] = "bound"
            elif len(matches) > 1:
                source["resolutionStatus"] = "ambiguous"
        mapped: dict[int, list[dict[str, Any]]] = {}
        for source in capture["purchases"]:
            item_id = source.get("purchaseItemId")
            if item_id:
                mapped.setdefault(int(item_id), []).append(source)
        for sources in mapped.values():
            if len(sources) > 1:
                for source in sources:
                    source["purchaseItemId"] = None
                    source["resolutionStatus"] = "ambiguous"

    @staticmethod
    def _preview_payload(capture: dict[str, Any], *, request_key: str) -> dict[str, Any]:
        materials = capture["materials"]
        purchases = capture["purchases"]
        return {"requestKey": request_key, "sourceDocumentId": capture["sourceDocumentId"],
            "sourceFingerprint": capture["sourceFingerprint"], "captureFingerprint": capture["captureFingerprint"],
            "sourceCounts": capture["sourceCounts"], "materialCount": len(materials), "steelRateCount": sum(row.get("materialLatestRate") is not None or row.get("defaultMaterialRate") is not None for row in materials),
            "purchaseRateCount": sum(row.get("totalCurrentRate") is not None for row in purchases),
            "materialCreates": sum(row.get("bindingStatus") == "create" for row in materials),
            "materialConflicts": [row for row in materials if row.get("bindingStatus") == "ambiguous"],
            "purchaseUnmapped": [row for row in purchases if row.get("resolutionStatus") != "bound"],
            "phaseOuts": [row["sourceRecordId"] for row in materials if not row.get("inUse")],
            "conflicts": capture["exceptions"], "fingerprint": request_fingerprint(capture)}

    @staticmethod
    def _preview_audit_payload(result: dict[str, Any]) -> dict[str, Any]:
        return {"requestKey": result.get("requestKey"), "fingerprint": result.get("fingerprint"),
            "sourceFingerprint": result.get("sourceFingerprint"), "materialCount": result.get("materialCount"),
            "purchaseRateCount": result.get("purchaseRateCount"),
            "materialConflictCount": len(result.get("materialConflicts", [])),
            "purchaseUnmappedCount": len(result.get("purchaseUnmapped", [])),
            "phaseOutCount": len(result.get("phaseOuts", [])), "conflictCount": len(result.get("conflicts", []))}

    def preview(self, *, request_key: str, actor: str, reason: str = "") -> dict[str, Any]:
        key = request_key.strip()
        if not key or not actor.strip():
            raise PartIdentityError("SAFARI_IMPORT_INPUT_INVALID", "A unique idempotency key and actor are required for a preview.")
        self._guard()
        existing = _record_by_key(self._rows("SafariMasterImportRequest"), "RequestKey", key)
        if existing:
            fields = _fields(existing)
            if fields.get("Action") != "preview":
                raise PartIdentityError("SAFARI_IMPORT_REQUEST_CONFLICT", "This key belongs to a different import action.")
            payload = _decode_json(fields.get("Payload"), {}) or {}
            capture = _load_capture(self.client, key, expected_fingerprint=str(payload.get("captureFingerprint") or ""))
            result = self._preview_payload(capture, request_key=key)
            self._audit(f"{key}:preview", str(fields.get("Actor") or actor), self._preview_audit_payload(result),
                str(fields.get("Reason") or reason), event_type="safari_master_rate_import_previewed",
                entity_type="SafariMasterImportRequest")
            return result
        saved_chunks = [row for row in self._rows("SafariMasterImportCapture")
            if str(_fields(row).get("RequestKey") or "") == key]
        if saved_chunks:
            capture = _load_capture(self.client, key)
        else:
            capture = self._read_capture()
            self._resolve_materials(capture)
            self._resolve_purchases(capture)
            _store_capture(self.client, key, capture)
        result = self._preview_payload(capture, request_key=key)
        fp = request_fingerprint({"action": "preview", "captureFingerprint": capture["captureFingerprint"]})
        result_summary = {name: result[name] for name in ("requestKey", "sourceDocumentId", "sourceFingerprint",
            "captureFingerprint", "sourceCounts", "materialCount", "steelRateCount", "purchaseRateCount",
            "materialCreates", "fingerprint")}
        result_summary.update({"materialConflictCount": len(result.get("materialConflicts", [])),
            "purchaseUnmappedCount": len(result.get("purchaseUnmapped", [])),
            "phaseOutCount": len(result.get("phaseOuts", [])), "conflictCount": len(result.get("conflicts", []))})
        from app.grist_types import canonical_grist_json_cell
        payload = canonical_grist_json_cell({"captureKey": _capture_key(key), "captureFingerprint": capture["captureFingerprint"],
            "previewFingerprint": result["fingerprint"]})
        _create(self.client, "SafariMasterImportRequest", [{"RequestKey": key, "Action": "preview", "RequestFingerprint": fp,
            "Status": "previewed", "Payload": payload, "Result": canonical_grist_json_cell(result_summary), "CreatedAt": grist_datetime(utc_now()),
            "UpdatedAt": grist_datetime(utc_now()), "Actor": actor.strip(), "Reason": reason.strip()}])
        self._audit(f"{key}:preview", actor, self._preview_audit_payload(result), reason,
            event_type="safari_master_rate_import_previewed", entity_type="SafariMasterImportRequest")
        return result

    def _find_request(self, request_key: str) -> dict[str, Any]:
        row = _record_by_key(self._rows("SafariMasterImportRequest"), "RequestKey", request_key)
        if not row:
            raise PartIdentityError("SAFARI_IMPORT_PREVIEW_REQUIRED", "Create and review a persisted import preview before Apply.")
        if _fields(row).get("Action") != "preview":
            raise PartIdentityError("SAFARI_IMPORT_REQUEST_CONFLICT", "The supplied key is not an import preview request.")
        return row

    def _bind_or_create_materials(self, capture: dict[str, Any]) -> None:
        rows = self._rows("Material")
        by_key = {str(_fields(row).get("MaterialKey") or ""): row for row in rows}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            name = str(_fields(row).get("CanonicalName") or "").strip().casefold()
            if name:
                by_name.setdefault(name, []).append(row)
        pending: list[dict[str, Any]] = []
        for source in capture["materials"]:
            if source.get("bindingStatus") == "bound":
                continue
            if source.get("bindingStatus") == "ambiguous":
                continue
            key = f"costing-new:MasterMaterial:{source['sourceRecordId']}"
            existing = by_key.get(key)
            if existing:
                source["materialId"] = int(existing["id"])
                source["bindingStatus"] = "bound"
                continue
            same_name = by_name.get(source["canonicalName"].casefold(), [])
            if len(same_name) == 1:
                source["materialId"] = int(same_name[0]["id"])
                source["bindingStatus"] = "bound"
                continue
            if len(same_name) > 1:
                source["bindingStatus"] = "ambiguous"
                continue
            pending.append({"MaterialKey": key, "CanonicalName": source["canonicalName"],
                "ODSDisplayName": source["canonicalName"], "MappingStatus": "source_imported",
                "SourceImportKey": key})
        _create(self.client, "Material", pending)
        rows = self._rows("Material") if pending else rows
        by_key = {str(_fields(row).get("MaterialKey") or ""): row for row in rows}
        for source in capture["materials"]:
            if source.get("bindingStatus") != "create":
                continue
            key = f"costing-new:MasterMaterial:{source['sourceRecordId']}"
            created = by_key.get(key)
            if not created:
                raise PartIdentityError("SAFARI_IMPORT_WRITE_UNCONFIRMED", "Grist did not confirm a new imported Material identity.")
            source["materialId"] = int(created["id"])
            source["bindingStatus"] = "bound"

    def _upsert_rows(self, table: str, key_field: str, rows: list[dict[str, Any]]) -> None:
        existing_rows = self._rows(table)
        index = {str(_fields(row).get(key_field) or ""): row for row in existing_rows}
        pending = []
        for fields in rows:
            key = str(fields[key_field])
            old = index.get(key)
            if old:
                if str(_fields(old).get("Fingerprint") or "") != str(fields.get("Fingerprint") or ""):
                    raise PartIdentityError("SAFARI_IMPORT_ROW_CONFLICT", f"Staged import row {key!r} conflicts with its immutable fingerprint.")
            else:
                pending.append(fields)
        _create(self.client, table, pending)

    def apply(self, *, request_key: str, expected_fingerprint: str, actor: str, reason: str) -> dict[str, Any]:
        key = request_key.strip()
        if not key or not expected_fingerprint or not actor.strip() or not reason.strip():
            raise PartIdentityError("SAFARI_IMPORT_INPUT_INVALID", "The preview key, exact preview fingerprint, actor and apply reason are required.")
        self._guard(writing=True)
        with self.registry.lock:
            request = self._find_request(key)
            fields = _fields(request)
            payload = _decode_json(fields.get("Payload"), {}) or {}
            capture = _load_capture(self.client, key, expected_fingerprint=str(payload.get("captureFingerprint") or ""))
            preview = self._preview_payload(capture, request_key=key)
            if preview.get("fingerprint") != expected_fingerprint or payload.get("previewFingerprint") != expected_fingerprint:
                raise PartIdentityError("SAFARI_IMPORT_PREVIEW_STALE", "The exact persisted preview fingerprint is required; create a new preview after any source change.")
            if fields.get("Status") == "published":
                return deepcopy(_decode_json(fields.get("Result"), {}) or {})
            if fields.get("Status") == "previewed":
                observed = self._read_capture()
                if observed["sourceFingerprint"] != capture.get("sourceFingerprint"):
                    raise PartIdentityError("SAFARI_IMPORT_SOURCE_DRIFT", "The source changed after preview. Start a new import key and preview.")
                _patch(self.client, "SafariMasterImportRequest", int(request["id"]), {"Status": "applying", "UpdatedAt": grist_datetime(utc_now())})
            # Once status is applying, all retries use the immutable capture
            # persisted with the original request and do not reread upstream.
            request = _record_by_key(self._rows("SafariMasterImportRequest"), "RequestKey", key) or request
            active_rows = self._rows("SafariMasterImportControl")
            control = _record_by_key(active_rows, "ControlKey", "active")
            previous_version = int(_fields(control).get("Version") or 0) if control else 0
            import_key = f"safari-master-import:{key}"
            import_fp = request_fingerprint({"request": key, "capture": capture.get("captureFingerprint")})
            header = _record_by_key(self._rows("SafariMasterImport"), "ImportKey", import_key)
            if not header:
                _create(self.client, "SafariMasterImport", [{"ImportKey": import_key, "Version": previous_version + 1,
                    "SourceDocumentId": capture["sourceDocumentId"], "SourceWorkspaceId": capture.get("sourceWorkspaceId", ""),
                    "CapturedAt": grist_datetime(datetime.fromisoformat(capture["capturedAt"].replace("Z", "+00:00"))),
                    "Fingerprint": capture["sourceFingerprint"], "Status": "staging", "MasterCount": len(capture["materials"]),
                    "SteelRateCount": sum(row.get("materialLatestRate") is not None or row.get("defaultMaterialRate") is not None for row in capture["materials"]),
                    "PurchaseRateCount": sum(row.get("totalCurrentRate") is not None for row in capture["purchases"]),
                    "ExceptionCount": len(preview.get("conflicts", [])) + len(preview.get("purchaseUnmapped", [])),
                    "Actor": actor.strip(), "RequestKey": key, "RequestFingerprint": import_fp}])
                header = _record_by_key(self._rows("SafariMasterImport"), "ImportKey", import_key)
            if not header:
                raise PartIdentityError("SAFARI_IMPORT_WRITE_UNCONFIRMED", "Grist did not confirm the immutable import version header.")
            import_id = int(header["id"])
            header_version = int(_fields(header).get("Version") or previous_version + 1)
            self._bind_or_create_materials(capture)
            material_rows = []
            for row in capture["materials"]:
                row_key = f"{import_key}:MasterMaterial:{row['sourceRecordId']}"
                fields_out = {"ImportRowKey": row_key, "Import": import_id, "SourceDocumentId": row["sourceDocumentId"],
                    "SourceTable": row["sourceTable"], "SourceRecordId": row["sourceRecordId"], "CanonicalName": row["canonicalName"],
                    "Code": row["code"], "InUse": bool(row["inUse"]), "KGsPerMeter": row.get("kgPerMeter"),
                    "LatestKGsPerMeterDate": row.get("latestKgPerMeterDate"), "MaterialLatestRate": row.get("materialLatestRate"),
                    "DefaultMaterialRate": row.get("defaultMaterialRate"), "TransportLatestRate": row.get("transportLatestRate"),
                    "TotalRate": row.get("totalRate"), "LatestRateDate": row.get("latestRateDate"), "RateBasis": row.get("rateBasis"),
                    "SelectedRateLogId": row.get("selectedRateLogId"), "RateLogEvidence": _json(row.get("rateLogEvidence") or []),
                    "Material": row.get("materialId"), "Fingerprint": row["masterFingerprint"]}
                material_rows.append(fields_out)
            self._upsert_rows("SafariMasterImportMaterial", "ImportRowKey", material_rows)
            purchase_rows = []
            for row in capture["purchases"]:
                row_key = f"{import_key}:PurchasePartsMaster:{row['sourceRecordId']}"
                purchase_rows.append({"ImportRowKey": row_key, "Import": import_id, "SourceDocumentId": row["sourceDocumentId"],
                    "SourceTable": row["sourceTable"], "SourceRecordId": row["sourceRecordId"], "PartName": row["partName"],
                    "PartCode": row["partCode"], "MasterPartName": row["masterPartName"], "Unit": row["unit"],
                    "NosPerKG": row.get("nosPerKg"), "PerNosFromKg": row.get("perNosFromKg"), "DefaultRate": row.get("defaultRate"),
                    "CurrentUnitRate": row.get("currentUnitRate"), "Freight": row.get("freight"), "Handling": row.get("handling"),
                    "TotalCurrentRate": row.get("totalCurrentRate"), "FreightIncluded": row.get("freightIncluded"),
                    "LastPurchaseDate": row.get("lastPurchaseDate"), "SelectedRateLogId": row.get("selectedRateLogId"),
                    "RateLogEvidence": _json(row.get("rateLogEvidence") or []), "Vendor": row.get("vendor"), "Currency": row.get("currency"),
                    "CurrencyEvidence": row.get("currencyEvidence"),
                    "PurchaseItem": row.get("purchaseItemId"), "ResolutionStatus": row.get("resolutionStatus"),
                    "Fingerprint": row["masterFingerprint"]})
            self._upsert_rows("SafariMasterImportPurchase", "ImportRowKey", purchase_rows)
            # Verify complete staged generations before advancing the pointer.
            staged_materials = [row for row in self._rows("SafariMasterImportMaterial") if _int_ref(_fields(row).get("Import")) == import_id]
            staged_purchases = [row for row in self._rows("SafariMasterImportPurchase") if _int_ref(_fields(row).get("Import")) == import_id]
            if len(staged_materials) != len(material_rows) or len(staged_purchases) != len(purchase_rows):
                raise PartIdentityError("SAFARI_IMPORT_STAGING_INCOMPLETE", "The local import is incomplete; the active pointer was not changed. Retry with the same preview key.")
            _patch(self.client, "SafariMasterImport", int(header["id"]), {"Status": "published", "PublishedAt": grist_datetime(utc_now())})
            manifest_fp = request_fingerprint({"import": import_key, "capture": capture["sourceFingerprint"],
                "materials": len(staged_materials), "purchases": len(staged_purchases)})
            pointer_already_published = control and _int_ref(_fields(control).get("ActiveImport")) == import_id
            published_version = int(_fields(control).get("Version") or header_version) if pointer_already_published else max(header_version, previous_version + 1)
            control_fields = {"ActiveImport": import_id, "Version": published_version, "Fingerprint": manifest_fp,
                "UpdatedAt": grist_datetime(utc_now()), "RequestKey": key}
            if control:
                _patch(self.client, "SafariMasterImportControl", int(control["id"]), control_fields)
            else:
                _create(self.client, "SafariMasterImportControl", [{"ControlKey": "active", **control_fields}])
            result = {"status": "published", "importId": import_id, "importKey": import_key,
                "version": published_version, "fingerprint": manifest_fp,
                "sourceFingerprint": capture["sourceFingerprint"], "materialCount": len(staged_materials),
                "purchaseCount": len(staged_purchases), "unmappedPurchaseCount": sum(row.get("resolutionStatus") != "bound" for row in capture["purchases"]),
                "createdAt": utc_now()}
            self._audit(key, actor, result, reason)
            from app.grist_types import canonical_grist_json_cell
            _patch(self.client, "SafariMasterImportRequest", int(request["id"]), {"Status": "published", "Result": canonical_grist_json_cell(result),
                "UpdatedAt": grist_datetime(utc_now()), "Reason": reason.strip()})
            return result

    def _audit(self, key: str, actor: str, result: dict[str, Any], reason: str, *,
               event_type: str = "safari_master_rate_import_published",
               entity_type: str = "SafariMasterImport") -> None:
        rows = self._rows("AuditEvent")
        if any(str(_fields(row).get("RequestKey") or "") == key for row in rows):
            return
        _create(self.client, "AuditEvent", [{"EventType": event_type, "Actor": actor,
            "OccurredAt": grist_datetime(utc_now()), "EntityType": entity_type, "EntityId": str(result.get("importId") or result.get("requestKey") or ""),
            "Reason": reason, "Payload": _json(result), "RequestKey": key, "RequestFingerprint": request_fingerprint(result)}])

    def recover(self, request_key: str) -> dict[str, Any]:
        row = self._find_request(request_key.strip())
        fields = _fields(row)
        if fields.get("Status") == "published":
            return deepcopy(_decode_json(fields.get("Result"), {}) or {})
        payload = _decode_json(fields.get("Payload"), {}) or {}
        return self.apply(request_key=request_key, expected_fingerprint=str(payload.get("previewFingerprint") or ""),
            actor=str(fields.get("Actor") or "recovery"), reason=str(fields.get("Reason") or "same-key recovery"))


def _int_ref(value: Any) -> int | None:
    if isinstance(value, list) and len(value) > 2 and value[0] == "R":
        value = value[2]
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def active_local_import(client: GristClient) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """One batched local read of the active manifest and both current rate tables."""
    controls = client.fetch_table_records_with_ids("SafariMasterImportControl")
    active = _record_by_key(controls, "ControlKey", "active")
    if not active:
        return None, [], []
    import_id = _int_ref(_fields(active).get("ActiveImport"))
    if not import_id:
        raise PartIdentityError("SAFARI_IMPORT_POINTER_INVALID", "The local current-rate pointer is incomplete.")
    material_rows = client.fetch_table_records_with_ids("SafariMasterImportMaterial")
    purchase_rows = client.fetch_table_records_with_ids("SafariMasterImportPurchase")
    material_rows = [row for row in material_rows if _int_ref(_fields(row).get("Import")) == import_id]
    purchase_rows = [row for row in purchase_rows if _int_ref(_fields(row).get("Import")) == import_id]
    header = next((row for row in client.fetch_table_records_with_ids("SafariMasterImport") if int(row.get("id", -1)) == import_id), None)
    if not header or _fields(header).get("Status") != "published":
        raise PartIdentityError("SAFARI_IMPORT_POINTER_INVALID", "The current import version is not completely published.")
    expected = int(_fields(header).get("MasterCount") or 0), int(_fields(header).get("PurchaseRateCount") or 0)
    if expected[0] != len(material_rows) or expected[1] > len(purchase_rows):
        raise PartIdentityError("SAFARI_IMPORT_POINTER_INVALID", "The current local import version is incomplete.")
    manifest = {"importId": import_id, "version": _fields(active).get("Version"),
        "fingerprint": _fields(active).get("Fingerprint"), "sourceFingerprint": _fields(header).get("Fingerprint"),
        "sourceDocumentId": _fields(header).get("SourceDocumentId")}
    return manifest, material_rows, purchase_rows


def load_local_material_weight_catalog(client: GristClient | None = None) -> dict[str, Any]:
    """Ordinary Part option/factor readers resolve only published local rows."""
    client = client or GristClient.from_safari_environment()
    manifest, materials, _ = active_local_import(client)
    if manifest is None:
        return {"status": "unavailable", "source": "Safari-local-import", "records": [], "message": "Run the explicit one-time Safari master import before selecting imported materials."}
    local = {int(row.get("id", -1)): _fields(row) for row in client.fetch_table_records_with_ids("Material")}
    records = []
    for row in materials:
        fields = _fields(row)
        material_id = _int_ref(fields.get("Material"))
        bound = local.get(material_id or -1, {})
        kgm = _num(fields.get("KGsPerMeter"))
        records.append({"canonicalName": str(fields.get("CanonicalName") or ""), "masterMaterialId": _int_ref(fields.get("SourceRecordId")),
            "materialId": material_id, "materialCanonicalName": str(bound.get("CanonicalName") or ""), "code": str(fields.get("Code") or ""),
            "inUse": bool(fields.get("InUse")), "kgPerMeter": str(kgm) if kgm is not None and kgm > 0 else None,
            "factorStatus": "available" if kgm is not None and kgm > 0 else "unavailable", "sourceDate": fields.get("LatestKGsPerMeterDate"),
            "source": "Costing-New", "sourceDocumentId": manifest["sourceDocumentId"], "sourceTable": "MasterMaterial",
            "sourceField": "KGsPerMeter", "factorUnit": "kg/m", "localImportVersion": manifest["version"],
            "localImportFingerprint": manifest["fingerprint"], "localMaterialId": material_id})
    return {"status": "available", "source": "Safari-local-import", "sourceDocumentId": manifest["sourceDocumentId"],
        "readOnly": True, "localManifest": manifest, "records": records}


def load_local_interim_rates(names: list[str], client: GristClient | None = None) -> dict[str, Any]:
    client = client or GristClient.from_safari_environment()
    manifest, rows, _ = active_local_import(client)
    if manifest is None:
        return {"status": "unavailable", "source": "Safari-local-import", "rows": [], "sourceDocumentId": ""}
    wanted = {str(name).strip().casefold() for name in names}
    local = {int(row.get("id", -1)): _fields(row) for row in client.fetch_table_records_with_ids("Material")}
    output = []
    for row in rows:
        fields = _fields(row)
        material = local.get(_int_ref(fields.get("Material")) or -1, {})
        aliases = {str(fields.get("CanonicalName") or "").casefold(), str(material.get("CanonicalName") or "").casefold()}
        if not wanted.intersection(aliases):
            continue
        rate = _num(fields.get("MaterialLatestRate")) if fields.get("RateBasis") == "MaterialLatestRate" else _num(fields.get("DefaultMaterialRate"))
        rate_log_rows = _decode_json(fields.get("RateLogEvidence"), []) or []
        selected = next((item.get("fields", {}) for item in rate_log_rows
            if str(item.get("id")) == str(fields.get("SelectedRateLogId"))), {})
        if selected:
            from app.cost_dates import cost_date_evidence
            date_evidence = cost_date_evidence(selected, source_kind="rate_log")
        else:
            from app.cost_dates import cost_date_evidence
            date_evidence = cost_date_evidence({"LatestRateDate": fields.get("LatestRateDate")}, source_kind="rate_master")
        output.append({"material": str(material.get("CanonicalName") or fields.get("CanonicalName") or ""),
            "canonicalMaterial": str(material.get("CanonicalName") or fields.get("CanonicalName") or ""),
            "status": "available" if rate is not None else "rate_unavailable", "costingNewRate": str(rate) if rate is not None else None,
            "basis": fields.get("RateBasis"), "masterRecordId": _int_ref(fields.get("SourceRecordId")),
            "selectedRateLogId": fields.get("SelectedRateLogId"), "rateLogEvidence": rate_log_rows,
            **date_evidence, "rateSourceDate": date_evidence.get("rateSourceDate") or fields.get("LatestRateDate"),
            "effectiveAt": date_evidence.get("effectiveAt") or fields.get("LatestRateDate"), "uom": "kg",
            "sourceDocumentId": manifest["sourceDocumentId"], "localImportVersion": manifest["version"],
            "localImportFingerprint": manifest["fingerprint"]})
    return {"status": "available", "source": "Safari-local-import", "sourceDocumentId": manifest["sourceDocumentId"],
        "localManifest": manifest, "rows": output}
