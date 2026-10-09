"""Grist-backed Part manufacturing baselines and workbook reconciliation.

Source rows are immutable observations. Establishment links normalized typed
requirements to the Part's current Rev A; later comparisons only append
observations, differences and decisions and never rewrite that baseline.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
from typing import Any, Callable

from app.domain import utc_now
from app.grist_parts import GristAdminClientShim, GristPartRegistry, _ref, _serialized
from app.grist_types import grist_datetime
from app.part_identity import PartIdentityError, request_fingerprint
from app.part_mapping import MAPPING_POLICY_VERSION, mapping_detail


FAMILIES = {
    "mcl": {"sheet": "5. Material Cut List Price", "process": "material_cut"},
    "toolshop": {"sheet": "Tool Shop Items", "process": "tool_shop"},
    "cnc": {"sheet": "CNC Cut List", "process": "cnc"},
}
FAMILY_ORDER = tuple(FAMILIES)
FAMILY_TO_SHEET = {value["sheet"]: key for key, value in FAMILIES.items()}
COMPARISON_POLICY = "part-requirement-fields-v1"
BASELINE_TABLES = {
    "PartBaselineProcessing", "PartBaselineFamily", "PartWorkbookComparison", "PartComparisonFamily",
    "PartRequirementDifference", "PartWorkbookDecision", "PartChangeProposal",
}
NORMALIZED_TABLES = {
    "LineMaster", "LineRevision", "LineDetail", "SourceLineObservation", "SourceLineMapping",
    "PartRevisionLine", "PartRevision",
}
TABLE_KEY_FIELDS = {
    "PartBaselineProcessing": "BaselineKey", "PartBaselineFamily": "FamilyKey",
    "LineMaster": "LineKey", "LineRevision": "RevisionKey", "LineDetail": "DetailKey",
    "SourceLineObservation": "ObservationKey", "SourceLineMapping": "MappingKey",
    "PartRevisionLine": "RevisionLineKey", "PartWorkbookComparison": "ComparisonKey",
    "PartComparisonFamily": "ComparisonFamilyKey", "PartRequirementDifference": "DifferenceKey",
    "PartWorkbookDecision": "DecisionKey", "PartChangeProposal": "ProposalKey",
}


def _digest(value: Any) -> str:
    # Normalized requirements deliberately retain Decimal values until final
    # Grist projection so quantities compare exactly. Convert them to canonical
    # JSON strings only at the fingerprint boundary.
    return request_fingerprint(json.loads(_json(value)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _unjson(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f") if value else "0"


def _number(value: Any) -> float | None:
    decimal = _decimal(value)
    if decimal is None:
        return None
    result = float(decimal)
    return result if math.isfinite(result) else None


def _normal_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).casefold()
    return text or None


def _ref_id(value: Any) -> int | None:
    return _ref(value)


def _row_fields(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("fields", {}) if isinstance(row, dict) else {}


def _part_row_key(value: Any) -> str:
    return str(value or "")


def _family_review_rows(mapping: dict[str, Any], part_id: str) -> dict[str, dict[str, Any]]:
    groups_by_family = {family: [] for family in FAMILY_ORDER}
    for group in mapping.get("groups", []):
        family = FAMILY_TO_SHEET.get(str(group.get("sheet") or ""))
        if family:
            groups_by_family[family].append(group)
    diagnostics = mapping.get("sourceSheets") or {}
    result = {}
    for family in FAMILY_ORDER:
        sheet = FAMILIES[family]["sheet"]
        family_groups = groups_by_family[family]
        mapped = [group for group in family_groups if group.get("reviewed") and (group.get("part") or {}).get("id") == part_id]
        diag = diagnostics.get(sheet) or {}
        result[family] = {
            "family": family,
            "sheet": sheet,
            "sourceEvidenceStatus": str(diag.get("status") or "missing_diagnostic"),
            "labelHeader": diag.get("labelHeader"),
            "groupCount": len(family_groups),
            "mappedGroupCount": len(mapped),
            "unreviewedGroupCount": sum(not group.get("reviewed") for group in family_groups),
            "requirementCount": sum(len(group.get("rows") or []) for group in mapped),
            "groups": mapped,
        }
    return result


def _family_confirmations(mapping: dict[str, Any], part_id: str, submitted: Any, *, strict: bool = True) -> tuple[dict[str, dict[str, Any]], list[str]]:
    current = _family_review_rows(mapping, part_id)
    choices = submitted if isinstance(submitted, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for family in FAMILY_ORDER:
        evidence = current[family]
        choice = choices.get(family)
        if isinstance(choice, str):
            choice = {"status": choice, "confirmedComplete": choice == "not_applicable"}
        if not isinstance(choice, dict):
            result[family] = {**evidence, "applicabilityStatus": "unconfirmed", "completenessConfirmed": False}
            errors.append(f"{evidence['sheet']}: select Applicable or Confirmed not applicable.")
            continue
        status = str(choice.get("status") or "").strip().casefold()
        if status not in {"applicable", "not_applicable"}:
            errors.append(f"{evidence['sheet']}: select Applicable or Confirmed not applicable.")
        confirmed = bool(choice.get("confirmedComplete", False))
        if evidence["sourceEvidenceStatus"] != "ok":
            errors.append(f"{evidence['sheet']}: evidence is {evidence['sourceEvidenceStatus']}; missing or unreadable evidence cannot be called not applicable.")
        if status == "applicable":
            if evidence["mappedGroupCount"] == 0 and confirmed:
                errors.append(f"{evidence['sheet']}: Applicable requires at least one saved, current Part mapping.")
            if strict and evidence["mappedGroupCount"] and not confirmed:
                errors.append(f"{evidence['sheet']}: confirm that all requirements for this Part in the workbook have been mapped.")
        elif status == "not_applicable" and evidence["mappedGroupCount"]:
            errors.append(f"{evidence['sheet']}: this Part has mapped source rows, so the family cannot be marked not applicable.")
        elif status == "not_applicable" and not confirmed:
            errors.append(f"{evidence['sheet']}: confirm that this source family does not apply to the Part.")
        if strict and status == "applicable" and evidence["mappedGroupCount"] == 0:
            errors.append(f"{evidence['sheet']}: Applicable requires at least one saved, current Part mapping.")
        result[family] = {**evidence, "applicabilityStatus": status or "unconfirmed", "completenessConfirmed": confirmed}
    return result, errors


def _raw_requirement(row: dict[str, Any], family: str, group: dict[str, Any]) -> dict[str, Any]:
    fields = dict(row.get("fields") or {})
    sheet = FAMILIES[family]["sheet"]
    material = fields.get("material_to_cut") or fields.get("material_used")
    quantity = _decimal(fields.get("qty"))
    weight_kg_raw = fields.get("total_weight_kg")
    if weight_kg_raw in (None, ""):
        weight_kg_raw = fields.get("part_weight_kg")
    weight_kg = _decimal(weight_kg_raw)
    weight_grams = _decimal(fields.get("total_grams"))
    length = _decimal(fields.get("length"))
    width = _decimal(fields.get("width"))
    thickness = _decimal(fields.get("thickness"))
    dimension = fields.get("dimension_to_cut_mm")
    if dimension in (None, ""):
        dimension = fields.get("dimension_to_cut_inches")
    item_name = fields.get("item_name")
    item_code = fields.get("item_code")
    plate_part = fields.get("product_part_name") if family == "cnc" else None
    toolshop_detail = fields.get("toolshop_part_name") if family == "toolshop" else None
    group_value = fields.get("optional_item_group_1")
    # This list contains only engineering requirements. CR Log, remarks, rates,
    # calculated cost and the source Part label remain observation evidence.
    physical = {
        "family": family,
        "material": _normal_text(material),
        "quantity": _decimal_text(quantity),
        "quantity_uom": "nos",
        "dimension": _normal_text(dimension),
        "dimension_uom": "mm" if fields.get("dimension_to_cut_mm") not in (None, "") else "in" if fields.get("dimension_to_cut_inches") not in (None, "") else None,
        "length": _decimal_text(length), "width": _decimal_text(width), "thickness": _decimal_text(thickness),
        "length_uom": "mm" if length is not None else None,
        "width_uom": "mm" if width is not None else None,
        "thickness_uom": "mm" if thickness is not None else None,
        "weight_kg": _decimal_text(weight_kg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if weight_kg is not None else None),
        "weight_grams": _decimal_text(weight_grams), "weight_uom": "kg" if weight_kg is not None else "g" if weight_grams is not None else None,
        "item_code": _normal_text(item_code), "item_name": _normal_text(item_name),
        "plate_part": _normal_text(plate_part), "toolshop_detail": _normal_text(toolshop_detail),
        "item_group": _normal_text(group_value),
    }
    return {
        "family": family, "processType": FAMILIES[family]["process"], "sheet": sheet,
        "row": int(row.get("row") or 0), "source": row, "group": group,
        "groupKey": str(group.get("key") or ""), "description": str(group.get("description") or ""),
        "quantity": quantity, "weightKg": weight_kg, "weightGrams": weight_grams,
        "dimension": str(dimension) if dimension not in (None, "") else None,
        "dimensionMM": _decimal(dimension) if physical["dimension_uom"] == "mm" else None,
        "itemName": str(item_name or ""), "itemCode": str(item_code or ""),
        "material": str(material or ""), "platePart": str(plate_part or ""),
        "partCategory": str(fields.get("part_category") or ""),
        "length": length, "width": width, "thickness": thickness,
        "physical": physical,
        "rawFields": fields,
    }


def _requirements_for_part(mapping: dict[str, Any], part_id: str) -> list[dict[str, Any]]:
    requirements = []
    for group in mapping.get("groups", []):
        family = FAMILY_TO_SHEET.get(str(group.get("sheet") or ""))
        if family and group.get("reviewed") and (group.get("part") or {}).get("id") == part_id:
            for row in group.get("rows") or []:
                requirements.append(_raw_requirement(row, family, group))
    requirements.sort(key=lambda item: (FAMILY_ORDER.index(item["family"]), _digest(item["physical"]), item["row"]))
    return requirements


def _validate_requirements(requirements: list[dict[str, Any]]) -> list[str]:
    errors = []
    for item in requirements:
        quantity = item["quantity"]
        if quantity is None or quantity <= 0:
            errors.append(f"{item['sheet']} row {item['row']}: Quantity must be a positive exact number before this baseline can be established.")
    return errors


def _candidate_similarity(left: dict[str, Any], right: dict[str, Any]) -> int:
    if left.get("family") != right.get("family"):
        return 0
    a, b = left.get("physical") or {}, right.get("physical") or {}
    # Source coordinates, descriptions, rates, calculated costs, CR/audit text
    # and quantity/weights are not requirement identity. Their values are still
    # compared after a strong two-field correspondence is established.
    anchors = ("material", "dimension", "length", "width", "thickness", "item_code", "item_name", "plate_part", "toolshop_detail", "item_group")
    score = sum(1 for key in anchors if a.get(key) not in (None, "") and a.get(key) == b.get(key))
    material_changed = a.get("material") not in (None, "") and b.get("material") not in (None, "") and a.get("material") != b.get("material")
    # A shared dimension or item-group label alone must not silently make a
    # material substitution the same requirement. A stable item/plate/tool
    # identifier can still support an automatic candidate correspondence.
    stable_codes = ("item_code", "item_name", "plate_part", "toolshop_detail")
    if material_changed and not any(a.get(key) not in (None, "") and a.get(key) == b.get(key) for key in stable_codes):
        return min(score, 1)
    return score


class PartBaselineService:
    def __init__(self, registry: GristPartRegistry):
        self.registry = registry
        self.client = registry.client

    def _verify_schema(self):
        if not hasattr(self.client, "api_key"):
            return
        shim = GristAdminClientShim(self.client)
        tables = {str(row.get("id")) for row in shim.list_tables()}
        missing = (BASELINE_TABLES | NORMALIZED_TABLES) - tables
        if missing:
            raise PartIdentityError("PART_BASELINE_SCHEMA_REQUIRED", "Safari Grist needs additive Part baseline schema v11: " + ", ".join(sorted(missing)))
        required_columns = {
            "PartRevision": {"ManufacturingBaselineStatus", "ManufacturingBaseline", "ManufacturingBaselineSourceHash"},
            "PartMappingReview": {"ActionType", "MappingPolicyVersion"},
            "LineDetail": {"EngineeringAttributes", "DimensionText", "ExactQuantityText"},
            "PartRevisionLine": {"RequirementKey", "BaselineProcessing", "QuantityExact"},
            "PartRequirementDifference": {"BaselineRequirementKey", "ResolvedFromDifference"},
            "PartWorkbookDecision": {"MatchedBaselineRevisionLine"},
        }
        for table, required in required_columns.items():
            columns = {str(row.get("id")) for row in shim.list_columns(table)}
            missing_columns = required - columns
            if missing_columns:
                raise PartIdentityError("PART_BASELINE_SCHEMA_REQUIRED", f"Safari Grist {table} needs v11 columns: " + ", ".join(sorted(missing_columns)))

    def _upsert_many(self, table: str, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if not records:
            return {}
        key_field = TABLE_KEY_FIELDS[table]
        rows = self.registry._rows(table)
        by_key = {str(_row_fields(row).get(key_field) or ""): row for row in rows}
        missing = []
        for fields in records:
            key = str(fields.get(key_field) or "")
            if not key:
                raise PartIdentityError("PART_BASELINE_KEY_REQUIRED", f"{table}.{key_field} cannot be blank.")
            old = by_key.get(key)
            if old:
                if str(_row_fields(old).get("RequestFingerprint") or "") != str(fields.get("RequestFingerprint") or ""):
                    raise PartIdentityError("PART_BASELINE_REQUEST_CONFLICT", f"The {table} row {key!r} is already linked to different evidence.")
            else:
                missing.append({"fields": fields})
        if missing:
            self.client.create_table_records(table, missing)
        confirmed = self.registry._rows(table)
        by_key = {str(_row_fields(row).get(key_field) or ""): row for row in confirmed}
        result = {}
        for fields in records:
            row = by_key.get(str(fields[key_field]))
            if not row or str(_row_fields(row).get("RequestFingerprint") or "") != str(fields.get("RequestFingerprint") or ""):
                raise PartIdentityError("PART_BASELINE_WRITE_UNCONFIRMED", f"Grist did not confirm {table} record {fields[key_field]!r} after Save.")
            result[str(fields[key_field])] = row
        return result

    def _update_confirmed(self, table: str, row: dict[str, Any], key_field: str, key: str, values: dict[str, Any]):
        self.client.update_table_records(table, [{"id": int(row["id"]), "fields": values}])
        confirmed = self.registry._find(self.registry._rows(table), key_field, key)
        ref_fields = {"ManufacturingBaseline", "PartRevision", "ProductPart", "BaselineProcessing", "BaselineRevision"}
        def matches(name, actual, expected):
            return _ref_id(actual) == _ref_id(expected) if name in ref_fields else actual == expected
        if not confirmed or any(not matches(name, _row_fields(confirmed).get(name), value) for name, value in values.items()):
            raise PartIdentityError("PART_BASELINE_WRITE_UNCONFIRMED", f"Grist did not confirm the {table} publication state.")
        return confirmed

    def _part_and_revision(self, part_id: str):
        part = self.registry.get_part(part_id)
        if not part:
            raise PartIdentityError("PART_NOT_FOUND", "The selected canonical Part no longer exists.")
        if part.get("legacy"):
            raise PartIdentityError("PART_LEGACY_READ_ONLY", "A managed Rev A Part is required to establish a manufacturing baseline.")
        revision = self.registry.current_revision(part_id)
        return part, revision

    def review(self, *, part_id: str, file_id: str, workbook_path: str, source_hash: str,
               association: Any, groups: list[dict[str, Any]], mapping_store: Any) -> dict[str, Any]:
        self._verify_schema()
        part, revision = self._part_and_revision(part_id)
        mapping = mapping_detail(mapping_store, file_id=file_id, source_hash=source_hash, association=association, groups=groups, refresh_parts=True)
        family_states = _family_review_rows(mapping, part_id)
        baseline_id = _ref_id(_row_fields(revision).get("ManufacturingBaseline"))
        baseline_rows = self.registry._rows("PartBaselineProcessing") if self._table_available("PartBaselineProcessing") else []
        baseline_row = next((row for row in baseline_rows if baseline_id and int(row.get("id", -1)) == baseline_id), None)
        pending_rows = [row for row in baseline_rows
            if _ref_id(_row_fields(row).get("ProductPart")) == int(part["gristRecordId"])
            and str(_row_fields(row).get("Status") or "").casefold() not in {"established", "abandoned"}]
        if not baseline_row and pending_rows:
            baseline_row = max(pending_rows, key=lambda row: int(row.get("id", 0)))
        active_revision_lines = [row for row in self.registry._rows("PartRevisionLine") if _ref_id(_row_fields(row).get("PartRevision")) == int(revision["id"])]
        component_rows = [row for row in self.registry._rows("PartComponentRevision") if _ref_id(_row_fields(row).get("ParentRevision")) == int(revision["id"])]
        purchase_rows = [row for row in self.registry._rows("PartPurchaseSpecification") if _ref_id(_row_fields(row).get("PartRevision")) == int(revision["id"])]
        current_baseline_status = str(_row_fields(baseline_row).get("Status") or "") if baseline_row else ""
        baseline_status = str(_row_fields(revision).get("ManufacturingBaselineStatus") or "not_established")
        if pending_rows or (baseline_row and current_baseline_status != "established"):
            baseline_status = "recovery_required"
        return {
            "partId": part_id, "partNumber": part.get("partNumber"), "partName": part.get("name"),
            "engineeringRevision": part.get("engineeringRevision") or "A",
            "revisionLifecycle": _row_fields(revision).get("BaselineStatus") or _row_fields(revision).get("Status") or "draft",
            "baselineStatus": baseline_status,
            "baseline": {"id": baseline_row.get("id"), **_row_fields(baseline_row)} if baseline_row else None,
            "source": {"fileId": file_id, "workbookPath": workbook_path, "sourceHash": source_hash,
                "associationKey": association.id if association else "", "associationVersion": association.version if association else 0,
                "mappingPolicyVersion": mapping.get("mappingPolicyVersion"), "mappingVersion": mapping.get("version")},
            "families": family_states,
            "mapping": {"unresolvedGroups": mapping.get("unresolvedGroups", 0),
                "groups": [{"key": item.get("key"), "sheet": item.get("sheet"), "description": item.get("description"),
                    "rows": len(item.get("rows") or []), "reviewed": item.get("reviewed"),
                    "partId": (item.get("part") or {}).get("id")} for item in mapping.get("groups", [])]},
            "existingContent": {"processRequirements": len(active_revision_lines), "components": len(component_rows),
                "purchaseSpecifications": len(purchase_rows),
                "requiresAppendConfirmation": bool(active_revision_lines or component_rows or purchase_rows)},
        }

    def baseline_detail(self, part_id: str) -> dict[str, Any]:
        self._verify_schema()
        part, revision = self._part_and_revision(part_id)
        revision_fields = _row_fields(revision)
        baseline_id = _ref_id(revision_fields.get("ManufacturingBaseline"))
        baseline = next((row for row in self.registry._rows("PartBaselineProcessing") if baseline_id and int(row.get("id", -1)) == baseline_id), None)
        families = []
        if baseline_id:
            families = [{"id": int(row["id"]), **_row_fields(row)} for row in self.registry._rows("PartBaselineFamily")
                if _ref_id(_row_fields(row).get("BaselineProcessing")) == baseline_id]
        return {"status": str(revision_fields.get("ManufacturingBaselineStatus") or "not_established"),
            "sourceHash": revision_fields.get("ManufacturingBaselineSourceHash") or "",
            "baseline": ({"id": int(baseline["id"]), **_row_fields(baseline)} if baseline else None),
            "families": families, "requirements": self._baseline_requirements(part, revision, baseline) if baseline else [],
            "revisionLabel": _row_fields(revision).get("RevisionLabel") or "A",
            "revisionLifecycle": _row_fields(revision).get("BaselineStatus") or _row_fields(revision).get("Status") or "draft"}

    def _table_available(self, table: str) -> bool:
        if hasattr(self.client, "tables"):
            return table in self.client.tables
        return True

    @_serialized
    def establish(self, *, part_id: str, file_id: str, workbook_name: str, workbook_path: str,
                  source_hash: str, association: Any, groups: list[dict[str, Any]], mapping_store: Any,
                  source_families: Any, actor: str, reason: str, request_key: str,
                  expected_hash: str, expected_association: str, expected_association_version: int,
                  append_existing: bool = False, expected_mapping_version: int | None = None,
                  before_write: Callable[[], None] | None = None) -> dict[str, Any]:
        self.registry._verify_writer()
        self._verify_schema()
        if not request_key.strip() or not actor.strip():
            raise PartIdentityError("PART_BASELINE_INPUT_REQUIRED", "A request identity and attributable actor are required.")
        if expected_hash != source_hash or not association or association.id != expected_association or association.version != expected_association_version:
            raise PartIdentityError("PART_BASELINE_STALE", "The workbook or Product Model association changed; review current evidence before establishing this baseline.")
        part, revision = self._part_and_revision(part_id)
        revision_fields = _row_fields(revision)
        if str(revision_fields.get("BaselineStatus") or revision_fields.get("Status") or "draft").casefold() == "finalized":
            raise PartIdentityError("PART_REVISION_LOCKED", "The current Part revision is finalized; establish a baseline on a draft Rev A revision.")
        mapping = mapping_detail(mapping_store, file_id=file_id, source_hash=source_hash, association=association, groups=groups, refresh_parts=True)
        if expected_mapping_version is not None and mapping.get("version") != expected_mapping_version:
            raise PartIdentityError("PART_BASELINE_STALE", "Saved Part mappings changed after baseline review; reload the evidence before publishing.")
        families, family_errors = _family_confirmations(mapping, part_id, source_families)
        if family_errors:
            raise PartIdentityError("PART_BASELINE_INCOMPLETE", "Cannot establish the Part baseline: " + " ".join(family_errors))
        requirements = _requirements_for_part(mapping, part_id)
        requirement_errors = _validate_requirements(requirements)
        if requirement_errors:
            raise PartIdentityError("PART_BASELINE_REQUIREMENTS_INCOMPLETE", "Cannot establish the Part baseline: " + " ".join(requirement_errors))
        pending = [row for row in self.registry._rows("PartBaselineProcessing")
            if _ref_id(_row_fields(row).get("ProductPart")) == int(part["gristRecordId"])
            and str(_row_fields(row).get("Status") or "").casefold() not in {"established", "abandoned"}]
        other_pending = [row for row in pending if _row_fields(row).get("RequestKey") != request_key]
        if other_pending:
            raise PartIdentityError("PART_BASELINE_RECOVERY_REQUIRED", "Another initial baseline write is incomplete. Retry its original request so Grist can recover it before starting a different workbook operation.")

        request_fingerprint_value = _digest([part_id, file_id, workbook_name, workbook_path, source_hash,
            association.id, association.version, mapping.get("mappingPolicyVersion"), source_families,
            append_existing, actor, reason.strip()])
        baseline_status = str(revision_fields.get("ManufacturingBaselineStatus") or "not_established").casefold()
        baseline_ref = _ref_id(revision_fields.get("ManufacturingBaseline"))
        if baseline_status == "established" and baseline_ref:
            existing = next((row for row in self.registry._rows("PartBaselineProcessing") if int(row.get("id", -1)) == baseline_ref), None)
            if existing and _row_fields(existing).get("RequestKey") == request_key:
                if _row_fields(existing).get("RequestFingerprint") != request_fingerprint_value:
                    raise PartIdentityError("PART_REQUEST_CONFLICT", "This baseline request key was retried with different workbook evidence or completeness selections.")
                result = self._recover_established(existing, revision, part)
                request = self.registry._find(self.registry._rows("PartRegistryRequest"), "RequestKey", request_key)
                if request and _row_fields(request).get("Status") != "published":
                    self.registry._complete_request(request, result)
                return result
            raise PartIdentityError("PART_BASELINE_EXISTS", "This Part already has an established manufacturing baseline. Use workbook comparison instead of replacing it.")

        # A response-lost retry may already have appended this operation's
        # normalized PartRevisionLine rows. They are its own resumable payload,
        # not manually existing content requiring a second append confirmation.
        existing_lines = [row for row in self.registry._rows("PartRevisionLine")
            if _ref_id(_row_fields(row).get("PartRevision")) == int(revision["id"])
            and str(_row_fields(row).get("RequestKey") or "") != request_key]
        existing_components = [row for row in self.registry._rows("PartComponentRevision") if _ref_id(_row_fields(row).get("ParentRevision")) == int(revision["id"])]
        existing_specs = [row for row in self.registry._rows("PartPurchaseSpecification") if _ref_id(_row_fields(row).get("PartRevision")) == int(revision["id"])]
        if (existing_lines or existing_components or existing_specs) and not append_existing:
            raise PartIdentityError("PART_BASELINE_JOIN_CONFIRMATION_REQUIRED", "This Part already has requirements, subparts or purchase specifications. Review the existing content and confirm that mapped workbook requirements will be appended without replacing it.")
        request = self.registry._begin_request(request_key, "establish_part_baseline", request_fingerprint_value, part_id)
        if request.get("fields", {}).get("Status") == "published":
            saved = next((row for row in self.registry._rows("PartBaselineProcessing") if _row_fields(row).get("RequestKey") == request_key), None)
            if saved:
                return self._recover_established(saved, revision, part)
        baseline_key = str((pending[0].get("fields", {}) if pending else {}).get("BaselineKey") or f"part-baseline:{_digest([part_id, request_key])}")
        baseline_fingerprint = _digest([baseline_key, request_fingerprint_value, [item["physical"] for item in requirements]])
        current_time = (pending[0].get("fields", {}) if pending else {}).get("OccurredAt") or grist_datetime(utc_now())
        reason_text = reason.strip() or "Establish initial workbook manufacturing baseline"
        baseline_fields = {"BaselineKey": baseline_key, "ProductPart": int(part["gristRecordId"]), "PartRevision": int(revision["id"]),
            "FileKey": file_id, "WorkbookName": workbook_name, "WorkbookPath": workbook_path, "SourceHash": source_hash,
            "MappingPolicyVersion": mapping.get("mappingPolicyVersion") or MAPPING_POLICY_VERSION, "Status": "processing",
            "RequirementCount": len(requirements), "ContentFingerprint": _digest([item["physical"] for item in requirements]),
            "Actor": actor.strip(), "Reason": reason_text, "OccurredAt": current_time, "RequestKey": request_key,
            "RequestFingerprint": request_fingerprint_value}
        baseline_row = (pending[0] if pending else None)
        if baseline_row and _row_fields(baseline_row).get("RequestFingerprint") != request_fingerprint_value:
            raise PartIdentityError("PART_REQUEST_CONFLICT", "The original baseline request key was retried with different workbook evidence or completeness selections.")
        if before_write:
            before_write()
        baseline_row = self._upsert_many("PartBaselineProcessing", [baseline_fields])[baseline_key]
        baseline_id = int(baseline_row["id"])
        family_records = []
        for family in FAMILY_ORDER:
            evidence = families[family]
            family_key = f"{baseline_key}:family:{family}"
            family_records.append({"FamilyKey": family_key, "BaselineProcessing": baseline_id, "ProductPart": int(part["gristRecordId"]),
                "Family": family, "ApplicabilityStatus": evidence["applicabilityStatus"],
                "SourceEvidenceStatus": evidence["sourceEvidenceStatus"], "MappedGroupCount": evidence["mappedGroupCount"],
                "RequirementCount": evidence["requirementCount"], "CompletenessConfirmed": evidence["completenessConfirmed"],
                "Actor": actor.strip(), "Reason": reason_text, "OccurredAt": current_time, "RequestKey": request_key,
                "RequestFingerprint": _digest([family_key, evidence["applicabilityStatus"], evidence["sourceEvidenceStatus"],
                    evidence["mappedGroupCount"], evidence["requirementCount"], evidence["completenessConfirmed"], request_fingerprint_value])})
        self._upsert_many("PartBaselineFamily", family_records)

        # Stable keys use requirement values and duplicate ordinals, never source
        # row positions. The source row remains immutable provenance on the
        # observation and mapping records.
        requirement_rows = []
        duplicate_counts: Counter[str] = Counter()
        for item in requirements:
            identity_fields = {key: item["physical"].get(key) for key in (
                "family", "material", "dimension", "length", "width", "thickness", "item_code", "item_name", "plate_part", "toolshop_detail", "item_group")}
            identity_fingerprint = _digest(identity_fields)
            duplicate_counts[identity_fingerprint] += 1
            ordinal = duplicate_counts[identity_fingerprint]
            requirement_key = f"part-requirement:{_digest([part_id, identity_fields, ordinal])}"
            physical_signature = _digest(item["physical"])
            line_key = f"part-baseline-line:{_digest([part_id, requirement_key])}"
            revision_key = f"part-baseline-revision:{_digest([line_key, physical_signature])}"
            item.update({"requirementKey": requirement_key, "lineKey": line_key,
                "lineRevisionKey": revision_key, "identityFields": identity_fields,
                "physicalSignature": physical_signature})
            requirement_rows.append(item)

        line_master_rows = []
        for item in requirement_rows:
            fp = _digest([item["lineKey"], part_id, item["identityFields"], item["processType"]])
            line_master_rows.append({"LineKey": item["lineKey"], "ProductPart": int(part["gristRecordId"]),
                "ProcessType": item["processType"], "Identity": _json(item["identityFields"]), "Status": "accepted",
                "SourcePartName": item["description"], "RequestKey": request_key, "RequestFingerprint": fp})
        masters_by_key = self._upsert_many("LineMaster", line_master_rows)
        line_revision_rows = []
        for item in requirement_rows:
            master_id = int(masters_by_key[item["lineKey"]]["id"])
            fp = _digest([item["lineRevisionKey"], master_id, item["physicalSignature"], request_fingerprint_value])
            line_revision_rows.append({"RevisionKey": item["lineRevisionKey"], "LineMaster": master_id,
                "PreviousRevision": None, "PhysicalSignature": item["physicalSignature"], "Status": "accepted",
                "Snapshot": None, "Actor": actor.strip(), "Reason": reason_text,
                "RequestKey": request_key, "RequestFingerprint": fp})
        revisions_by_key = self._upsert_many("LineRevision", line_revision_rows)

        observation_rows = []
        for item in requirement_rows:
            row = item["source"]
            observation_key = f"part-baseline-observation:{_digest([baseline_key, item['sheet'], item['row'], source_hash])}"
            item["observationKey"] = observation_key
            observation_fp = _digest([observation_key, item["rawFields"], row.get("sourceCells"), row.get("sourceHeaders"), request_fingerprint_value])
            observation_rows.append({"ObservationKey": observation_key, "Snapshot": None, "BaselineProcessing": baseline_id,
                "SourceHash": source_hash, "DependencyHashes": _json({}), "ParserVersion": COMPARISON_POLICY,
                "SheetName": item["sheet"], "SourceRow": item["row"], "Status": "accepted_baseline",
                "Cells": _json({"sourceCells": row.get("sourceCells") or {}, "sourceHeaders": row.get("sourceHeaders") or {},
                    "sourceHeaderCells": row.get("sourceHeaderCells") or {}, "headerRow": row.get("headerRow")}),
                "ObservedFields": _json(item["rawFields"]), "PartDisplayName": item["description"],
                "WorkbookName": workbook_name, "WorkbookPath": workbook_path, "AssociationKey": association.id,
                "AssociationVersion": association.version, "MappingGroupKey": item["groupKey"],
                "RequestKey": request_key, "RequestFingerprint": observation_fp})
        observations_by_key = self._upsert_many("SourceLineObservation", observation_rows)

        detail_rows = []
        source_mapping_rows = []
        revision_line_rows = []
        for item in requirement_rows:
            row = item["source"]
            master_row = masters_by_key[item["lineKey"]]
            master_id = int(master_row["id"])
            revision_row = revisions_by_key[item["lineRevisionKey"]]
            line_revision_id = int(revision_row["id"])
            observation_row = observations_by_key[item["observationKey"]]
            observation_id = int(observation_row["id"])
            fields = item["rawFields"]
            line_detail_key = f"part-baseline-detail:{item['lineRevisionKey']}"
            detail_fp = _digest([line_detail_key, item["physical"], item["rawFields"], request_fingerprint_value])
            weight_value = item["weightKg"] if item["weightKg"] is not None else item["weightGrams"]
            detail_rows.append({"DetailKey": line_detail_key, "LineRevision": line_revision_id,
                "ProcessType": item["processType"], "Material": None, "PurchaseItem": None,
                "ProcessOperation": None, "WorkCenter": None, "Quantity": _number(item["quantity"]),
                "QuantityUOM": "Nos", "DimensionMM": _number(item["dimensionMM"]), "DimensionInches": _number(fields.get("dimension_to_cut_inches")),
                "DimensionText": item["dimension"], "DimensionUOM": item["physical"]["dimension_uom"],
                "WeightGrams": _number(item["weightGrams"]), "WeightKg": _number(item["weightKg"]),
                "PartWeightKg": _number(fields.get("part_weight_kg")), "Length": _number(item["length"]),
                "Width": _number(item["width"]), "Thickness": _number(item["thickness"]),
                "ItemName": item["itemName"], "ItemCode": item["itemCode"], "PlatePart": item["platePart"],
                "PartCategory": item["partCategory"], "MaterialDisplayName": item["material"],
                "ExactQuantityText": _decimal_text(item["quantity"]), "ExactWeightKgText": _decimal_text(item["weightKg"]),
                "WeightUOM": "kg" if item["weightKg"] is not None else "g" if item["weightGrams"] is not None else "",
                "EngineeringAttributes": _json({"comparisonFields": item["physical"], "optionalItemGroup1": fields.get("optional_item_group_1"),
                    "sourcePartLabel": item["description"], "sourceSheet": item["sheet"]}),
                "RequestKey": request_key, "RequestFingerprint": detail_fp})
            source_mapping_key = f"part-baseline-source-map:{_digest([item['observationKey'], item['lineKey']])}"
            source_mapping_rows.append({"MappingKey": source_mapping_key, "Observation": observation_id, "LineMaster": master_id,
                "Status": "accepted_part_baseline", "RequestKey": request_key,
                "RequestFingerprint": _digest([source_mapping_key, observation_id, master_id, request_fingerprint_value])})
            link_key = f"part-baseline-revision-line:{_digest([baseline_key, item['requirementKey']])}"
            revision_line_rows.append({"RevisionLineKey": link_key, "RequirementKey": item["requirementKey"],
                "PartRevision": int(revision["id"]), "BaselineProcessing": baseline_id,
                "LineMaster": master_id, "LineRevision": line_revision_id, "SourceLineObservation": observation_id,
                "ProcessType": item["processType"], "QuantityPerPart": _number(item["quantity"]),
                "QuantityExact": _decimal_text(item["quantity"]), "QuantityUOM": "Nos", "Status": "active",
                "Actor": actor.strip(), "Reason": reason_text, "OccurredAt": current_time, "RequestKey": request_key,
                "RequestFingerprint": _digest([link_key, item["requirementKey"], item["physical"], request_fingerprint_value])})
        self._upsert_many("LineDetail", detail_rows)
        self._upsert_many("SourceLineMapping", source_mapping_rows)
        self._upsert_many("PartRevisionLine", revision_line_rows)

        ready = self._update_confirmed("PartBaselineProcessing", baseline_row, "BaselineKey", baseline_key,
            {"Status": "ready_to_publish"})
        committed_at = grist_datetime(utc_now())
        committed_revision = self._update_confirmed("PartRevision", revision, "RevisionKey", str(revision_fields.get("RevisionKey") or ""), {
            "ManufacturingBaselineStatus": "established", "ManufacturingBaseline": baseline_id,
            "ManufacturingBaselineSourceHash": source_hash})
        completed = self._update_confirmed("PartBaselineProcessing", ready, "BaselineKey", baseline_key,
            {"Status": "established", "EstablishedAt": committed_at, "EstablishedBy": actor.strip()})
        result = {"baselineKey": baseline_key, "baselineStatus": "established", "partId": part_id,
            "partNumber": part.get("partNumber"), "engineeringRevision": "A", "revisionLifecycle": revision_fields.get("BaselineStatus") or "draft",
            "sourceHash": source_hash, "requirementCount": len(requirements), "families": families,
            "baselineRecordId": int(completed["id"]), "revisionRecordId": int(committed_revision["id"]), "idempotent": bool(pending),
            "finalizationRequired": True}
        self.registry._complete_request(request, result)
        return result

    def _recover_established(self, baseline: dict[str, Any], revision: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
        fields = _row_fields(baseline)
        baseline_id = int(baseline["id"])
        revision_fields = _row_fields(revision)
        if _ref_id(revision_fields.get("ManufacturingBaseline")) != baseline_id:
            # Recovery is safe only when all normalized child writes already
            # exist; the retry proceeds through deterministic keyed upserts.
            raise PartIdentityError("PART_BASELINE_RECOVERY_REQUIRED", "The baseline request is incomplete; retry the original request to finish Grist publication.")
        if str(fields.get("Status") or "") != "established":
            self._update_confirmed("PartBaselineProcessing", baseline, "BaselineKey", str(fields["BaselineKey"]),
                {"Status": "established", "EstablishedAt": grist_datetime(utc_now()), "EstablishedBy": fields.get("Actor") or ""})
        return {"baselineKey": fields.get("BaselineKey"), "baselineStatus": "established", "partId": part.get("id"),
            "partNumber": part.get("partNumber"), "engineeringRevision": "A", "revisionLifecycle": revision_fields.get("BaselineStatus") or "draft",
            "sourceHash": fields.get("SourceHash"), "requirementCount": fields.get("RequirementCount") or 0,
            "baselineRecordId": baseline_id, "revisionRecordId": int(revision["id"]), "idempotent": True,
            "finalizationRequired": True}

    def _baseline_requirements(self, part: dict[str, Any], revision: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
        baseline_id = int(baseline["id"])
        links = [row for row in self.registry._rows("PartRevisionLine")
            if _ref_id(_row_fields(row).get("PartRevision")) == int(revision["id"])
            and _ref_id(_row_fields(row).get("BaselineProcessing")) == baseline_id
            and str(_row_fields(row).get("Status") or "active").casefold() == "active"]
        masters = {int(row["id"]): row for row in self.registry._rows("LineMaster")}
        revisions = {int(row["id"]): row for row in self.registry._rows("LineRevision")}
        details_by_revision: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self.registry._rows("LineDetail"):
            rev_id = _ref_id(_row_fields(row).get("LineRevision"))
            if rev_id:
                details_by_revision[rev_id].append(_row_fields(row))
        observations = {int(row["id"]): row for row in self.registry._rows("SourceLineObservation")}
        result = []
        for link_row in links:
            link = _row_fields(link_row)
            master_id = _ref_id(link.get("LineMaster"))
            line_revision_id = _ref_id(link.get("LineRevision"))
            observation_id = _ref_id(link.get("SourceLineObservation"))
            detail = (details_by_revision.get(line_revision_id or 0) or [{}])[0]
            attributes = _unjson(detail.get("EngineeringAttributes"))
            physical = attributes.get("comparisonFields") if isinstance(attributes, dict) else None
            if not isinstance(physical, dict):
                physical = {"family": FAMILY_TO_SHEET.get(str(_row_fields(observations.get(observation_id, {})).get("SheetName") or "")),
                    "material": _normal_text(detail.get("MaterialDisplayName")),
                    "quantity": _decimal_text(_decimal(link.get("QuantityExact") or link.get("QuantityPerPart"))),
                    "dimension": _normal_text(detail.get("DimensionText")),
                    "length": _decimal_text(_decimal(detail.get("Length"))), "width": _decimal_text(_decimal(detail.get("Width"))),
                    "thickness": _decimal_text(_decimal(detail.get("Thickness"))),
                    "weight_kg": _decimal_text((_decimal(detail.get("WeightKg")) or Decimal(0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if detail.get("WeightKg") not in (None, "") else None,
                    "weight_grams": _decimal_text(_decimal(detail.get("WeightGrams"))), "item_code": _normal_text(detail.get("ItemCode")),
                    "item_name": _normal_text(detail.get("ItemName")), "plate_part": _normal_text(detail.get("PlatePart")),
                    "toolshop_detail": None, "item_group": None}
            observation = observations.get(observation_id or -1)
            observed = _unjson(_row_fields(observation or {}).get("ObservedFields"))
            cells = _unjson(_row_fields(observation or {}).get("Cells"))
            result.append({"id": int(link_row["id"]), "key": link.get("RequirementKey"),
                "family": physical.get("family") or FAMILY_TO_SHEET.get(str(_row_fields(observation or {}).get("SheetName") or "")),
                "physical": physical, "rawFields": observed, "cellEvidence": cells,
                "sheet": _row_fields(observation or {}).get("SheetName"), "row": _row_fields(observation or {}).get("SourceRow"),
                "lineMasterId": master_id, "lineRevisionId": line_revision_id, "requirementLineId": int(link_row["id"]),
                "lineKey": _row_fields(masters.get(master_id or -1, {})).get("LineKey"),
                "revisionKey": _row_fields(revisions.get(line_revision_id or -1, {})).get("RevisionKey")})
        return result

    @_serialized
    def compare(self, *, part_id: str, file_id: str, workbook_name: str, workbook_path: str,
                source_hash: str, association: Any, groups: list[dict[str, Any]], mapping_store: Any,
                source_families: Any, actor: str, reason: str, request_key: str,
                expected_hash: str, expected_association: str, expected_association_version: int,
                expected_mapping_version: int | None = None,
                before_write: Callable[[], None] | None = None) -> dict[str, Any]:
        self.registry._verify_writer()
        self._verify_schema()
        if not request_key.strip() or not actor.strip():
            raise PartIdentityError("PART_COMPARISON_INPUT_REQUIRED", "A request identity and attributable actor are required.")
        if expected_hash != source_hash or not association or association.id != expected_association or association.version != expected_association_version:
            raise PartIdentityError("PART_COMPARISON_STALE", "The workbook or Product Model association changed; reload evidence before comparison.")
        part, revision = self._part_and_revision(part_id)
        revision_fields = _row_fields(revision)
        baseline_id = _ref_id(revision_fields.get("ManufacturingBaseline"))
        if str(revision_fields.get("ManufacturingBaselineStatus") or "") != "established" or not baseline_id:
            raise PartIdentityError("PART_BASELINE_NOT_ESTABLISHED", "Establish and publish the initial Part baseline before comparing another workbook.")
        baseline = next((row for row in self.registry._rows("PartBaselineProcessing") if int(row.get("id", -1)) == baseline_id), None)
        if not baseline:
            raise PartIdentityError("PART_BASELINE_UNAVAILABLE", "The Grist baseline provenance record is unavailable; comparison is paused.")
        mapping = mapping_detail(mapping_store, file_id=file_id, source_hash=source_hash, association=association, groups=groups, refresh_parts=True)
        if expected_mapping_version is not None and mapping.get("version") != expected_mapping_version:
            raise PartIdentityError("PART_COMPARISON_STALE", "Saved Part mappings changed after comparison review; reload the evidence before comparing.")
        family_states, family_errors = _family_confirmations(mapping, part_id, source_families, strict=False)
        incoming = _requirements_for_part(mapping, part_id)
        invalid = _validate_requirements(incoming)
        if invalid:
            family_errors.extend(invalid)
        # Incomplete evidence is retained and explicitly classified; it cannot
        # generate deletion proposals.
        for family, state in family_states.items():
            state["complete"] = state["sourceEvidenceStatus"] == "ok" and state["completenessConfirmed"] and state["applicabilityStatus"] in {"applicable", "not_applicable"}
        baseline_requirements = self._baseline_requirements(part, revision, baseline)
        comparison_fp = _digest([part_id, baseline_id, file_id, source_hash, association.id, association.version,
            mapping.get("mappingPolicyVersion"), source_families, actor, reason.strip(), COMPARISON_POLICY])
        request = self.registry._begin_request(request_key, "compare_part_workbook", comparison_fp, part_id)
        comparison_key = f"part-comparison:{_digest([part_id, baseline_id, request_key])}"
        existing_comparison = self.registry._find(self.registry._rows("PartWorkbookComparison"), "ComparisonKey", comparison_key)
        if existing_comparison:
            if _row_fields(existing_comparison).get("RequestFingerprint") != comparison_fp:
                raise PartIdentityError("PART_REQUEST_CONFLICT", "This comparison request key already contains different source or completeness evidence.")
            if str(_row_fields(existing_comparison).get("Status") or "").casefold() in {"complete", "incomplete_evidence"}:
                recovered = self.get_comparison(comparison_key)
                recovered["idempotent"] = True
                if request.get("fields", {}).get("Status") != "published":
                    self.registry._complete_request(request, recovered)
                return recovered
        # A retry of an `evaluating` record resumes all deterministic child
        # upserts below. Reusing only the header would incorrectly publish a
        # partial set after a Grist response was lost between table writes.
        if before_write:
            before_write()
        current_time = (_row_fields(existing_comparison).get("OccurredAt") if existing_comparison else None) or grist_datetime(utc_now())
        comparison_row = self._upsert_many("PartWorkbookComparison", [{"ComparisonKey": comparison_key,
            "ProductPart": int(part["gristRecordId"]), "BaselineProcessing": baseline_id, "BaselineRevision": int(revision["id"]),
            "FileKey": file_id, "WorkbookName": workbook_name, "WorkbookPath": workbook_path,
            "SourceHash": source_hash, "AssociationKey": association.id, "AssociationVersion": association.version,
            "BaselineSourceHash": _row_fields(baseline).get("SourceHash"),
            "MappingPolicyVersion": mapping.get("mappingPolicyVersion") or MAPPING_POLICY_VERSION,
            "Status": "evaluating", "DecisionStatus": "pending_review", "Actor": actor.strip(),
            "Reason": reason.strip() or "Compare incoming workbook with accepted Part baseline", "OccurredAt": current_time,
            "RequestKey": request_key, "RequestFingerprint": comparison_fp}])[comparison_key]
        comparison_id = int(comparison_row["id"])
        family_rows = []
        for family in FAMILY_ORDER:
            state = family_states[family]
            family_key = f"{comparison_key}:family:{family}"
            family_rows.append({"ComparisonFamilyKey": family_key, "WorkbookComparison": comparison_id,
                "ProductPart": int(part["gristRecordId"]), "Family": family,
                "ApplicabilityStatus": state["applicabilityStatus"], "SourceEvidenceStatus": state["sourceEvidenceStatus"],
                "MappedGroupCount": state["mappedGroupCount"], "RequirementCount": state["requirementCount"],
                "CompletenessConfirmed": bool(state["completenessConfirmed"]), "RequestKey": request_key,
                "RequestFingerprint": _digest([family_key, state, comparison_fp])})
        self._upsert_many("PartComparisonFamily", family_rows)

        # Incoming observations are persisted before difference rows so every
        # decision can cite a specific immutable source row and cell.
        observation_rows = []
        for item in incoming:
            source = item["source"]
            item["observationKey"] = f"part-comparison-observation:{_digest([comparison_key, item['sheet'], item['row'], source_hash])}"
            observation_rows.append({"ObservationKey": item["observationKey"], "Snapshot": None,
                "BaselineProcessing": baseline_id, "WorkbookComparison": comparison_id, "SourceHash": source_hash,
                "DependencyHashes": _json({}), "ParserVersion": COMPARISON_POLICY, "SheetName": item["sheet"],
                "SourceRow": item["row"], "Status": "incoming_comparison", "Cells": _json({
                    "sourceCells": source.get("sourceCells") or {}, "sourceHeaders": source.get("sourceHeaders") or {},
                    "sourceHeaderCells": source.get("sourceHeaderCells") or {}, "headerRow": source.get("headerRow")}),
                "ObservedFields": _json(item["rawFields"]), "PartDisplayName": item["description"],
                "WorkbookName": workbook_name, "WorkbookPath": workbook_path,
                "AssociationKey": association.id, "AssociationVersion": association.version,
                "MappingGroupKey": item["groupKey"], "RequestKey": request_key,
                "RequestFingerprint": _digest([item["observationKey"], item["rawFields"], source.get("sourceCells"), comparison_fp])})
        observations_by_key = self._upsert_many("SourceLineObservation", observation_rows)
        for item in incoming:
            item["observationId"] = int(observations_by_key[item["observationKey"]]["id"])

        differences = self._classify(baseline_requirements, incoming, family_states)
        difference_rows = []
        for index, difference in enumerate(differences):
            base = difference.get("baseline")
            inc = difference.get("incoming")
            family = difference["family"]
            raw_id = [comparison_key, difference["type"], family,
                (base or {}).get("key"), (inc or {}).get("observationKey"), index]
            diff_key = f"part-difference:{_digest(raw_id)}"
            difference["key"] = diff_key
            differences_for_fields = difference.get("fieldDifferences") or []
            difference_rows.append({"DifferenceKey": diff_key, "WorkbookComparison": comparison_id,
                "ProductPart": int(part["gristRecordId"]), "Family": family,
                "DifferenceType": difference["type"], "Status": "pending_review",
                "BaselineRevisionLine": (base or {}).get("requirementLineId"),
                "BaselineLineMaster": (base or {}).get("lineMasterId"),
                "BaselineRequirementKey": (base or {}).get("key") or "",
                "ResolvedFromDifference": None,
                "IncomingObservation": (inc or {}).get("observationId"),
                "IncomingRequirementKey": _digest((inc or {}).get("physical") or {}) if inc else "",
                "MappingGroupKey": (inc or {}).get("groupKey") or "",
                "MappingEvidenceFingerprint": (inc or {}).get("group", {}).get("evidenceFingerprint") or "",
                "SourceSheet": (inc or {}).get("sheet") or (base or {}).get("sheet") or FAMILIES[family]["sheet"],
                "SourceRow": int(inc.get("row") or 0) if inc else None,
                "SourceCellEvidence": _json((inc or {}).get("source", {}).get("sourceCells") or (base or {}).get("cellEvidence") or {}),
                "BaselineValues": _json({"physical": (base or {}).get("physical"), "rawFields": (base or {}).get("rawFields")}) if base else "",
                "IncomingValues": _json({"physical": (inc or {}).get("physical"), "rawFields": (inc or {}).get("rawFields")}) if inc else "",
                "FieldDifferences": _json(differences_for_fields),
                "CandidateBaselineLines": _json(difference.get("candidateBaselineLineKeys") or []),
                "RequestKey": request_key, "RequestFingerprint": _digest([diff_key, raw_id, difference, comparison_fp])})
        self._upsert_many("PartRequirementDifference", difference_rows)
        counts = Counter(item["type"] for item in differences)
        incomplete = bool(family_errors or any(not value.get("complete") for value in family_states.values()))
        status = "incomplete_evidence" if incomplete else "complete"
        final_comparison = self._update_confirmed("PartWorkbookComparison", comparison_row, "ComparisonKey", comparison_key, {
            "Status": status, "MatchCount": counts["match"], "AdditionCount": counts["proposed_addition"],
            "ModificationCount": counts["proposed_modification"], "DeletionCount": counts["proposed_deletion"],
            "AmbiguousCount": counts["ambiguous_correspondence"] + counts["incomplete_evidence"]})
        response = self.get_comparison(comparison_key)
        response["idempotent"] = False
        response["evidenceWarnings"] = list(dict.fromkeys([*response.get("evidenceWarnings", []), *family_errors]))
        self.registry._complete_request(request, response)
        return response

    def _classify(self, baseline: list[dict[str, Any]], incoming: list[dict[str, Any]], families: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for family in FAMILY_ORDER:
            old = [item for item in baseline if item.get("family") == family]
            new = [item for item in incoming if item.get("family") == family]
            old_by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
            new_by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in old:
                old_by_sig[_digest(item.get("physical") or {})].append(item)
            for item in new:
                new_by_sig[_digest(item.get("physical") or {})].append(item)
            used_old: set[int] = set()
            used_new: set[int] = set()
            for signature in sorted(set(old_by_sig) & set(new_by_sig)):
                old_rows, new_rows = old_by_sig[signature], new_by_sig[signature]
                for base, inc in zip(old_rows, new_rows):
                    used_old.add(id(base)); used_new.add(id(inc))
                    output.append({"type": "match", "family": family, "baseline": base, "incoming": inc, "fieldDifferences": []})
            old_left = [item for item in old if id(item) not in used_old]
            new_left = [item for item in new if id(item) not in used_new]
            if not families[family].get("complete"):
                for inc in new_left:
                    output.append({"type": "incomplete_evidence", "family": family, "baseline": None, "incoming": inc})
                for base in old_left:
                    output.append({"type": "incomplete_evidence", "family": family, "baseline": base, "incoming": None})
                continue
            candidates: dict[int, list[int]] = {}
            candidate_scores: dict[tuple[int, int], int] = {}
            reverse: dict[int, list[int]] = defaultdict(list)
            for n_idx, inc in enumerate(new_left):
                hits = [b_idx for b_idx, base in enumerate(old_left) if _candidate_similarity(base, inc) >= 1]
                candidates[n_idx] = hits
                for b_idx in hits:
                    candidate_scores[(n_idx, b_idx)] = _candidate_similarity(old_left[b_idx], inc)
                    reverse[b_idx].append(n_idx)
            paired_new: set[int] = set()
            paired_old: set[int] = set()
            ambiguous_new: set[int] = set()
            ambiguous_old: set[int] = set()
            for n_idx, hits in candidates.items():
                if len(hits) == 1 and len(reverse[hits[0]]) == 1 and candidate_scores[(n_idx, hits[0])] >= 2:
                    b_idx = hits[0]
                    if n_idx in paired_new or b_idx in paired_old:
                        continue
                    base, inc = old_left[b_idx], new_left[n_idx]
                    diffs = [{"field": key, "baseline": (base.get("physical") or {}).get(key), "incoming": (inc.get("physical") or {}).get(key)}
                        for key in sorted(set(base.get("physical") or {}) | set(inc.get("physical") or {}))
                        if (base.get("physical") or {}).get(key) != (inc.get("physical") or {}).get(key)]
                    output.append({"type": "proposed_modification", "family": family, "baseline": base, "incoming": inc, "fieldDifferences": diffs})
                    paired_new.add(n_idx); paired_old.add(b_idx)
                elif hits:
                    ambiguous_new.add(n_idx); ambiguous_old.update(hits)
            # If unmatched rows remain on both sides but lack shared identity
            # evidence, do not manufacture an addition/deletion pair. Preserve
            # the uncertainty for a user to match explicitly.
            unmatched_new = [index for index in range(len(new_left)) if index not in paired_new and index not in ambiguous_new]
            unmatched_old = [index for index in range(len(old_left)) if index not in paired_old and index not in ambiguous_old]
            if unmatched_new and unmatched_old:
                ambiguous_old.update(unmatched_old)
                for n_idx in unmatched_new:
                    ambiguous_new.add(n_idx)
            for n_idx, inc in enumerate(new_left):
                if n_idx in paired_new:
                    continue
                if n_idx in ambiguous_new:
                    hit_lines = [old_left[index].get("key") for index in candidates[n_idx]] or [old_left[index].get("key") for index in unmatched_old]
                    output.append({"type": "ambiguous_correspondence", "family": family, "baseline": None,
                        "incoming": inc, "candidateBaselineLineKeys": hit_lines})
                else:
                    output.append({"type": "proposed_addition", "family": family, "baseline": None, "incoming": inc})
            for b_idx, base in enumerate(old_left):
                if b_idx in paired_old:
                    continue
                if b_idx in ambiguous_old:
                    # The incoming-side ambiguous row already names this
                    # candidate; preserve baseline provenance as a separate
                    # unresolved correspondence record for user matching.
                    output.append({"type": "ambiguous_correspondence", "family": family, "baseline": base,
                        "incoming": None, "candidateBaselineLineKeys": [base.get("key")]})
                else:
                    output.append({"type": "proposed_deletion", "family": family, "baseline": base, "incoming": None})
        return output

    def get_comparison(self, comparison_key: str) -> dict[str, Any]:
        self._verify_schema()
        comparison = self.registry._find(self.registry._rows("PartWorkbookComparison"), "ComparisonKey", comparison_key)
        if not comparison:
            raise PartIdentityError("PART_COMPARISON_NOT_FOUND", "The saved workbook comparison was not found.")
        comparison_fields = _row_fields(comparison)
        comparison_id = int(comparison["id"])
        families = [{"id": int(row["id"]), **_row_fields(row)} for row in self.registry._rows("PartComparisonFamily")
            if _ref_id(_row_fields(row).get("WorkbookComparison")) == comparison_id]
        differences = [{"id": int(row["id"]), **_row_fields(row)} for row in self.registry._rows("PartRequirementDifference")
            if _ref_id(_row_fields(row).get("WorkbookComparison")) == comparison_id]
        decisions = [{"id": int(row["id"]), **_row_fields(row)} for row in self.registry._rows("PartWorkbookDecision")
            if _ref_id(_row_fields(row).get("WorkbookComparison")) == comparison_id]
        proposals = [{"id": int(row["id"]), **_row_fields(row)} for row in self.registry._rows("PartChangeProposal")
            if _ref_id(_row_fields(row).get("WorkbookComparison")) == comparison_id]
        return {"comparison": {"id": comparison_id, **comparison_fields}, "families": families,
            "differences": differences, "decisions": decisions, "proposals": proposals,
            "policy": COMPARISON_POLICY, "engineeringFieldsOnly": True,
            "evidenceWarnings": [f"{row.get('Family')}: comparison evidence is incomplete ({row.get('SourceEvidenceStatus') or 'unknown source status'}; {row.get('ApplicabilityStatus') or 'applicability unconfirmed'})."
                for row in families if str(row.get("SourceEvidenceStatus") or "") != "ok"
                    or not bool(row.get("CompletenessConfirmed"))
                    or str(row.get("ApplicabilityStatus") or "") not in {"applicable", "not_applicable"}]}

    @_serialized
    def decide(self, *, comparison_key: str, action: str, difference_keys: list[str],
               actor: str, reason: str, old_data: bool = False, replacement_part_id: str = "",
               baseline_requirement_key: str = "",
               request_key: str) -> dict[str, Any]:
        self.registry._verify_writer()
        self._verify_schema()
        allowed = {"keep_existing_baseline", "propose_part_change", "use_different_part", "match_correspondence"}
        if action not in allowed or not request_key.strip() or not actor.strip():
            raise PartIdentityError("PART_DECISION_INPUT_INVALID", "Select a supported workbook decision and include a request identity.")
        comparison = self.registry._find(self.registry._rows("PartWorkbookComparison"), "ComparisonKey", comparison_key)
        if not comparison:
            raise PartIdentityError("PART_COMPARISON_NOT_FOUND", "The saved workbook comparison was not found.")
        cf = _row_fields(comparison)
        part_id = _ref_id(cf.get("ProductPart"))
        part = next((row for row in self.registry._rows("ProductPart") if int(row.get("id", -1)) == part_id), None)
        stable_part_id = str(_row_fields(part or {}).get("StablePartId") or "")
        current = self.registry.get_part(stable_part_id)
        revision = self.registry.current_revision(stable_part_id)
        if _ref_id(_row_fields(revision).get("ManufacturingBaseline")) != _ref_id(cf.get("BaselineProcessing")):
            raise PartIdentityError("PART_COMPARISON_STALE", "The accepted Part baseline changed after this comparison. Re-evaluate the workbook against the current baseline.")
        all_differences = [row for row in self.registry._rows("PartRequirementDifference") if _ref_id(_row_fields(row).get("WorkbookComparison")) == int(comparison["id"])]
        requested_keys = set(difference_keys or [])
        selected = [row for row in all_differences if _row_fields(row).get("DifferenceKey") in requested_keys]
        if not selected:
            raise PartIdentityError("PART_DECISION_INPUT_INVALID", "Select at least one saved source difference to resolve.")
        if any(str(_row_fields(row).get("DifferenceType") or "") in {"match", "incomplete_evidence"} for row in selected):
            raise PartIdentityError("PART_DECISION_INPUT_INVALID", "Resolve only conclusive non-match evidence; matching rows and incomplete families are not change decisions.")
        if not reason.strip():
            raise PartIdentityError("PART_DECISION_REASON_REQUIRED", "Record why this workbook difference is being kept, proposed or assigned to a different Part.")
        if action == "propose_part_change" and (cf.get("Status") != "complete" or any(_row_fields(row).get("DifferenceType") not in {"proposed_addition", "proposed_modification", "proposed_deletion"} for row in selected)):
            raise PartIdentityError("PART_CHANGE_PROPOSAL_INCOMPLETE", "Only complete, unambiguous additions, modifications and deletions can be collected into a pending Change Request proposal.")
        replacement = None
        if action == "use_different_part":
            if len(selected) != 1 or not replacement_part_id:
                raise PartIdentityError("PART_REPLACEMENT_REQUIRED", "Choose one incoming source difference and a replacement canonical Part.")
            replacement = self.registry.get_part(replacement_part_id)
            if not replacement or replacement.get("legacy") or replacement.get("status") == "retired" or replacement.get("publishStatus") != "published":
                raise PartIdentityError("PART_SELECTION_INVALID", "Choose an active, published canonical Part for the replacement mapping.")
            if not _ref_id(_row_fields(selected[0]).get("IncomingObservation")):
                raise PartIdentityError("PART_REPLACEMENT_SOURCE_REQUIRED", "A baseline-only deletion has no incoming source row to assign to another Part.")
        matched_baseline = None
        matched_modification = None
        if action == "match_correspondence":
            if len(selected) != 1 or str(_row_fields(selected[0]).get("DifferenceType") or "") != "ambiguous_correspondence":
                raise PartIdentityError("PART_MATCH_INPUT_INVALID", "Choose one incoming ambiguous row to match to a baseline requirement.")
            selected_fields = _row_fields(selected[0])
            incoming_observation_id = _ref_id(selected_fields.get("IncomingObservation"))
            if not incoming_observation_id:
                raise PartIdentityError("PART_MATCH_INPUT_INVALID", "Choose the incoming-side ambiguity row; a baseline-only row cannot be matched as incoming evidence.")
            candidates = _unjson(selected_fields.get("CandidateBaselineLines"))
            if not baseline_requirement_key or baseline_requirement_key not in candidates:
                raise PartIdentityError("PART_MATCH_CANDIDATE_INVALID", "Choose one of the baseline requirement candidates recorded for this incoming row.")
            matched_baseline = next((row for row in self.registry._rows("PartRevisionLine")
                if str(_row_fields(row).get("RequirementKey") or "") == baseline_requirement_key
                    and _ref_id(_row_fields(row).get("PartRevision")) == _ref_id(cf.get("BaselineRevision"))
                    and _ref_id(_row_fields(row).get("BaselineProcessing")) == _ref_id(cf.get("BaselineProcessing"))), None)
            if not matched_baseline:
                raise PartIdentityError("PART_MATCH_CANDIDATE_INVALID", "The selected baseline requirement is no longer part of this accepted baseline.")
            prior_match_decisions = [row for row in self.registry._rows("PartWorkbookDecision")
                if _ref_id(_row_fields(row).get("WorkbookComparison")) == int(comparison["id"])
                    and _ref_id(_row_fields(row).get("MatchedBaselineRevisionLine")) == int(matched_baseline["id"])
                    and str(_row_fields(row).get("Action") or "") == "match_correspondence"]
            if any(_row_fields(row).get("RequestKey") != request_key for row in prior_match_decisions):
                raise PartIdentityError("PART_MATCH_CANDIDATE_ALREADY_USED", "That baseline requirement has already been matched to another incoming source row.")
            existing_match_decisions = [row for row in self.registry._rows("PartWorkbookDecision")
                if _ref_id(_row_fields(row).get("PartRequirementDifference")) == int(selected[0]["id"])
                    and str(_row_fields(row).get("Action") or "") == "match_correspondence"]
            if any(_row_fields(row).get("RequestKey") != request_key for row in existing_match_decisions):
                raise PartIdentityError("PART_MATCH_SOURCE_ALREADY_USED", "That incoming source row has already been matched. Reload the saved comparison decisions.")
            if str(selected_fields.get("Status") or "") != "pending_review" and not existing_match_decisions:
                raise PartIdentityError("PART_MATCH_SOURCE_ALREADY_USED", "That ambiguity has already been resolved. Reload the saved comparison decisions.")
            # The incoming-side ambiguous record has no baseline side; recover
            # its selected baseline values from the canonical normalized line.
            baseline_record = next((row for row in self.registry._rows("PartBaselineProcessing")
                if int(row.get("id", -1)) == _ref_id(cf.get("BaselineProcessing"))), None)
            if not baseline_record:
                raise PartIdentityError("PART_BASELINE_UNAVAILABLE", "The comparison's normalized baseline record is unavailable.")
            baseline_item = next((item for item in self._baseline_requirements(current, revision, baseline_record)
                if item.get("key") == baseline_requirement_key), None)
            if not baseline_item:
                raise PartIdentityError("PART_MATCH_CANDIDATE_INVALID", "The selected normalized baseline requirement could not be read.")
            incoming_physical = _unjson(_row_fields(selected_fields.get("IncomingValues") or "{}")).get("physical")
            incoming_physical = incoming_physical or {}
            field_differences = [{"field": key, "baseline": (baseline_item.get("physical") or {}).get(key), "incoming": incoming_physical.get(key)}
                for key in sorted(set(baseline_item.get("physical") or {}) | set(incoming_physical))
                if (baseline_item.get("physical") or {}).get(key) != incoming_physical.get(key)]
            matched_modification = {"DifferenceKey": f"part-difference:{_digest([comparison_key, 'manual-match', baseline_requirement_key, incoming_observation_id])}",
                "WorkbookComparison": int(comparison["id"]), "ProductPart": int(part["id"]),
                "Family": _row_fields(selected[0]).get("Family"), "DifferenceType": "proposed_modification", "Status": "pending_review",
                "BaselineRevisionLine": int(matched_baseline["id"]), "BaselineLineMaster": _ref_id(_row_fields(matched_baseline).get("LineMaster")),
                "BaselineRequirementKey": baseline_requirement_key, "ResolvedFromDifference": int(selected[0]["id"]),
                "IncomingObservation": incoming_observation_id, "IncomingRequirementKey": _row_fields(selected[0]).get("IncomingRequirementKey") or "",
                "MappingGroupKey": _row_fields(selected[0]).get("MappingGroupKey") or "",
                "MappingEvidenceFingerprint": _row_fields(selected[0]).get("MappingEvidenceFingerprint") or "",
                "SourceSheet": _row_fields(selected[0]).get("SourceSheet") or "", "SourceRow": _row_fields(selected[0]).get("SourceRow"),
                "SourceCellEvidence": _row_fields(selected[0]).get("SourceCellEvidence") or "{}",
                "BaselineValues": _json({"physical": baseline_item.get("physical"), "rawFields": baseline_item.get("rawFields")}),
                "IncomingValues": _row_fields(selected[0]).get("IncomingValues") or "",
                "FieldDifferences": _json(field_differences), "CandidateBaselineLines": _json([]),
                "RequestKey": request_key, "RequestFingerprint": ""}

        fp = _digest([comparison_key, action, sorted(_row_fields(row).get("DifferenceKey") for row in selected),
            actor, reason.strip(), bool(old_data), replacement_part_id, baseline_requirement_key])
        req = self.registry._begin_request(request_key, "resolve_part_comparison", fp, comparison_key)
        if req.get("fields", {}).get("Status") == "published":
            saved_result = _unjson(req.get("fields", {}).get("Result"))
            if saved_result:
                saved_result["idempotent"] = True
                return saved_result
            return {**self.get_comparison(comparison_key), "idempotent": True}
        now = grist_datetime(utc_now())
        decision_rows = []
        for difference in selected:
            dfields = _row_fields(difference)
            key = f"part-decision:{_digest([comparison_key, dfields.get('DifferenceKey'), request_key])}"
            decision_rows.append({"DecisionKey": key, "WorkbookComparison": int(comparison["id"]),
                "PartRequirementDifference": int(difference["id"]), "ProductPart": int(part_id),
                "Action": action, "OldData": bool(old_data),
                "ReplacementPart": int(replacement["gristRecordId"]) if replacement and replacement.get("gristRecordId") else None,
                "MatchedBaselineRevisionLine": int(matched_baseline["id"]) if matched_baseline and action == "match_correspondence" else None,
                "Actor": actor.strip(), "Reason": reason.strip(), "OccurredAt": now, "RequestKey": request_key,
                "RequestFingerprint": _digest([key, fp])})
        self._upsert_many("PartWorkbookDecision", decision_rows)
        if matched_modification:
            matched_modification["RequestFingerprint"] = _digest([matched_modification["DifferenceKey"], fp])
            self._upsert_many("PartRequirementDifference", [matched_modification])
        for difference in selected:
            state = {"keep_existing_baseline": "kept_existing_baseline", "propose_part_change": "proposed_change",
                "use_different_part": "replacement_selected", "match_correspondence": "matched_correspondence"}[action]
            self._update_confirmed("PartRequirementDifference", difference, "DifferenceKey",
                str(_row_fields(difference).get("DifferenceKey")), {"Status": state})
        if action == "match_correspondence" and matched_baseline:
            matched_counterpart = next((row for row in all_differences
                if _row_fields(row).get("BaselineRequirementKey") == baseline_requirement_key
                    and str(_row_fields(row).get("DifferenceType") or "") == "ambiguous_correspondence"
                    and not _ref_id(_row_fields(row).get("IncomingObservation"))), None)
            if matched_counterpart and _row_fields(matched_counterpart).get("Status") == "pending_review":
                self._update_confirmed("PartRequirementDifference", matched_counterpart, "DifferenceKey",
                    str(_row_fields(matched_counterpart).get("DifferenceKey")), {"Status": "matched_correspondence"})
        proposal_result = None
        if action == "propose_part_change":
            proposal_key = f"part-change-proposal:{_digest([comparison_key, request_key])}"
            proposal = {"ProposalKey": proposal_key, "WorkbookComparison": int(comparison["id"]),
                "ProductPart": int(part_id), "BaselineProcessing": int(_ref_id(cf.get("BaselineProcessing"))),
                "BaselineRevision": int(_ref_id(cf.get("BaselineRevision"))), "Status": "pending_change_request",
                "ProposedChangeCount": len(selected), "CRRequired": True, "Actor": actor.strip(),
                "Reason": reason.strip(), "OccurredAt": now, "RequestKey": request_key,
                "RequestFingerprint": _digest([proposal_key, fp])}
            saved_proposal = self._upsert_many("PartChangeProposal", [proposal])[proposal_key]
            proposal_result = {"proposalKey": proposal_key, "id": int(saved_proposal["id"]),
                "status": "pending_change_request", "crRequired": True,
                "message": "The accepted Rev A baseline is unchanged. Approved Change Request processing is required before publication."}
        decision_status = "kept_existing_baseline" if action == "keep_existing_baseline" else "pending_change_request" if action == "propose_part_change" else "replacement_selected_pending_mapping_save"
        comparison_values = {"DecisionStatus": decision_status}
        if action == "match_correspondence":
            pending_ambiguous = sum(1 for row in self.registry._rows("PartRequirementDifference")
                if _ref_id(_row_fields(row).get("WorkbookComparison")) == int(comparison["id"])
                    and _row_fields(row).get("DifferenceType") == "ambiguous_correspondence"
                    and _row_fields(row).get("Status") == "pending_review")
            comparison_values.update({"DecisionStatus": "ambiguous_match_recorded",
                "AmbiguousCount": pending_ambiguous,
                "ModificationCount": int(cf.get("ModificationCount") or 0) + 1})
        self._update_confirmed("PartWorkbookComparison", comparison, "ComparisonKey", comparison_key,
            comparison_values)
        result = {**self.get_comparison(comparison_key), "proposalResult": proposal_result,
            "replacementMapping": ({"partId": replacement_part_id, "path": cf.get("WorkbookPath") or "",
                "sourceSheet": _row_fields(selected[0]).get("SourceSheet"), "sourceRow": _row_fields(selected[0]).get("SourceRow"),
                "groupKey": _row_fields(selected[0]).get("MappingGroupKey") or "",
                "evidenceFingerprint": _row_fields(selected[0]).get("MappingEvidenceFingerprint") or "",
                "sourceHash": cf.get("SourceHash"), "fileKey": cf.get("FileKey"),
                "associationKey": cf.get("AssociationKey") or "", "associationVersion": cf.get("AssociationVersion") or 0} if replacement else None),
            "idempotent": False}
        self.registry._complete_request(req, result)
        return result
