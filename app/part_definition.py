"""Direct, Grist-backed maintenance and controlled publication of Part definitions.

The source workbook is retained as immutable provenance. This service reads and
writes the accepted Safari definition only; opening the workbook is reserved for
the explicit comparison workflow.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timezone
import hashlib
import hmac
import json
import math
import os
import re
from typing import Any

from app.exceptions import GristError
from app.domain import utc_now
from app.grist_parts import GristPartFeatures, GristPartRegistry, _ref, _serialized
from app.grist_types import canonical_grist_json_cell, decode_grist_any_object, grist_datetime
from app.material_basis import MCL_KG_PER_PIECE, ONE_ITEM_PER_OCCURRENCE, WEIGHT_PER_PIECE, WEIGHT_PER_PIECE_BASES
from app.part_identity import PartIdentityError, request_fingerprint


FAMILY_PROCESS = {"mcl": "material_cut", "toolshop": "tool_shop", "cnc": "cnc"}
ACTIVE_STATUSES = {"active", "accepted", "published", "finalized"}
REMOVED_STATUSES = {"void", "removed", "inactive", "retired"}
LENGTH_TO_MM = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "cm": 10.0, "centimeter": 10.0,
    "centimeters": 10.0, "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "metre": 1000.0,
    "metres": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4}
MASS_TO_KG = {"kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "g": 0.001, "gram": 0.001, "grams": 0.001}
LINE_DETAIL_FIELDS = {
    "ProcessType", "Material", "PurchaseItem", "ProcessOperation", "WorkCenter", "SourceDepartment",
    "IssueRoute", "ActivityKind", "Quantity", "QuantityUOM", "DimensionMM", "DimensionInches",
    "WeightGrams", "WeightKg", "PartWeightKg", "Length", "Width", "Thickness", "IssueSlipNo",
    "IssueSlipDesc", "InternalMakingCost", "ExternalMachiningCost", "RateCached", "CostCached",
    "ItemName", "MaterialDisplayName", "OptionGroup", "DimensionText", "DimensionUOM", "PlatePart",
    "PartCategory", "ItemCode", "ExactQuantityText", "ExactWeightKgText", "WeightUOM",
    "EngineeringAttributes", "ToolShopRemarks",
}


def _fields(row: dict[str, Any] | None) -> dict[str, Any]:
    return row.get("fields", row) if row else {}


def _int_ref(value: Any) -> int | None:
    return _ref(value)


def _json_cell(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return deepcopy(value)
    return decode_grist_any_object(value)


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def _num(value: Any, *, allow_blank: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    if value in (None, "") and allow_blank:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _next_revision_label(value: Any) -> str:
    label = str(value or "A").strip().upper()
    if not label or not label.isalpha():
        raise PartIdentityError("PART_REVISION_UNCLASSIFIED", "This legacy revision label is not a managed engineering revision. Review its classification before creating a successor.")
    ordinal = 0
    for char in label:
        ordinal = ordinal * 26 + ord(char) - ord("A") + 1
    ordinal += 1
    output = ""
    while ordinal:
        ordinal, remainder = divmod(ordinal - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


class GristPartDefinitionService:
    """Read editable definitions, save drafts and publish approved successors."""

    def __init__(self, registry: GristPartRegistry, *, material_weight_catalog_provider=None):
        self.registry = registry
        self.client = registry.client
        self.features = GristPartFeatures(registry)
        self.material_weight_catalog_provider = material_weight_catalog_provider
        self._material_weight_catalog_cache: dict[str, Any] | None = None

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return self.registry._rows(table)

    @staticmethod
    def _missing_table(exc: Exception, table: str) -> bool:
        message = str(exc).casefold()
        return isinstance(exc, GristError) and ("404" in message or "not found" in message) and table.casefold() in message

    def _optional_rows(self, table: str) -> list[dict[str, Any]]:
        try:
            return self._rows(table)
        except Exception as exc:
            if self._missing_table(exc, table):
                return []
            raise

    def _material_weight_catalog(self) -> dict[str, Any]:
        if self._material_weight_catalog_cache is not None:
            return self._material_weight_catalog_cache
        if self.material_weight_catalog_provider is None:
            self._material_weight_catalog_cache = {"status": "unavailable", "source": "Costing-New", "records": []}
            return self._material_weight_catalog_cache
        try:
            result = self.material_weight_catalog_provider()
            if not isinstance(result, dict) or result.get("status") != "available" or not isinstance(result.get("records"), list):
                raise ValueError("The material weight source did not return a usable catalogue.")
            self._material_weight_catalog_cache = result
        except Exception as exc:
            self._material_weight_catalog_cache = {"status": "unavailable", "source": "Costing-New", "records": [],
                "message": str(exc)[:300]}
        return self._material_weight_catalog_cache

    @staticmethod
    def _material_catalog_document_id(catalog: dict[str, Any]) -> str:
        document_id = str(catalog.get("sourceDocumentId") or "").strip()
        if document_id:
            return document_id
        candidates = {str(record.get("sourceDocumentId") or "").strip() for record in catalog.get("records", [])
            if isinstance(record, dict) and str(record.get("sourceDocumentId") or "").strip()}
        return next(iter(candidates)) if len(candidates) == 1 else ""

    def _material_weight_evidence(self, saved_name: Any) -> dict[str, Any] | None:
        """Resolve a display-only preview by a saved Safari identity's canonical name.

        This compatibility helper deliberately does not use a client supplied
        MaterialDisplayName. Writes call ``_material_weight_evidence_for_identity``
        and bind the result to the Safari Material record ID.
        """
        name = str(saved_name or "").strip()
        if not name:
            return None
        from app.config import load_material_mapping
        from app.material_resolution import resolve_material
        try:
            resolved = resolve_material(name, load_material_mapping())
        except Exception:
            resolved = None
        canonical = (resolved.canonical_name if resolved else None) or name
        matches = [row for row in self._material_weight_catalog().get("records", [])
            if str(row.get("canonicalName") or "").strip().casefold() == canonical.casefold()]
        if len(matches) != 1:
            return {"status": "ambiguous" if len(matches) > 1 else "unavailable", "canonicalName": canonical,
                "source": "Costing-New", "sourceTable": "MasterMaterial", "sourceField": "KGsPerMeter", "factorUnit": "kg/m"}
        return deepcopy(matches[0])

    def _material_signing_key(self) -> bytes:
        # The configured Safari Grist API key is already a stable server-only
        # secret across app workers. Deployments may provide a dedicated stable
        # key to make Grist-token rotation independent of saved evidence.
        dedicated = os.getenv("SAFARI_MATERIAL_EVIDENCE_HMAC_KEY", "").strip()
        key = dedicated or str(getattr(self.client, "api_key", "") or "")
        return key.encode("utf-8")

    def _sign_material_payload(self, payload: dict[str, Any]) -> str:
        key = self._material_signing_key()
        if not key:
            return ""
        return hmac.new(key, _stable(payload).encode("utf-8"), hashlib.sha256).hexdigest()

    def _verify_material_payload(self, payload: dict[str, Any], signature: Any) -> bool:
        expected = self._sign_material_payload(payload)
        return bool(expected and isinstance(signature, str) and hmac.compare_digest(expected, signature))

    @staticmethod
    def _material_name(fields: dict[str, Any]) -> str:
        return str(fields.get("CanonicalName") or fields.get("Name") or fields.get("CanonicalMaterialName") or "").strip()

    def _material_row(self, material_id: int | None) -> dict[str, Any] | None:
        if material_id is None:
            return None
        return next((row for row in self._rows("Material") if int(row.get("id", -1)) == material_id), None)

    def _identity_alias_bindings(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        result = []
        for row in self._optional_rows("IdentityAlias"):
            fields = _fields(row)
            if str(fields.get("EntityType") or "") != "MaterialMasterBinding":
                continue
            try:
                payload = json.loads(str(fields.get("Source") or ""))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            signature = payload.pop("signature", None)
            valid = (payload.get("schema") == "safari-material-master-binding-v1"
                and self._verify_material_payload(payload, signature)
                and str(fields.get("EntityId") or "") == str(payload.get("entityId") or "")
                and str(fields.get("Value") or "") == str(payload.get("masterMaterialName") or "")
                and str(fields.get("NormalizedValue") or "") == str(payload.get("masterMaterialName") or "").casefold()
                and _int_ref(fields.get("SourceRow")) == _int_ref(payload.get("sourceMasterMaterialId"))
                and _int_ref(payload.get("safariMaterialId")) is not None
                and bool(str(payload.get("reason") or "").strip())
                and bool(str(payload.get("reviewedBy") or "").strip()))
            if valid:
                result.append((row, payload))
        return result

    def _material_master_for_identity(self, material_id: int | None, *, saved_evidence: Any = None) -> tuple[dict[str, Any] | None, str, str]:
        """Resolve one source master from canonical Safari identity or a reviewed binding."""
        material = self._material_row(material_id)
        material_name = self._material_name(_fields(material)) if material else ""
        if not material_id or not material or not material_name:
            return None, "unavailable", material_name

        catalog = self._material_weight_catalog()
        records = catalog.get("records", []) if catalog.get("status") == "available" else []
        source_document_id = self._material_catalog_document_id(catalog)
        explicit = [payload for _, payload in self._identity_alias_bindings()
            if _int_ref(payload.get("safariMaterialId")) == material_id
            and str(payload.get("sourceDocumentId") or "") == source_document_id]
        explicit_ids = {int(payload["sourceMasterMaterialId"]) for payload in explicit
            if _int_ref(payload.get("sourceMasterMaterialId")) is not None}
        if len(explicit_ids) > 1:
            return None, "ambiguous", material_name
        if explicit_ids:
            matches = [record for record in records if _int_ref(record.get("masterMaterialId")) in explicit_ids
                and str(record.get("sourceDocumentId") or source_document_id) == source_document_id
                and str(record.get("canonicalName") or "") == str(next(p["masterMaterialName"] for p in explicit if int(p["sourceMasterMaterialId"]) in explicit_ids))]
        else:
            from app.config import load_material_mapping
            from app.material_resolution import resolve_material
            try:
                resolved = resolve_material(material_name, load_material_mapping())
                canonical = resolved.canonical_name or material_name
            except Exception:
                canonical = material_name
            matches = [record for record in records
                if str(record.get("canonicalName") or "").strip().casefold() == canonical.strip().casefold()
                and str(record.get("sourceDocumentId") or source_document_id) == source_document_id]
        if len(matches) != 1:
            return None, "ambiguous" if len(matches) > 1 else ("unavailable" if catalog.get("status") != "available" else "unbound"), material_name
        record = matches[0]
        factor = _num(record.get("kgPerMeter"))
        if (factor is None or factor <= 0 or str(record.get("source") or "") != "Costing-New"
                or str(record.get("sourceTable") or "") != "MasterMaterial"
                or str(record.get("sourceField") or "") != "KGsPerMeter"
                or str(record.get("factorUnit") or "") != "kg/m"):
            return None, "unavailable", material_name
        resolved_record = deepcopy(record)
        resolved_record["sourceDocumentId"] = str(resolved_record.get("sourceDocumentId") or source_document_id)
        return resolved_record, "available", material_name

    def _signed_material_evidence(self, material_id: int, material_name: str, record: dict[str, Any]) -> dict[str, Any]:
        payload = {"schema": "safari-material-weight-evidence-v1", "materialRef": int(material_id),
            "materialCanonicalName": material_name, "canonicalName": str(record.get("canonicalName") or ""),
            "masterMaterialId": _int_ref(record.get("masterMaterialId")), "kgPerMeter": str(record.get("kgPerMeter")),
            "sourceDate": record.get("sourceDate"), "source": "Costing-New", "sourceDocumentId": str(record.get("sourceDocumentId") or ""),
            "sourceTable": "MasterMaterial", "sourceField": "KGsPerMeter", "factorUnit": "kg/m", "factorStatus": "available"}
        return {**payload, "signature": self._sign_material_payload(payload), "status": "available"}

    def _verified_saved_material_evidence(self, evidence: Any, material_id: int | None) -> dict[str, Any] | None:
        if not isinstance(evidence, dict) or evidence.get("schema") != "safari-material-weight-evidence-v1":
            return None
        payload = {key: value for key, value in evidence.items() if key not in {"signature", "status"}}
        material = self._material_row(material_id)
        canonical = self._material_name(_fields(material)) if material else ""
        if (_int_ref(payload.get("materialRef")) != material_id or not canonical
                or str(payload.get("materialCanonicalName") or "") != canonical
                or payload.get("factorStatus") != "available" or payload.get("source") != "Costing-New"
                or payload.get("sourceTable") != "MasterMaterial" or payload.get("sourceField") != "KGsPerMeter"
                or payload.get("factorUnit") != "kg/m" or not str(payload.get("sourceDocumentId") or "")
                or _num(payload.get("kgPerMeter")) is None or _num(payload.get("kgPerMeter")) <= 0
                or _int_ref(payload.get("masterMaterialId")) is None
                or not self._verify_material_payload(payload, evidence.get("signature"))):
            return None
        return {**payload, "signature": evidence["signature"], "status": "available"}

    def _material_weight_evidence_for_identity(self, material_id: int | None, *, saved_evidence: Any = None) -> dict[str, Any]:
        trusted_saved = self._verified_saved_material_evidence(saved_evidence, material_id)
        if trusted_saved:
            return trusted_saved
        record, status, material_name = self._material_master_for_identity(material_id, saved_evidence=saved_evidence)
        if record and material_id is not None:
            return self._signed_material_evidence(material_id, material_name, record)
        return {"status": status, "canonicalName": material_name, "source": "Costing-New", "sourceTable": "MasterMaterial",
            "sourceField": "KGsPerMeter", "factorUnit": "kg/m"}

    @staticmethod
    def _legacy_duplicate_material_basis(item: dict[str, Any]) -> bool:
        """Spot the old imported MCL shape where piece count appears twice.

        Tool Shop rows remain process-rate quantities; equal piece counts there
        are not reclassified as material consumption.
        """
        if str(item.get("family") or "").casefold() != "mcl":
            return False
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        attributes = _json_cell(detail.get("EngineeringAttributes")) or {}
        if isinstance(attributes, dict) and attributes.get("materialConsumptionBasis"):
            return False
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        if str(provenance.get("type") or "").casefold() in {"safari_authored", "safari_edited"}:
            return False
        detail_unit = str(detail.get("QuantityUOM") or "").strip().casefold()
        row_unit = str(item.get("uom") or "").strip().casefold()
        piece_units = {"nos", "no", "each", "ea", "pcs", "piece", "pieces"}
        detail_quantity = _num(detail.get("Quantity"), allow_blank=True)
        occurrence_quantity = _num(item.get("quantity"), allow_blank=True)
        return (detail_unit in piece_units and row_unit in piece_units and detail_quantity is not None
            and occurrence_quantity is not None and math.isclose(detail_quantity, occurrence_quantity, rel_tol=0, abs_tol=1e-12))

    def _material_source_master(self, source_master_id: int) -> dict[str, Any]:
        catalog = self._material_weight_catalog()
        if catalog.get("status") != "available":
            raise PartIdentityError("PART_MATERIAL_CATALOG_UNAVAILABLE", "Costing-New is unavailable; a new material binding cannot be reviewed right now.")
        records = [record for record in catalog.get("records", [])
            if _int_ref(record.get("masterMaterialId")) == source_master_id]
        if len(records) != 1 or not str(records[0].get("canonicalName") or "").strip():
            raise PartIdentityError("PART_MATERIAL_BINDING_IDENTITY_INVALID", "Choose one existing Costing-New master with a canonical name.")
        return deepcopy(records[0])

    def _material_binding_choice(self, source_master_id: int, material_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        record = self._material_source_master(source_master_id)
        material = self._material_row(material_id)
        if not material or not self._material_name(_fields(material)):
            raise PartIdentityError("PART_MATERIAL_BINDING_IDENTITY_INVALID", "Choose one existing Costing-New master and one existing canonical Safari Material.")
        return record, material

    @_serialized
    def bind_material_master(self, part_id: str, *, source_master_id: Any, material_id: Any = None,
            actor: str, reason: str, request_key: str, capture_canonical_identity: bool = False) -> dict[str, Any]:
        """Record an approver-reviewed source binding, optionally capturing its canonical Safari identity."""
        self.registry._verify_writer()
        self._check_established(self._current_revision(part_id))
        if str(actor or "").strip().casefold() != os.getenv("SAFARI_ENGINEERING_APPROVER_USERNAME", "Irshad").strip().casefold():
            raise PartIdentityError("PART_MATERIAL_BINDING_REVIEWER_REQUIRED", "A material master binding must be reviewed by the configured engineering approver.")
        master_ref = _int_ref(source_master_id)
        safari_ref = _int_ref(material_id)
        if (not master_ref or (capture_canonical_identity and safari_ref is not None)
                or (not capture_canonical_identity and not safari_ref)
                or not str(reason or "").strip() or not str(request_key or "").strip()):
            raise PartIdentityError("PART_MATERIAL_BINDING_INPUT_REQUIRED", "Choose one source master and either its reviewed canonical capture or an existing Safari Material, then provide a review reason.")
        request_key_value = request_key.strip()
        prior = self.registry._find(self._rows("PartRegistryRequest"), "RequestKey", request_key_value)
        record = None
        material = None
        catalog: dict[str, Any] = {}
        recovered_capture = False
        if capture_canonical_identity and prior:
            prior_fields = _fields(prior)
            if (prior_fields.get("RequestType") != "part_definition_material_binding"
                    or str(prior_fields.get("EntityUUID") or "") != str(part_id)):
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This material binding request key was already used for another operation.")

            # A published request must be replayable during a Costing-New outage.
            # Its signed alias binds the source identity, captured Safari row, reviewer,
            # reason, request fingerprint and original request key together.
            matching_aliases = [payload for _, payload in self._identity_alias_bindings()
                if _int_ref(payload.get("sourceMasterMaterialId")) == master_ref
                and str(payload.get("requestKey") or "") == request_key_value]
            if len(matching_aliases) > 1:
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The saved request has more than one verified source identity; resolve it through reviewed data repair.")
            if matching_aliases:
                payload = matching_aliases[0]
                source_document_id = str(payload.get("sourceDocumentId") or "")
                canonical_name = str(payload.get("masterMaterialName") or "").strip()
                material_id_saved = _int_ref(payload.get("safariMaterialId"))
                material = self._material_row(material_id_saved)
                material_fields = _fields(material) if material else {}
                material_name = self._material_name(material_fields)
                expected_fingerprint = request_fingerprint([part_id, source_document_id, master_ref,
                    "capture-canonical", canonical_name.casefold(), actor.strip(), reason.strip()])
                valid_alias = (source_document_id and canonical_name and material_id_saved and material
                    and payload.get("entityId") == f"material-weight:{source_document_id}:MasterMaterial:{master_ref}"
                    and str(payload.get("sourceTable") or "") == "MasterMaterial"
                    and str(payload.get("masterMaterialName") or "") == canonical_name
                    and str(payload.get("reviewedBy") or "") == actor.strip()
                    and str(payload.get("reason") or "") == reason.strip()
                    and str(payload.get("requestFingerprint") or "") == expected_fingerprint
                    and str(payload.get("requestKey") or "") == request_key_value
                    and _int_ref(payload.get("sourceMasterMaterialId")) == master_ref
                    and _int_ref(payload.get("safariMaterialId")) == material_id_saved
                    and str(payload.get("safariMaterialName") or "") == material_name
                    and material_name.casefold() == canonical_name.casefold()
                    and str(prior_fields.get("RequestFingerprint") or "") == expected_fingerprint)
                if not valid_alias:
                    raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The saved request does not match its signed canonical identity evidence.")
                if str(prior_fields.get("Status") or "").casefold() == "published":
                    try:
                        saved_result = json.loads(str(prior_fields.get("Result") or "{}"))
                    except (TypeError, ValueError):
                        saved_result = None
                    expected_identity_captured = str(material_fields.get("MappingStatus") or "") == "reviewed_source_master_capture"
                    if (not isinstance(saved_result, dict) or saved_result.get("status") != "bound"
                            or _int_ref(saved_result.get("masterMaterialId")) != master_ref
                            or _int_ref(saved_result.get("materialId")) != material_id_saved
                            or str(saved_result.get("masterName") or "") != canonical_name
                            or str(saved_result.get("materialName") or "") != material_name
                            or bool(saved_result.get("identityCaptured")) != expected_identity_captured):
                        raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The published request result conflicts with its signed canonical identity evidence.")
                    return saved_result
                record = {"canonicalName": canonical_name, "masterMaterialId": master_ref,
                    "sourceDocumentId": source_document_id}
                catalog = {"status": "unavailable", "sourceDocumentId": source_document_id, "records": []}
                recovered_capture = True
            elif str(prior_fields.get("Status") or "").casefold() == "published":
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The published request has no verified signed canonical identity evidence.")
            else:
                # If the first response was lost after Grist created the keyed
                # canonical Material but before the alias, recover only a unique
                # deterministic row whose full request fingerprint matches.
                suffix = f":MasterMaterial:{master_ref}"
                candidates = []
                for row in self._rows("Material"):
                    fields = _fields(row)
                    key = str(fields.get("MaterialKey") or "")
                    canonical_name = self._material_name(fields)
                    if (not key.startswith("material-master:") or not key.endswith(suffix)
                            or str(fields.get("ODSDisplayName") or "") != canonical_name
                            or str(fields.get("MappingStatus") or "") != "reviewed_source_master_capture"
                            or not canonical_name):
                        continue
                    source_document_id = key[len("material-master:"):-len(suffix)]
                    expected_fingerprint = request_fingerprint([part_id, source_document_id, master_ref,
                        "capture-canonical", canonical_name.casefold(), actor.strip(), reason.strip()])
                    if expected_fingerprint == str(prior_fields.get("RequestFingerprint") or ""):
                        candidates.append((row, source_document_id, canonical_name))
                if len(candidates) > 1:
                    raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "More than one captured Material matches the interrupted request; resolve it through reviewed data repair.")
                if candidates:
                    material, source_document_id, canonical_name = candidates[0]
                    record = {"canonicalName": canonical_name, "masterMaterialId": master_ref,
                        "sourceDocumentId": source_document_id}
                    catalog = {"status": "unavailable", "sourceDocumentId": source_document_id, "records": []}
                    recovered_capture = True
        if not recovered_capture:
            record = self._material_source_master(master_ref) if capture_canonical_identity else None
            catalog = self._material_weight_catalog()
        if not capture_canonical_identity:
            record, material = self._material_binding_choice(master_ref, safari_ref)
        source_document_id = str(record.get("sourceDocumentId") or self._material_catalog_document_id(catalog))
        if not source_document_id:
            raise PartIdentityError("PART_MATERIAL_BINDING_IDENTITY_INVALID", "The source catalogue document identity is unavailable; no binding was written.")
        canonical_name = str(record.get("canonicalName") or "").strip()
        material_key = f"material-master:{source_document_id}:MasterMaterial:{master_ref}"
        identity_captured = False
        if capture_canonical_identity:
            matching_materials = [row for row in self._rows("Material")
                if self._material_name(_fields(row)).casefold() == canonical_name.casefold()]
            if len(matching_materials) > 1:
                raise PartIdentityError("PART_MATERIAL_BINDING_IDENTITY_INVALID", "More than one Safari Material has this canonical name; resolve the duplicate identity before capture.")
            material = matching_materials[0] if matching_materials else None
            if material and str(_fields(material).get("MaterialKey") or "") == material_key:
                material_fields = _fields(material)
                if (self._material_name(material_fields).casefold() != canonical_name.casefold()
                        or str(material_fields.get("ODSDisplayName") or "") != canonical_name
                        or str(material_fields.get("MappingStatus") or "") != "reviewed_source_master_capture"):
                    raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The source-derived Material key already belongs to another canonical identity.")
            if material is None:
                material = self.registry._find(self._rows("Material"), "MaterialKey", material_key)
                if material:
                    material_fields = _fields(material)
                    if (str(material_fields.get("MaterialKey") or "") != material_key
                            or self._material_name(material_fields).casefold() != canonical_name.casefold()
                            or str(material_fields.get("ODSDisplayName") or "") != canonical_name
                            or str(material_fields.get("MappingStatus") or "") != "reviewed_source_master_capture"):
                        raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The source-derived Material key already belongs to another canonical identity.")
            safari_ref = _int_ref(material.get("id")) if material else None
            identity_captured = bool(material and _fields(material).get("MappingStatus") == "reviewed_source_master_capture")
        if capture_canonical_identity and not canonical_name:
            raise PartIdentityError("PART_MATERIAL_BINDING_IDENTITY_INVALID", "The selected source master has no usable canonical name.")
        entity_id = f"material-weight:{source_document_id}:MasterMaterial:{master_ref}"
        fingerprint_payload = ([part_id, source_document_id, master_ref, "capture-canonical", canonical_name.casefold(), actor.strip(), reason.strip()]
            if capture_canonical_identity else [part_id, source_document_id, master_ref, safari_ref, actor.strip(), reason.strip()])
        fingerprint = request_fingerprint(fingerprint_payload)
        if prior:
            prior_fields = _fields(prior)
            if prior_fields.get("RequestType") != "part_definition_material_binding" or prior_fields.get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This material binding request key was already used with different data.")
            if str(prior_fields.get("Status") or "").casefold() == "published":
                if capture_canonical_identity:
                    raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "The published request could not be verified through its signed canonical identity evidence.")
                try:
                    return json.loads(str(prior_fields.get("Result") or "{}"))
                except ValueError:
                    pass
        existing = self.registry._find(self._rows("IdentityAlias"), "EntityId", entity_id)
        existing_binding = None
        if existing:
            bound = self._identity_alias_bindings()
            existing_binding = next((payload for row, payload in bound if int(row.get("id", -1)) == int(existing.get("id", -2))), None)
            if (not existing_binding
                    or _int_ref(existing_binding.get("sourceMasterMaterialId")) != master_ref
                    or str(existing_binding.get("sourceDocumentId") or "") != source_document_id
                    or (safari_ref is not None and _int_ref(existing_binding.get("safariMaterialId")) != safari_ref)
                    or (capture_canonical_identity and _int_ref(existing_binding.get("safariMaterialId")) != safari_ref)):
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "This Costing-New master already has a different or unverified Safari binding; resolve it through reviewed data repair.")
            if capture_canonical_identity and _int_ref(existing_binding.get("safariMaterialId")) != safari_ref:
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "This source master is already bound to a different Safari Material; no identity was re-keyed.")
        bound_elsewhere = [payload for _, payload in self._identity_alias_bindings()
            if str(payload.get("sourceDocumentId") or "") == source_document_id
            and safari_ref is not None and _int_ref(payload.get("safariMaterialId")) == safari_ref
            and _int_ref(payload.get("sourceMasterMaterialId")) != master_ref]
        if bound_elsewhere:
            raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "This Safari Material already has a different Costing-New master binding; review the canonical identity before changing it.")

        if not existing_binding and not self._material_signing_key():
            raise PartIdentityError("PART_MATERIAL_BINDING_SIGNING_UNAVAILABLE", "The server has no stable signing key for reviewed material bindings.")

        # Do all source, identity, duplicate-binding and signing checks before
        # creating the request journal. A rejected review must not leave a
        # false pending operation behind.
        request = prior or self.registry._begin_request(request_key.strip(), "part_definition_material_binding", fingerprint, part_id)
        if capture_canonical_identity and material is None:
            material = self.registry._ensure_keyed("Material", "MaterialKey", material_key, {
                "MaterialKey": material_key, "CanonicalName": canonical_name, "ODSDisplayName": canonical_name,
                "MappingStatus": "reviewed_source_master_capture"})
            safari_ref = _int_ref(material.get("id"))
            if not safari_ref or self._material_name(_fields(material)).casefold() != canonical_name.casefold():
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "Grist did not confirm the expected captured canonical Material identity.")
            identity_captured = True
        if existing_binding:
            result = {"status": "bound", "idempotent": True, "materialId": safari_ref, "masterMaterialId": master_ref,
                "masterName": canonical_name, "materialName": self._material_name(_fields(material)),
                "identityCaptured": bool(capture_canonical_identity and _fields(material).get("MappingStatus") == "reviewed_source_master_capture")}
        else:
            source_payload = {"schema": "safari-material-master-binding-v1", "entityId": entity_id,
                "sourceDocumentId": source_document_id, "sourceTable": "MasterMaterial", "sourceMasterMaterialId": master_ref,
                "masterMaterialName": canonical_name, "safariMaterialId": safari_ref,
                "safariMaterialName": self._material_name(_fields(material)), "reviewedBy": actor.strip(), "reason": reason.strip(),
                "createdAt": grist_datetime(utc_now()), "requestKey": request_key.strip(), "requestFingerprint": fingerprint,
                "identityAction": "captured" if identity_captured else "reused_or_existing"}
            fields = {"EntityType": "MaterialMasterBinding", "EntityId": entity_id, "Value": source_payload["masterMaterialName"],
                "NormalizedValue": source_payload["masterMaterialName"].casefold(), "SourceRow": master_ref,
                "Source": json.dumps({**source_payload, "signature": self._sign_material_payload(source_payload)}, sort_keys=True)}
            binding_signature = json.loads(fields["Source"]).get("signature")
            if not binding_signature:
                raise PartIdentityError("PART_MATERIAL_BINDING_SIGNING_UNAVAILABLE", "The server has no stable signing key for reviewed material bindings.")
            self.registry._ensure_keyed("IdentityAlias", "EntityId", entity_id, fields)
            saved_binding_row = self.registry._find(self._rows("IdentityAlias"), "EntityId", entity_id)
            saved_binding = next((payload for row, payload in self._identity_alias_bindings()
                if saved_binding_row and int(row.get("id", -1)) == int(saved_binding_row.get("id", -2))), None)
            if (not saved_binding
                    or _int_ref(saved_binding.get("sourceMasterMaterialId")) != master_ref
                    or str(saved_binding.get("sourceDocumentId") or "") != source_document_id
                    or _int_ref(saved_binding.get("safariMaterialId")) != safari_ref):
                raise PartIdentityError("PART_MATERIAL_BINDING_CONFLICT", "Grist returned a conflicting material binding; no success was recorded.")
            result = {"status": "bound", "idempotent": False, "materialId": safari_ref, "masterMaterialId": master_ref,
                "masterName": canonical_name, "materialName": self._material_name(_fields(material)),
                "identityCaptured": bool(capture_canonical_identity and _fields(material).get("MappingStatus") == "reviewed_source_master_capture")}
        audit_key = f"material-binding:{request_key.strip()}"
        self.registry._ensure_keyed("AuditEvent", "RequestKey", audit_key, {"EventType": "part_material_master_canonical_capture_reviewed" if capture_canonical_identity else "part_material_master_binding_reviewed",
            "Actor": actor.strip(), "OccurredAt": grist_datetime(utc_now()), "EntityType": "MaterialMasterBinding",
            "EntityId": entity_id, "Reason": reason.strip(), "Payload": json.dumps(result, sort_keys=True),
            "RequestKey": audit_key, "RequestFingerprint": fingerprint})
        self.registry._complete_request(request, result)
        return result

    def _require_publication_schema(self) -> None:
        missing = []
        for table in ("PartDefinitionDraft", "PartEngineeringChangeRequest", "PartDefinitionVersion", "PartDefinitionPublication"):
            try:
                self._rows(table)
            except Exception as exc:
                if self._missing_table(exc, table):
                    missing.append(table)
                else:
                    raise
        if missing:
            raise PartIdentityError("PART_DEFINITION_SCHEMA_REQUIRED", "Apply the additive Part-definition maintenance schema before editing or publishing: " + ", ".join(missing))

    def _publication_schema_available(self) -> bool:
        for table in ("PartDefinitionDraft", "PartEngineeringChangeRequest", "PartDefinitionVersion", "PartDefinitionPublication"):
            try:
                self._rows(table)
            except Exception as exc:
                if self._missing_table(exc, table):
                    return False
                raise
        return True

    def _find(self, table: str, field: str, value: str) -> dict[str, Any] | None:
        return self.registry._find(self._rows(table), field, value)

    def _part_row(self, part_id: str) -> dict[str, Any]:
        row = self._find("ProductPart", "StablePartId", str(part_id))
        if not row:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        return row

    def _current_revision(self, part_id: str) -> dict[str, Any]:
        part = self.registry.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected Part no longer exists.")
        row = next((item for item in self._rows("PartRevision") if int(item.get("id", -1)) == int(part["revisionRecordId"])), None)
        if not row:
            raise PartIdentityError("PART_REVISION_UNAVAILABLE", "The Part has no readable saved definition revision.")
        return row

    def _baseline(self, revision: dict[str, Any]) -> dict[str, Any] | None:
        baseline_id = _int_ref(_fields(revision).get("ManufacturingBaseline"))
        if not baseline_id:
            return None
        return next((row for row in self._rows("PartBaselineProcessing") if int(row.get("id", -1)) == baseline_id), None)

    def _check_established(self, revision: dict[str, Any]) -> None:
        baseline = self._baseline(revision)
        if not baseline or str(_fields(baseline).get("Status") or "").casefold() != "established":
            raise PartIdentityError("PART_BASELINE_REQUIRED", "Direct maintenance is available after the initial manufacturing baseline has been established. Use the existing establishment review for an unestablished Part.")

    def _stored_version(self, revision_id: int) -> dict[str, Any] | None:
        # Older additive schemas have no P1 tables yet. The accepted Rev A
        # remains readable through the existing normalized Part tables.
        rows = self._optional_rows("PartDefinitionVersion")
        matches = [row for row in rows if _int_ref(_fields(row).get("PartRevision")) == revision_id]
        if len(matches) > 1:
            raise PartIdentityError("PART_DEFINITION_DUPLICATE", "More than one accepted definition snapshot points at this engineering revision.")
        return matches[0] if matches else None

    def _revision_history(self, part_record_id: int, current_id: int) -> list[dict[str, Any]]:
        revisions = [row for row in self._rows("PartRevision") if _int_ref(_fields(row).get("ProductPart")) == part_record_id]
        revisions.sort(key=lambda row: (int(_fields(row).get("Revision") or 0), int(row.get("id", 0))))
        return [{"id": int(row["id"]), "revisionLabel": _fields(row).get("RevisionLabel") or _fields(row).get("Revision"),
            "status": _fields(row).get("BaselineStatus") or _fields(row).get("Status") or "unclassified",
            "definitionHash": _fields(row).get("DefinitionHash"), "reason": _fields(row).get("FinalizationReason") or _fields(row).get("Reason"),
            "actor": _fields(row).get("FinalizedBy") or _fields(row).get("Actor"), "occurredAt": _fields(row).get("FinalizedAt") or _fields(row).get("OccurredAt"),
            "previousRevisionId": _int_ref(_fields(row).get("PreviousRevision")), "publicationCause": _fields(row).get("PublicationCause"),
            "current": int(row["id"]) == current_id} for row in revisions]

    def _definition_changes(self, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
        """Project immutable before/after snapshots as readable row and field changes."""
        family_label = {"mcl": "MCL", "toolshop": "Tool Shop", "cnc": "CNC", "other": "Other process"}
        material_by_id = {int(row["id"]): str(_fields(row).get("CanonicalName") or _fields(row).get("Name") or _fields(row).get("MaterialDisplayName") or "")
            for row in self._optional_rows("Material")}

        def identity(collection: str, item: dict[str, Any]) -> str:
            if collection == "requirements":
                if str(item.get("identityStatus") or "") in {"stable", "safari_authored"}:
                    return str(item.get("requirementKey") or "")
                return f"legacy-line:{item.get('lineMasterId')}:{item.get('lineRevisionId')}:{item.get('family')}"
            return str((item.get("componentIdentity") if collection == "components" else item.get("specificationIdentity")) or "")

        def item_name(collection: str, item: dict[str, Any]) -> str:
            detail = item.get("detail") or {}
            if collection == "requirements":
                return str(detail.get("ItemName") or detail.get("PlatePart") or detail.get("MaterialDisplayName") or item.get("sourcePartName") or "Requirement")
            if collection == "components":
                return str(item.get("childPartNumber") or item.get("childName") or item.get("childPartId") or "Unresolved child Part")
            return str(item.get("code") or item.get("description") or item.get("manufacturerPartNumber") or "Purchase specification")

        def values(collection: str, item: dict[str, Any]) -> dict[str, Any]:
            detail = item.get("detail") or {}
            if collection == "requirements":
                family = str(item.get("family") or "other").casefold()
                status = item.get("fieldStatus") if isinstance(item.get("fieldStatus"), dict) else {}

                def recorded(name: str, value: Any) -> Any:
                    state = str(status.get(name) or "").casefold()
                    if state == "missing":
                        return None
                    if state == "blank":
                        return "Blank in source"
                    if value in (None, ""):
                        return None
                    # Legacy Grist rows can have default zeroes without evidence
                    # that the source or editor recorded them. Preserve explicit
                    # zero only where the saved field-status proves it.
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0 and state != "value":
                        return None
                    return value

                def number(name: str, unit: str) -> str | None:
                    value = recorded(name, detail.get(name))
                    if value is None:
                        return None
                    return value if isinstance(value, str) else f"{value} {unit}"

                def flatten_attributes(prefix: str, value: Any) -> None:
                    if isinstance(value, dict):
                        for key in sorted(value):
                            if str(key) in {"fieldStatus", "materialWeightEvidence"}:
                                continue
                            flatten_attributes(f"{prefix}.{key}" if prefix else str(key), value[key])
                    elif prefix:
                        result[f"Engineering attribute · {prefix}"] = value

                result: dict[str, Any] = {}
                quantity = item.get("quantity")
                if quantity not in (None, ""):
                    result["Quantity per Part"] = f"{quantity} {item.get('uom') or ''}".strip()
                item_name_value = detail.get("ItemName") or item.get("sourcePartName")
                if item_name_value:
                    result["Item name"] = item_name_value
                material = material_by_id.get(_int_ref(detail.get("Material")) or -1) or detail.get("MaterialDisplayName")
                if material:
                    result["Material"] = material

                if family in {"mcl", "toolshop"}:
                    cut_length = str(detail.get("DimensionText") or "").strip() or None
                    if cut_length:
                        numeric_text = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", cut_length)
                        if numeric_text and _num(numeric_text.group(1)) == 0:
                            cut_length = None
                    if not cut_length:
                        mm = recorded("DimensionMM", detail.get("DimensionMM"))
                        inches = recorded("DimensionInches", detail.get("DimensionInches"))
                        if mm is not None:
                            cut_length = f"{mm} mm"
                        elif inches is not None:
                            cut_length = f"{inches} in"
                    if cut_length:
                        text_match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|millimeters?|cm|centimeters?|m|meters?|metres?|in|inches?)\s*", cut_length, re.IGNORECASE)
                        if text_match:
                            unit = text_match.group(2).casefold()
                            factor_mm = next((LENGTH_TO_MM[key] for key in (unit, unit.rstrip("s")) if key in LENGTH_TO_MM), None)
                            numeric = _num(text_match.group(1))
                            result["Dimension to Cut"] = f"{numeric * factor_mm:g} mm" if numeric is not None and factor_mm else cut_length
                        else:
                            result["Dimension to Cut"] = cut_length
                    weight_evidence = item.get("materialWeightEvidence")
                    factor = _num(weight_evidence.get("kgPerMeter")) if isinstance(weight_evidence, dict) else None
                    length_mm = _num(detail.get("DimensionMM"))
                    if length_mm is None and _num(detail.get("DimensionInches")) is not None:
                        length_mm = _num(detail.get("DimensionInches")) * LENGTH_TO_MM["in"]
                    if length_mm is None and cut_length:
                        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|millimeters?|cm|centimeters?|m|meters?|metres?|in|inches?)\s*", cut_length, re.IGNORECASE)
                        if match:
                            unit = match.group(2).casefold()
                            factor_mm = next((LENGTH_TO_MM[key] for key in (unit, unit.rstrip("s")) if key in LENGTH_TO_MM), None)
                            amount = _num(match.group(1))
                            length_mm = amount * factor_mm if amount is not None and factor_mm else None
                    if factor is not None and factor > 0 and length_mm is not None and length_mm > 0:
                        per_piece_kg = length_mm / 1000.0 * factor
                        result["Calculated weight per piece"] = f"{format(per_piece_kg, '.12g')} kg"
                        parent_quantity = _num(item.get("quantity"))
                        if parent_quantity is not None and parent_quantity > 0:
                            result["Calculated weight per parent Part"] = f"{format(per_piece_kg * parent_quantity, '.12g')} kg"
                    elif isinstance(weight_evidence, dict):
                        result["Material conversion evidence"] = str(weight_evidence.get("status") or "unavailable").replace("_", " ")
                    if item.get("materialConsumptionStatus") == "legacy_duplicate_ambiguous":
                        result["Legacy consumption basis"] = "Review required: saved piece count appears both as rate consumption and Qty per Part"
                    elif item.get("materialConsumptionResolution"):
                        result["Legacy consumption basis"] = str(item["materialConsumptionResolution"]).replace("_", " ")
                if family == "cnc":
                    part_weight = number("PartWeightKg", "kg")
                    saved_kg = recorded("WeightKg", detail.get("WeightKg"))
                    saved_g = recorded("WeightGrams", detail.get("WeightGrams"))
                    if part_weight is not None:
                        result["Per Part Weight"] = part_weight
                    saved_total = [f"{saved_kg} kg" if saved_kg is not None and not isinstance(saved_kg, str) else saved_kg,
                        f"{saved_g} g" if saved_g is not None and not isinstance(saved_g, str) else saved_g]
                    saved_total = [value for value in saved_total if value is not None]
                    if saved_total:
                        result["Saved total weight"] = " / ".join(saved_total)
                    for name, label in (("Length", "Plate length"), ("Width", "Plate width"), ("Thickness", "Plate thickness")):
                        dimension = number(name, str(detail.get("DimensionUOM") or ""))
                        if dimension is not None:
                            result[label] = dimension

                rate_quantity = recorded("Quantity", detail.get("Quantity"))
                if rate_quantity is not None and detail.get("QuantityUOM"):
                    result["Rate-basis consumption"] = rate_quantity if isinstance(rate_quantity, str) else f"{rate_quantity} {detail.get('QuantityUOM')}"
                remarks = recorded("ToolShopRemarks", detail.get("ToolShopRemarks"))
                if remarks is not None:
                    result["Remarks"] = remarks
                plate_part = recorded("PlatePart", detail.get("PlatePart"))
                if plate_part is not None:
                    result["Plate part to cut"] = plate_part
                field_labels = {
                    "IssueRoute": "Issue route", "ActivityKind": "Activity kind", "SourceDepartment": "Source department",
                    "ItemCode": "Item code", "PartCategory": "Part category", "OptionGroup": "Option group",
                    "ProcessType": "Process type", "InternalMakingCost": "Internal making cost",
                    "ExternalMachiningCost": "External machining cost", "WeightUOM": "Weight unit",
                    "WeightKg": "Saved total weight", "WeightGrams": "Saved total weight",
                }
                for source_name, label in field_labels.items():
                    if label in result:
                        continue
                    value = recorded(source_name, detail.get(source_name))
                    if value is not None:
                        unit = "kg" if source_name == "WeightKg" else "g" if source_name == "WeightGrams" else ""
                        result[label] = f"{value} {unit}".strip() if unit and not isinstance(value, str) else value
                attributes = _json_cell(detail.get("EngineeringAttributes")) or {}
                flatten_attributes("", attributes)
                return result
            if collection == "components":
                return {"Canonical child Part": item_name(collection, item), "Quantity": f"{item.get('quantity') if item.get('quantity') is not None else '—'} {item.get('uom') or ''}".strip(),
                    "Sourcing route": item.get("sourcingRoute") or "auto", "Pinned child revision": item.get("childRevisionLabel") or item.get("childRevisionId") or "current accepted"}
            return {"Specification code": item.get("code") or "—", "Manufacturer": item.get("manufacturer") or "—",
                "Manufacturer part number": item.get("manufacturerPartNumber") or "—", "Description": item.get("description") or "—",
                "Costing unit": item.get("costingUOM") or "—", "Currency": item.get("costingCurrency") or "—"}

        numeric_display_fields = {
            "Quantity per Part", "Dimension to Cut", "Calculated weight per piece",
            "Calculated weight per parent Part", "Per Part Weight", "Plate length",
            "Plate width", "Plate thickness", "Saved total weight", "Rate-basis consumption", "Quantity",
            "Internal making cost", "External machining cost",
        }
        length_to_mm = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
            "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0,
            "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "metre": 1000.0, "metres": 1000.0,
            "in": 25.4, "inch": 25.4, "inches": 25.4}
        mass_to_kg = {"kg": 1.0, "kilogram": 1.0, "kilograms": 1.0,
            "g": 0.001, "gram": 0.001, "grams": 0.001}
        count_units = {"no": "each", "nos": "each", "ea": "each", "each": "each",
            "pc": "each", "pcs": "each", "piece": "each", "pieces": "each"}

        def normalized_number(candidate: Any, unit: str = "") -> Any:
            numeric = _num(candidate)
            if numeric is None:
                return None
            unit_key = re.sub(r"\s+", " ", unit.strip().casefold())
            if unit_key in length_to_mm:
                numeric *= length_to_mm[unit_key]
                unit_key = "mm"
            elif unit_key in mass_to_kg:
                numeric *= mass_to_kg[unit_key]
                unit_key = "kg"
            else:
                unit_key = count_units.get(unit_key, unit_key)
            # Decimal formatting is comparison-only; displayed before/after
            # values retain their original human-readable representation.
            return (format(numeric, ".12g"), unit_key)

        def semantic_value(field: str, value: Any) -> Any:
            # JSON attributes are typed data: object key order is irrelevant,
            # numbers compare numerically, and numeric-looking strings remain
            # text. This preserves values such as a specification "001".
            if isinstance(value, dict):
                return {str(key): semantic_value(field, item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
            if isinstance(value, list):
                return [semantic_value(field, item) for item in value]
            if isinstance(value, bool) or value is None:
                return value
            if isinstance(value, (int, float)):
                return normalized_number(value)
            if not isinstance(value, str) or field not in numeric_display_fields:
                return value

            parts = value.split("/") if field == "Saved total weight" else [value]
            normalized_parts = []
            for part in parts:
                match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*?)?\s*", part)
                if not match:
                    return value
                normalized = normalized_number(match.group(1), str(match.group(2) or ""))
                if normalized is None:
                    return value
                normalized_parts.append(normalized)
            return tuple(sorted(normalized_parts)) if field == "Saved total weight" else normalized_parts[0]

        changes: list[dict[str, Any]] = []
        for collection in ("requirements", "components", "purchases"):
            old_rows = [item for item in before.get(collection, []) if isinstance(item, dict)]
            new_rows = [item for item in after.get(collection, []) if isinstance(item, dict)]
            old_counts: dict[str, int] = {}
            new_counts: dict[str, int] = {}
            for item in old_rows: old_counts[identity(collection, item)] = old_counts.get(identity(collection, item), 0) + 1
            for item in new_rows: new_counts[identity(collection, item)] = new_counts.get(identity(collection, item), 0) + 1
            ambiguous = {key for key, count in (old_counts | new_counts).items() if count > 1 or not key or key.startswith("legacy-line:None:None")}
            old_by_id = {identity(collection, item): item for item in old_rows if identity(collection, item) not in ambiguous}
            new_by_id = {identity(collection, item): item for item in new_rows if identity(collection, item) not in ambiguous}
            for key in sorted(ambiguous):
                sample = next((item for item in new_rows + old_rows if identity(collection, item) == key), {})
                changes.append({"kind": "unavailable", "family": family_label.get(str(sample.get("family") or ""), "Store" if collection == "components" else "Purchase"),
                    "item": item_name(collection, sample), "occurrence": key or "unclassified", "field": "Legacy row identity",
                    "before": "Unavailable", "after": "Legacy evidence preserved; no inferred match was made"})
            for key in sorted(old_by_id.keys() | new_by_id.keys()):
                old, new = old_by_id.get(key), new_by_id.get(key)
                sample = new or old or {}
                family = family_label.get(str(sample.get("family") or ""), "Store" if collection == "components" else "Purchase")
                values_before = values(collection, old) if old else {}
                values_after = values(collection, new) if new else {}
                for field in sorted(values_before.keys() | values_after.keys()):
                    left, right = values_before.get(field), values_after.get(field)
                    if old and new and semantic_value(field, left) == semantic_value(field, right):
                        continue
                    changes.append({"kind": "added" if old is None else "removed" if new is None else "modified", "family": family,
                        "item": item_name(collection, sample), "occurrence": key, "field": field,
                        "before": left if old else "Not present", "after": right if new else "Not present"})
        return changes

    def publication_recovery(self, part_id: str, *, actor: str = "") -> dict[str, Any]:
        """Expose only durable retry identity while normal reads are generation-blocked."""
        part = self._part_row(part_id)
        part_record_id = int(part["id"])
        change_requests = self._optional_rows("PartEngineeringChangeRequest")
        journals = self._optional_rows("PartDefinitionPublication")
        can_recover = str(actor or "").strip().casefold() == os.getenv("SAFARI_ENGINEERING_APPROVER_USERNAME", "Irshad").strip().casefold()
        items = []
        for journal in journals:
            fields = _fields(journal)
            status = str(fields.get("Status") or "").casefold()
            if status == "published":
                continue
            plan = _json_cell(fields.get("Plan")) or {}
            affected = [int(value) for value in plan.get("affectedPartRecordIds", []) if str(value).isdigit()]
            if part_record_id not in affected:
                continue
            change_request_key = str(plan.get("changeRequestKey") or "")
            cr = next((row for row in change_requests if str(_fields(row).get("ChangeRequestKey") or "") == change_request_key), None)
            if not cr:
                change_request_id = _int_ref(fields.get("ChangeRequest"))
                cr = next((row for row in change_requests if int(row.get("id", -1)) == change_request_id), None) if change_request_id else None
            cr_fields = _fields(cr)
            change_request_key = change_request_key or str(cr_fields.get("ChangeRequestKey") or "")
            request_key = self._verified_publication_owner(journal, cr)
            allowed = can_recover and str(plan.get("approver") or "").strip().casefold() == str(actor or "").strip().casefold()
            items.append({"changeRequestKey": change_request_key, "requestKey": request_key, "rootPartId": str(plan.get("partId") or ""),
                "status": status or "unknown", "changeRequestStatus": str(cr_fields.get("Status") or "unknown").casefold(),
                "reason": str(cr_fields.get("Reason") or plan.get("reason") or ""),
                "completedStages": (_json_cell(fields.get("CompletedStages")) or {}).get("stages", []),
                "dependentPartCount": len(plan.get("affectedPartRecordIds") or []) - 1,
                "configurationCount": len(plan.get("affectedCodeRecordIds") or []),
                "canRetry": bool(allowed and change_request_key and request_key)})
            if not allowed:
                items[-1]["requestKey"] = ""
        return {"status": "recovering" if items else "clear", "items": items}

    def _verified_publication_owner(self, journal: dict[str, Any], cr: dict[str, Any] | None) -> str:
        """Return the original operation only when all durable ownership facts agree."""
        fields, cf = _fields(journal), _fields(cr)
        plan = _json_cell(fields.get("Plan")) or {}
        key, approver = str(plan.get("operationKey") or ""), str(plan.get("approver") or "").strip()
        cr_key = str(cf.get("ChangeRequestKey") or "")
        request = self._find("PartRegistryRequest", "RequestKey", key) if key else None
        rf = _fields(request)
        fp = request_fingerprint([cr_key, approver, key])
        root = (plan.get("parts") or [{}])[0]
        root_part = next((row for row in self._rows("ProductPart") if int(row.get("id", -1)) == _int_ref(cf.get("ProductPart"))), None)
        if (not cr or not key or not approver or not cr_key or
                _int_ref(fields.get("ChangeRequest")) != int(cr["id"]) or
                request_fingerprint(plan) != fields.get("PlanFingerprint") or
                plan.get("changeRequestKey") != cr_key or
                not root_part or plan.get("partId") != _fields(root_part).get("StablePartId") or root.get("partId") != plan.get("partId") or
                _int_ref(cf.get("ProductPart")) != _int_ref(root.get("partRecordId")) or
                _int_ref(cf.get("BaseRevision")) != _int_ref(plan.get("baseRevisionId")) or
                _int_ref(root.get("currentRevisionId")) != _int_ref(cf.get("BaseRevision")) or
                request_fingerprint(root.get("content")) != request_fingerprint(_json_cell(cf.get("AfterDefinition"))) or
                plan.get("impactFingerprint") != cf.get("ImpactFingerprint") or
                str(cf.get("ApprovedBy") or "").strip() != approver or
                fields.get("RequestKey") != key or fields.get("RequestFingerprint") != fp or
                rf.get("RequestKey") != key or rf.get("RequestType") != "part_definition_approval_publication" or
                rf.get("EntityUUID") != cr_key or rf.get("RequestFingerprint") != fp):
            raise PartIdentityError("PART_PUBLICATION_PLAN_CONFLICT", "The saved publication ownership or reviewed plan cannot be verified.")
        return key

    def _verified_approval_owner(self, cr: dict[str, Any]) -> str:
        fields = _fields(cr)
        cr_key = str(fields.get("ChangeRequestKey") or "")
        key = str(fields.get("RequestKey") or "")
        approver = str(fields.get("ApprovedBy") or "").strip()
        request = self._find("PartRegistryRequest", "RequestKey", key) if key else None
        fingerprint = request_fingerprint([cr_key, approver, key])
        if (not cr_key or not key or not approver or not request or fields.get("RequestFingerprint") != fingerprint or
                _fields(request).get("RequestFingerprint") != fingerprint or
                _fields(request).get("RequestType") != "part_definition_approval_publication" or
                _fields(request).get("EntityUUID") != cr_key):
            raise PartIdentityError("PART_PUBLICATION_PLAN_CONFLICT", "The saved approval ownership cannot be verified.")
        return key

    def _definition_from_rows(self, part_id: str, revision_id: int) -> dict[str, Any]:
        from app.baseline_bindings import apply_effective_baseline_override, effective_baseline_overrides

        part_row = self._part_row(part_id)
        part_fields = _fields(part_row)
        revision = next((row for row in self._rows("PartRevision") if int(row.get("id", -1)) == revision_id), None)
        if not revision:
            raise PartIdentityError("PART_REVISION_UNAVAILABLE", "The selected saved Part revision could not be read.")
        revision_fields = _fields(revision)
        baseline = self._baseline(revision)
        baseline_fields = _fields(baseline)
        baseline_id = int(baseline["id"]) if baseline else None
        family_rows = [row for row in self._rows("PartBaselineFamily") if _int_ref(_fields(row).get("BaselineProcessing")) == baseline_id] if baseline_id else []
        applicability = {}
        for family_row in family_rows:
            fields = _fields(family_row)
            family = str(fields.get("Family") or "").casefold()
            if family in FAMILY_PROCESS:
                status = str(fields.get("ApplicabilityStatus") or "unknown").casefold()
                applicability[family] = {"applicable": status not in {"not_applicable", "excluded", "not applicable"},
                    "status": status, "sourceEvidenceStatus": fields.get("SourceEvidenceStatus"),
                    "completenessConfirmed": bool(fields.get("CompletenessConfirmed")), "requirementCount": fields.get("RequirementCount") or 0}
        for family in FAMILY_PROCESS:
            applicability.setdefault(family, {"applicable": None, "status": "unknown", "sourceEvidenceStatus": "unknown", "completenessConfirmed": False, "requirementCount": 0})

        revisions = {int(row["id"]): _fields(row) for row in self._rows("LineRevision")}
        masters = {int(row["id"]): _fields(row) for row in self._rows("LineMaster")}
        effective_overrides = effective_baseline_overrides(self._optional_rows("PartRegistryRequest"), part_id, revision_id)
        details_by_revision: dict[int, list[dict[str, Any]]] = {}
        for row in self._rows("LineDetail"):
            fields = _fields(row)
            line_revision_id = _int_ref(fields.get("LineRevision"))
            if line_revision_id:
                details_by_revision.setdefault(line_revision_id, []).append({**fields, "_effectiveDetailId": int(row["id"])})
        source_observations = {int(row["id"]): _fields(row) for row in self._rows("SourceLineObservation")}
        requirement_key_counts: dict[str, int] = {}
        active_links = []
        for row in self._rows("PartRevisionLine"):
            link = _fields(row)
            if _int_ref(link.get("PartRevision")) != revision_id or str(link.get("Status") or "active").casefold() in REMOVED_STATUSES:
                continue
            active_links.append((row, link))
            raw_key = str(link.get("RequirementKey") or "")
            if raw_key:
                requirement_key_counts[raw_key] = requirement_key_counts.get(raw_key, 0) + 1
        requirements = []
        for link_row, link in active_links:
            line_master_id, line_revision_id = _int_ref(link.get("LineMaster")), _int_ref(link.get("LineRevision"))
            master, line_revision = masters.get(line_master_id or -1, {}), revisions.get(line_revision_id or -1, {})
            details = details_by_revision.get(line_revision_id or -1, [])
            detail = deepcopy(details[0]) if len(details) == 1 else {}
            detail_row_id = detail.pop("_effectiveDetailId", None)
            if detail_row_id is not None:
                detail = _fields(apply_effective_baseline_override({"id": detail_row_id, "fields": detail}, effective_overrides))
            family = {"material_cut": "mcl", "tool_shop": "toolshop", "cnc": "cnc"}.get(str(link.get("ProcessType") or master.get("ProcessType") or detail.get("ProcessType") or "").casefold(), "other")
            raw_key = str(link.get("RequirementKey") or "")
            key = raw_key
            identity_status = "stable" if raw_key else "legacy_unclassified"
            legacy_requirement_key = None
            if raw_key and requirement_key_counts.get(raw_key, 0) > 1:
                # Keep every ambiguous historical occurrence visible and uniquely
                # addressable in the draft UI without rewriting its old row.
                key = f"legacy-duplicate-requirement:{part_id}:{revision_id}:{link_row.get('id')}"
                identity_status = "legacy_duplicate_unclassified"
                legacy_requirement_key = raw_key
            elif not raw_key:
                key = f"legacy-requirement:{part_id}:{revision_id}:{link.get('RevisionLineKey') or link_row.get('id')}"
            detail.pop("DetailKey", None)
            detail.pop("LineRevision", None)
            detail.pop("RequestKey", None)
            detail.pop("RequestFingerprint", None)
            engineering = _json_cell(detail.get("EngineeringAttributes"))
            detail["EngineeringAttributes"] = engineering or {}
            for reference_field in ("Material", "PurchaseItem", "ProcessOperation", "WorkCenter"):
                detail[reference_field] = _int_ref(detail.get(reference_field))
            provenance = {"type": link.get("ProvenanceType") or ("ods_baseline" if _int_ref(link.get("SourceLineObservation")) else "legacy_unclassified"),
                "sourceObservationId": _int_ref(link.get("SourceLineObservation")), "evidence": _json_cell(link.get("ProvenanceEvidence")) or {}}
            field_status = deepcopy((engineering or {}).get("fieldStatus", {}))
            for weight_field in ("WeightKg", "WeightGrams"):
                if weight_field in field_status:
                    continue
                saved_weight = detail.get(weight_field)
                if saved_weight in (None, ""):
                    field_status[weight_field] = "missing" if saved_weight is None else "blank"
                    continue
                numeric_weight = _num(saved_weight, allow_blank=True)
                if numeric_weight is not None and numeric_weight > 0:
                    field_status[weight_field] = "value"
                elif str(provenance.get("type") or "").casefold() in {"safari_authored", "safari_edited"}:
                    # Older Safari-authored rows predate explicit status cells;
                    # their editor-created zeroes are Grist defaults, not saved totals.
                    field_status[weight_field] = "missing"
                else:
                    # Preserve uncertainty for ambiguous legacy/source rows rather
                    # than presenting a default zero as a recorded weight.
                    field_status[weight_field] = "unknown"
            observation = source_observations.get(_int_ref(link.get("SourceLineObservation")) or -1, {})
            observed = _json_cell(observation.get("ObservedFields")) or {}
            cells = _json_cell(observation.get("Cells")) or {}
            source_headers = cells.get("sourceHeaders") if isinstance(cells.get("sourceHeaders"), dict) else {}
            remarks_key = "toolshop_remarks" if "toolshop_remarks" in observed else "toolshop_part_name"
            remarks_present = remarks_key in observed and (remarks_key != "toolshop_part_name" or str(source_headers.get(remarks_key) or "").strip().casefold() == "remarks")
            plate_present = "product_part_name" in observed
            for field_name, source_present, source_value in (
                ("ToolShopRemarks", remarks_present, observed.get(remarks_key)),
                ("PlatePart", plate_present, observed.get("product_part_name")),
            ):
                if field_name not in field_status and source_present:
                    field_status[field_name] = "blank" if source_value is None or not str(source_value).strip() else "value"
                elif field_name not in field_status:
                    field_status[field_name] = "missing"
            item = {"requirementKey": key, "legacyRequirementKey": legacy_requirement_key,
                "identityStatus": identity_status, "family": family,
                "lineMasterId": line_master_id, "lineRevisionId": line_revision_id,
                "lineIdentityStatus": "resolved" if master and line_revision and _int_ref(line_revision.get("LineMaster")) == line_master_id else "unresolved",
                "lineStatus": line_revision.get("Status") or "unclassified", "processType": link.get("ProcessType") or master.get("ProcessType") or family,
                "quantity": link.get("QuantityExact") if link.get("QuantityExact") not in (None, "") else link.get("QuantityPerPart"),
                "uom": link.get("QuantityUOM") or "", "sourcePartName": master.get("SourcePartName") or "",
                "physicalSignature": line_revision.get("PhysicalSignature") or "", "detail": detail,
                "fieldStatus": field_status, "provenance": provenance}
            item["baselineMaterialResolution"] = (engineering or {}).get("baselineMaterialResolution")
            if family in {"mcl", "toolshop"}:
                attributes = _json_cell(detail.get("EngineeringAttributes")) or {}
                saved_evidence = attributes.get("materialWeightEvidence") if isinstance(attributes, dict) else None
                item["materialWeightEvidence"] = self._verified_saved_material_evidence(saved_evidence, _int_ref(detail.get("Material")))
                if item["materialWeightEvidence"] is None:
                    material = self._material_row(_int_ref(detail.get("Material")))
                    item["materialWeightEvidence"] = {"status": "unverified" if saved_evidence else "unavailable",
                        "canonicalName": self._material_name(_fields(material)) if material else ""}
                if self._legacy_duplicate_material_basis(item):
                    item["materialConsumptionStatus"] = "legacy_duplicate_ambiguous"
                    item["materialConsumptionResolution"] = None
                else:
                    item["materialConsumptionStatus"] = "resolved"
                    item["materialConsumptionResolution"] = (attributes.get("materialConsumptionBasis")
                        if isinstance(attributes, dict) else None)
            requirements.append(item)

        components = []
        part_by_record = {int(row["id"]): _fields(row) for row in self._rows("ProductPart")}
        revision_by_id = {int(row["id"]): _fields(row) for row in self._rows("PartRevision")}
        meta_by_id = {int(row["id"]): _fields(row) for row in self._rows("PartMetadataVersion")}
        for component_row in self._rows("PartComponentRevision"):
            component = _fields(component_row)
            if _int_ref(component.get("ParentRevision")) != revision_id or str(component.get("ComponentStatus") or component.get("Status") or "active").casefold() in REMOVED_STATUSES:
                continue
            child_revision_id = _int_ref(component.get("ChildRevision"))
            child_revision = revision_by_id.get(child_revision_id or -1, {})
            child_part_record = _int_ref(child_revision.get("ProductPart"))
            child_part = part_by_record.get(child_part_record or -1, {})
            child_meta = meta_by_id.get(_int_ref(child_part.get("CurrentMetadataVersion")) or -1, {})
            identity = str(component.get("ComponentIdentity") or "")
            components.append({"componentIdentity": identity or f"legacy-component:{part_id}:{revision_id}:{component.get('ComponentKey') or component_row.get('id')}",
                "identityStatus": "stable" if identity else "legacy_unclassified", "componentRecordId": int(component_row["id"]),
                "childPartId": child_part.get("StablePartId") or "", "childPartNumber": child_part.get("PartNumber") or "",
                "childName": child_meta.get("DisplayName") or child_part.get("DisplayName") or "",
                "childRevisionId": child_revision_id, "childRevisionLabel": child_revision.get("RevisionLabel") or child_revision.get("Revision"),
                "quantity": component.get("Quantity"), "uom": component.get("QuantityUOM") or "", "sourcingRoute": component.get("SourcingRoute") or "auto",
                "status": component.get("ComponentStatus") or component.get("Status") or "active", "provenance": component.get("Reason") or ""})

        purchases = []
        for purchase_row in self._rows("PartPurchaseSpecification"):
            fields = _fields(purchase_row)
            if _int_ref(fields.get("PartRevision")) != revision_id or str(fields.get("Status") or "active").casefold() != "active":
                continue
            identity = str(fields.get("SpecificationIdentity") or "")
            purchases.append({"specificationIdentity": identity or f"legacy-specification:{fields.get('SpecificationKey') or purchase_row.get('id')}",
                "identityStatus": "stable" if identity else "legacy_unclassified", "specificationRecordId": int(purchase_row["id"]),
                "specificationKey": fields.get("SpecificationKey"), "purchaseItemId": _int_ref(fields.get("PurchaseItem")),
                "code": fields.get("SpecificationCode") or "", "manufacturer": fields.get("Manufacturer") or "",
                "manufacturerPartNumber": fields.get("ManufacturerPartNumber") or "", "description": fields.get("Description") or "",
                "attributes": _json_cell(fields.get("Attributes")) or {}, "costingUOM": fields.get("CostingUOM") or "",
                "costingCurrency": fields.get("CostingCurrency") or "", "status": fields.get("Status") or "active"})

        saved = self._stored_version(revision_id)
        if saved:
            snapshot = _json_cell(_fields(saved).get("Content"))
            if not snapshot:
                raise PartIdentityError("PART_DEFINITION_UNREADABLE", "The accepted Part-definition snapshot is not valid canonical JSON.")
            # The normalized records remain the costing source. This immutable
            # snapshot supplies complete applicability, identity and provenance.
            applicability = snapshot.get("applicability", applicability)
        return {"partId": part_id, "partNumber": part_fields.get("PartNumber"), "partName": part_fields.get("DisplayName") or "",
            "partRecordId": int(part_row["id"]), "revisionId": revision_id,
            "revisionLabel": revision_fields.get("RevisionLabel") or revision_fields.get("Revision") or "A",
            "revisionStatus": revision_fields.get("BaselineStatus") or revision_fields.get("Status") or "draft",
            "revisionHistory": self._revision_history(int(part_row["id"]), revision_id),
            "baselineStatus": baseline_fields.get("Status") or "not_established", "source": {"workbookName": baseline_fields.get("WorkbookName"),
                "workbookPath": baseline_fields.get("WorkbookPath"), "sourceHash": baseline_fields.get("SourceHash"), "readOnly": True},
            "applicability": applicability, "requirements": requirements, "components": components, "purchases": purchases,
            "finalizeOnPublication": str(revision_fields.get("BaselineStatus") or "draft").casefold() == "finalized",
            "contentFingerprint": request_fingerprint({"applicability": applicability, "requirements": requirements, "components": components, "purchases": purchases})}

    def get_definition(self, part_id: str, *, actor: str = "") -> dict[str, Any]:
        part = self._part_row(part_id)
        revision = self._current_revision(part_id)
        self._check_established(revision)
        definition = self._definition_from_rows(part_id, int(revision["id"]))
        history = definition.get("revisionHistory") or []
        revision_labels = {int(item["id"]): str(item.get("revisionLabel") or item["id"]) for item in history}
        for item in history:
            previous_id = _int_ref(item.get("previousRevisionId"))
            if previous_id and previous_id in revision_labels:
                before = self._definition_from_rows(part_id, previous_id)
                after = definition if int(item["id"]) == int(revision["id"]) else self._definition_from_rows(part_id, int(item["id"]))
                item["beforeRevisionLabel"] = revision_labels[previous_id]
                item["changedRows"] = self._definition_changes(before, after)
            else:
                item["beforeRevisionLabel"] = None
                item["changedRows"] = None
        draft_rows = self._optional_rows("PartDefinitionDraft")
        cr_rows = self._optional_rows("PartEngineeringChangeRequest")
        publication_schema_available = self._publication_schema_available()
        drafts = [row for row in draft_rows if _int_ref(_fields(row).get("ProductPart")) == int(part["id"])
            and str(_fields(row).get("Status") or "open").casefold() == "open"]
        draft = max(drafts, key=lambda row: int(_fields(row).get("SaveVersion") or 0)) if drafts else None
        if draft:
            content = _json_cell(_fields(draft).get("Content"))
            definition["draft"] = {"draftKey": _fields(draft).get("DraftKey"), "saveVersion": _fields(draft).get("SaveVersion"),
                "baseRevisionId": _int_ref(_fields(draft).get("BaseRevision")), "content": content,
                "reason": _fields(draft).get("Reason") or "", "updatedAt": _fields(draft).get("UpdatedAt"),
                "reopenedFromChangeRequest": _int_ref(_fields(draft).get("ReopenedFromChangeRequest")),
                "stale": _int_ref(_fields(draft).get("BaseRevision")) != int(revision["id"])}
        part_requests = [row for row in cr_rows
            if _int_ref(_fields(row).get("ProductPart")) == int(part["id"])
            and str(_fields(row).get("Status") or "").casefold() in {"pending_approval", "approved", "publishing", "publication_failed"}]
        definition["pendingChangeRequests"] = [{"key": _fields(row).get("ChangeRequestKey"), "status": _fields(row).get("Status"),
            "reason": _fields(row).get("Reason"), "createdAt": _fields(row).get("CreatedAt"),
            "approvedBy": _fields(row).get("ApprovedBy"), "approvedAt": _fields(row).get("ApprovedAt"),
            "before": _json_cell(_fields(row).get("BeforeDefinition")) or {},
            "after": _json_cell(_fields(row).get("AfterDefinition")) or {},
            "impact": _json_cell(_fields(row).get("ImpactPreview")) or {}}
            for row in sorted(part_requests, key=lambda item: str(_fields(item).get("CreatedAt") or ""), reverse=True)]
        stale_requests = [row for row in cr_rows
            if _int_ref(_fields(row).get("ProductPart")) == int(part["id"])
            and str(_fields(row).get("Status") or "").casefold() == "stale"]
        definition["staleChangeRequests"] = [{"key": _fields(row).get("ChangeRequestKey"), "status": "stale",
            "reason": _fields(row).get("Reason"), "staleAt": _fields(row).get("StaleAt"),
            "staleCode": _fields(row).get("StaleCode"), "staleReason": _fields(row).get("StaleReason"),
            "before": _json_cell(_fields(row).get("BeforeDefinition")) or {},
            "after": _json_cell(_fields(row).get("AfterDefinition")) or {},
            "impact": _json_cell(_fields(row).get("ImpactPreview")) or {},
            "canReopen": not _int_ref(_fields(row).get("ReopenedDraft"))}
            for row in sorted(stale_requests, key=lambda item: str(_fields(item).get("StaleAt") or ""), reverse=True)]
        definition["canApprove"] = publication_schema_available and str(actor or "").strip().casefold() == os.getenv("SAFARI_ENGINEERING_APPROVER_USERNAME", "Irshad").strip().casefold()
        for item, row in zip(definition["pendingChangeRequests"], sorted(part_requests, key=lambda item: str(_fields(item).get("CreatedAt") or ""), reverse=True)):
            fields = _fields(row)
            if definition["canApprove"] and str(fields.get("Status") or "").casefold() == "approved" and fields.get("ApprovedBy"):
                if str(fields.get("ApprovedBy") or "").strip() == str(actor or "").strip():
                    item["approvalRecoveryKey"] = self._verified_approval_owner(row)
        definition["actions"] = {"editPart": publication_schema_available, "revisionHistory": True, "compareWithOds": True,
            "publicationSchemaAvailable": publication_schema_available,
            "comparisonSourceAvailable": bool(definition["source"].get("workbookPath"))}
        return definition

    def options(self, part_id: str) -> dict[str, Any]:
        self._check_established(self._current_revision(part_id))
        parts = [{"id": _fields(row).get("StablePartId"), "partNumber": _fields(row).get("PartNumber"), "name": _fields(row).get("DisplayName") or ""}
            for row in self._rows("ProductPart") if _fields(row).get("StablePartId") and str(_fields(row).get("Status") or "active").casefold() == "active"]
        materials = []
        material_rows = self._rows("Material")
        catalog = self._material_weight_catalog()
        source_document_id = self._material_catalog_document_id(catalog)
        verified_bindings = [payload for _, payload in self._identity_alias_bindings()]
        explicit_material_ids_by_master: dict[int, set[int]] = {}
        for payload in verified_bindings:
            master_id = _int_ref(payload.get("sourceMasterMaterialId"))
            material_id = _int_ref(payload.get("safariMaterialId"))
            if (master_id is not None and material_id is not None
                    and str(payload.get("sourceDocumentId") or "") == source_document_id):
                explicit_material_ids_by_master.setdefault(master_id, set()).add(material_id)

        from app.config import load_material_mapping
        from app.material_resolution import resolve_material
        try:
            material_mappings = load_material_mapping()
        except Exception:
            material_mappings = {}
        material_ids_by_canonical_name: dict[str, set[int]] = {}
        for local in material_rows:
            local_id = _int_ref(local.get("id"))
            local_name = self._material_name(_fields(local))
            if local_id is None or not local_name:
                continue
            try:
                resolved = resolve_material(local_name, material_mappings)
                resolved_name = resolved.canonical_name or local_name
            except Exception:
                resolved_name = local_name
            material_ids_by_canonical_name.setdefault(resolved_name.strip().casefold(), set()).add(local_id)

        material_ids_by_master: dict[int, set[int]] = {}
        for record in catalog.get("records", []) if catalog.get("status") == "available" else []:
            master_id = _int_ref(record.get("masterMaterialId"))
            if master_id is None:
                continue
            exact_ids = set(explicit_material_ids_by_master.get(master_id, set()))
            if not exact_ids:
                canonical_name = str(record.get("canonicalName") or "").strip()
                exact_ids.update(material_ids_by_canonical_name.get(canonical_name.casefold(), set()))
            material_ids_by_master[master_id] = exact_ids

        for row in material_rows:
            fields = _fields(row)
            name = self._material_name(fields) or fields.get("MaterialDisplayName") or fields.get("Description") or f"Material {row['id']}"
            evidence = self._material_weight_evidence_for_identity(int(row["id"]))
            source_aliases = [str(record.get("canonicalName") or "") for record in catalog.get("records", [])
                if _int_ref(record.get("masterMaterialId")) is not None
                and material_ids_by_master.get(int(record["masterMaterialId"]), set()) == {int(row["id"])}]
            materials.append({"id": int(row["id"]), "name": name, "code": fields.get("Code") or fields.get("MaterialCode") or "",
                "sourceAliases": sorted(set(source_aliases), key=str.casefold),
                "weightEvidence": evidence, "kgPerMeter": evidence.get("kgPerMeter") if evidence else None,
                "weightStatus": evidence.get("factorStatus", evidence.get("status", "unavailable")) if evidence else "unavailable"})
        material_catalog = []
        if catalog.get("status") == "available":
            for record in catalog.get("records", []):
                master_id = _int_ref(record.get("masterMaterialId"))
                if master_id is None:
                    continue
                bound_ids = material_ids_by_master.get(master_id, set())
                material_catalog.append({"sourceKey": f"{source_document_id}:MasterMaterial:{master_id}",
                    "masterMaterialId": master_id, "canonicalName": str(record.get("canonicalName") or ""),
                    "code": str(record.get("code") or ""), "kgPerMeter": record.get("kgPerMeter"),
                    "factorStatus": record.get("factorStatus") or "unavailable", "sourceDate": record.get("sourceDate"),
                    "materialId": next(iter(bound_ids)) if len(bound_ids) == 1 else None,
                    "bindingStatus": "bound" if len(bound_ids) == 1 else ("ambiguous" if bound_ids else "unbound")})
        purchase_items = [{"id": int(row["id"]), "name": _fields(row).get("Name") or _fields(row).get("Description") or "", "code": _fields(row).get("Code") or ""}
            for row in self._rows("PurchaseItem")]
        units = {"Nos", "each", "pcs", "kg", "g", "mm", "cm", "m", "in", "inch"}
        for table, field_names in (("LineDetail", ("QuantityUOM", "DimensionUOM", "WeightUOM")),
                                   ("PartRevisionLine", ("QuantityUOM",)), ("CostingProcessRate", ("RateUOM",))):
            for row in self._optional_rows(table):
                fields = _fields(row)
                units.update(str(fields.get(name) or "").strip() for name in field_names if str(fields.get(name) or "").strip())
        return {"parts": sorted(parts, key=lambda item: str(item["partNumber"])), "materials": sorted(materials, key=lambda item: str(item["name"]).casefold()),
            "purchaseItems": sorted(purchase_items, key=lambda item: str(item["name"]).casefold()), "families": list(FAMILY_PROCESS),
            "units": sorted(units, key=lambda item: item.casefold()),
            "materialWeightSource": {"status": catalog.get("status", "unavailable"), "source": "Costing-New",
                "readAt": catalog.get("readAt"), "readOnly": True, "catalogCount": len(catalog.get("records", [])),
                "selectableCount": sum(1 for item in material_catalog if item["bindingStatus"] == "bound"),
                "unboundCount": sum(1 for item in material_catalog if item["bindingStatus"] == "unbound")},
            "materialCatalog": sorted(material_catalog, key=lambda item: str(item["canonicalName"]).casefold())}

    def _requirement_line_identity(self, raw: dict[str, Any], existing: dict[str, Any] | None,
                                   base_requirements: list[dict[str, Any]]) -> tuple[int | None, int | None]:
        """Resolve retained line identity from the immutable base membership."""
        supplied = []
        for name in ("lineMasterId", "lineRevisionId"):
            value = raw.get(name)
            invalid_scalar = (not isinstance(value, (int, float, str, type(None))) or isinstance(value, bool)
                or isinstance(value, float) and (not math.isfinite(value) or not value.is_integer())
                or isinstance(value, str) and bool(value.strip()) and not value.strip().isdigit())
            ref = None if invalid_scalar else _int_ref(value)
            if value not in (None, "") and (invalid_scalar or ref is None or ref <= 0):
                raise PartIdentityError("PART_REQUIREMENT_LINE_IDENTITY_INVALID", "A physical line reference is invalid.")
            supplied.append(ref)
        if not existing:
            # A reviewed duplicate occurrence is reclassified as a genuinely new
            # identity; its old physical references are evidence, not a binding.
            legacy_key = str(raw.get("legacyRequirementKey") or "")
            candidates = [item for item in base_requirements if legacy_key and item.get("legacyRequirementKey") == legacy_key
                and item.get("identityStatus") == "legacy_duplicate_unclassified"]
            reviewed = (str(raw.get("identityStatus") or "").casefold() == "safari_authored"
                and str(raw.get("requirementKey") or "").startswith("safari-authored-requirement:")
                and any(tuple(supplied) == (_int_ref(item.get("lineMasterId")), _int_ref(item.get("lineRevisionId"))) for item in candidates))
            if any(supplied) and not reviewed:
                raise PartIdentityError("PART_REQUIREMENT_LINE_IDENTITY_INVALID", "A new requirement cannot select an arbitrary physical line identity.")
            return None, None
        master_id, revision_id = _int_ref(existing.get("lineMasterId")), _int_ref(existing.get("lineRevisionId"))
        if any(value is not None and value != expected for value, expected in zip(supplied, (master_id, revision_id))):
            raise PartIdentityError("PART_REQUIREMENT_LINE_IDENTITY_INVALID", "A retained requirement must use its saved physical line and revision.")
        master = next((row for row in self._rows("LineMaster") if int(row.get("id", -1)) == master_id), None)
        revision = next((row for row in self._rows("LineRevision") if int(row.get("id", -1)) == revision_id), None)
        if not master_id or not revision_id or not master or not revision or _int_ref(_fields(revision).get("LineMaster")) != master_id:
            raise PartIdentityError("PART_REQUIREMENT_LINE_IDENTITY_UNRESOLVED", "The saved requirement has an unresolved physical line or revision; review is required before publication.")
        return master_id, revision_id

    def _validate_plan_line_identities(self, parts: list[dict[str, Any]]) -> None:
        """Validate every frozen Part against its historical base before writes."""
        with self.registry.read_scope():
            self._validate_plan_line_identities_in_scope(parts)

    def _validate_plan_line_identities_in_scope(self, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            part_id, revision_id = str(part["partId"]), int(part["currentRevisionId"])
            revision = next((row for row in self._rows("PartRevision") if int(row.get("id", -1)) == revision_id), None)
            if not revision or _int_ref(_fields(revision).get("ProductPart")) != int(self._part_row(part_id)["id"]):
                raise PartIdentityError("PART_REVISION_MISMATCH", "The saved base revision does not belong to this Part.")
            base = self._definition_from_rows(part_id, revision_id)
            existing = {str(item.get("requirementKey")): item for item in base["requirements"]}
            seen: set[str] = set()
            for item in part["content"].get("requirements", []):
                key = str(item.get("requirementKey") or "")
                if not key or key in seen:
                    raise PartIdentityError("PART_DEFINITION_IDENTITY_DUPLICATE", "Each published requirement must have a unique saved identity.")
                seen.add(key)
                master, revision = self._requirement_line_identity(item, existing.get(key), base["requirements"])
                item["lineMasterId"], item["lineRevisionId"] = master, revision

    def _normalize_content(self, part_id: str, base_revision_id: int, payload: dict[str, Any], request_key: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise PartIdentityError("PART_DEFINITION_INPUT_INVALID", "A complete Part definition object is required.")
        base = next((row for row in self._rows("PartRevision") if int(row.get("id", -1)) == base_revision_id), None)
        if not base or _int_ref(_fields(base).get("ProductPart")) != int(self._part_row(part_id)["id"]):
            raise PartIdentityError("PART_REVISION_MISMATCH", "The saved base revision does not belong to this Part.")
        current = self._definition_from_rows(part_id, base_revision_id)
        raw_app = payload.get("applicability")
        if not isinstance(raw_app, dict):
            raise PartIdentityError("PART_DEFINITION_INPUT_INVALID", "Save an explicit applicability choice for MCL, Tool Shop and CNC.")
        applicability = {}
        for family in FAMILY_PROCESS:
            item = raw_app.get(family)
            if isinstance(item, bool):
                applicable = item
                status = "applicable" if item else "not_applicable"
            elif isinstance(item, dict) and type(item.get("applicable")) is bool:
                applicable = bool(item["applicable"])
                status = "applicable" if applicable else "not_applicable"
            else:
                raise PartIdentityError("PART_DEFINITION_APPLICABILITY_INVALID", f"Choose whether {family} applies to this Part.")
            applicability[family] = {"applicable": applicable, "status": status}

        raw_requirements = payload.get("requirements")
        raw_components = payload.get("components")
        raw_purchases = payload.get("purchases", [])
        if not isinstance(raw_requirements, list) or not isinstance(raw_components, list) or not isinstance(raw_purchases, list):
            raise PartIdentityError("PART_DEFINITION_INPUT_INVALID", "Requirements, components and purchase specifications must be lists.")
        existing_by_key = {str(item.get("requirementKey")): item for item in current["requirements"]}
        known_uoms = {"nos", "no", "each", "ea", "pcs", "piece", "pieces", "kg", "kilogram", "kilograms",
            "g", "gram", "grams", "mm", "millimeter", "millimeters", "cm", "centimeter", "centimeters",
            "m", "meter", "meters", "metre", "metres", "in", "inch", "inches", "sec", "second", "seconds",
            "min", "minute", "minutes", "hr", "hour", "hours", "day", "days"}
        for table, fields_to_read in (("LineDetail", ("QuantityUOM", "DimensionUOM", "WeightUOM")),
                                      ("PartRevisionLine", ("QuantityUOM",)), ("PartComponentRevision", ("QuantityUOM",)),
                                      ("CostingProcessRate", ("RateUOM",))):
            for row in self._optional_rows(table):
                fields = _fields(row)
                known_uoms.update(str(fields.get(name) or "").strip().casefold() for name in fields_to_read if str(fields.get(name) or "").strip())
        seen_requirements: set[str] = set()
        requirements = []
        for index, raw in enumerate(raw_requirements):
            if not isinstance(raw, dict):
                raise PartIdentityError("PART_DEFINITION_INPUT_INVALID", "Each requirement must be a saved requirement object.")
            family = str(raw.get("family") or "").strip().casefold()
            key = str(raw.get("requirementKey") or f"authored-requirement:{request_fingerprint([part_id, base_revision_id, request_key, index])}").strip()
            existing = existing_by_key.get(key)
            submitted_identity_status = str(raw.get("identityStatus") or "").casefold()
            if raw.get("legacyRequirementKey") and not (existing and submitted_identity_status == "legacy_duplicate_unclassified"):
                if not (submitted_identity_status == "safari_authored" and key.startswith("safari-authored-requirement:") and not existing):
                    raise PartIdentityError("PART_DEFINITION_LEGACY_IDENTITY_REVIEW_REQUIRED", "Assign a reviewed new requirement identity to this ambiguous legacy occurrence before changing its identity.")
            if existing and existing.get("identityStatus") == "legacy_duplicate_unclassified" and submitted_identity_status != "legacy_duplicate_unclassified":
                raise PartIdentityError("PART_DEFINITION_LEGACY_IDENTITY_REVIEW_REQUIRED", "An ambiguous legacy occurrence can only be reclassified with a new Safari-authored requirement identity.")
            line_master_id, line_revision_id = self._requirement_line_identity(raw, existing, current["requirements"])
            raw = {**raw, "lineMasterId": line_master_id, "lineRevisionId": line_revision_id}
            if family not in FAMILY_PROCESS and not (family == "other" and existing and raw.get("lineRevisionId") == existing.get("lineRevisionId")):
                raise PartIdentityError("PART_DEFINITION_FAMILY_INVALID", "New direct requirements must belong to MCL, Tool Shop or CNC. Existing other-process lines can only be preserved unchanged.")
            if family in FAMILY_PROCESS and not applicability[family]["applicable"]:
                raise PartIdentityError("PART_DEFINITION_FAMILY_NOT_APPLICABLE", f"Mark {family} applicable before adding requirements to it.")
            if key in seen_requirements:
                raise PartIdentityError("PART_DEFINITION_IDENTITY_DUPLICATE", "Requirement identities must be unique within one Part revision.")
            seen_requirements.add(key)
            detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
            detail = {str(name): deepcopy(value) for name, value in detail.items() if str(name) in LINE_DETAIL_FIELDS}
            unchanged_legacy_line = bool(existing and self._same_requirement_physical_content(raw, existing, detail))
            old_detail = (existing or {}).get("detail") if isinstance((existing or {}).get("detail"), dict) else {}
            submitted_field_status = raw.get("fieldStatus") if isinstance(raw.get("fieldStatus"), dict) else {}
            old_field_status = (existing or {}).get("fieldStatus") if isinstance((existing or {}).get("fieldStatus"), dict) else {}

            def unchanged_raw_detail_value(name: str) -> bool:
                if not existing:
                    return False
                was_present, is_present = name in old_detail, name in detail
                if was_present != is_present:
                    return False
                if not was_present:
                    return True
                previous, submitted = old_detail.get(name), detail.get(name)
                if name in {"QuantityUOM", "DimensionUOM", "WeightUOM"}:
                    return str(previous or "").strip().casefold() == str(submitted or "").strip().casefold()
                previous_number, submitted_number = _num(previous, allow_blank=True), _num(submitted, allow_blank=True)
                if previous_number is not None and submitted_number is not None:
                    return previous_number == submitted_number
                return previous == submitted

            quantity = _num(raw.get("quantity"), allow_blank=True)
            uom = str(raw.get("uom") or "").strip()
            if quantity is None and not unchanged_legacy_line:
                raise PartIdentityError("PART_DEFINITION_QUANTITY_INVALID", "New or changed requirements need a finite positive quantity.")
            if quantity is not None and quantity <= 0:
                raise PartIdentityError("PART_DEFINITION_QUANTITY_INVALID", "Requirement quantities must be finite and greater than zero.")
            if not uom and not existing:
                raise PartIdentityError("PART_DEFINITION_UOM_REQUIRED", "New requirements need a quantity unit.")
            if uom and uom.casefold() not in known_uoms and not unchanged_legacy_line:
                raise PartIdentityError("PART_DEFINITION_UOM_UNSUPPORTED", "Choose a saved or supported quantity unit.")
            for name in ("Material", "PurchaseItem", "ProcessOperation", "WorkCenter"):
                submitted_ref = detail.get(name)
                if submitted_ref in (None, ""):
                    detail[name] = None if name in detail else detail.get(name)
                    continue
                ref = _int_ref(submitted_ref)
                if ref is None:
                    if unchanged_legacy_line and submitted_ref == (existing.get("detail") or {}).get(name):
                        continue
                    raise PartIdentityError("PART_DEFINITION_IDENTITY_INVALID", f"{name} must reference an existing Safari identity.")
                if ref is not None and not any(int(row.get("id", -1)) == ref for row in self._rows(name)):
                    if unchanged_legacy_line and ref == _int_ref((existing.get("detail") or {}).get(name)):
                        continue
                    raise PartIdentityError("PART_DEFINITION_IDENTITY_UNRESOLVED", f"The selected {name} identity is not available in Safari Grist.")
                detail[name] = ref
            dimension_uom = str(detail.get("DimensionUOM") or "").strip().casefold()
            dimensions = [_num(detail.get(name), allow_blank=True) for name in ("Length", "Width", "Thickness")]
            legacy_unknown_numeric_fields: set[str] = set()
            for name in ("DimensionMM", "DimensionInches", "PartWeightKg", "Length", "Width", "Thickness"):
                submitted_value = detail.get(name)
                if submitted_value in (None, ""):
                    continue
                number = _num(submitted_value)
                if number is None:
                    if unchanged_raw_detail_value(name):
                        legacy_unknown_numeric_fields.add(name)
                        continue
                    raise PartIdentityError("PART_DEFINITION_DETAIL_NUMBER_INVALID", f"{name} must be a finite numeric value where supplied.")
                if name in {"DimensionMM", "DimensionInches", "PartWeightKg", "Length", "Width", "Thickness"} and number <= 0:
                    if unchanged_raw_detail_value(name):
                        legacy_unknown_numeric_fields.add(name)
                        continue
                    if name == "PartWeightKg":
                        raise PartIdentityError("PART_DEFINITION_PART_WEIGHT_INVALID", "Per Part Weight must be a finite positive kg value.")
                    if name in {"DimensionMM", "DimensionInches"}:
                        raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INVALID", "Dimension to Cut must be a finite positive value.")
                    raise PartIdentityError("PART_DEFINITION_DETAIL_NUMBER_INVALID", f"{name} must be greater than zero where supplied.")
            preserved_zero_dimensions = {
                name for name, value in zip(("Length", "Width", "Thickness"), dimensions)
                if unchanged_raw_detail_value(name) and value == 0
            }
            effective_dimensions = [None if name in preserved_zero_dimensions else value
                                    for name, value in zip(("Length", "Width", "Thickness"), dimensions)]
            dimension_group_unchanged = bool(existing and all(unchanged_raw_detail_value(name)
                for name in ("Length", "Width", "Thickness", "DimensionUOM")))
            if family == "cnc":
                if any(value is not None and value <= 0 for value in effective_dimensions) and not dimension_group_unchanged:
                    raise PartIdentityError("PART_DEFINITION_DIMENSION_INVALID", "Entered CNC plate dimensions must be finite and positive.")
                if any(value is not None for value in effective_dimensions) and (not all(value is not None for value in effective_dimensions) or dimension_uom not in LENGTH_TO_MM) and not dimension_group_unchanged:
                    raise PartIdentityError("PART_DEFINITION_DIMENSION_UOM_INVALID", "CNC length, width and thickness must be supplied together with a supported unit.")
            elif any(value is not None for value in effective_dimensions):
                if any(value is not None and value <= 0 for value in effective_dimensions) and not dimension_group_unchanged:
                    raise PartIdentityError("PART_DEFINITION_DIMENSION_INVALID", "Entered dimensions must be finite and positive.")
                if dimension_uom not in LENGTH_TO_MM and not dimension_group_unchanged:
                    raise PartIdentityError("PART_DEFINITION_DIMENSION_UOM_INVALID", "Entered dimensions need a supported unit.")

            dimension_mm = _num(detail.get("DimensionMM"), allow_blank=True)
            dimension_inches = _num(detail.get("DimensionInches"), allow_blank=True)
            old_dimension_mm = _num(old_detail.get("DimensionMM"), allow_blank=True)
            old_dimension_inches = _num(old_detail.get("DimensionInches"), allow_blank=True)
            dimension_text = str(detail.get("DimensionText") or "")
            dimension_changed = any(not unchanged_raw_detail_value(name)
                for name in ("DimensionMM", "DimensionInches", "DimensionText", "DimensionUOM"))
            value_mm = dimension_mm if dimension_mm is not None else dimension_inches * LENGTH_TO_MM["in"] if dimension_inches is not None else None
            parsed_text = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|millimeters?|cm|centimeters?|m|meters?|metres?|in|inches?)\s*", dimension_text, re.IGNORECASE) if dimension_text.strip() else None
            if dimension_text.strip() and not parsed_text and not unchanged_raw_detail_value("DimensionText"):
                raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INVALID", "Dimension to Cut must be a finite number followed by a supported length unit.")
            if dimension_changed and (dimension_mm is not None or dimension_inches is not None):
                if any(value is not None and value <= 0 for value in (dimension_mm, dimension_inches)):
                    raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INVALID", "Dimension to Cut must be a finite positive value.")
                if dimension_uom not in LENGTH_TO_MM:
                    raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_UOM_INVALID", "Dimension to Cut needs an explicit supported unit.")
                if dimension_mm is not None and dimension_inches is not None:
                    equivalent_mm = dimension_inches * LENGTH_TO_MM["in"]
                    if abs(dimension_mm - equivalent_mm) > max(0.01, abs(dimension_mm) * 1e-7):
                        raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INCONSISTENT", "The saved mm and inch cut-length values do not agree.")
                if parsed_text:
                    text_mm = float(parsed_text.group(1)) * LENGTH_TO_MM[parsed_text.group(2).casefold()]
                    if abs((dimension_mm if dimension_mm is not None else value_mm) - text_mm) > max(0.01, abs(value_mm) * 1e-7):
                        raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INCONSISTENT", "Dimension to Cut text and numeric values do not agree.")

            part_weight = _num(detail.get("PartWeightKg"), allow_blank=True)
            old_part_weight = _num(old_detail.get("PartWeightKg"), allow_blank=True)
            weight_changed = not unchanged_raw_detail_value("PartWeightKg")
            if weight_changed and part_weight is not None and part_weight <= 0:
                raise PartIdentityError("PART_DEFINITION_PART_WEIGHT_INVALID", "Per Part Weight must be a finite positive kg value.")
            if weight_changed and part_weight is not None:
                detail["ExactWeightKgText"] = format(part_weight, ".15g")
            if dimension_changed and (dimension_mm is not None or dimension_inches is not None) and family in {"mcl", "toolshop"} and (value_mm is None or value_mm <= 0):
                raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_INVALID", "Dimension to Cut must be a finite positive value.")

            material_changed = not unchanged_raw_detail_value("Material")
            saved_attributes = _json_cell(detail.get("EngineeringAttributes")) or {}
            old_attributes = _json_cell(old_detail.get("EngineeringAttributes")) or {}
            old_weight_evidence = old_attributes.get("materialWeightEvidence") if isinstance(old_attributes, dict) else None
            material_id = _int_ref(detail.get("Material"))
            material_row = self._material_row(material_id)
            canonical_material_name = self._material_name(_fields(material_row)) if material_row else ""
            if canonical_material_name:
                # A submitted label is display text, never the binding authority.
                detail["MaterialDisplayName"] = canonical_material_name
            material_weight_evidence = self._material_weight_evidence_for_identity(
                material_id, saved_evidence=old_weight_evidence)
            detail_attributes = deepcopy(saved_attributes) if isinstance(saved_attributes, dict) else {}
            # Ignore all client-supplied factors, IDs, dates and provenance. Only
            # the server-resolved canonical identity or a verified saved capture
            # can survive this boundary.
            detail_attributes["materialWeightEvidence"] = deepcopy(material_weight_evidence)

            saved_material_basis = str(old_attributes.get("materialConsumptionBasis") or "").strip().casefold()
            submitted_material_basis = str(raw.get("materialConsumptionResolution") or "").strip().casefold()
            legacy_basis_review = bool(existing and self._legacy_duplicate_material_basis(existing))
            material_basis = submitted_material_basis if legacy_basis_review else (submitted_material_basis or saved_material_basis)
            supported_material_bases = {WEIGHT_PER_PIECE, ONE_ITEM_PER_OCCURRENCE}
            if family == "mcl":
                supported_material_bases.add(MCL_KG_PER_PIECE)
            if legacy_basis_review and material_basis not in supported_material_bases:
                raise PartIdentityError("PART_MATERIAL_BASIS_REVIEW_REQUIRED",
                    "This legacy MCL/Tool Shop row stores the same piece count in normalized consumption and Qty per Part. Review the per-piece material basis before saving.")
            if material_basis and material_basis not in supported_material_bases:
                raise PartIdentityError("PART_MATERIAL_BASIS_INVALID", "Choose physical weight per piece or one stock item per occurrence.")
            detail_attributes.pop("materialConsumptionBasis", None)
            if material_basis:
                detail_attributes["materialConsumptionBasis"] = material_basis
            if family in {"mcl", "toolshop"}:
                detail["EngineeringAttributes"] = detail_attributes
            weight_factor = _num(material_weight_evidence.get("kgPerMeter")) if isinstance(material_weight_evidence, dict) else None
            calculated_material_weight_kg = (value_mm / 1000.0 * weight_factor
                if family in {"mcl", "toolshop"} and value_mm is not None and value_mm > 0 and weight_factor is not None and weight_factor > 0 else None)

            rate_uom = str(detail.get("QuantityUOM") or "").strip()
            if material_basis in WEIGHT_PER_PIECE_BASES:
                if family not in {"mcl", "toolshop"} or calculated_material_weight_kg is None:
                    raise PartIdentityError("PART_MATERIAL_WEIGHT_REQUIRED", "A trusted material conversion and positive Dimension to Cut are required for per-piece weight resolution.")
                rate_uom = "kg"
                detail["QuantityUOM"] = rate_uom
            elif material_basis == "one_item_per_occurrence":
                if family not in {"mcl", "toolshop"}:
                    raise PartIdentityError("PART_MATERIAL_BASIS_INVALID", "One stock item per occurrence applies only to MCL or Tool Shop rows.")
                rate_uom = str(old_detail.get("QuantityUOM") or detail.get("QuantityUOM") or "Nos").strip() or "Nos"
                if rate_uom.casefold() not in {"nos", "no", "each", "ea", "pcs", "piece", "pieces"}:
                    raise PartIdentityError("PART_MATERIAL_BASIS_INVALID", "One stock item per occurrence requires a piece-count unit.")
                detail["QuantityUOM"] = rate_uom
            if rate_uom and rate_uom.casefold() not in known_uoms and not unchanged_legacy_line:
                raise PartIdentityError("PART_DEFINITION_RATE_UOM_UNSUPPORTED", "Choose a saved or supported consumption unit.")
            rate_unit = rate_uom.casefold()
            rate_unit_changed = not unchanged_raw_detail_value("QuantityUOM")
            quantity_changed = not unchanged_raw_detail_value("Quantity")
            rate_quantity_value = _num(detail.get("Quantity"), allow_blank=True)
            if detail.get("Quantity") not in (None, "") and rate_quantity_value is None and quantity_changed:
                raise PartIdentityError("PART_DEFINITION_DETAIL_NUMBER_INVALID", "Normalized consumption must be a finite numeric value where supplied.")
            consumption_derived = False
            missing_cross_dimension_conversion = False
            if material_basis in WEIGHT_PER_PIECE_BASES:
                detail["Quantity"] = calculated_material_weight_kg
                detail["ExactQuantityText"] = format(calculated_material_weight_kg, ".15g")
                rate_quantity_value = detail["Quantity"]
                consumption_derived = True
            elif material_basis == "one_item_per_occurrence":
                detail["Quantity"] = 1
                detail["ExactQuantityText"] = "1"
                rate_quantity_value = 1
                consumption_derived = True
            elif family in {"mcl", "toolshop"} and rate_unit in LENGTH_TO_MM:
                if value_mm is not None and value_mm > 0:
                    detail["Quantity"] = value_mm / LENGTH_TO_MM[rate_unit]
                    detail["ExactQuantityText"] = format(detail["Quantity"], ".15g")
                    rate_quantity_value = detail["Quantity"]
                    consumption_derived = True
                elif not existing or dimension_changed or rate_unit_changed:
                    raise PartIdentityError("PART_DEFINITION_CUT_LENGTH_REQUIRED", "A length-based consumption row needs a positive Dimension to Cut.")
            elif family in {"mcl", "toolshop"} and rate_unit in MASS_TO_KG:
                if calculated_material_weight_kg is not None:
                    detail["Quantity"] = calculated_material_weight_kg / MASS_TO_KG[rate_unit]
                    detail["ExactQuantityText"] = format(detail["Quantity"], ".15g")
                    rate_quantity_value = detail["Quantity"]
                    consumption_derived = True
                else:
                    # Existing rows may retain a truly unchanged legacy basis,
                    # but a changed/new material or cut length cannot reuse it.
                    unchanged_basis = bool(existing and not dimension_changed and not material_changed and not rate_unit_changed
                        and not quantity_changed and (value_mm is None or value_mm <= 0 or
                            material_weight_evidence.get("status") == "available"
                            and self._verified_saved_material_evidence(material_weight_evidence, material_id)))
                    if not unchanged_basis:
                        detail["Quantity"] = None
                        detail["ExactQuantityText"] = None
                        rate_quantity_value = None
                    has_physical_measure = value_mm is not None and value_mm > 0
                    missing_cross_dimension_conversion = has_physical_measure and not unchanged_basis
            elif family == "cnc" and rate_unit in MASS_TO_KG:
                if part_weight is not None and part_weight > 0:
                    detail["Quantity"] = part_weight / MASS_TO_KG[rate_unit]
                    detail["ExactQuantityText"] = format(detail["Quantity"], ".15g")
                    rate_quantity_value = detail["Quantity"]
                    consumption_derived = True
                elif not existing or weight_changed or rate_unit_changed:
                    raise PartIdentityError("PART_DEFINITION_PART_WEIGHT_REQUIRED", "A kg-based CNC consumption row needs a positive Per Part Weight.")
            else:
                needs_cross_dimension_conversion = (
                    family in {"mcl", "toolshop"} and rate_unit in MASS_TO_KG
                    or family == "cnc" and rate_unit in LENGTH_TO_MM)
                physical_basis_changed = dimension_changed if family in {"mcl", "toolshop"} else weight_changed
                explicitly_reaffirmed_manual = submitted_field_status.get("Quantity") == "manual-reaffirmed"
                if needs_cross_dimension_conversion and (physical_basis_changed or rate_unit_changed) and existing \
                        and not quantity_changed and not explicitly_reaffirmed_manual:
                    # A cut length cannot keep an old mass quantity, or a per-part
                    # weight keep an old length quantity, without conversion evidence.
                    detail["Quantity"] = None
                    detail["ExactQuantityText"] = None
                    rate_quantity_value = None
                    missing_cross_dimension_conversion = (value_mm is not None and value_mm > 0) if family in {"mcl", "toolshop"} else (part_weight is not None and part_weight > 0)
                elif needs_cross_dimension_conversion and rate_quantity_value in (None, ""):
                    missing_cross_dimension_conversion = (value_mm is not None and value_mm > 0) if family in {"mcl", "toolshop"} else (part_weight is not None and part_weight > 0)
                if rate_quantity_value is not None:
                    numeric_quantity = _num(rate_quantity_value)
                    if numeric_quantity is not None:
                        detail["Quantity"] = numeric_quantity
                        detail["ExactQuantityText"] = format(numeric_quantity, ".15g")

            if dimension_changed and (dimension_mm is not None or dimension_inches is not None) and family in {"mcl", "toolshop"}:
                if dimension_mm is None:
                    detail["DimensionMM"] = value_mm
                if dimension_inches is None:
                    detail["DimensionInches"] = value_mm / LENGTH_TO_MM["in"]
                if not dimension_text:
                    unit_label = "in" if dimension_uom in {"in", "inch", "inches"} else "mm"
                    source_value = dimension_inches if unit_label == "in" else dimension_mm
                    detail["DimensionText"] = f"{source_value} {unit_label}"
            for name in ("Quantity", "DimensionMM", "DimensionInches", "WeightGrams", "WeightKg", "PartWeightKg",
                         "Length", "Width", "Thickness", "InternalMakingCost", "ExternalMachiningCost", "RateCached", "CostCached"):
                if name not in detail or detail[name] in (None, ""):
                    continue
                numeric = _num(detail[name])
                if numeric is None or (name in {"Quantity", "Length", "Width", "Thickness"} and numeric <= 0 and name not in preserved_zero_dimensions):
                    if unchanged_raw_detail_value(name):
                        legacy_unknown_numeric_fields.add(name)
                        continue
                    raise PartIdentityError("PART_DEFINITION_DETAIL_NUMBER_INVALID", f"{name} must be a finite positive number where supplied.")
                detail[name] = numeric
            process_type = FAMILY_PROCESS.get(family) or str(raw.get("processType") or (existing or {}).get("processType") or "")
            if family in FAMILY_PROCESS:
                detail["ProcessType"] = process_type
                rate_quantity = _num(detail.get("Quantity"), allow_blank=True)
                rate_uom = str(detail.get("QuantityUOM") or "").strip()
                if not unchanged_legacy_line and (rate_quantity is None or rate_quantity <= 0 or not rate_uom) \
                        and not (missing_cross_dimension_conversion and rate_uom):
                    raise PartIdentityError("PART_DEFINITION_RATE_BASIS_INVALID", "New or changed manufacturing requirements need a finite positive rate quantity and its unit.")
                if family in {"mcl", "toolshop"} and not unchanged_legacy_line and not _int_ref(detail.get("Material")):
                    raise PartIdentityError("PART_DEFINITION_MATERIAL_REQUIRED", "Choose the canonical Material To Use for a new or changed MCL or Tool Shop requirement.")
            field_status = deepcopy(submitted_field_status)
            if legacy_unknown_numeric_fields:
                for name in legacy_unknown_numeric_fields:
                    field_status.setdefault(name, "unknown")
            if consumption_derived:
                field_status["Quantity"] = "derived"
            elif missing_cross_dimension_conversion or detail.get("Quantity") in (None, ""):
                field_status["Quantity"] = "missing"
            else:
                field_status["Quantity"] = "manual"
            if not existing:
                for name in ("WeightKg", "WeightGrams"):
                    prior_status = str(field_status.get(name) or "").casefold()
                    if name not in detail:
                        field_status[name] = "missing"
                    elif detail[name] in (None, ""):
                        field_status[name] = "blank"
                    elif _num(detail[name], allow_blank=True) == 0 and prior_status != "value":
                        # Grist returns zero for several physically empty numeric
                        # cells. Treat it as an absent saved total unless the
                        # editor/source supplied explicit value evidence.
                        field_status[name] = "missing"
                    else:
                        field_status[name] = "value"
            for name in ("ToolShopRemarks", "PlatePart", "DimensionText", "ItemCode", "ItemName", "MaterialDisplayName"):
                if name in detail:
                    if detail[name] in (None, ""):
                        field_status[name] = "missing" if field_status.get(name) == "missing" else "blank"
                    else:
                        field_status[name] = "value"
            engineering = _json_cell(detail.get("EngineeringAttributes")) or {}
            engineering["fieldStatus"] = field_status
            detail["EngineeringAttributes"] = canonical_grist_json_cell(engineering)
            requirements.append({"requirementKey": key, "identityStatus": raw.get("identityStatus") or ("stable" if existing else "safari_authored"),
                "family": family, "lineMasterId": line_master_id, "lineRevisionId": line_revision_id,
                "processType": process_type, "quantity": quantity, "quantityExact": str(raw.get("quantityExact") or ("" if quantity is None else str(raw.get("quantity")))),
                "uom": uom, "detail": detail, "provenance": deepcopy(raw.get("provenance") or {"type": "safari_authored", "evidence": {}}),
                "sourcePartName": str(raw.get("sourcePartName") or ""), "fieldStatus": field_status,
                "materialWeightEvidence": deepcopy(material_weight_evidence) if family in {"mcl", "toolshop"} else None,
                "materialConsumptionStatus": "resolved",
                "materialConsumptionResolution": material_basis or None})

        parts_by_stable = {str(_fields(row).get("StablePartId") or ""): row for row in self._rows("ProductPart") if _fields(row).get("StablePartId")}
        current_parts = {str(_fields(row).get("StablePartId") or ""): row for row in self._rows("ProductPart") if _fields(row).get("StablePartId")}
        revisions_by_id = {int(row["id"]): _fields(row) for row in self._rows("PartRevision")}
        existing_components = {str(item.get("componentIdentity")): item for item in current["components"]}
        component_keys: set[str] = set()
        components = []
        seen_child_ids: set[str] = set()
        for index, raw in enumerate(raw_components):
            if not isinstance(raw, dict):
                raise PartIdentityError("PART_COMPONENT_INPUT_INVALID", "Each component must identify a managed child Part.")
            child_id = str(raw.get("childPartId") or "").strip()
            child = parts_by_stable.get(child_id)
            quantity = _num(raw.get("quantity"))
            uom = str(raw.get("uom") or "").strip()
            route = str(raw.get("sourcingRoute") or "auto").casefold()
            identity = str(raw.get("componentIdentity") or f"authored-component:{request_fingerprint([part_id, base_revision_id, request_key, index])}").strip()
            if not child or child_id == part_id or quantity is None or quantity <= 0 or not uom or route not in {"auto", "make", "buy"} or identity in component_keys:
                raise PartIdentityError("PART_COMPONENT_INPUT_INVALID", "Components need a unique occurrence identity, other managed Part, positive quantity, unit and auto/make/buy route.")
            component_keys.add(identity)
            previous = existing_components.get(identity)
            if previous and previous.get("childPartId") != child_id:
                raise PartIdentityError("PART_COMPONENT_IDENTITY_REUSE", "Changing a component identity to a different Part is not allowed; remove the old occurrence and add a distinct reviewed requirement.")
            current_child_revision_id = int(_int_ref(_fields(child).get("CurrentPartRevision")) or 0)
            requested_revision = _int_ref(raw.get("childRevisionId"))
            if previous and requested_revision and requested_revision != _int_ref(previous.get("childRevisionId")):
                raise PartIdentityError("PART_COMPONENT_REVISION_UNAUTHORIZED", "A dependent child revision pin can change only through the reviewed propagation closure.")
            if requested_revision and requested_revision != current_child_revision_id and not previous:
                raise PartIdentityError("PART_COMPONENT_REVISION_STALE", "New component occurrences must pin the current accepted child revision.")
            child_revision_id = _int_ref(previous.get("childRevisionId")) if previous else current_child_revision_id
            if not child_revision_id:
                raise PartIdentityError("PART_COMPONENT_REVISION_UNAVAILABLE", "The selected child Part has no current accepted revision.")
            if _int_ref(revisions_by_id.get(child_revision_id, {}).get("ProductPart")) != int(child["id"]):
                raise PartIdentityError("PART_COMPONENT_REVISION_MISMATCH", "The selected child revision does not belong to the chosen canonical Part.")
            components.append({"componentIdentity": identity, "identityStatus": raw.get("identityStatus") or ("stable" if previous else "safari_authored"),
                "childPartId": child_id, "childPartRecordId": int(child["id"]), "childRevisionId": child_revision_id,
                "quantity": quantity, "uom": uom, "sourcingRoute": route, "status": "active", "provenance": str(raw.get("provenance") or "Safari-authored Part requirement")})
            seen_child_ids.add(child_id)
        # A Stores issue route is a line-level issue instruction. It never
        # creates or implies a separately purchased component requirement.
        adjacency: dict[str, set[str]] = {}
        for row in self._rows("ProductPart"):
            parent_id = str(_fields(row).get("StablePartId") or "")
            if not parent_id:
                continue
            current_revision_id = _int_ref(_fields(row).get("CurrentPartRevision"))
            adjacency[parent_id] = set()
            for component_row in self._rows("PartComponentRevision"):
                fields = _fields(component_row)
                if _int_ref(fields.get("ParentRevision")) != current_revision_id or str(fields.get("ComponentStatus") or fields.get("Status") or "active").casefold() in REMOVED_STATUSES:
                    continue
                child_rev = revisions_by_id.get(_int_ref(fields.get("ChildRevision")) or -1, {})
                child_part_record = _int_ref(child_rev.get("ProductPart"))
                child_part = next((item for item in self._rows("ProductPart") if int(item.get("id", -1)) == child_part_record), None)
                child_stable = str(_fields(child_part).get("StablePartId") or "") if child_part else ""
                if child_stable:
                    adjacency[parent_id].add(child_stable)
        adjacency[part_id] = {item["childPartId"] for item in components}
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise PartIdentityError("PART_COMPONENT_CYCLE", "These requirements would create a recursive current Part composition cycle.")
            if node in visited:
                return
            visiting.add(node)
            for child_id in adjacency.get(node, set()):
                visit(child_id)
            visiting.remove(node)
            visited.add(node)
        for node in adjacency:
            visit(node)

        existing_purchases = {str(item.get("specificationIdentity")): item for item in current["purchases"]}
        purchase_items = {int(row["id"]): row for row in self._rows("PurchaseItem")}
        purchase_ids: set[str] = set()
        purchases = []
        for index, raw in enumerate(raw_purchases):
            if not isinstance(raw, dict):
                raise PartIdentityError("PART_PURCHASE_SPEC_INPUT_INVALID", "Each purchase specification must be an object.")
            identity = str(raw.get("specificationIdentity") or f"authored-specification:{request_fingerprint([part_id, base_revision_id, request_key, index])}").strip()
            item_id = _int_ref(raw.get("purchaseItemId"))
            if identity in purchase_ids or (item_id is not None and item_id not in purchase_items):
                raise PartIdentityError("PART_PURCHASE_IDENTITY_INVALID", "Choose unique stable purchase identities and an existing Safari PurchaseItem.")
            purchase_ids.add(identity)
            old = existing_purchases.get(identity)
            if old and old.get("purchaseItemId") != item_id:
                raise PartIdentityError("PART_PURCHASE_IDENTITY_REUSE", "A purchase specification identity cannot be reassigned to a different canonical PurchaseItem.")
            code, uom, currency = str(raw.get("code") or "").strip(), str(raw.get("costingUOM") or "").strip(), str(raw.get("costingCurrency") or "INR").strip().upper()
            if not code or not uom or not currency:
                raise PartIdentityError("PART_PURCHASE_SPEC_INPUT_INVALID", "Purchase specification code, costing unit and currency are required.")
            purchases.append({"specificationIdentity": identity, "identityStatus": raw.get("identityStatus") or ("stable" if old else "safari_authored"),
                "purchaseItemId": item_id, "code": code, "manufacturer": str(raw.get("manufacturer") or "").strip(),
                "manufacturerPartNumber": str(raw.get("manufacturerPartNumber") or "").strip(), "description": str(raw.get("description") or "").strip(),
                "attributes": deepcopy(raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}), "costingUOM": uom,
                "costingCurrency": currency, "status": "active"})
        return {"applicability": applicability, "requirements": requirements, "components": components, "purchases": purchases,
            "finalizeOnPublication": bool(payload.get("finalizeOnPublication", current.get("finalizeOnPublication", False))),
            "baseRevisionId": base_revision_id, "partId": part_id}

    @staticmethod
    def _same_requirement_physical_content(raw: dict[str, Any], existing: dict[str, Any], detail: dict[str, Any]) -> bool:
        """Allow malformed legacy evidence only when the whole physical line is unchanged."""
        if _int_ref(raw.get("lineRevisionId")) != _int_ref(existing.get("lineRevisionId")):
            return False
        if str(raw.get("family") or "").strip().casefold() != str(existing.get("family") or "").strip().casefold():
            return False
        if str(raw.get("processType") or existing.get("processType") or "") != str(existing.get("processType") or ""):
            return False
        raw_quantity, old_quantity = _num(raw.get("quantity"), allow_blank=True), _num(existing.get("quantity"), allow_blank=True)
        if raw_quantity != old_quantity or str(raw.get("uom") or "").strip() != str(existing.get("uom") or "").strip():
            return False
        old_detail = existing.get("detail") if isinstance(existing.get("detail"), dict) else {}
        numeric_fields = {"Quantity", "DimensionMM", "DimensionInches", "WeightGrams", "WeightKg", "PartWeightKg",
            "Length", "Width", "Thickness", "InternalMakingCost", "ExternalMachiningCost", "RateCached", "CostCached"}
        reference_fields = {"Material", "PurchaseItem", "ProcessOperation", "WorkCenter"}
        for name in LINE_DETAIL_FIELDS:
            new_value, old_value = detail.get(name), old_detail.get(name)
            if new_value in (None, "") and old_value in (None, ""):
                continue
            if name in numeric_fields:
                new_number, old_number = _num(new_value), _num(old_value)
                if new_number is None or old_number is None:
                    if new_value != old_value:
                        return False
                elif new_number != old_number:
                    return False
            elif name in reference_fields:
                new_ref, old_ref = _int_ref(new_value), _int_ref(old_value)
                if new_ref is None or old_ref is None:
                    if new_value != old_value:
                        return False
                elif new_ref != old_ref:
                    return False
            elif name == "EngineeringAttributes":
                if _stable(_json_cell(new_value) or {}) != _stable(_json_cell(old_value) or {}):
                    return False
            elif new_value != old_value:
                return False
        return True

    @_serialized
    def save_draft(self, part_id: str, payload: dict[str, Any], *, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        self._require_publication_schema()
        if not isinstance(payload, dict):
            raise PartIdentityError("PART_DEFINITION_INPUT_INVALID", "A complete Part definition object is required.")
        part_row = self._part_row(part_id)
        current = self._current_revision(part_id)
        self._check_established(current)
        expected_save_version = payload.get("expectedSaveVersion")
        if isinstance(expected_save_version, bool) or not isinstance(expected_save_version, int) or expected_save_version < 0:
            raise PartIdentityError("PART_DEFINITION_SAVE_VERSION_REQUIRED", "Include the saved draft version you reviewed; use 0 when no draft exists.")
        base_revision_id = _int_ref(payload.get("baseRevisionId"))
        if base_revision_id != int(current["id"]):
            raise PartIdentityError("PART_DEFINITION_STALE_BASE", "The Part's accepted revision changed. Reload its current definition before saving a draft.")
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_DEFINITION_INPUT_REQUIRED", "A reason, trusted actor and idempotency key are required to save a draft.")
        content = self._normalize_content(part_id, base_revision_id, payload, request_key)
        content_fingerprint = request_fingerprint(content)
        canonical_draft_key = f"part-definition-draft:{part_id}:{base_revision_id}"
        requested_draft_key = str(payload.get("draftKey") or "").strip()
        draft_key = requested_draft_key or canonical_draft_key
        reopened_prefix = canonical_draft_key + ":reopened:"
        if draft_key != canonical_draft_key and not draft_key.startswith(reopened_prefix):
            raise PartIdentityError("PART_DEFINITION_DRAFT_REQUIRED", "The selected saved draft does not belong to this Part and accepted revision.")
        operation_fingerprint = request_fingerprint([part_id, base_revision_id, expected_save_version,
            content_fingerprint, actor.strip(), reason.strip()]) if draft_key == canonical_draft_key else request_fingerprint(
                [part_id, draft_key, base_revision_id, expected_save_version, content_fingerprint, actor.strip(), reason.strip()])
        draft = self._find("PartDefinitionDraft", "DraftKey", draft_key)
        if draft and (_int_ref(_fields(draft).get("ProductPart")) != int(part_row["id"])
                or _int_ref(_fields(draft).get("BaseRevision")) != base_revision_id):
            raise PartIdentityError("PART_DEFINITION_DRAFT_REQUIRED", "The selected saved draft does not belong to this Part and accepted revision.")
        if requested_draft_key and draft is None and draft_key != canonical_draft_key:
            raise PartIdentityError("PART_DEFINITION_DRAFT_REQUIRED", "Reopen the stale Change Request before saving its retained content.")
        prior_request = self._find("PartRegistryRequest", "RequestKey", request_key)
        if prior_request:
            request_fields = _fields(prior_request)
            if (request_fields.get("RequestFingerprint") != operation_fingerprint
                    or request_fields.get("RequestType") != "part_definition_draft_save"):
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different draft content or generation.")
            old_result = self._request_result(prior_request)
            if old_result is not None:
                current_version = int(_fields(draft).get("SaveVersion") or 0) if draft else 0
                if current_version > int(old_result.get("saveVersion") or 0):
                    raise PartIdentityError("PART_DEFINITION_DRAFT_REPLAY_SUPERSEDED", "This completed draft save was superseded by a newer draft. Reload the current version before continuing.")
                return {**old_result, "idempotent": True}
            draft_fields = _fields(draft)
            if (draft and draft_fields.get("RequestKey") == request_key
                    and draft_fields.get("RequestFingerprint") == operation_fingerprint
                    and int(draft_fields.get("SaveVersion") or 0) == expected_save_version + 1
                    and draft_fields.get("ContentFingerprint") == content_fingerprint):
                recovered = {"draftKey": draft_key, "saveVersion": expected_save_version + 1, "baseRevisionId": base_revision_id,
                    "contentFingerprint": content_fingerprint, "content": content, "status": "saved", "idempotent": True}
                self.registry._complete_request(prior_request, recovered)
                return recovered
        if draft:
            fields = _fields(draft)
            status = str(fields.get("Status") or "open").casefold()
            if status != "open":
                raise PartIdentityError("PART_DEFINITION_DRAFT_IMMUTABLE", "A submitted or published draft is immutable; create a new reviewed draft after its Change Request is resolved.")
            current_save_version = int(fields.get("SaveVersion") or 0)
        else:
            current_save_version = 0
        if expected_save_version != current_save_version:
            raise PartIdentityError("PART_DEFINITION_DRAFT_STALE", f"This draft is version {current_save_version}; reload before saving against the current version.")
        request = prior_request or self.registry._begin_request(request_key, "part_definition_draft_save", operation_fingerprint, part_id)
        for old_draft in self._rows("PartDefinitionDraft"):
            old_fields = _fields(old_draft)
            if (_int_ref(old_fields.get("ProductPart")) == int(part_row["id"])
                    and str(old_fields.get("Status") or "open").casefold() == "open"
                    and _int_ref(old_fields.get("BaseRevision")) != base_revision_id):
                self.client.update_table_records("PartDefinitionDraft", [{"id": int(old_draft["id"]), "fields": {
                    "Status": "stale", "UpdatedAt": grist_datetime(utc_now()), "RequestKey": request_key,
                    "RequestFingerprint": request_fingerprint([old_fields.get("DraftKey"), "stale", base_revision_id])}}])
        now = grist_datetime(utc_now())
        if draft:
            fields = _fields(draft)
            if _int_ref(fields.get("BaseRevision")) != base_revision_id:
                raise PartIdentityError("PART_DEFINITION_STALE_BASE", "The saved draft belongs to another accepted Part revision.")
            save_version = current_save_version + 1
            self.client.update_table_records("PartDefinitionDraft", [{"id": int(draft["id"]), "fields": {
                "SaveVersion": save_version, "Status": "open", "Content": canonical_grist_json_cell(content),
                "ContentFingerprint": content_fingerprint, "Actor": actor.strip(), "Reason": reason.strip(),
                "UpdatedAt": now, "RequestKey": request_key, "RequestFingerprint": operation_fingerprint}}])
        else:
            save_version = 1
            draft = self.registry._ensure_keyed("PartDefinitionDraft", "DraftKey", draft_key, {
                "DraftKey": draft_key, "ProductPart": int(part_row["id"]), "BaseRevision": base_revision_id,
                "SaveVersion": save_version, "Status": "open", "Content": canonical_grist_json_cell(content),
                "ContentFingerprint": content_fingerprint, "Actor": actor.strip(), "Reason": reason.strip(),
                "CreatedAt": now, "UpdatedAt": now, "RequestKey": request_key, "RequestFingerprint": operation_fingerprint})
        result = {"draftKey": draft_key, "saveVersion": save_version, "baseRevisionId": base_revision_id,
            "contentFingerprint": content_fingerprint, "content": content, "status": "saved", "idempotent": False}
        self.registry._complete_request(request, result)
        return result

    @_serialized
    def reopen_stale_change_request(self, part_id: str, change_request_key: str, *, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        self._require_publication_schema()
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_CHANGE_REQUEST_REOPEN_INPUT_REQUIRED", "A reopen reason, trusted actor and idempotency key are required.")
        cr = self._find("PartEngineeringChangeRequest", "ChangeRequestKey", change_request_key)
        if not cr or _int_ref(_fields(cr).get("ProductPart")) != int(self._part_row(part_id)["id"]):
            raise PartIdentityError("PART_CHANGE_REQUEST_NOT_FOUND", "The stale Change Request does not belong to this Part.")
        fields = _fields(cr)
        if str(fields.get("Status") or "").casefold() != "stale":
            raise PartIdentityError("PART_CHANGE_REQUEST_STATE_INVALID", "Only a stale Change Request can be reopened as a new draft.")
        reopened_ref = _int_ref(fields.get("ReopenedDraft"))
        linked_draft = next((row for row in self._rows("PartDefinitionDraft")
            if reopened_ref and int(row.get("id", -1)) == reopened_ref), None)
        reopened_drafts = [row for row in self._rows("PartDefinitionDraft")
            if _int_ref(_fields(row).get("ReopenedFromChangeRequest")) == int(cr["id"])]
        if reopened_ref and not linked_draft:
            raise PartIdentityError("PART_CHANGE_REQUEST_REOPEN_INTEGRITY", "The stale Change Request's recorded reopened draft is missing.")
        existing_reopened = linked_draft or (reopened_drafts[0] if reopened_drafts else None)
        if existing_reopened and str(_fields(existing_reopened).get("RequestKey") or "") != request_key:
            raise PartIdentityError("PART_CHANGE_REQUEST_ALREADY_REOPENED", "This stale Change Request already has a reopened draft; reload it before continuing.")
        operation_fingerprint = request_fingerprint([part_id, change_request_key, actor.strip(), reason.strip()])
        request = self.registry._begin_request(request_key, "part_definition_change_request_reopen", operation_fingerprint, change_request_key)
        old_result = self._request_result(request)
        if old_result is not None:
            return {**old_result, "idempotent": True}
        old_draft_id = _int_ref(fields.get("Draft"))
        old_draft = next((row for row in self._rows("PartDefinitionDraft") if int(row.get("id", -1)) == old_draft_id), None)
        after = _json_cell(fields.get("AfterDefinition"))
        if not old_draft or not after:
            raise PartIdentityError("PART_CHANGE_REQUEST_INTEGRITY", "The stale Change Request is missing its immutable submitted draft evidence.")
        old_fields = _fields(old_draft)
        submitted_content = _json_cell(old_fields.get("Content"))
        submitted_fingerprint = str(old_fields.get("ContentFingerprint") or "")
        if (not submitted_content or not submitted_fingerprint
                or request_fingerprint(submitted_content) != submitted_fingerprint
                or request_fingerprint(after) != submitted_fingerprint):
            raise PartIdentityError("PART_CHANGE_REQUEST_INTEGRITY", "The immutable submitted definition no longer matches its saved draft fingerprint.")

        current = self._current_revision(part_id)
        base_revision_id = int(current["id"])
        retained = deepcopy(after)
        retained["baseRevisionId"] = base_revision_id
        content = self._normalize_content(part_id, base_revision_id, retained, request_key)
        content_fingerprint = request_fingerprint(content)
        draft_key = f"part-definition-draft:{part_id}:{base_revision_id}:reopened:{request_fingerprint(change_request_key)}"
        reopened = self._find("PartDefinitionDraft", "DraftKey", draft_key)
        if reopened:
            reopened_fields = _fields(reopened)
            if (_int_ref(reopened_fields.get("ProductPart")) != int(self._part_row(part_id)["id"])
                    or _int_ref(reopened_fields.get("BaseRevision")) != base_revision_id
                    or _int_ref(reopened_fields.get("SupersedesDraft")) != old_draft_id
                    or _int_ref(reopened_fields.get("ReopenedFromChangeRequest")) != int(cr["id"])
                    or request_fingerprint(_json_cell(reopened_fields.get("Content")) or {}) != str(reopened_fields.get("ContentFingerprint") or "")):
                raise PartIdentityError("PART_DEFINITION_DRAFT_INTEGRITY", "The reopened draft does not match its stale Change Request lineage.")
            if not _int_ref(fields.get("ReopenedDraft")) and str(reopened_fields.get("RequestKey") or "") != request_key:
                raise PartIdentityError("PART_CHANGE_REQUEST_ALREADY_REOPENED", "This stale Change Request already has a reopened draft; reload it before continuing.")
        else:
            if _int_ref(fields.get("ReopenedDraft")):
                raise PartIdentityError("PART_CHANGE_REQUEST_REOPEN_INTEGRITY", "The stale Change Request's recorded reopened draft is missing.")
            reopened = self.registry._ensure_keyed("PartDefinitionDraft", "DraftKey", draft_key, {
                "DraftKey": draft_key, "ProductPart": int(self._part_row(part_id)["id"]), "BaseRevision": base_revision_id,
                "SupersedesDraft": old_draft_id, "ReopenedFromChangeRequest": int(cr["id"]),
                "SaveVersion": 1, "Status": "open", "Content": canonical_grist_json_cell(content),
                "ContentFingerprint": content_fingerprint, "Actor": actor.strip(), "Reason": reason.strip(),
                "CreatedAt": grist_datetime(utc_now()), "UpdatedAt": grist_datetime(utc_now()),
                "RequestKey": request_key, "RequestFingerprint": operation_fingerprint})
        if not _int_ref(fields.get("ReopenedDraft")):
            self.client.update_table_records("PartEngineeringChangeRequest", [{"id": int(cr["id"]), "fields": {"ReopenedDraft": int(reopened["id"])}}])
        audit_key = f"part-change-request-reopen:{request_fingerprint(change_request_key)}"
        self.registry._ensure_keyed("AuditEvent", "RequestKey", audit_key, {
            "EventType": "part_definition_change_request_reopened", "Actor": actor.strip(), "OccurredAt": grist_datetime(utc_now()),
            "EntityType": "PartEngineeringChangeRequest", "EntityId": str(cr["id"]), "Reason": reason.strip(),
            "Payload": canonical_grist_json_cell({"changeRequestKey": change_request_key, "submittedDraftId": old_draft_id,
                "reopenedDraftKey": draft_key, "submittedContentFingerprint": submitted_fingerprint,
                "reopenedContentFingerprint": request_fingerprint(_json_cell(_fields(reopened).get("Content")) or {})}),
            "RequestKey": audit_key, "RequestFingerprint": request_fingerprint([change_request_key, draft_key, reason.strip()])})
        reopened_fields = _fields(reopened)
        result = {"changeRequestKey": change_request_key, "draftKey": draft_key,
            "saveVersion": int(reopened_fields.get("SaveVersion") or 1), "baseRevisionId": base_revision_id,
            "content": _json_cell(reopened_fields.get("Content")) or content, "status": str(reopened_fields.get("Status") or "open"),
            "idempotent": bool(_int_ref(fields.get("ReopenedDraft")))}
        self.registry._complete_request(request, result)
        return result

    def _request_result(self, request: dict[str, Any]) -> dict[str, Any] | None:
        fields = _fields(request)
        if str(fields.get("Status") or "").casefold() != "published":
            return None
        value = fields.get("Result")
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _current_graph(self) -> dict[str, Any]:
        parts = self._rows("ProductPart")
        revisions = self._rows("PartRevision")
        current_by_part = {int(row["id"]): _int_ref(_fields(row).get("CurrentPartRevision")) for row in parts}
        part_by_record = {int(row["id"]): _fields(row) for row in parts}
        part_by_stable = {str(_fields(row).get("StablePartId") or ""): row for row in parts if _fields(row).get("StablePartId")}
        revision_by_id = {int(row["id"]): _fields(row) for row in revisions}
        current_by_stable = {str(_fields(row).get("StablePartId") or ""): current_by_part.get(int(row["id"])) for row in parts if _fields(row).get("StablePartId")}
        edges = []
        for row in self._rows("PartComponentRevision"):
            fields = _fields(row)
            if str(fields.get("ComponentStatus") or fields.get("Status") or "active").casefold() in REMOVED_STATUSES:
                continue
            parent_revision = _int_ref(fields.get("ParentRevision"))
            child_revision = _int_ref(fields.get("ChildRevision"))
            parent_fields = revision_by_id.get(parent_revision or -1, {})
            child_fields = revision_by_id.get(child_revision or -1, {})
            parent_part_id, child_part_id = _int_ref(parent_fields.get("ProductPart")), _int_ref(child_fields.get("ProductPart"))
            if not parent_part_id or not child_part_id:
                continue
            parent_part, child_part = part_by_record.get(parent_part_id, {}), part_by_record.get(child_part_id, {})
            edges.append({"rowId": int(row["id"]), "parentPartRecordId": parent_part_id, "parentPartId": str(parent_part.get("StablePartId") or ""),
                "parentRevisionId": parent_revision, "childPartRecordId": child_part_id, "childPartId": str(child_part.get("StablePartId") or ""),
                "childRevisionId": child_revision, "quantity": fields.get("Quantity"), "uom": fields.get("QuantityUOM"),
                "identity": fields.get("ComponentIdentity"), "route": fields.get("SourcingRoute"), "isCurrentParent": current_by_part.get(parent_part_id) == parent_revision,
                "isCurrentChildPin": current_by_part.get(child_part_id) == child_revision,
                "parentKind": str(parent_part.get("IdentityKind") or "part").casefold()})
        configs = self._rows("CostingConfiguration")
        config_revisions = self._rows("CostingConfigurationRevision")
        selections = self._rows("ConfigurationPartSelection")
        codes = {int(row["id"]): _fields(row) for row in self._rows("ProductModelCode")}
        active_configs_by_code: dict[int, list[dict[str, Any]]] = {}
        for row in configs:
            config_fields = _fields(row)
            code_id = _int_ref(config_fields.get("ProductModelCode"))
            if code_id and str(config_fields.get("Status") or "active").casefold() == "active":
                active_configs_by_code.setdefault(code_id, []).append(row)
        config_by_code = {code_id: rows[0] for code_id, rows in active_configs_by_code.items()}
        revision_by_config = {int(row["id"]): row for row in config_revisions}
        current_configs = []
        for code_id, config in config_by_code.items():
            if not code_id:
                continue
            config_fields = _fields(config)
            current_revision_id = _int_ref(config_fields.get("CurrentRevision"))
            revision = revision_by_config.get(current_revision_id or -1)
            if not revision or str(_fields(revision).get("Status") or "").casefold() != "published":
                continue
            for selection in selections:
                fields = _fields(selection)
                if _int_ref(fields.get("ConfigurationRevision")) != current_revision_id or str(fields.get("Status") or "active").casefold() in REMOVED_STATUSES:
                    continue
                part_record_id = _int_ref(fields.get("ProductPart"))
                if not part_record_id:
                    continue
                part_fields = part_by_record.get(part_record_id, {})
                current_configs.append({"codeRecordId": code_id, "code": codes.get(code_id, {}).get("Code") or "", "configRecordId": int(config["id"]),
                    "configRevisionId": current_revision_id, "selectionRecordId": int(selection["id"]),
                    "selectionIdentity": fields.get("SelectionIdentity") or fields.get("SelectionKey"),
                    "partRecordId": part_record_id, "partId": str(part_fields.get("StablePartId") or ""),
                    "partRevisionId": _int_ref(fields.get("PartRevision")) or current_by_part.get(part_record_id),
                    "quantity": fields.get("Quantity"), "uom": fields.get("QuantityUOM"), "route": fields.get("SourcingRoute"),
                    "label": fields.get("OccurrenceLabel"), "optionGroup": fields.get("OptionGroup"),
                    "configurationRevisionNumber": _fields(revision).get("Revision")})
        generation = request_fingerprint({"parts": [(int(row["id"]), _int_ref(_fields(row).get("CurrentPartRevision"))) for row in parts],
            "components": [{k: edge[k] for k in ("rowId", "parentRevisionId", "childRevisionId", "quantity", "uom", "identity")} for edge in edges],
            "configurations": current_configs})
        return {"parts": parts, "partByRecord": part_by_record, "partByStable": part_by_stable, "currentByPart": current_by_part,
            "currentByStable": current_by_stable, "revisionById": revision_by_id, "edges": edges,
            "configs": current_configs, "configRows": configs, "configRevisionRows": config_revisions, "selectionRows": selections,
            "activeConfigConflicts": {code_id: [int(row["id"]) for row in rows]
                for code_id, rows in active_configs_by_code.items() if len(rows) > 1}, "generation": generation}

    def impact_preview(self, part_id: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self._current_revision(part_id)
        graph = self._current_graph()
        target = self._part_row(part_id)
        target_record = int(target["id"])
        target_revision = int(current["id"])
        parent_paths: dict[int, dict[str, tuple[str, ...]]] = {}
        parent_edge_paths: dict[int, dict[str, tuple[int, ...]]] = {}
        edges_by_id = {edge["rowId"]: edge for edge in graph["edges"]}
        parent_order: list[int] = []
        unknown = []
        for code_record_id, config_ids in graph.get("activeConfigConflicts", {}).items():
            unknown.append({"code": "ACTIVE_CONFIGURATION_DUPLICATE", "modelCodeRecordId": code_record_id,
                "configurationRecordIds": config_ids})
        path_queue: list[tuple[int, tuple[str, ...], tuple[int, ...]]] = [(target_record, (part_id,), ())]
        seen_paths: set[tuple[int, tuple[str, ...], tuple[int, ...]]] = set()
        while path_queue:
            child_record, path, edge_path = path_queue.pop(0)
            path_state = (child_record, path, edge_path)
            if path_state in seen_paths:
                continue
            seen_paths.add(path_state)
            child_current_revision = graph["currentByPart"].get(child_record)
            for edge in graph["edges"]:
                if (not edge["isCurrentParent"] or edge["childPartRecordId"] != child_record
                        or edge["childRevisionId"] != child_current_revision):
                    continue
                parent_record = edge["parentPartRecordId"]
                parent_path = (str(edge["parentPartId"]), *path)
                next_edge_path = (*edge_path, int(edge["rowId"]))
                if str(edge["parentPartId"]) in path:
                    unknown.append({"code": "DEPENDENCY_GRAPH_CYCLE", "partId": edge["parentPartId"], "path": list(parent_path)})
                    continue
                if edge["parentKind"] in {"part", "assembly"}:
                    path_signature = _stable([[(edges_by_id[edge_id].get("parentPartId"), edges_by_id[edge_id].get("identity") or edges_by_id[edge_id].get("rowId"), edges_by_id[edge_id].get("childPartId")) for edge_id in next_edge_path]])
                    parent_paths.setdefault(parent_record, {})[path_signature] = parent_path
                    parent_edge_paths.setdefault(parent_record, {})[path_signature] = next_edge_path
                    if not edge.get("identity"):
                        unknown.append({"code": "DEPENDENCY_EDGE_IDENTITY_UNCLASSIFIED", "parentPartId": edge["parentPartId"],
                            "childPartId": edge["childPartId"], "componentRecordId": edge["rowId"], "path": list(parent_path)})
                    if parent_record not in parent_order:
                        parent_order.append(parent_record)
                    path_queue.append((parent_record, parent_path, next_edge_path))
                else:
                    unknown.append({"code": "NON_ASSEMBLY_COMPOSITE_COVERAGE_UNSUPPORTED", "parentPartId": edge["parentPartId"],
                        "parentKind": edge["parentKind"], "path": list(parent_path)})
        affected_parts = []
        proposed_labels: dict[int, str | None] = {target_record: None}
        for record_id in parent_order:
            paths = parent_paths[record_id]
            row = next((item for item in graph["parts"] if int(item["id"]) == record_id), None)
            stable_id = str(_fields(row).get("StablePartId") or "") if row else ""
            revision_id = graph["currentByPart"].get(record_id)
            revision = graph["revisionById"].get(revision_id or -1, {})
            label = _fields(revision).get("RevisionLabel") or _fields(revision).get("Revision")
            parent_kind = str(_fields(row).get("IdentityKind") or "part").casefold() if row else "part"
            try:
                proposed_label = _next_revision_label(label or "A")
            except PartIdentityError:
                proposed_label = None
                unknown.append({"code": "UNCLASSIFIED_DEPENDENT_REVISION", "partId": stable_id, "revisionLabel": label})
            proposed_labels[record_id] = proposed_label
            affected_parts.append({"partRecordId": record_id, "partId": stable_id, "partNumber": _fields(row).get("PartNumber") if row else "",
                "currentRevisionId": revision_id, "currentRevisionLabel": label, "proposedRevisionLabel": proposed_label,
                "kind": parent_kind, "paths": [list(path) for _, path in sorted(paths.items())], "status": f"current_{parent_kind}"})
        target_revision_fields = _fields(current)
        try:
            proposed_target_label = _next_revision_label(target_revision_fields.get("RevisionLabel") or target_revision_fields.get("Revision") or "A")
        except PartIdentityError:
            proposed_target_label = None
            unknown.append({"code": "UNCLASSIFIED_TARGET_REVISION", "partId": part_id, "revisionLabel": target_revision_fields.get("RevisionLabel") or target_revision_fields.get("Revision")})
        proposed_labels[target_record] = proposed_target_label
        for parent in affected_parts:
            details = []
            for signature, edge_ids in sorted(parent_edge_paths.get(parent["partRecordId"], {}).items()):
                path_details = []
                for edge_id in reversed(edge_ids):
                    edge = edges_by_id[edge_id]
                    parent_revision_fields = graph["revisionById"].get(edge["parentRevisionId"] or -1, {})
                    child_revision_fields = graph["revisionById"].get(edge["childRevisionId"] or -1, {})
                    path_details.append({"componentIdentity": edge.get("identity"), "componentRecordId": edge["rowId"],
                        "parentPartId": edge["parentPartId"], "parentRevisionId": edge["parentRevisionId"],
                        "parentRevisionLabel": _fields(parent_revision_fields).get("RevisionLabel") or _fields(parent_revision_fields).get("Revision"),
                        "proposedParentRevisionLabel": proposed_labels.get(edge["parentPartRecordId"]),
                        "childPartId": edge["childPartId"], "pinnedChildRevisionId": edge["childRevisionId"],
                        "pinnedChildRevisionLabel": _fields(child_revision_fields).get("RevisionLabel") or _fields(child_revision_fields).get("Revision"),
                        "proposedChildRevisionLabel": proposed_labels.get(edge["childPartRecordId"]),
                        "quantity": edge.get("quantity"), "uom": edge.get("uom"), "sourcingRoute": edge.get("route")})
                details.append({"partPath": parent_paths[parent["partRecordId"]][signature], "edges": path_details})
            parent["pathDetails"] = details
        changed_part_records = {target_record, *parent_paths.keys()}
        code_uses = []
        for item in graph["configs"]:
            if item["partRecordId"] in changed_part_records:
                proposed_label = proposed_labels.get(item["partRecordId"])
                dependency_paths = ([list(path) for _, path in sorted(parent_paths.get(item["partRecordId"], {}).items())]
                    if item["partRecordId"] != target_record else [[part_id]])
                if not item.get("selectionIdentity"):
                    unknown.append({"code": "CONFIGURATION_SELECTION_IDENTITY_UNCLASSIFIED",
                        "modelCodeRecordId": item["codeRecordId"], "selectionRecordId": item["selectionRecordId"]})
                current_config_revision = item.get("configurationRevisionNumber")
                code_uses.append({"modelCodeRecordId": item["codeRecordId"], "modelCode": item["code"], "configurationRecordId": item["configRecordId"],
                    "configurationRevisionId": item["configRevisionId"], "selectionRecordId": item["selectionRecordId"],
                    "currentConfigurationRevision": current_config_revision,
                    "proposedConfigurationRevision": int(current_config_revision or 0) + 1,
                    "selectionIdentity": item["selectionIdentity"], "partId": item["partId"], "partRevisionId": item["partRevisionId"],
                    "proposedPartRevisionLabel": proposed_label, "quantity": item["quantity"],
                    "uom": item["uom"], "sourcingRoute": item["route"], "label": item["label"], "optionGroup": item["optionGroup"],
                    "dependencyPaths": dependency_paths})
        for parent in affected_parts:
            if parent["status"] not in {"current_part", "current_assembly"}:
                continue
            parent_revision_fields = graph["revisionById"].get(parent["currentRevisionId"] or -1, {})
            if str(parent_revision_fields.get("BaselineStatus") or "").casefold() == "finalized" and content and not content.get("finalizeOnPublication") and str(target_revision_fields.get("BaselineStatus") or "").casefold() != "finalized":
                unknown.append({"code": "FINALIZED_DEPENDENT_DEPENDS_ON_UNFINALIZED_PART", "partId": parent["partId"],
                    "kind": parent["kind"], "message": "Finalize the changed Part in the approved CR before repinning this finalized dependent definition."})
        assemblies = [item for item in affected_parts if item["kind"] == "assembly"]
        return {"status": "complete_with_gaps" if unknown else "complete", "target": {"partId": part_id, "partRecordId": target_record,
                "currentRevisionId": target_revision, "currentRevisionLabel": target_revision_fields.get("RevisionLabel") or target_revision_fields.get("Revision"),
                "proposedRevisionLabel": proposed_target_label},
            "assemblies": assemblies, "dependents": affected_parts, "modelCodeConfigurations": code_uses,
            "spares": {"status": "unsupported_by_b2_p1", "items": [], "message": "B2 spare-definition composition propagation is reserved for the later B2 composition package."},
            "unknown": unknown, "dependencyGeneration": graph["generation"],
            "fingerprint": request_fingerprint({"target": target_revision, "assemblies": assemblies, "dependents": affected_parts,
                "codes": code_uses, "unknown": unknown, "generation": graph["generation"]})}

    @_serialized
    def submit_change_request(self, part_id: str, payload: dict[str, Any], *, actor: str, reason: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        self._require_publication_schema()
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_CHANGE_REQUEST_INPUT_REQUIRED", "A change reason, trusted actor and idempotency key are required.")
        draft_key = str(payload.get("draftKey") or "").strip()
        prior = self._find("PartRegistryRequest", "RequestKey", request_key)
        cr_key = f"part-change-request:{part_id}:{request_key}"
        cr = self._find("PartEngineeringChangeRequest", "ChangeRequestKey", cr_key)
        if prior and (_fields(prior).get("RequestType") != "part_definition_change_request" or _fields(prior).get("EntityUUID") != part_id):
            raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different data.")
        frozen = _json_cell(_fields(prior).get("Payload")) if prior else None
        if prior and not cr and isinstance(frozen, dict) and frozen.get("kind") == "part-definition-submission-v1":
            draft = self._find("PartDefinitionDraft", "DraftKey", draft_key)
            saved = frozen.get("result") or {}
            expected = request_fingerprint([part_id, draft_key, saved.get("baseRevisionId"), request_fingerprint(saved.get("after")), reason.strip(), (saved.get("impact") or {}).get("fingerprint"), actor.strip()])
            if (frozen.get("partId") != part_id or frozen.get("draftKey") != draft_key
                    or frozen.get("actor") != actor.strip() or frozen.get("reason") != reason.strip()
                    or not draft or _int_ref(frozen.get("draftId")) != int(draft["id"])
                    or _int_ref(_fields(draft).get("ProductPart")) != int(self._part_row(part_id)["id"])
                    or str(_fields(draft).get("Status") or "open").casefold() != "open"
                    or not saved.get("before") or not saved.get("after") or not saved.get("impact")
                    or request_fingerprint(_json_cell(_fields(draft).get("Content"))) != request_fingerprint(saved.get("after"))
                    or saved.get("requestFingerprint") != expected or expected != _fields(prior).get("RequestFingerprint")):
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different data.")
            cr = self.registry._ensure_keyed("PartEngineeringChangeRequest", "ChangeRequestKey", cr_key, {
                "ChangeRequestKey": cr_key, "ProductPart": int(self._part_row(part_id)["id"]), "Draft": int(draft["id"]),
                "BaseRevision": saved["baseRevisionId"], "Status": "pending_approval", "Reason": reason.strip(),
                "BeforeDefinition": canonical_grist_json_cell(saved["before"]), "AfterDefinition": canonical_grist_json_cell(saved["after"]),
                "ImpactPreview": canonical_grist_json_cell(saved["impact"]), "ImpactFingerprint": saved["impact"]["fingerprint"],
                "InitiatedBy": actor.strip(), "CreatedAt": frozen["createdAt"], "RequestKey": request_key,
                "RequestFingerprint": saved["requestFingerprint"]})
        if cr:
            f = _fields(cr)
            draft = self._find("PartDefinitionDraft", "DraftKey", draft_key)
            content = _json_cell(_fields(draft).get("Content")) if draft else None
            after = _json_cell(f.get("AfterDefinition"))
            before = _json_cell(f.get("BeforeDefinition"))
            impact = _json_cell(f.get("ImpactPreview"))
            fingerprint = request_fingerprint([part_id, draft_key, _int_ref(f.get("BaseRevision")), request_fingerprint(after), reason.strip(), (impact or {}).get("fingerprint"), actor.strip()])
            if (not prior or not draft or _int_ref(f.get("Draft")) != int(draft["id"])
                    or _int_ref(f.get("ProductPart")) != int(self._part_row(part_id)["id"])
                    or f.get("Reason") != reason.strip() or f.get("InitiatedBy") != actor.strip()
                    or not after or not before or not impact or request_fingerprint(content) != request_fingerprint(after)
                    or _fields(prior).get("RequestFingerprint") != fingerprint):
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This request key was already used with different data.")
            result = self._request_result(prior)
            captured = {"changeRequestKey": cr_key, "status": "pending_approval", "baseRevisionId": _int_ref(f.get("BaseRevision")), "reason": reason.strip(), "before": before, "after": after, "impact": impact, "requestFingerprint": fingerprint}
            if isinstance(frozen, dict) and frozen.get("kind") == "part-definition-submission-v1" and request_fingerprint(frozen.get("result")) != request_fingerprint(captured):
                raise PartIdentityError("PART_REQUEST_CONFLICT", "The submitted Change Request differs from its frozen journal.")
            if result is not None:
                if request_fingerprint(result) != request_fingerprint(captured):
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "The submitted Change Request differs from its recorded result.")
                return result
            if str(_fields(draft).get("Status") or "open").casefold() == "open":
                self.client.update_table_records("PartDefinitionDraft", [{"id": int(draft["id"]), "fields": {"Status": "submitted", "UpdatedAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": fingerprint}}])
            elif _fields(draft).get("RequestKey") != request_key or _fields(draft).get("RequestFingerprint") != fingerprint:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "The submitted draft belongs to another request.")
            result = captured
            self.registry._complete_request(prior, result)
            return result
        current = self._current_revision(part_id)
        self._check_established(current)
        draft = self._find("PartDefinitionDraft", "DraftKey", draft_key)
        if not draft or _int_ref(_fields(draft).get("ProductPart")) != int(self._part_row(part_id)["id"]):
            raise PartIdentityError("PART_DEFINITION_DRAFT_REQUIRED", "Save this complete Part definition as a Grist draft before requesting approval.")
        draft_fields = _fields(draft)
        if str(draft_fields.get("Status") or "open").casefold() != "open":
            raise PartIdentityError("PART_DEFINITION_DRAFT_IMMUTABLE", "Only an open saved draft can be submitted; reopen a stale Change Request to review a new draft.")
        base_id = _int_ref(draft_fields.get("BaseRevision"))
        if base_id != int(current["id"]):
            raise PartIdentityError("PART_DEFINITION_STALE_BASE", "The accepted Part revision changed after this draft was started. Reload and rebase before review.")
        content = _json_cell(draft_fields.get("Content"))
        if not content or request_fingerprint(content) != draft_fields.get("ContentFingerprint"):
            raise PartIdentityError("PART_DEFINITION_DRAFT_INTEGRITY", "The saved draft does not match its recorded content fingerprint.")
        revalidated_content = self._normalize_content(part_id, int(base_id), content, request_key)
        comparable_content = deepcopy(content)
        normalized_by_key = {str(item.get("requirementKey")): item for item in revalidated_content["requirements"]}
        for item in comparable_content.get("requirements", []):
            normalized = normalized_by_key.get(str(item.get("requirementKey"))) or {}
            for field in ("lineMasterId", "lineRevisionId"):
                if item.get(field) in (None, "") and normalized.get(field) is not None:
                    item[field] = normalized[field]
        if request_fingerprint(revalidated_content) != request_fingerprint(comparable_content):
            raise PartIdentityError("PART_DEFINITION_DRAFT_INVALID", "The saved draft no longer passes current Part-definition validation. Reopen and save a corrected draft.")
        if not actor.strip() or not reason.strip() or not request_key.strip():
            raise PartIdentityError("PART_CHANGE_REQUEST_INPUT_REQUIRED", "A change reason, trusted actor and idempotency key are required.")
        before = self._definition_from_rows(part_id, int(current["id"]))
        impact = self.impact_preview(part_id, content)
        if impact["unknown"]:
            raise PartIdentityError("PART_CHANGE_REQUEST_IMPACT_BLOCKED", "The dependency preview is incomplete or has an unsupported path. Resolve every listed issue before submitting this Change Request.")
        operation_fingerprint = request_fingerprint([part_id, draft_key, base_id, draft_fields.get("ContentFingerprint"), reason.strip(), impact["fingerprint"], actor.strip()])
        result = {"changeRequestKey": cr_key, "status": "pending_approval", "baseRevisionId": base_id,
            "reason": reason.strip(), "before": before, "after": content, "impact": impact,
            "requestFingerprint": operation_fingerprint}
        if not prior:
            now = grist_datetime(utc_now())
            frozen = {"kind": "part-definition-submission-v1", "partId": part_id, "draftKey": draft_key,
                "draftId": int(draft["id"]), "actor": actor.strip(), "reason": reason.strip(), "createdAt": now, "result": result}
            self.client.create_table_records("PartRegistryRequest", [{"fields": {"RequestKey": request_key,
                "RequestType": "part_definition_change_request", "RequestFingerprint": operation_fingerprint,
                "EntityUUID": part_id, "Status": "publishing", "StartedAt": now, "UpdatedAt": now,
                "WriterHostId": self.registry.host_id, "CoordinatorId": self.registry.coordinator_id,
                "Payload": json.dumps(frozen, sort_keys=True)}}])
        request = self.registry._begin_request(request_key, "part_definition_change_request", operation_fingerprint, part_id)
        old_result = self._request_result(request)
        if old_result is not None:
            return old_result
        cr_key = f"part-change-request:{part_id}:{request_key}"
        cr = self.registry._ensure_keyed("PartEngineeringChangeRequest", "ChangeRequestKey", cr_key, {
            "ChangeRequestKey": cr_key, "ProductPart": int(self._part_row(part_id)["id"]), "Draft": int(draft["id"]),
            "BaseRevision": base_id, "Status": "pending_approval", "Reason": reason.strip(),
            "BeforeDefinition": canonical_grist_json_cell(before), "AfterDefinition": canonical_grist_json_cell(content),
            "ImpactPreview": canonical_grist_json_cell(impact), "ImpactFingerprint": impact["fingerprint"],
            "InitiatedBy": actor.strip(), "CreatedAt": grist_datetime(utc_now()), "RequestKey": request_key,
            "RequestFingerprint": operation_fingerprint})
        self.client.update_table_records("PartDefinitionDraft", [{"id": int(draft["id"]), "fields": {
            "Status": "submitted", "UpdatedAt": grist_datetime(utc_now()), "RequestKey": request_key,
            "RequestFingerprint": operation_fingerprint}}])
        self.registry._complete_request(request, result)
        return result

    def _revision_key(self, part_id: str, change_request_key: str) -> str:
        return f"managed-part-revision:{part_id}:{request_fingerprint(change_request_key)}"

    def _revision_row_for_part(self, part_id: str, revision_key: str, current_revision: dict[str, Any], *, cause: str, change_request_id: int,
                               actor: str, reason: str, request_key: str, baseline_status: str, label: str) -> dict[str, Any]:
        part_row = self._part_row(part_id)
        existing = self._find("PartRevision", "RevisionKey", revision_key)
        if existing:
            return existing
        version = max([int(_fields(row).get("Revision") or 0) for row in self._rows("PartRevision")
            if _int_ref(_fields(row).get("ProductPart")) == int(part_row["id"])], default=0) + 1
        current_fields = _fields(current_revision)
        baseline_id = _int_ref(current_fields.get("ManufacturingBaseline"))
        baseline = next((row for row in self._rows("PartBaselineProcessing")
            if baseline_id and int(row.get("id", -1)) == baseline_id), None) if baseline_id else None
        baseline_fields = _fields(baseline)
        return self.registry._ensure_keyed("PartRevision", "RevisionKey", revision_key, {
            "RevisionKey": revision_key, "ProductPart": int(part_row["id"]), "Revision": version,
            "RevisionLabel": label, "Status": "publishing", "BaselineStatus": "publishing",
            "ManufacturingBaseline": baseline_id,
            "ManufacturingBaselineStatus": baseline_fields.get("Status") or current_fields.get("ManufacturingBaselineStatus"),
            "ManufacturingBaselineSourceHash": baseline_fields.get("SourceHash") or current_fields.get("ManufacturingBaselineSourceHash"),
            "PreviousRevision": int(current_revision["id"]), "ChangeRequest": int(change_request_id), "PublicationCause": cause,
            "Actor": actor.strip(), "Reason": reason.strip(), "RequestKey": request_key,
            "OccurredAt": grist_datetime(utc_now()),
            "RequestFingerprint": request_fingerprint([part_id, current_revision["id"], label, cause, actor, reason, baseline_status, request_key])})

    def _active_link_rows(self, revision_id: int) -> list[dict[str, Any]]:
        return [row for row in self._rows("PartRevisionLine") if _int_ref(_fields(row).get("PartRevision")) == revision_id
            and str(_fields(row).get("Status") or "active").casefold() not in REMOVED_STATUSES]

    def _existing_line_details(self, line_revision_id: int | None) -> dict[str, Any]:
        rows = [row for row in self._rows("LineDetail") if _int_ref(_fields(row).get("LineRevision")) == line_revision_id]
        if len(rows) > 1:
            raise PartIdentityError("PART_LINE_DETAIL_AMBIGUOUS", "This line revision has multiple detail rows; resolve the legacy conflict before publishing a successor.")
        fields = _fields(rows[0]) if rows else {}
        return {key: value for key, value in fields.items() if key in LINE_DETAIL_FIELDS}

    def _materialize_line(self, part_id: str, revision_id: int, cr_key: str, item: dict[str, Any], actor: str, reason: str, request_key: str) -> dict[str, Any]:
        requirement_key = str(item["requirementKey"])
        existing_master_id, existing_revision_id = _int_ref(item.get("lineMasterId")), _int_ref(item.get("lineRevisionId"))
        detail = {key: deepcopy(value) for key, value in item.get("detail", {}).items() if key in LINE_DETAIL_FIELDS}
        engineering_attributes = _json_cell(detail.get("EngineeringAttributes"))
        if engineering_attributes is not None:
            detail["EngineeringAttributes"] = canonical_grist_json_cell(engineering_attributes)
        existing_detail = self._existing_line_details(existing_revision_id)
        comparable_detail = deepcopy(existing_detail)
        comparable_detail["EngineeringAttributes"] = _json_cell(comparable_detail.get("EngineeringAttributes")) or {}
        detail_for_compare = deepcopy(detail)
        detail_for_compare["EngineeringAttributes"] = _json_cell(detail_for_compare.get("EngineeringAttributes")) or {}
        same_detail = existing_revision_id is not None and _stable(detail_for_compare) == _stable(comparable_detail)
        if existing_revision_id and same_detail:
            return {"lineMasterId": existing_master_id, "lineRevisionId": existing_revision_id, "changed": False}
        if not existing_master_id:
            line_key = f"safari-authored-line:{request_fingerprint([part_id, requirement_key])}"
            master = self.registry._ensure_keyed("LineMaster", "LineKey", line_key, {"LineKey": line_key,
                "ProductPart": int(self._part_row(part_id)["id"]), "ProcessType": item["processType"],
                "Identity": canonical_grist_json_cell({"requirementKey": requirement_key, "family": item["family"]}),
                "Status": "accepted", "SourcePartName": str(detail.get("ItemName") or detail.get("MaterialDisplayName") or "Safari-authored requirement"),
                "RequestKey": request_key, "RequestFingerprint": request_fingerprint([line_key, item, actor, reason])})
            existing_master_id = int(master["id"])
        if not existing_master_id:
            raise PartIdentityError("PART_LINE_MASTER_REQUIRED", "The requirement identity cannot be resolved to a saved LineMaster.")
        previous = next((row for row in self._rows("LineRevision") if int(row.get("id", -1)) == existing_revision_id), None) if existing_revision_id else None
        signature = request_fingerprint({key: value for key, value in detail.items() if key not in {"EngineeringAttributes", "RequestKey", "RequestFingerprint"}})
        line_revision_key = f"part-definition-line-revision:{cr_key}:{requirement_key}"
        line_revision = self.registry._ensure_keyed("LineRevision", "RevisionKey", line_revision_key, {
            "RevisionKey": line_revision_key, "LineMaster": existing_master_id,
            "PreviousRevision": int(previous["id"]) if previous else None, "PhysicalSignature": signature,
            "Status": "accepted", "Snapshot": None, "Actor": actor.strip(), "Reason": reason.strip(),
            "RequestKey": request_key, "RequestFingerprint": request_fingerprint([line_revision_key, signature, actor, reason])})
        revision_id_new = int(line_revision["id"])
        detail_key = f"part-definition-line-detail:{cr_key}:{requirement_key}"
        detail["LineRevision"] = revision_id_new
        detail["DetailKey"] = detail_key
        detail["RequestKey"] = request_key
        detail["RequestFingerprint"] = request_fingerprint([detail_key, signature, actor, reason])
        self.registry._ensure_keyed("LineDetail", "DetailKey", detail_key, detail)
        return {"lineMasterId": existing_master_id, "lineRevisionId": revision_id_new, "changed": True}

    def _write_definition_rows(self, part_id: str, new_revision: dict[str, Any], cr_key: str, content: dict[str, Any], *, actor: str, reason: str, request_key: str,
                               repin: dict[str, int] | None = None) -> dict[str, Any]:
        revision_id = int(new_revision["id"])
        repin = repin or {}
        lines_written = 0
        materialized_requirements = []
        for item in content.get("requirements", []):
            materialized = self._materialize_line(part_id, revision_id, cr_key, item, actor, reason, request_key)
            requirement_key = str(item["requirementKey"])
            provenance = deepcopy(item.get("provenance") or {})
            if materialized["changed"]:
                provenance["type"] = "safari_edited" if item.get("identityStatus") in {"stable", "legacy_unclassified"} else "safari_authored"
                evidence = provenance.get("evidence") if isinstance(provenance.get("evidence"), dict) else {}
                if _int_ref(provenance.get("sourceObservationId")):
                    evidence["sourceObservationId"] = _int_ref(provenance.get("sourceObservationId"))
                provenance["evidence"] = evidence
            item["lineMasterId"] = materialized["lineMasterId"]
            item["lineRevisionId"] = materialized["lineRevisionId"]
            item["provenance"] = provenance
            materialized_requirements.append(item)
            link_key = f"part-definition-membership:{cr_key}:{requirement_key}"
            fields = {"RevisionLineKey": link_key, "RequirementKey": requirement_key, "PartRevision": revision_id,
                "LineMaster": materialized["lineMasterId"], "LineRevision": materialized["lineRevisionId"],
                "SourceLineObservation": _int_ref(provenance.get("sourceObservationId")),
                "ProcessType": item.get("processType"), "QuantityPerPart": item.get("quantity"), "QuantityExact": item.get("quantityExact"),
                "QuantityUOM": item.get("uom"), "Status": "active", "Actor": actor.strip(), "Reason": reason.strip(),
                "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([cr_key, requirement_key, item]),
                "ProvenanceType": str(provenance.get("type") or "safari_authored"),
                "ProvenanceEvidence": canonical_grist_json_cell(provenance.get("evidence") or {})}
            self.registry._ensure_keyed("PartRevisionLine", "RevisionLineKey", link_key, fields)
            lines_written += 1
        for component in content.get("components", []):
            component_identity = str(component["componentIdentity"])
            child_part = self._part_row(str(component["childPartId"]))
            child_revision_id = repin.get(str(component["childPartId"])) or _int_ref(component.get("childRevisionId"))
            if not child_revision_id:
                child_revision_id = _int_ref(_fields(child_part).get("CurrentPartRevision"))
            key = f"part-definition-component:{cr_key}:{component_identity}"
            component_fields = {"ComponentKey": key, "ComponentIdentity": component_identity, "ParentRevision": revision_id,
                "ChildRevision": child_revision_id, "Quantity": component.get("quantity"), "QuantityUOM": component.get("uom"),
                "SourcingRoute": component.get("sourcingRoute") or "auto", "Status": "active", "ComponentStatus": "active",
                "Actor": actor.strip(), "Reason": reason.strip(), "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([cr_key, component])}
            self.registry._ensure_keyed("PartComponentRevision", "ComponentKey", key, component_fields)
        for spec in content.get("purchases", []):
            identity = str(spec["specificationIdentity"])
            key = f"part-definition-specification:{cr_key}:{identity}"
            fields = {"SpecificationKey": key, "SpecificationIdentity": identity, "ProductPart": int(self._part_row(part_id)["id"]),
                "PartRevision": revision_id, "PurchaseItem": spec.get("purchaseItemId"), "SpecificationCode": spec.get("code"),
                "Manufacturer": spec.get("manufacturer"), "ManufacturerPartNumber": spec.get("manufacturerPartNumber"),
                "Description": spec.get("description"), "Attributes": canonical_grist_json_cell(spec.get("attributes") or {}),
                "CostingUOM": spec.get("costingUOM"), "CostingCurrency": spec.get("costingCurrency"), "Status": "active",
                "Actor": actor.strip(), "Reason": reason.strip(), "CreatedAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([cr_key, spec, actor, reason])}
            self.registry._ensure_keyed("PartPurchaseSpecification", "SpecificationKey", key, fields)
        # Drawings are immutable evidence attached to a Part revision. Cloning
        # keeps the historical links and the accepted successor self-contained.
        drawings = [row for row in self._rows("PartDrawing") if _int_ref(_fields(row).get("PartRevision")) == int(_int_ref(_fields(new_revision).get("PreviousRevision") or 0) or 0)
            and str(_fields(row).get("Status") or "active").casefold() not in REMOVED_STATUSES]
        for drawing_row in drawings:
            drawing = _fields(drawing_row)
            identity = str(drawing.get("DrawingIdentity") or drawing_row.get("id"))
            key = f"part-definition-drawing:{cr_key}:{identity}"
            copied = {name: drawing.get(name) for name in ("DrawingIdentity", "LinkType", "FilePath", "ExternalURL", "FileVersion", "ContentHash", "Status")}
            copied.update({"DrawingKey": key, "PartRevision": revision_id, "Actor": actor.strip(), "Reason": reason.strip(),
                "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key, "RequestFingerprint": request_fingerprint([cr_key, identity, drawing])})
            self.registry._ensure_keyed("PartDrawing", "DrawingKey", key, copied)
        return {"requirementsWritten": lines_written, "componentCount": len(content.get("components", [])), "purchaseCount": len(content.get("purchases", [])),
            "requirements": materialized_requirements}

    def _content_for_assembly(self, part_id: str, revision_id: int, repin: dict[str, int]) -> dict[str, Any]:
        content = self._definition_from_rows(part_id, revision_id)
        for component in content.get("components", []):
            if component.get("childPartId") in repin:
                component["childRevisionId"] = repin[component["childPartId"]]
        return {"applicability": content["applicability"], "requirements": content["requirements"], "components": content["components"],
            "purchases": content["purchases"], "finalizeOnPublication": str(content.get("revisionStatus") or "").casefold() == "finalized",
            "baseRevisionId": revision_id, "partId": part_id}

    def _make_plan(self, cr: dict[str, Any], approver: str, operation_key: str) -> dict[str, Any]:
        fields = _fields(cr)
        part_row = next((row for row in self._rows("ProductPart") if int(row.get("id", -1)) == _int_ref(fields.get("ProductPart"))), None)
        part_id = str(_fields(part_row).get("StablePartId") or "") if part_row else ""
        if not part_id:
            raise PartIdentityError("PART_NOT_FOUND", "The Part referenced by this Change Request no longer exists.")
        current = self._current_revision(part_id)
        if int(current["id"]) != _int_ref(fields.get("BaseRevision")):
            raise PartIdentityError("PART_DEFINITION_STALE_BASE", "The Part's accepted revision changed after the Change Request was submitted.")
        after = _json_cell(fields.get("AfterDefinition"))
        impact = _json_cell(fields.get("ImpactPreview"))
        if not after or not impact or impact.get("fingerprint") != fields.get("ImpactFingerprint"):
            raise PartIdentityError("PART_CHANGE_REQUEST_INTEGRITY", "The approved Change Request does not match its saved definition or reviewed impact.")
        validated_after = self._normalize_content(part_id, int(current["id"]), after, f"publication-validation:{fields.get('ChangeRequestKey')}")
        comparable_after = deepcopy(after)
        normalized_by_key = {str(item.get("requirementKey")): item for item in validated_after.get("requirements", [])}
        for item in comparable_after.get("requirements", []):
            normalized = normalized_by_key.get(str(item.get("requirementKey"))) or {}
            for name in ("lineMasterId", "lineRevisionId"):
                if item.get(name) in (None, "") and normalized.get(name) is not None:
                    item[name] = normalized[name]
        if request_fingerprint(validated_after) != request_fingerprint(comparable_after):
            raise PartIdentityError("PART_CHANGE_REQUEST_DEFINITION_INVALID", "The approved saved definition no longer satisfies current physical-consumption and numeric invariants. Reopen it, save a corrected draft and request approval again.")
        after = validated_after
        current_impact = self.impact_preview(part_id, after)
        if current_impact.get("fingerprint") != impact.get("fingerprint"):
            raise PartIdentityError("PART_DEPENDENCY_CLOSURE_STALE", "Current assemblies or Model Code configurations changed after the dependency preview. Reopen the definition, review the new impact and submit a new Change Request.")
        blocking_unknown = list(current_impact.get("unknown", []))
        if blocking_unknown:
            raise PartIdentityError("PART_DEPENDENCY_CLOSURE_INCOMPLETE", "The reviewed dependency closure has unresolved or unsupported paths; publication is blocked until coverage is explicit.")
        dependents = current_impact.get("dependents", current_impact.get("assemblies", []))
        if not current_impact.get("target", {}).get("proposedRevisionLabel") or any(not item.get("proposedRevisionLabel") for item in dependents):
            raise PartIdentityError("PART_REVISION_UNCLASSIFIED", "A current Part or assembly has an unclassified legacy revision label; no managed engineering revision will be invented.")
        repin: dict[str, int] = {part_id: 0}
        parts = [{"partId": part_id, "partRecordId": int(part_row["id"]), "currentRevisionId": int(current["id"]),
            "revisionLabel": current_impact["target"]["proposedRevisionLabel"], "kind": str(_fields(part_row).get("IdentityKind") or "part"),
            "content": after, "finalize": bool(after.get("finalizeOnPublication") or str(_fields(current).get("BaselineStatus") or "").casefold() == "finalized")}]
        for dependent in impact.get("dependents", impact.get("assemblies", [])):
            dep_id = dependent["partId"]
            dep_revision_id = int(dependent["currentRevisionId"])
            dep_content = self._content_for_assembly(dep_id, dep_revision_id, {})
            dep_content["finalizeOnPublication"] = str(self._revision_by_id(dep_revision_id).get("BaselineStatus") or "").casefold() == "finalized"
            parts.append({"partId": dep_id, "partRecordId": int(dependent["partRecordId"]), "currentRevisionId": dep_revision_id,
                "revisionLabel": dependent["proposedRevisionLabel"], "kind": dependent.get("kind") or "assembly", "content": dep_content,
                "finalize": bool(dep_content["finalizeOnPublication"])})
        target_finalize = parts[0]["finalize"]
        if any(item["finalize"] for item in parts[1:]) and not target_finalize:
            raise PartIdentityError("PART_DEPENDENT_FINALIZATION_REQUIRED", "A finalized current dependent Part or assembly cannot be repinned to a Part that remains unfinalized. Mark the Part revision for finalization in this approved Change Request.")
        config_groups: dict[int, list[dict[str, Any]]] = {}
        target_record_id = int(part_row["id"])
        for use in impact.get("modelCodeConfigurations", []):
            config_groups.setdefault(int(use["modelCodeRecordId"]), []).append(use)
        config_plans = []
        graph = self._current_graph()
        for code_record_id, affected in sorted(config_groups.items()):
            current_config = next((row for row in graph["configRows"] if _int_ref(_fields(row).get("ProductModelCode")) == code_record_id), None)
            if not current_config:
                raise PartIdentityError("PART_CONFIGURATION_IMPACT_STALE", "An impacted Model Code configuration no longer exists.")
            revision_id = _int_ref(_fields(current_config).get("CurrentRevision"))
            revision = next((row for row in graph["configRevisionRows"] if int(row.get("id", -1)) == revision_id), None)
            if not revision or str(_fields(revision).get("Status") or "").casefold() != "published":
                raise PartIdentityError("PART_CONFIGURATION_IMPACT_INCOMPLETE", "An impacted Model Code has no verified current configuration revision.")
            version = int(_fields(current_config).get("Version") or _fields(revision).get("Revision") or 0) + 1
            config_plans.append({"codeRecordId": code_record_id, "configurationRecordId": int(current_config["id"]),
                "currentRevisionId": int(revision_id), "revision": version, "currency": _fields(revision).get("Currency") or "INR",
                "selections": [dict(item) for item in graph["configs"] if item["codeRecordId"] == code_record_id]})
        return {"changeRequestKey": fields.get("ChangeRequestKey"), "partId": part_id, "baseRevisionId": int(current["id"]),
            "approver": approver, "reason": fields.get("Reason"), "operationKey": operation_key, "parts": parts,
            "configurations": config_plans, "impactFingerprint": impact["fingerprint"],
            "dependencyGeneration": impact.get("dependencyGeneration"), "affectedPartRecordIds": [item["partRecordId"] for item in parts],
            "affectedCodeRecordIds": [item["codeRecordId"] for item in config_plans]}

    def _revision_by_id(self, revision_id: int) -> dict[str, Any]:
        row = next((item for item in self._rows("PartRevision") if int(item.get("id", -1)) == int(revision_id)), None)
        return _fields(row)

    def _assert_no_pending_publication(self, publication: dict[str, Any], record_id: int) -> None:
        return None

    def _mark_change_request_stale(self, cr: dict[str, Any], *, code: str, reason: str, request_key: str) -> None:
        fields = _fields(cr)
        stale_at = fields.get("StaleAt") or grist_datetime(utc_now())
        self.client.update_table_records("PartEngineeringChangeRequest", [{"id": int(cr["id"]), "fields": {
            "Status": "stale", "StaleAt": stale_at, "StaleCode": code, "StaleReason": reason}}])
        event_key = f"part-change-request-stale:{request_fingerprint(fields.get('ChangeRequestKey'))}"
        self.registry._ensure_keyed("AuditEvent", "RequestKey", event_key, {
            "EventType": "part_definition_change_request_stale", "Actor": str(fields.get("ApprovedBy") or fields.get("InitiatedBy") or ""),
            "OccurredAt": stale_at, "EntityType": "PartEngineeringChangeRequest", "EntityId": str(cr["id"]),
            "Reason": reason, "Payload": canonical_grist_json_cell({"changeRequestKey": fields.get("ChangeRequestKey"),
                "staleCode": code, "reviewedImpactFingerprint": fields.get("ImpactFingerprint"), "approvalRequestKey": request_key}),
            "RequestKey": event_key, "RequestFingerprint": request_fingerprint([fields.get("ChangeRequestKey"), code, reason])})

    @_serialized
    def approve_and_publish(self, change_request_key: str, *, actor: str, request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        self._require_publication_schema()
        cr = self._find("PartEngineeringChangeRequest", "ChangeRequestKey", change_request_key)
        if not cr:
            raise PartIdentityError("PART_CHANGE_REQUEST_NOT_FOUND", "The submitted Part Change Request no longer exists.")
        if not actor.strip() or not request_key.strip():
            raise PartIdentityError("PART_APPROVAL_INPUT_REQUIRED", "A trusted approver and idempotency key are required.")
        required_approver = os.getenv("SAFARI_ENGINEERING_APPROVER_USERNAME", "Irshad").strip().casefold()
        if actor.strip().casefold() != required_approver:
            raise PartIdentityError("PART_CHANGE_REQUEST_APPROVER_REQUIRED", "Only the currently designated Irshad engineering approver can approve Part-definition publication.")
        fields = _fields(cr)
        operation_fingerprint = request_fingerprint([change_request_key, actor.strip(), request_key])
        publication_key = str(fields.get("PublicationKey") or f"part-definition-publication:{request_fingerprint(change_request_key)}")
        existing_journal = self._find("PartDefinitionPublication", "PublicationKey", publication_key)
        if existing_journal:
            original_key = self._verified_publication_owner(existing_journal, cr)
            if str((_json_cell(_fields(existing_journal).get("Plan")) or {}).get("approver") or "").strip() != actor.strip():
                raise PartIdentityError("PART_PUBLICATION_PLAN_CONFLICT", "The publication belongs to a different approver.")
            if request_key != original_key:
                raise PartIdentityError("PART_PUBLICATION_RECOVER_ORIGINAL", "This publication already started. Recover its original approved operation before attempting another request.")
        elif str(fields.get("Status") or "").casefold() in {"approved", "publishing", "publication_failed"} and fields.get("ApprovedBy"):
            original_key = self._verified_approval_owner(cr)
            approver = str(fields.get("ApprovedBy") or "").strip()
            if actor.strip() != approver:
                raise PartIdentityError("PART_PUBLICATION_PLAN_CONFLICT", "The saved approval ownership cannot be verified.")
            if request_key != original_key:
                raise PartIdentityError("PART_PUBLICATION_RECOVER_ORIGINAL", "This approval already started. Retry its original operation.")
        prior = self._find("PartRegistryRequest", "RequestKey", request_key)
        if prior:
            prior = self.registry._begin_request(request_key, "part_definition_approval_publication", operation_fingerprint, change_request_key)
            old_result = self._request_result(prior)
            if old_result is not None:
                return old_result
        if str(fields.get("Status") or "").casefold() not in {"published", "stale"}:
            part_row = next((row for row in self._rows("ProductPart") if int(row.get("id", -1)) == _int_ref(fields.get("ProductPart"))), None)
            after = _json_cell(fields.get("AfterDefinition"))
            part_id = str(_fields(part_row).get("StablePartId") or "")
            if not part_id or not after or not _int_ref(fields.get("BaseRevision")):
                raise PartIdentityError("PART_CHANGE_REQUEST_INTEGRITY", "The submitted definition has no valid Part or base revision.")
            self._validate_plan_line_identities([{"partId": part_id, "currentRevisionId": _int_ref(fields.get("BaseRevision")), "content": after}])
        request = self.registry._begin_request(request_key, "part_definition_approval_publication", operation_fingerprint, change_request_key)
        old_result = self._request_result(request)
        if old_result is not None:
            return old_result
        status = str(fields.get("Status") or "").casefold()
        if status == "stale":
            self._mark_change_request_stale(cr, code=str(fields.get("StaleCode") or "PART_DEPENDENCY_CLOSURE_STALE"),
                reason=str(fields.get("StaleReason") or "The saved dependency review is stale. Reopen it as a new draft and request fresh approval."),
                request_key=request_key)
            raise PartIdentityError("PART_CHANGE_REQUEST_STALE", "This Change Request is stale and cannot be approved. Reopen its saved content, review the current dependency closure and submit a new request.")
        if status not in {"pending_approval", "approved", "publishing", "publication_failed", "published"}:
            raise PartIdentityError("PART_CHANGE_REQUEST_STATE_INVALID", "Only a submitted pending Change Request can be approved and published.")
        publication_key = str(fields.get("PublicationKey") or f"part-definition-publication:{request_fingerprint(change_request_key)}")
        journal = self._find("PartDefinitionPublication", "PublicationKey", publication_key)
        if journal:
            journal_fields = _fields(journal)
            saved_plan = _json_cell(journal_fields.get("Plan"))
            plan_fingerprint = request_fingerprint(saved_plan) if saved_plan else ""
            if (_int_ref(journal_fields.get("ChangeRequest")) != int(cr["id"])
                    or journal_fields.get("PlanFingerprint") != plan_fingerprint or not saved_plan
                    or saved_plan.get("changeRequestKey") != change_request_key):
                raise PartIdentityError("PART_PUBLICATION_PLAN_CONFLICT", "The saved publication plan differs from the approved dependency closure.")
            plan = saved_plan
        else:
            if str(fields.get("Status") or "").casefold() == "published":
                result = {"changeRequestKey": change_request_key, "status": "published",
                    "publishedRevisionId": _int_ref(fields.get("PublishedRevision")), "idempotent": True}
                self.registry._complete_request(request, result)
                return result
            try:
                plan = self._make_plan(cr, actor.strip(), request_key)
            except PartIdentityError as exc:
                if exc.code in {"PART_DEFINITION_STALE_BASE", "PART_DEPENDENCY_CLOSURE_STALE"}:
                    self._mark_change_request_stale(cr, code=exc.code, reason=str(exc), request_key=request_key)
                raise
            approved_at = fields.get("ApprovedAt") or grist_datetime(utc_now())
            if not fields.get("ApprovedBy"):
                self.client.update_table_records("PartEngineeringChangeRequest", [{"id": int(cr["id"]), "fields": {
                    "Status": "approved", "ApprovedBy": actor.strip(), "ApprovedAt": approved_at, "RequestKey": request_key,
                    "RequestFingerprint": operation_fingerprint}}])
                cr = self._find("PartEngineeringChangeRequest", "ChangeRequestKey", change_request_key) or cr
            plan_fingerprint = request_fingerprint(plan)
            journal = self.registry._ensure_keyed("PartDefinitionPublication", "PublicationKey", publication_key, {
                "PublicationKey": publication_key, "ChangeRequest": int(cr["id"]), "Status": "publishing",
                "Plan": canonical_grist_json_cell(plan), "PlanFingerprint": plan_fingerprint,
                "AffectedParts": canonical_grist_json_cell({"recordIds": plan["affectedPartRecordIds"]}),
                "AffectedCodes": canonical_grist_json_cell({"recordIds": plan["affectedCodeRecordIds"]}),
                "CompletedStages": canonical_grist_json_cell({"stages": []}), "LastError": "",
                "CreatedAt": grist_datetime(utc_now()), "UpdatedAt": grist_datetime(utc_now()),
                "RequestKey": request_key, "RequestFingerprint": operation_fingerprint})
            self.client.update_table_records("PartEngineeringChangeRequest", [{"id": int(cr["id"]), "fields": {"Status": "publishing", "PublicationKey": publication_key}}])
        try:
            result = self._apply_plan(journal, cr, plan, request_key=request_key, actor=actor.strip())
            self.registry._complete_request(request, result)
            return result
        except Exception as exc:
            current_journal = self._find("PartDefinitionPublication", "PublicationKey", publication_key) or journal
            if str(_fields(current_journal).get("Status") or "").casefold() != "published":
                self.client.update_table_records("PartDefinitionPublication", [{"id": int(current_journal["id"]), "fields": {
                    "Status": "publication_failed", "LastError": str(exc)[:1000], "UpdatedAt": grist_datetime(utc_now())}}])
            raise

    def _apply_plan(self, journal: dict[str, Any], cr: dict[str, Any], plan: dict[str, Any], *, request_key: str, actor: str) -> dict[str, Any]:
        cr_key = str(plan["changeRequestKey"])
        current_journal = self._find("PartDefinitionPublication", "PublicationKey", str(_fields(journal).get("PublicationKey"))) or journal
        plan_fields = _json_cell(_fields(current_journal).get("Plan")) or plan
        if request_fingerprint(plan_fields) != _fields(current_journal).get("PlanFingerprint"):
            raise PartIdentityError("PART_PUBLICATION_PLAN_INTEGRITY", "The durable publication plan failed its fingerprint check.")
        self._validate_plan_line_identities(plan_fields["parts"])
        rows_by_part: dict[str, dict[str, Any]] = {}
        for item in plan_fields["parts"]:
            current_revision = next((row for row in self._rows("PartRevision") if int(row.get("id", -1)) == int(item["currentRevisionId"])), None)
            if not current_revision:
                raise PartIdentityError("PART_REVISION_UNAVAILABLE", "A current revision required by the approved publication plan is unavailable.")
            key = self._revision_key(str(item["partId"]), cr_key)
            row = self._revision_row_for_part(str(item["partId"]), key, current_revision, cause=cr_key, change_request_id=int(cr["id"]), actor=actor,
                reason=str(plan_fields.get("reason") or ""), request_key=request_key,
                baseline_status="finalized" if item["finalize"] else "draft", label=str(item["revisionLabel"]))
            rows_by_part[str(item["partId"])] = row
        repin = {part_id: int(row["id"]) for part_id, row in rows_by_part.items()}
        # Stage 1: persist every immutable definition row while all current
        # pointers still reference the previously accepted coherent generation.
        for item in plan_fields["parts"]:
            part_id = str(item["partId"])
            content = item["content"]
            written = self._write_definition_rows(part_id, rows_by_part[part_id], cr_key, content, actor=actor,
                reason=str(plan_fields.get("reason") or ""), request_key=request_key, repin=repin)
            content["requirements"] = written["requirements"]
            for component in content.get("components", []):
                if component.get("childPartId") in repin:
                    component["childRevisionId"] = repin[component["childPartId"]]
            snapshot_key = f"part-definition-version:{request_fingerprint(cr_key + ':' + part_id)}"
            previous_version = next((row for row in self._rows("PartDefinitionVersion") if _int_ref(_fields(row).get("PartRevision")) == int(item["currentRevisionId"])), None)
            part_row = self._part_row(part_id)
            snapshot = {"applicability": content.get("applicability", {}), "requirements": content.get("requirements", []),
                "components": content.get("components", []), "purchases": content.get("purchases", []),
                "finalizeOnPublication": item["finalize"], "publicationCause": cr_key,
                "provenance": {"type": "safari_change_request", "changeRequestKey": cr_key}}
            self.registry._ensure_keyed("PartDefinitionVersion", "DefinitionKey", snapshot_key, {
                "DefinitionKey": snapshot_key, "ProductPart": int(part_row["id"]), "PartRevision": int(rows_by_part[part_id]["id"]),
                "ChangeRequest": int(cr["id"]), "PreviousDefinition": int(previous_version["id"]) if previous_version else None,
                "Content": canonical_grist_json_cell(snapshot), "ContentFingerprint": request_fingerprint(snapshot),
                "PublicationCause": cr_key, "Actor": actor, "Reason": str(plan_fields.get("reason") or ""),
                "OccurredAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([cr_key, part_id, snapshot])})
        self._update_stage(current_journal, "definition_rows_written")

        # Stage 2: create immutable successor configuration versions with all
        # current selection identities/overrides retained and the changed Part
        # pinned to its new accepted engineering revision.
        config_rows_by_code: dict[int, dict[str, Any]] = {}
        for config in plan_fields.get("configurations", []):
            config_row = next((row for row in self._rows("CostingConfiguration") if int(row.get("id", -1)) == int(config["configurationRecordId"])), None)
            if not config_row:
                raise PartIdentityError("PART_CONFIGURATION_STALE_BASE", "An impacted Model Code configuration changed after review; its current successor was not published.")
            revision_key = f"configuration-part-change:{cr_key}:{config['codeRecordId']}"
            revision_row = self.registry._ensure_keyed("CostingConfigurationRevision", "ConfigurationRevisionKey", revision_key, {
                "ConfigurationRevisionKey": revision_key, "Configuration": int(config_row["id"]), "Revision": int(config["revision"]),
                "Status": "publishing", "Currency": config.get("currency") or "INR", "Actor": actor,
                "ChangeRequest": int(cr["id"]), "PublicationCause": cr_key,
                "Reason": str(plan_fields.get("reason") or ""), "CreatedAt": grist_datetime(utc_now()),
                "RequestKey": request_key, "RequestFingerprint": request_fingerprint([cr_key, config])})
            current_pointer = _int_ref(_fields(config_row).get("CurrentRevision"))
            successor_id = int(revision_row["id"])
            if current_pointer not in {int(config["currentRevisionId"]), successor_id}:
                raise PartIdentityError("PART_CONFIGURATION_STALE_BASE", "An impacted Model Code configuration changed to a foreign generation after review; the approved successor was not published.")
            revision_fields = _fields(revision_row)
            if (_int_ref(revision_fields.get("Configuration")) != int(config_row["id"])
                    or str(revision_fields.get("PublicationCause") or "") != cr_key
                    or int(revision_fields.get("Revision") or 0) != int(config["revision"])):
                raise PartIdentityError("PART_CONFIGURATION_STALE_BASE", "The current configuration successor does not match this verified publication plan.")
            for selection in config["selections"]:
                part_revision = repin.get(selection["partId"]) or selection.get("partRevisionId")
                if not part_revision:
                    part_revision = _int_ref(_fields(self._part_row(selection["partId"])).get("CurrentPartRevision"))
                selection_key = f"{revision_key}:{selection['selectionIdentity']}"
                selection_fields = {"SelectionKey": selection_key, "SelectionIdentity": selection["selectionIdentity"],
                    "ConfigurationRevision": int(revision_row["id"]), "ProductPart": int(selection["partRecordId"]),
                    "StablePartId": selection["partId"], "PartRevision": part_revision, "Quantity": selection["quantity"],
                    "QuantityUOM": selection["uom"], "SourcingRoute": selection["route"], "OccurrenceLabel": selection["label"],
                    "OptionGroup": selection["optionGroup"], "Status": "active", "Actor": actor,
                    "Reason": str(plan_fields.get("reason") or ""), "RequestKey": request_key,
                    "RequestFingerprint": request_fingerprint([cr_key, config["codeRecordId"], selection])}
                self.registry._ensure_keyed("ConfigurationPartSelection", "SelectionKey", selection_key, selection_fields)
            config_rows_by_code[int(config["codeRecordId"])] = {"config": config_row, "revision": revision_row}
        self._update_stage(current_journal, "configuration_rows_written")

        # Stage 3: validate physical closures before moving any current pointer.
        # A dependent parent may also reference the target directly while its
        # child assembly references that same target. Finalize every planned
        # child before its consumers, independent of preview/table row order.
        for item in self._child_first_part_order(plan_fields["parts"]):
            row = rows_by_part[str(item["partId"])]
            if item["finalize"]:
                closure = self.features._physical_closure(int(row["id"]), set())
                self.client.update_table_records("PartRevision", [{"id": int(row["id"]), "fields": {
                    "Status": "finalized", "BaselineStatus": "finalized", "DefinitionHash": closure["definitionHash"],
                    "FinalizedAt": grist_datetime(utc_now()), "FinalizedBy": actor,
                    "FinalizationReason": str(plan_fields.get("reason") or "")}}])
            else:
                row = rows_by_part[str(item["partId"])]
                self.client.update_table_records("PartRevision", [{"id": int(row["id"]), "fields": {"Status": "accepted", "BaselineStatus": "draft"}}])
        for item in config_rows_by_code.values():
            self.client.update_table_records("CostingConfigurationRevision", [{"id": int(item["revision"]["id"]), "fields": {"Status": "published"}}])
        self._update_stage(current_journal, "successor_content_verified")

        # The journal blocks reads for this reviewed closure while multiple
        # current pointers are updated. Readers resume only after every pointer
        # is confirmed and the journal is marked published.
        for item in plan_fields["parts"]:
            part_row = self._part_row(str(item["partId"]))
            current_pointer = _int_ref(_fields(part_row).get("CurrentPartRevision"))
            successor_id = int(rows_by_part[str(item["partId"])]["id"])
            if current_pointer not in {int(item["currentRevisionId"]), successor_id}:
                raise PartIdentityError("PART_DEFINITION_STALE_BASE", "A current Part/assembly pointer changed during publication; the approved generation was not partially advanced.")
        for config in plan_fields.get("configurations", []):
            config_row = next((row for row in self._rows("CostingConfiguration") if int(row.get("id", -1)) == int(config["configurationRecordId"])), None)
            current_pointer = _int_ref(_fields(config_row).get("CurrentRevision")) if config_row else None
            successor = config_rows_by_code.get(int(config["codeRecordId"]))
            successor_id = int(successor["revision"]["id"]) if successor else None
            if not config_row or current_pointer not in {int(config["currentRevisionId"]), successor_id}:
                raise PartIdentityError("PART_CONFIGURATION_STALE_BASE", "A current configuration pointer changed during publication; the approved generation was not partially advanced.")
        for item in plan_fields["parts"]:
            part_row = self._part_row(str(item["partId"]))
            revision_row = rows_by_part[str(item["partId"])]
            if _int_ref(_fields(part_row).get("CurrentPartRevision")) != int(revision_row["id"]):
                self.client.update_table_records("ProductPart", [{"id": int(part_row["id"]), "fields": {
                    "CurrentPartRevision": int(revision_row["id"]), "EngineeringRevision": str(item["revisionLabel"]),
                }}])
        for code_id, item in config_rows_by_code.items():
            config = item["config"]
            revision = item["revision"]
            if _int_ref(_fields(config).get("CurrentRevision")) != int(revision["id"]):
                self.client.update_table_records("CostingConfiguration", [{"id": int(config["id"]), "fields": {
                    "CurrentRevision": int(revision["id"]), "Version": int(_fields(revision).get("Revision") or 0),
                    "Currency": _fields(revision).get("Currency") or "INR", "UpdatedAt": grist_datetime(utc_now()),
                    "Actor": actor, "Reason": str(plan_fields.get("reason") or ""), "RequestKey": request_key,
                    "RequestFingerprint": request_fingerprint([cr_key, code_id, revision["id"]])}}])
        self._update_stage(current_journal, "current_pointers_moved")
        self.registry._ensure_keyed("AuditEvent", "RequestKey", f"{request_key}:approved-publication", {
            "EventType": "part_definition_change_request_published", "Actor": actor, "OccurredAt": grist_datetime(utc_now()),
            "EntityType": "ProductPart", "EntityId": plan_fields["partId"], "Reason": str(plan_fields.get("reason") or ""),
            "Payload": canonical_grist_json_cell({"changeRequestKey": cr_key, "publishedRevisionId": int(rows_by_part[plan_fields["partId"]]["id"]),
                "dependentParts": plan_fields["affectedPartRecordIds"], "modelCodes": plan_fields["affectedCodeRecordIds"]}),
            "RequestKey": f"{request_key}:approved-publication", "RequestFingerprint": request_fingerprint([cr_key, plan_fields])})
        self.client.update_table_records("PartDefinitionPublication", [{"id": int(current_journal["id"]), "fields": {
            "Status": "published", "CompletedAt": grist_datetime(utc_now()), "UpdatedAt": grist_datetime(utc_now()), "LastError": ""}}])
        self.client.update_table_records("PartEngineeringChangeRequest", [{"id": int(cr["id"]), "fields": {
            "Status": "published", "PublishedRevision": int(rows_by_part[plan_fields["partId"]]["id"]), "PublicationKey": _fields(current_journal).get("PublicationKey")}}])
        if _int_ref(_fields(cr).get("Draft")):
            self.client.update_table_records("PartDefinitionDraft", [{"id": int(_int_ref(_fields(cr).get("Draft"))), "fields": {
                "Status": "published", "UpdatedAt": grist_datetime(utc_now()), "RequestKey": request_key,
                "RequestFingerprint": request_fingerprint([cr_key, "draft_published"])}}])
        return {"changeRequestKey": cr_key, "status": "published", "publishedRevisionId": int(rows_by_part[plan_fields["partId"]]["id"]),
            "publishedRevisionLabel": next(item["revisionLabel"] for item in plan_fields["parts"] if item["partId"] == plan_fields["partId"]),
            "dependentPartCount": len(plan_fields["parts"]) - 1, "configurationCount": len(config_rows_by_code), "idempotent": False}

    @staticmethod
    def _child_first_part_order(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {str(item["partId"]): item for item in parts}
        original_order = {str(item["partId"]): index for index, item in enumerate(parts)}
        parents_by_child: dict[str, set[str]] = {part_id: set() for part_id in by_id}
        children_by_parent: dict[str, set[str]] = {part_id: set() for part_id in by_id}
        for parent_id, item in by_id.items():
            for component in (item.get("content") or {}).get("components", []):
                child_id = str(component.get("childPartId") or "")
                if child_id in by_id:
                    children_by_parent[parent_id].add(child_id)
                    parents_by_child[child_id].add(parent_id)
        ready = sorted((part_id for part_id, children in children_by_parent.items() if not children),
            key=original_order.__getitem__)
        result: list[dict[str, Any]] = []
        emitted: set[str] = set()
        while ready:
            child_id = ready.pop(0)
            emitted.add(child_id)
            result.append(by_id[child_id])
            for parent_id in sorted(parents_by_child[child_id], key=original_order.__getitem__):
                children_by_parent[parent_id].discard(child_id)
                if not children_by_parent[parent_id] and parent_id not in emitted and parent_id not in ready:
                    ready.append(parent_id)
                    ready.sort(key=original_order.__getitem__)
        if len(result) != len(parts):
            raise PartIdentityError("PART_COMPONENT_CYCLE", "The reviewed successor definitions contain a cycle; no dependent revision was finalized.")
        return result

    def _update_stage(self, journal: dict[str, Any], stage: str) -> None:
        current = self._find("PartDefinitionPublication", "PublicationKey", str(_fields(journal).get("PublicationKey"))) or journal
        stages = _json_cell(_fields(current).get("CompletedStages")) or {"stages": []}
        values = list(stages.get("stages") or [])
        if stage not in values:
            values.append(stage)
        self.client.update_table_records("PartDefinitionPublication", [{"id": int(current["id"]), "fields": {
            "CompletedStages": canonical_grist_json_cell({"stages": values}), "UpdatedAt": grist_datetime(utc_now()), "LastError": ""}}])
