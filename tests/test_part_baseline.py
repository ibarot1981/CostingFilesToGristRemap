from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

from app.grist_parts import GristPartRegistry
from app.part_baseline import PartBaselineService
from app.part_baseline import _family_confirmations, _raw_requirement
from app.part_identity import PartIdentityError
from app.part_mapping import MemoryPartStore, PartConflict, PartRegistryMappingStore, PartSourceGroups, mapping_detail, save_mapping
from test_grist_parts_recovery import MemoryGrist


MCL = "5. Material Cut List Price"
TOOLSHOP = "Tool Shop Items"
CNC = "CNC Cut List"


def source_row(row_number: int, *, quantity="2", dimension="3 x 25", material="MS Plate", weight="0.105",
               remarks="source note", rate="99"):
    fields = {"material_to_cut": material, "dimension_to_cut_mm": dimension, "qty": quantity,
        "optional_item_group_1": "HF", "in_use": "Yes", "total_weight_kg": weight,
        "product_part_name": "Plate 1", "part_category": "Frame", "remarks": remarks, "rate": rate}
    headers = {"material_to_cut": "Material to Cut", "dimension_to_cut_mm": "Dimension to Cut (mm)",
        "qty": "Quantity Nos", "optional_item_group_1": "Optional Item Group 1", "in_use": "In Use",
        "total_weight_kg": "Total Weight (kg)", "product_part_name": "Plate Part to Cut",
        "part_category": "Part Category", "remarks": "Remarks", "rate": "Rate"}
    cells = {key: {"cell": f"{column}{row_number}", "value": value}
        for key, column, value in [("material_to_cut", "B", material), ("dimension_to_cut_mm", "C", dimension),
            ("qty", "D", quantity), ("total_weight_kg", "E", weight), ("remarks", "F", remarks), ("rate", "G", rate)]}
    return {"sheet": MCL, "row": row_number, "fields": fields, "sourceHeaders": headers,
        "sourceHeaderCells": {"material_to_cut": "B8", "dimension_to_cut_mm": "C8", "qty": "D8"},
        "sourceCells": cells, "headerRow": 8, "availableFields": list(fields)}


def all_source_diagnostics():
    return {sheet: {"present": True, "status": "ok", "labelField": "product_part_name" if sheet != CNC else "part_category",
        "labelHeader": "Machine Piece Description" if sheet != CNC else "Part Category", "labelHeaderCell": "A8"}
        for sheet in (MCL, TOOLSHOP, CNC)}


class PartBaselineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = MemoryGrist()
        self.registry = GristPartRegistry(self.client, Path(self.temp.name) / "parts.sqlite3")
        self.registry._verify_writer = lambda: None
        self.part = self.registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description="Chassis", variant="Standard", expected_name="S1K — Chassis — Standard",
            actor="test operator", reason="isolated baseline fixture", request_key="create-baseline-fixture",
            revision_assertion="A")["part"]
        self.mapping_store = PartRegistryMappingStore(MemoryPartStore(), self.registry)
        self.association = SimpleNamespace(id="association:pilot", version=1)
        self.service = PartBaselineService(self.registry)

    def groups(self, rows):
        items = []
        if rows:
            items.append({"key": "mcl:chassis", "mappingPolicyVersion": "part-source-labels-v2",
                "evidenceFingerprint": "evidence:" + ";".join(str(row["row"]) for row in rows),
                "sheet": MCL, "labelField": "product_part_name", "description": "Chassis", "rows": rows,
                "part": None, "reviewed": False, "version": 0})
        return PartSourceGroups(items, all_source_diagnostics())

    def save_rows(self, rows, source_hash, *, expected_version=None, request_key=None):
        groups = self.groups(rows)
        detail = mapping_detail(self.mapping_store, file_id="pilot.ods", source_hash=source_hash,
            association=self.association, groups=groups)
        if expected_version is None:
            expected_version = detail["version"]
        group_reasons = {}
        for group in detail.get("groups", []):
            previous = group.get("previousAssignment") or {}
            prior_ids = set(previous.get("assignedPartIds") or [])
            current_id = str((group.get("part") or {}).get("id") or "")
            if previous.get("hasPriorAssignment") and (not current_id or any(value != self.part["id"] for value in prior_ids)):
                group_reasons[group["key"]] = "Isolated test mapping replacement"
        result = save_mapping(self.mapping_store, file_id="pilot.ods", source_hash=source_hash,
            association=self.association, groups=groups, decisions={"mcl:chassis": self.part["id"]},
            expected_hash=source_hash, expected_version=expected_version,
            expected_association=self.association.id, expected_association_version=self.association.version,
            actor="mapping reviewer", group_reasons=group_reasons, request_key=request_key or f"map:{source_hash}")
        return groups, result

    def family_choices(self, *, mcl_status="applicable", complete=True):
        return {"mcl": {"status": mcl_status, "confirmedComplete": complete},
            "toolshop": {"status": "not_applicable", "confirmedComplete": True},
            "cnc": {"status": "not_applicable", "confirmedComplete": True}}

    def family_group(self, family, row_number, *, description=None, **overrides):
        sheet = {"mcl": MCL, "toolshop": TOOLSHOP, "cnc": CNC}[family]
        row = source_row(row_number)
        row["sheet"] = sheet
        row["fields"].update(overrides)
        label_field = "part_category" if family == "cnc" else "product_part_name"
        label = description or (row["fields"].get(label_field) or f"{family} item")
        row["fields"][label_field] = label
        key = f"{family}:{row_number}:{label}"
        return {"key": key, "mappingPolicyVersion": "part-source-labels-v2", "evidenceFingerprint": f"evidence:{key}",
            "sheet": sheet, "labelField": label_field, "description": label, "blankDescription": False,
            "rows": [row], "part": None, "reviewed": False, "version": 0}

    def save_group_set(self, groups, source_hash, *, file_id="pilot.ods"):
        groups = PartSourceGroups(groups, all_source_diagnostics())
        detail = mapping_detail(self.mapping_store, file_id=file_id, source_hash=source_hash,
            association=self.association, groups=groups)
        return save_mapping(self.mapping_store, file_id=file_id, source_hash=source_hash, association=self.association,
            groups=groups, decisions={group["key"]: self.part["id"] for group in groups}, expected_hash=source_hash,
            expected_version=detail["version"], expected_association=self.association.id,
            expected_association_version=self.association.version, actor="mapping reviewer", request_key=f"map:{source_hash}")

    def family_choices_for(self, applicable):
        return {family: {"status": "applicable" if family in applicable else "not_applicable", "confirmedComplete": True}
            for family in ("mcl", "toolshop", "cnc")}

    def establish_group_set(self, families, *, source_hash="family-baseline", file_id="pilot.ods"):
        groups = []
        applicable = set(families)
        for index, family in enumerate(families, start=20):
            if family == "mcl":
                groups.extend([self.family_group(family, index, description=f"MCL item {index}"),
                    self.family_group(family, index + 100, description=f"MCL item {index + 100}", quantity="1")])
            elif family == "toolshop":
                groups.append(self.family_group(family, index, description=f"Tool {index}", toolshop_part_name=f"Tool {index}", item_code=f"TS-{index:02}", item_name=f"Tool {index}"))
            else:
                groups.append(self.family_group(family, index, description=f"Frame {index}", part_category=f"Frame {index}", product_part_name="Plate 1", length="300", width="25", thickness="3"))
        groups = PartSourceGroups(groups, all_source_diagnostics())
        mapping_result = self.save_group_set(groups, source_hash, file_id=file_id)
        result = self.service.establish(part_id=self.part["id"], file_id=file_id, workbook_name=f"{file_id}.ods",
            workbook_path=f"C:/isolated/{file_id}.ods", source_hash=source_hash, association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices_for(applicable),
            actor="baseline reviewer", reason="Verify family-specific baseline", request_key=f"baseline:{source_hash}",
            expected_hash=source_hash, expected_association=self.association.id,
            expected_association_version=self.association.version, expected_mapping_version=mapping_result["version"])
        return result

    def establish(self, rows, *, source_hash="hash-v1", request_key="baseline-v1", append_existing=False):
        groups, mapping_result = self.save_rows(rows, source_hash, request_key=f"map:{source_hash}")
        return self.service.establish(part_id=self.part["id"], file_id="pilot.ods", workbook_name="pilot.ods",
            workbook_path="C:/isolated/pilot.ods", source_hash=source_hash, association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices(),
            actor="baseline reviewer", reason="Initial Rev A review", request_key=request_key,
            expected_hash=source_hash, expected_association=self.association.id,
            expected_association_version=self.association.version, expected_mapping_version=mapping_result["version"],
            append_existing=append_existing)

    def compare_rows(self, rows, *, source_hash, request_key):
        groups, mapped = self.save_rows(rows, source_hash, request_key=f"map:{source_hash}")
        result = self.service.compare(part_id=self.part["id"], file_id="pilot.ods", workbook_name=f"{source_hash}.ods",
            workbook_path="C:/isolated/pilot.ods", source_hash=source_hash, association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices(), actor="comparison reviewer",
            reason="Review current mapped source", request_key=request_key, expected_hash=source_hash,
            expected_association=self.association.id, expected_association_version=self.association.version,
            expected_mapping_version=mapped["version"])
        return groups, mapped, result

    def decision_context(self, groups, *, source_hash, association=None):
        current_association = association or self.association
        mapping = mapping_detail(self.mapping_store, file_id="pilot.ods", source_hash=source_hash,
            association=current_association, groups=groups, refresh_parts=True)
        return {"fileId": "pilot.ods", "sourceHash": source_hash, "association": current_association, "mapping": mapping}

    def test_initial_assignment_needs_no_reason_and_baseline_writes_normalized_grist_records(self):
        rows = [source_row(10), source_row(11, quantity="1", dimension="5 x 25")]
        result = self.establish(rows)
        self.assertEqual(result["baselineStatus"], "established")
        self.assertEqual(result["engineeringRevision"], "A")
        self.assertTrue(result["finalizationRequired"])
        mapping_rows = self.client.tables["PartMappingReview"]
        self.assertEqual({row["fields"]["ActionType"] for row in mapping_rows}, {"initial_assignment"})
        self.assertEqual({row["fields"]["Reason"] for row in mapping_rows}, {"Initial Part assignment"})
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 2)
        self.assertEqual(len(self.client.tables["SourceLineObservation"]), 2)
        self.assertEqual(len(self.client.tables["PartBaselineFamily"]), 3)
        self.assertEqual(len(self.client.tables.get("CostSnapshot", [])), 0)
        detail = self.service.baseline_detail(self.part["id"])
        self.assertEqual(detail["status"], "established")
        self.assertEqual(detail["baseline"]["WorkbookName"], "pilot.ods")
        self.assertEqual(len(detail["requirements"]), 2)

    def test_toolshop_only_baseline_does_not_require_other_source_families(self):
        result = self.establish_group_set(["toolshop"], source_hash="toolshop-only", file_id="toolshop-only")
        self.assertEqual(result["baselineStatus"], "established")
        detail = self.service.baseline_detail(self.part["id"])
        self.assertEqual(len(detail["requirements"]), 1)
        self.assertEqual(detail["requirements"][0]["physical"]["family"], "toolshop")
        families = {row["fields"]["Family"]: row["fields"] for row in self.client.tables["PartBaselineFamily"]}
        self.assertEqual(families["toolshop"]["ApplicabilityStatus"], "applicable")
        self.assertEqual(families["mcl"]["ApplicabilityStatus"], "not_applicable")

    def test_cnc_only_baseline_uses_part_category_and_preserves_plate_identity(self):
        result = self.establish_group_set(["cnc"], source_hash="cnc-only", file_id="cnc-only")
        self.assertEqual(result["baselineStatus"], "established")
        requirement = self.service.baseline_detail(self.part["id"])["requirements"][0]
        self.assertEqual(requirement["physical"]["family"], "cnc")
        self.assertEqual(requirement["rawFields"]["part_category"], "Frame 20")
        self.assertEqual(requirement["physical"]["plate_part"], "plate 1")

    def test_combined_baseline_establishes_every_applicable_mapped_source_group(self):
        result = self.establish_group_set(["mcl", "toolshop", "cnc"], source_hash="combined-families", file_id="combined-families")
        self.assertEqual(result["requirementCount"], 4)
        detail = self.service.baseline_detail(self.part["id"])
        self.assertEqual({item["physical"]["family"] for item in detail["requirements"]}, {"mcl", "toolshop", "cnc"})
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 4)
        self.assertEqual(len(self.client.tables["SourceLineObservation"]), 4)

    def test_replacement_requires_a_reason_but_unchanged_context_reconfirmation_does_not(self):
        rows = [source_row(10)]
        self.save_rows(rows, "hash-first", request_key="map:first")
        second = self.registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description="Chassis Alt", variant="Standard", expected_name="S1K — Chassis Alt — Standard",
            actor="test operator", reason="replacement fixture", request_key="create-replacement-fixture",
            revision_assertion="A")["part"]
        replacement_groups = self.groups(rows)
        with self.assertRaises(PartConflict) as missing_reason:
            save_mapping(self.mapping_store, file_id="pilot.ods", source_hash="hash-second", association=self.association,
                groups=replacement_groups, decisions={"mcl:chassis": second["id"]}, expected_hash="hash-second",
                expected_version=1, expected_association=self.association.id, expected_association_version=self.association.version,
                actor="mapping reviewer", request_key="replace-with-reason")
        self.assertEqual(missing_reason.exception.code, "PART_REVIEW_REASON_REQUIRED")
        replaced = save_mapping(self.mapping_store, file_id="pilot.ods", source_hash="hash-second", association=self.association,
            groups=replacement_groups, decisions={"mcl:chassis": second["id"]}, expected_hash="hash-second",
            expected_version=1, expected_association=self.association.id, expected_association_version=self.association.version,
            actor="mapping reviewer", request_key="replace-with-reason", group_reasons={"mcl:chassis": "Distinct chassis design"})
        self.assertEqual(replaced["assignments"]["mcl:chassis"]["actionType"], "replace_assignment")
        reconfirmed = save_mapping(self.mapping_store, file_id="pilot.ods", source_hash="hash-third", association=self.association,
            groups=self.groups(rows), decisions={"mcl:chassis": second["id"]}, expected_hash="hash-third",
            expected_version=2, expected_association=self.association.id, expected_association_version=self.association.version,
            actor="mapping reviewer", request_key="reconfirm-current-context")
        self.assertEqual(reconfirmed["assignments"]["mcl:chassis"]["actionType"], "context_reconfirmation")
        self.assertIn("refreshed workbook", reconfirmed["assignments"]["mcl:chassis"]["reason"])

    def test_establishment_rejects_unmapped_applicable_family_and_missing_evidence_is_not_na(self):
        groups = self.groups([source_row(10)])
        _, errors = _family_confirmations({"groups": groups, "sourceSheets": groups.diagnostics}, self.part["id"],
            self.family_choices(mcl_status="applicable", complete=True))
        self.assertTrue(any("at least one saved" in error for error in errors))
        missing = {"groups": [], "sourceSheets": {**groups.diagnostics, MCL: {"status": "sheet_missing"}}}
        _, errors = _family_confirmations(missing, self.part["id"], {
            "mcl": {"status": "not_applicable", "confirmedComplete": True},
            "toolshop": {"status": "not_applicable", "confirmedComplete": True},
            "cnc": {"status": "not_applicable", "confirmedComplete": True}})
        self.assertTrue(any("cannot be called not applicable" in error for error in errors))

    def test_grist_commit_response_loss_recovers_the_same_initial_baseline(self):
        rows = [source_row(10)]
        groups, mapping_result = self.save_rows(rows, "hash-recovery", request_key="map:recovery")
        kwargs = {"part_id": self.part["id"], "file_id": "pilot.ods", "workbook_name": "pilot.ods",
            "workbook_path": "C:/isolated/pilot.ods", "source_hash": "hash-recovery", "association": self.association,
            "groups": groups, "mapping_store": self.mapping_store, "source_families": self.family_choices(),
            "actor": "baseline reviewer", "reason": "Retry exact write", "request_key": "baseline-recovery",
            "expected_hash": "hash-recovery", "expected_association": self.association.id,
            "expected_association_version": self.association.version, "expected_mapping_version": mapping_result["version"]}
        self.client.lose_after_create_table = "PartRevisionLine"
        with self.assertRaises(TimeoutError):
            self.service.establish(**kwargs)
        changed_context = {**kwargs, "source_hash": "hash-changed-after-partial-write"}
        with self.assertRaises(PartIdentityError) as recovery_required:
            self.service.establish(**changed_context)
        self.assertEqual(recovery_required.exception.code, "PART_BASELINE_RECOVERY_REQUIRED")
        review = self.service.review(part_id=self.part["id"], file_id="pilot.ods", workbook_path=kwargs["workbook_path"],
            source_hash="hash-recovery", association=self.association, groups=groups, mapping_store=self.mapping_store)
        self.assertEqual(review["baselineStatus"], "recovery_required")
        recovered = PartBaselineService(self.registry).establish(**kwargs)
        self.assertEqual(recovered["baselineStatus"], "established")
        self.assertTrue(recovered["idempotent"])
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 1)
        self.assertEqual(len(self.client.tables["SourceLineObservation"]), 1)

    def test_concurrent_initial_baseline_requests_cannot_claim_one_part_twice(self):
        rows = [source_row(10)]
        groups, mapping_result = self.save_rows(rows, "hash-concurrent", request_key="map:concurrent")
        second_registry = GristPartRegistry(self.client, self.registry.path)
        second_registry._verify_writer = lambda: None
        services = [self.service, PartBaselineService(second_registry)]
        arguments = {"part_id": self.part["id"], "file_id": "pilot.ods", "workbook_name": "pilot.ods",
            "workbook_path": "C:/isolated/pilot.ods", "source_hash": "hash-concurrent", "association": self.association,
            "groups": groups, "mapping_store": self.mapping_store, "source_families": self.family_choices(),
            "actor": "baseline reviewer", "reason": "Concurrent initial baseline", "expected_hash": "hash-concurrent",
            "expected_association": self.association.id, "expected_association_version": self.association.version,
            "expected_mapping_version": mapping_result["version"]}
        barrier = Barrier(2)

        def establish(service, request_key):
            barrier.wait()
            try:
                return ("ok", service.establish(**arguments, request_key=request_key))
            except Exception as error:
                return ("error", getattr(error, "code", type(error).__name__))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda item: establish(*item), [(services[0], "baseline-concurrent-a"),
                (services[1], "baseline-concurrent-b")]))
        self.assertEqual([state for state, _ in outcomes].count("ok"), 1)
        errors = [value for state, value in outcomes if state == "error"]
        self.assertEqual(errors, ["PART_BASELINE_EXISTS"])
        self.assertEqual(len(self.client.tables["PartBaselineProcessing"]), 1)
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 1)

    def test_existing_purchase_and_component_content_requires_append_and_is_preserved(self):
        revision = self.registry.current_revision(self.part["id"])
        self.client.tables["PartPurchaseSpecification"] = [{"id": 500, "fields": {"PartRevision": int(revision["id"]), "Status": "active"}}]
        self.client.tables["PartComponentRevision"] = [{"id": 501, "fields": {"ParentRevision": int(revision["id"]), "Quantity": 2}}]
        groups, mapping_result = self.save_rows([source_row(10)], "hash-existing", request_key="map:existing")
        arguments = {"part_id": self.part["id"], "file_id": "pilot.ods", "workbook_name": "pilot.ods",
            "workbook_path": "C:/isolated/pilot.ods", "source_hash": "hash-existing", "association": self.association,
            "groups": groups, "mapping_store": self.mapping_store, "source_families": self.family_choices(),
            "actor": "baseline reviewer", "reason": "Append reviewed workbook lines", "request_key": "baseline-existing",
            "expected_hash": "hash-existing", "expected_association": self.association.id,
            "expected_association_version": self.association.version, "expected_mapping_version": mapping_result["version"]}
        with self.assertRaises(Exception) as caught:
            self.service.establish(**arguments)
        self.assertEqual(caught.exception.code, "PART_BASELINE_JOIN_CONFIRMATION_REQUIRED")
        arguments["append_existing"] = True
        result = self.service.establish(**arguments)
        self.assertEqual(result["baselineStatus"], "established")
        self.assertEqual(len(self.client.tables["PartPurchaseSpecification"]), 1)
        self.assertEqual(len(self.client.tables["PartComponentRevision"]), 1)

    def test_comparison_retry_recovers_partial_grist_write_and_proposal_keeps_rev_a(self):
        self.establish([source_row(10), source_row(11, quantity="1", dimension="5 x 25")])
        incoming = [source_row(10, quantity="3"), source_row(11, quantity="1", dimension="5 x 25")]
        groups, mapped = self.save_rows(incoming, "hash-v2", expected_version=1, request_key="map:hash-v2")
        arguments = {"part_id": self.part["id"], "file_id": "pilot.ods", "workbook_name": "pilot-v2.ods",
            "workbook_path": "C:/isolated/pilot-v2.ods", "source_hash": "hash-v2", "association": self.association,
            "groups": groups, "mapping_store": self.mapping_store, "source_families": self.family_choices(),
            "actor": "comparison reviewer", "reason": "Check latest workbook", "request_key": "compare-v2",
            "expected_hash": "hash-v2", "expected_association": self.association.id,
            "expected_association_version": self.association.version, "expected_mapping_version": mapped["version"]}
        self.client.lose_after_create_table = "PartRequirementDifference"
        with self.assertRaises(TimeoutError):
            self.service.compare(**arguments)
        comparison = PartBaselineService(self.registry).compare(**arguments)
        self.assertEqual(comparison["comparison"]["Status"], "complete")
        self.assertEqual(len(self.client.tables["PartRequirementDifference"]), 2)
        difference_types = {row["DifferenceType"] for row in comparison["differences"]}
        self.assertEqual(difference_types, {"match", "proposed_modification"})
        diff = next(row for row in comparison["differences"] if row["DifferenceType"] == "proposed_modification")
        proposal = self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action="propose_part_change",
            difference_keys=[diff["DifferenceKey"]], actor="engineering reviewer", reason="Review quantity change",
            request_key="decide-v2")
        self.assertTrue(proposal["proposalResult"]["crRequired"])
        self.assertEqual(proposal["proposalResult"]["status"], "pending_change_request")
        part_after = self.registry.get_part(self.part["id"])
        revision_after = self.registry.current_revision(self.part["id"])
        self.assertEqual(part_after["engineeringRevision"], "A")
        self.assertEqual(revision_after["fields"]["ManufacturingBaselineStatus"], "established")
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 2)

    def test_incomplete_comparison_preserves_observations_without_claiming_deletion(self):
        self.establish([source_row(10), source_row(11, quantity="1", dimension="5 x 25")])
        groups, mapped = self.save_rows([source_row(10)], "hash-partial", expected_version=1, request_key="map:hash-partial")
        families = self.family_choices(complete=False)
        result = self.service.compare(part_id=self.part["id"], file_id="pilot.ods", workbook_name="partial.ods",
            workbook_path="C:/isolated/partial.ods", source_hash="hash-partial", association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=families, actor="reviewer", reason="Partial source",
            request_key="compare-partial", expected_hash="hash-partial", expected_association=self.association.id,
            expected_association_version=self.association.version, expected_mapping_version=mapped["version"])
        self.assertEqual(result["comparison"]["Status"], "incomplete_evidence")
        types = {row["DifferenceType"] for row in result["differences"]}
        self.assertNotIn("proposed_deletion", types)
        self.assertIn("incomplete_evidence", types)
        self.assertTrue(result["evidenceWarnings"])

    def test_keep_baseline_records_old_data_and_preserves_accepted_requirement(self):
        self.establish([source_row(10, quantity="2")])
        groups, mapped = self.save_rows([source_row(10, quantity="3")], "hash-old-workbook", expected_version=1,
            request_key="map:old-workbook")
        comparison = self.service.compare(part_id=self.part["id"], file_id="pilot.ods", workbook_name="older-export.ods",
            workbook_path="C:/isolated/older-export.ods", source_hash="hash-old-workbook", association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices(), actor="reviewer",
            reason="Review older workbook export", request_key="compare-old-workbook", expected_hash="hash-old-workbook",
            expected_association=self.association.id, expected_association_version=self.association.version,
            expected_mapping_version=mapped["version"])
        difference = next(item for item in comparison["differences"] if item["DifferenceType"] == "proposed_modification")
        kept = self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action="keep_existing_baseline",
            difference_keys=[difference["DifferenceKey"]], actor="engineering reviewer", reason="This export predates the accepted definition",
            old_data=True, request_key="decision-old-workbook")
        self.assertEqual(kept["comparison"]["DecisionStatus"], "kept_existing_baseline")
        decision = kept["decisions"][0]
        self.assertTrue(decision["OldData"])
        self.assertEqual(decision["Action"], "keep_existing_baseline")
        accepted = self.service.baseline_detail(self.part["id"])["requirements"]
        self.assertEqual(accepted[0]["physical"]["quantity"], "2")
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 1)
        incoming = next(row for row in self.client.tables["SourceLineObservation"]
            if row["fields"].get("Status") == "incoming_comparison")
        self.assertEqual(incoming["fields"]["SourceHash"], "hash-old-workbook")

    def test_different_part_decision_returns_exact_mapping_provenance(self):
        self.establish([source_row(10, quantity="2")])
        groups, mapped = self.save_rows([source_row(10, quantity="3")], "hash-distinct-design", expected_version=1,
            request_key="map:distinct-design")
        comparison = self.service.compare(part_id=self.part["id"], file_id="pilot.ods", workbook_name="pilot-new-design.ods",
            workbook_path="C:/isolated/pilot-new-design.ods", source_hash="hash-distinct-design", association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices(), actor="reviewer",
            reason="Compare a distinct chassis", request_key="compare-distinct-design", expected_hash="hash-distinct-design",
            expected_association=self.association.id, expected_association_version=self.association.version,
            expected_mapping_version=mapped["version"])
        difference = next(item for item in comparison["differences"] if item["DifferenceType"] == "proposed_modification")
        replacement = self.registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description="Separate Frame", variant="Standard", expected_name="S1K — Separate Frame — Standard",
            actor="reviewer", reason="Different physical design", request_key="create-separate-frame", revision_assertion="A")["part"]
        result = self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action="use_different_part",
            difference_keys=[difference["DifferenceKey"]], replacement_part_id=replacement["id"], actor="reviewer",
            reason="The incoming design is a separate Part", request_key="decision-separate-frame")
        self.assertEqual(result["replacementMapping"], {"partId": replacement["id"], "path": "C:/isolated/pilot-new-design.ods",
            "sourceSheet": MCL, "sourceRow": 10, "groupKey": "mcl:chassis", "evidenceFingerprint": "evidence:10",
            "sourceHash": "hash-distinct-design", "fileKey": "pilot.ods", "associationKey": "association:pilot", "associationVersion": 1})
        self.assertEqual(self.registry.get_part(self.part["id"])["engineeringRevision"], "A")
        self.assertEqual(len(self.client.tables["PartRevisionLine"]), 1)

    def test_material_substitution_can_be_matched_explicitly_then_proposed_as_a_cr_change(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2")])
        incoming_row = source_row(10, material="Aluminum", dimension="3 x 25", quantity="2")
        groups, mapped = self.save_rows([incoming_row], "hash-substitution", expected_version=1, request_key="map:substitution")
        comparison = self.service.compare(part_id=self.part["id"], file_id="pilot.ods", workbook_name="substitution.ods",
            workbook_path="C:/isolated/substitution.ods", source_hash="hash-substitution", association=self.association,
            groups=groups, mapping_store=self.mapping_store, source_families=self.family_choices(), actor="reviewer",
            reason="Check possible material substitution", request_key="compare-substitution",
            expected_hash="hash-substitution", expected_association=self.association.id,
            expected_association_version=self.association.version, expected_mapping_version=mapped["version"])
        incoming_ambiguity = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row["IncomingObservation"])
        baseline_ambiguity = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row["BaselineRevisionLine"])
        self.assertIn(baseline_ambiguity["BaselineRequirementKey"], incoming_ambiguity["CandidateBaselineLines"])
        self.client.lose_after_create_table = "PartRequirementDifference"
        match_arguments = {"comparison_key": comparison["comparison"]["ComparisonKey"],
            "action": "match_correspondence", "difference_keys": [incoming_ambiguity["DifferenceKey"]],
            "baseline_requirement_key": baseline_ambiguity["BaselineRequirementKey"],
            "actor": "engineering reviewer", "reason": "The material changed on the same identified item",
            "request_key": "match-substitution"}
        with self.assertRaises(TimeoutError):
            self.service.decide(**match_arguments)
        matched = PartBaselineService(self.registry).decide(**match_arguments)
        modification = next(row for row in matched["differences"] if row["DifferenceType"] == "proposed_modification")
        self.assertEqual(modification["BaselineRequirementKey"], baseline_ambiguity["BaselineRequirementKey"])
        field_differences = json.loads(modification["FieldDifferences"])
        self.assertEqual(field_differences, [{"field": "material", "baseline": "ms plate", "incoming": "aluminum"}])
        incoming_values = json.loads(modification["IncomingValues"])
        self.assertEqual(incoming_values["rawFields"]["material_to_cut"], "Aluminum")
        self.assertEqual(incoming_values["physical"]["quantity"], "2")
        self.assertEqual(incoming_values["physical"]["quantity_uom"], "nos")
        self.assertEqual(incoming_values["physical"]["dimension"], "3 x 25")
        self.assertEqual(incoming_values["physical"]["dimension_uom"], "mm")
        self.assertEqual(incoming_values["physical"]["weight_kg"], "0.11")
        self.assertEqual(incoming_values["physical"]["weight_uom"], "kg")
        self.assertIn("matched_correspondence", {row["Status"] for row in matched["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence"})
        self.assertEqual(matched["comparison"]["AmbiguousCount"], 0)
        proposal = self.service.decide(comparison_key=matched["comparison"]["ComparisonKey"], action="propose_part_change",
            difference_keys=[modification["DifferenceKey"]], actor="engineering reviewer", reason="Propose reviewed material substitution",
            request_key="propose-substitution")
        self.assertEqual(proposal["comparison"]["ModificationCount"], 1)
        self.assertEqual(self.registry.get_part(self.part["id"])["engineeringRevision"], "A")
        proposal_row = proposal["proposals"][0]
        self.assertEqual(proposal_row["ProposedChangeCount"], 1)
        linked_modification = next(row for row in proposal["differences"] if row["DifferenceKey"] == modification["DifferenceKey"])
        self.assertEqual(json.loads(linked_modification["FieldDifferences"]), field_differences)
        self.assertEqual(json.loads(linked_modification["IncomingValues"])["physical"]["material"], "aluminum")
        self.assertEqual(proposal_row["WorkbookComparison"], linked_modification["WorkbookComparison"])
        self.assertEqual(proposal_row["BaselineRevision"], proposal["comparison"]["BaselineRevision"])
        self.assertEqual(proposal_row["RequestKey"], "propose-substitution")

    def test_malformed_manual_match_evidence_is_recoverable_and_never_creates_a_proposal(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2")])
        groups, _mapped, comparison = self.compare_rows([source_row(10, material="Aluminum", dimension="3 x 25", quantity="2")],
            source_hash="hash-malformed-match", request_key="compare-malformed-match")
        incoming = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row.get("IncomingObservation"))
        baseline = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row.get("BaselineRevisionLine"))
        saved_row = next(row for row in self.client.tables["PartRequirementDifference"]
            if row["fields"].get("DifferenceKey") == incoming["DifferenceKey"])
        counts_before = {table: len(self.client.tables.get(table, [])) for table in ("PartWorkbookDecision", "PartChangeProposal", "PartRequirementDifference")}
        arguments = {"comparison_key": comparison["comparison"]["ComparisonKey"], "action": "match_correspondence",
            "difference_keys": [incoming["DifferenceKey"]], "baseline_requirement_key": baseline["BaselineRequirementKey"],
            "actor": "engineering reviewer", "reason": "Validate incoming evidence before matching", "request_key": "match-malformed"}
        for request_key, bad_value in (("match-malformed-json", "{not valid JSON"),
                                       ("match-missing-physical", json.dumps({"rawFields": {"qty": "2"}}))):
            saved_row["fields"]["IncomingValues"] = bad_value
            with self.subTest(request=request_key), self.assertRaises(Exception) as caught:
                self.service.decide(**{**arguments, "request_key": request_key})
            self.assertEqual(caught.exception.code, "PART_COMPARISON_EVIDENCE_INVALID")
        self.assertEqual({table: len(self.client.tables.get(table, [])) for table in counts_before}, counts_before)
        self.assertEqual(saved_row["fields"]["Status"], "pending_review")
        self.assertFalse(any(row["fields"].get("RequestKey", "").startswith("match-") for row in self.client.tables["PartRegistryRequest"]))

    def test_manual_match_preserves_multiple_legitimate_physical_changes(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2", weight="0.11")])
        groups, _mapped, comparison = self.compare_rows([source_row(10, material="Aluminum", dimension="4 x 25", quantity="3", weight="0.22")],
            source_hash="hash-changed-physical-evidence", request_key="compare-changed-physical-evidence")
        incoming = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row.get("IncomingObservation"))
        baseline = next(row for row in comparison["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence" and row.get("BaselineRevisionLine"))
        matched = self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action="match_correspondence",
            difference_keys=[incoming["DifferenceKey"]], baseline_requirement_key=baseline["BaselineRequirementKey"],
            actor="engineering reviewer", reason="Compare all changed physical values", request_key="match-changed-physical")
        modification = next(row for row in matched["differences"] if row.get("ResolvedFromDifference") == incoming["id"])
        values = json.loads(modification["FieldDifferences"])
        by_field = {item["field"]: (item["baseline"], item["incoming"]) for item in values}
        self.assertEqual(by_field["material"], ("ms plate", "aluminum"))
        self.assertEqual(by_field["quantity"], ("2", "3"))
        self.assertEqual(by_field["dimension"], ("3 x 25", "4 x 25"))
        self.assertEqual(by_field["weight_kg"], ("0.11", "0.22"))
        self.assertNotIn("quantity_uom", by_field)
        self.assertNotIn("dimension_uom", by_field)
        self.assertNotIn("weight_uom", by_field)
        self.assertEqual(len(self.client.tables.get("PartChangeProposal", [])), 0)

    def test_exact_decision_retries_recover_all_four_actions_without_duplicate_rows(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2"),
            source_row(11, material="Copper", dimension="9 x 20", quantity="1")])
        replacement = self.registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description="Separate Decision Part", variant="Standard", expected_name="S1K — Separate Decision Part — Standard",
            actor="reviewer", reason="Isolated decision retry", request_key="create-decision-replacement", revision_assertion="A")["part"]
        cases = [
            ("keep_existing_baseline", [source_row(10, material="MS Plate", dimension="3 x 25", quantity="3"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")], "proposed_modification", "decision-retry-keep", "", ""),
            ("propose_part_change", [source_row(10, material="MS Plate", dimension="3 x 25", quantity="2")], "proposed_deletion", "decision-retry-propose", "", ""),
            ("match_correspondence", [source_row(10, material="Aluminum", dimension="3 x 25", quantity="2"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")], "ambiguous_correspondence", "decision-retry-match", "", ""),
            ("use_different_part", [source_row(10, material="MS Plate", dimension="3 x 25", quantity="4"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")], "proposed_modification", "decision-retry-replacement", replacement["id"], ""),
        ]
        first_arguments = None
        for action, rows, difference_type, request_key, replacement_id, requirement_key in cases:
            groups, _mapped, comparison = self.compare_rows(rows, source_hash=f"hash-{request_key}", request_key=f"compare-{request_key}")
            if action == "match_correspondence":
                difference = next(row for row in comparison["differences"] if row["DifferenceType"] == difference_type and row.get("IncomingObservation"))
                baseline = next(row for row in comparison["differences"] if row["DifferenceType"] == difference_type and row.get("BaselineRevisionLine"))
                requirement_key = baseline["BaselineRequirementKey"]
            else:
                difference = next(row for row in comparison["differences"] if row["DifferenceType"] == difference_type)
            request_payload = {"action": action, "differenceKeys": [difference["DifferenceKey"]],
                "reason": f"Recover {action}", "oldData": False, "path": f"C:/isolated/{request_key}.ods",
                "groupKey": difference.get("MappingGroupKey") or "",
                "evidenceFingerprint": difference.get("MappingEvidenceFingerprint") or "",
                "replacementPartId": replacement_id, "baselineRequirementKey": requirement_key}
            context_state = {"source_hash": f"hash-{request_key}"}
            before_write = lambda gs=groups, state=context_state: self.decision_context(gs, source_hash=state["source_hash"])
            arguments = {"comparison_key": comparison["comparison"]["ComparisonKey"], "action": action,
                "difference_keys": [difference["DifferenceKey"]], "actor": "engineering reviewer", "reason": f"Recover {action}",
                "replacement_part_id": replacement_id, "baseline_requirement_key": requirement_key, "request_key": request_key,
                "request_payload": request_payload, "before_write": before_write}
            if first_arguments is None:
                first_arguments = arguments
            self.client.lose_after_create_table = "PartWorkbookDecision"
            with self.assertRaises(TimeoutError):
                self.service.decide(**arguments)
            # A confirmed partial write retries its exact request even if the
            # live workbook changes after the response is lost.
            context_state["source_hash"] = f"changed-after-commit-{request_key}"
            recovered = PartBaselineService(self.registry).decide(**arguments)
            self.assertTrue(recovered["idempotent"], action)
            self.assertEqual(sum(row["fields"].get("RequestKey") == request_key for row in self.client.tables["PartWorkbookDecision"]), 1, action)
            replay = self.service.decide(**arguments)
            self.assertTrue(replay["idempotent"], action)
        self.assertEqual(sum(row["fields"].get("RequestKey") == "decision-retry-propose" for row in self.client.tables["PartChangeProposal"]), 1)
        self.assertEqual(sum(row["fields"].get("ResolvedFromDifference") is not None
            and row["fields"].get("RequestKey") == "decision-retry-match" for row in self.client.tables["PartRequirementDifference"]), 1)
        with self.assertRaises(Exception) as conflict:
            changed_payload = {**first_arguments["request_payload"], "path": "C:/different-workbook.ods"}
            self.service.decide(**{**first_arguments, "request_payload": changed_payload})
        self.assertEqual(conflict.exception.code, "PART_REQUEST_CONFLICT")
        self.assertEqual(len([row for row in self.client.tables["PartWorkbookDecision"]
            if row["fields"].get("RequestKey", "").startswith("decision-retry-")]), 4)

    def test_every_decision_action_rejects_changed_workbook_association_or_mapping(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2"),
            source_row(11, material="Copper", dimension="9 x 20", quantity="1")])
        replacement = self.registry.create_part(scope_type="product", target_id="17", target_label="Safari 1000",
            description="Stale Decision Part", variant="Standard", expected_name="S1K — Stale Decision Part — Standard",
            actor="reviewer", reason="Isolated stale decision", request_key="create-stale-replacement", revision_assertion="A")["part"]
        before_decisions = len(self.client.tables.get("PartWorkbookDecision", []))
        before_proposals = len(self.client.tables.get("PartChangeProposal", []))
        baseline_ref = self.registry.current_revision(self.part["id"])["fields"]["ManufacturingBaseline"]
        from app.part_mapping import save_mapping

        source_cases = {
            "keep_existing_baseline": [source_row(10, material="MS Plate", dimension="3 x 25", quantity="3"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")],
            "propose_part_change": [source_row(10, material="MS Plate", dimension="3 x 25", quantity="2")],
            "match_correspondence": [source_row(10, material="Aluminum", dimension="3 x 25", quantity="2"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")],
            "use_different_part": [source_row(10, material="MS Plate", dimension="3 x 25", quantity="4"), source_row(11, material="Copper", dimension="9 x 20", quantity="1")],
        }
        for label in ("hash", "association", "mapping"):
            for index, (action, rows) in enumerate(source_cases.items()):
                request_key = f"stale-{label}-{index}"
                source_hash = f"hash-stale-{label}-{index}"
                groups, mapped, comparison = self.compare_rows(rows, source_hash=source_hash, request_key=f"compare-{request_key}")
                expected_type = "proposed_modification" if action in {"keep_existing_baseline", "use_different_part"} else "proposed_deletion" if action == "propose_part_change" else "ambiguous_correspondence"
                if action == "match_correspondence":
                    difference = next(row for row in comparison["differences"]
                        if row["DifferenceType"] == expected_type and row.get("IncomingObservation"))
                    baseline = next(row for row in comparison["differences"]
                        if row["DifferenceType"] == expected_type and row.get("BaselineRevisionLine"))
                    requirement_key = baseline["BaselineRequirementKey"]
                else:
                    difference = next(row for row in comparison["differences"] if row["DifferenceType"] == expected_type)
                    requirement_key = ""
                replacement_id = replacement["id"] if action == "use_different_part" else ""
                if label == "mapping":
                    save_mapping(self.mapping_store, file_id="pilot.ods", source_hash=source_hash, association=self.association,
                        groups=groups, decisions={groups[0]["key"]: replacement["id"]}, group_reasons={groups[0]["key"]: "Stale evidence fixture"}, expected_hash=source_hash,
                        expected_version=mapped["version"], expected_association=self.association.id,
                        expected_association_version=self.association.version, actor="mapping reviewer", request_key=f"remap-{request_key}")
                if label == "hash":
                    context_factory = lambda gs=groups, sh=source_hash: self.decision_context(gs, source_hash=f"changed-{sh}")
                elif label == "association":
                    changed_association = SimpleNamespace(id=f"association:changed:{index}", version=2)
                    context_factory = lambda gs=groups, sh=source_hash, assoc=changed_association: self.decision_context(gs, source_hash=sh, association=assoc)
                else:
                    context_factory = lambda gs=groups, sh=source_hash: self.decision_context(gs, source_hash=sh)
                with self.subTest(stale=label, action=action):
                    with self.assertRaises(Exception) as caught:
                        self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action=action,
                            difference_keys=[difference["DifferenceKey"]], actor="engineering reviewer", reason="Reject stale evidence",
                            replacement_part_id=replacement_id, baseline_requirement_key=requirement_key, request_key=request_key,
                            before_write=context_factory)
                    self.assertEqual(caught.exception.code, "PART_COMPARISON_STALE")
                    self.assertFalse(any(row["fields"].get("RequestKey") == request_key for row in self.client.tables["PartRegistryRequest"]))
        self.assertEqual(len(self.client.tables.get("PartWorkbookDecision", [])), before_decisions)
        self.assertEqual(len(self.client.tables.get("PartChangeProposal", [])), before_proposals)
        self.assertEqual(self.registry.current_revision(self.part["id"])["fields"]["ManufacturingBaseline"], baseline_ref)

    def test_baseline_only_deletion_requires_saved_complete_family_evidence(self):
        self.establish([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2"),
            source_row(11, material="Copper", dimension="9 x 20", quantity="1")])
        groups, _mapped, comparison = self.compare_rows([source_row(10, material="MS Plate", dimension="3 x 25", quantity="2")],
            source_hash="hash-baseline-only-deletion", request_key="compare-baseline-only-deletion")
        deletion = next(row for row in comparison["differences"] if row["DifferenceType"] == "proposed_deletion")
        self.assertIsNone(deletion["IncomingObservation"])
        family = next(row for row in self.client.tables["PartComparisonFamily"]
            if row["fields"].get("WorkbookComparison") == comparison["comparison"]["id"]
            and row["fields"].get("Family") == deletion["Family"])
        family["fields"]["CompletenessConfirmed"] = False
        before_decisions = len(self.client.tables.get("PartWorkbookDecision", []))
        before_proposals = len(self.client.tables.get("PartChangeProposal", []))
        with self.assertRaises(Exception) as caught:
            self.service.decide(comparison_key=comparison["comparison"]["ComparisonKey"], action="keep_existing_baseline",
                difference_keys=[deletion["DifferenceKey"]], actor="engineering reviewer", reason="Reject incomplete deletion evidence",
                request_key="incomplete-baseline-only-deletion",
                before_write=lambda: self.decision_context(groups, source_hash="hash-baseline-only-deletion"))
        self.assertEqual(caught.exception.code, "PART_COMPARISON_STALE")
        self.assertEqual(len(self.client.tables.get("PartWorkbookDecision", [])), before_decisions)
        self.assertEqual(len(self.client.tables.get("PartChangeProposal", [])), before_proposals)
        self.assertFalse(any(row["fields"].get("RequestKey") == "incomplete-baseline-only-deletion"
            for row in self.client.tables["PartRegistryRequest"]))


class PartRequirementComparisonPolicyTests(unittest.TestCase):
    def test_source_rates_and_audit_text_are_not_engineering_fields_but_raw_values_remain(self):
        first = _raw_requirement(source_row(10, quantity="1.000", weight="0.105", remarks="old note", rate="5"), "mcl",
            {"key": "group", "description": "Chassis"})
        second = _raw_requirement(source_row(99, quantity="1", weight="0.106", remarks="changed note", rate="50"), "mcl",
            {"key": "new-group", "description": "Other label"})
        self.assertEqual(first["physical"], second["physical"])
        self.assertEqual(first["physical"]["weight_kg"], "0.11")
        self.assertEqual(first["rawFields"]["rate"], "5")
        self.assertNotIn("rate", first["physical"])
        self.assertNotIn("remarks", first["physical"])
        service = object.__new__(PartBaselineService)
        classified = service._classify([first], [second], {"mcl": {"complete": True}, "toolshop": {"complete": True}, "cnc": {"complete": True}})
        self.assertEqual([row["type"] for row in classified], ["match"])

    def test_reordered_and_duplicate_looking_lines_match_one_to_one(self):
        def line(key, material, dimension, quantity="1"):
            return {"key": key, "family": "mcl", "physical": {"family": "mcl", "material": material,
                "dimension": dimension, "quantity": quantity, "quantity_uom": "nos"}}

        baseline = [line("steel-a", "steel", "3 x 25"), line("steel-b", "steel", "3 x 25"), line("al-a", "aluminum", "4 x 25")]
        incoming = [line("al-new", "aluminum", "4 x 25"), line("steel-new-a", "steel", "3 x 25"), line("steel-new-b", "steel", "3 x 25")]
        service = object.__new__(PartBaselineService)
        classified = service._classify(baseline, incoming, {"mcl": {"complete": True}, "toolshop": {"complete": True}, "cnc": {"complete": True}})
        self.assertEqual([row["type"] for row in classified].count("match"), 3)
        self.assertFalse(any(row["type"] in {"proposed_addition", "proposed_deletion", "ambiguous_correspondence"} for row in classified))

    def test_complete_evidence_identifies_additions_and_deletions_separately(self):
        def line(key, material, dimension):
            return {"key": key, "family": "mcl", "physical": {"family": "mcl", "material": material,
                "dimension": dimension, "quantity": "1", "quantity_uom": "nos"}}

        unchanged = line("unchanged", "steel", "3 x 25")
        added = line("added", "aluminum", "1 x 10")
        deleted = line("deleted", "copper", "2 x 20")
        service = object.__new__(PartBaselineService)
        complete = {"mcl": {"complete": True}, "toolshop": {"complete": True}, "cnc": {"complete": True}}
        additions = service._classify([unchanged], [unchanged, added], complete)
        deletions = service._classify([unchanged, deleted], [unchanged], complete)
        self.assertEqual({row["type"] for row in additions}, {"match", "proposed_addition"})
        self.assertEqual({row["type"] for row in deletions}, {"match", "proposed_deletion"})

    def test_material_substitution_with_only_one_identity_anchor_is_ambiguous(self):
        old = {"key": "old-line", "family": "mcl", "physical": {"family": "mcl", "material": "steel", "dimension": "3 x 25", "quantity": "2"}}
        new = {"key": "new-line", "family": "mcl", "physical": {"family": "mcl", "material": "aluminum", "dimension": "3 x 25", "quantity": "2"}}
        # Exercise the service classifier without creating or changing any Grist rows.
        service = object.__new__(PartBaselineService)
        differences = service._classify([old], [new], {"mcl": {"complete": True}, "toolshop": {"complete": True}, "cnc": {"complete": True}})
        self.assertEqual({row["type"] for row in differences}, {"ambiguous_correspondence"})


if __name__ == "__main__":
    unittest.main()
