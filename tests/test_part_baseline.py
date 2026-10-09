from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.grist_parts import GristPartRegistry
from app.part_baseline import PartBaselineService
from app.part_baseline import _family_confirmations, _raw_requirement
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
        if expected_version is None:
            from app.part_mapping import mapping_detail
            expected_version = mapping_detail(self.mapping_store, file_id="pilot.ods", source_hash=source_hash,
                association=self.association, groups=groups)["version"]
        result = save_mapping(self.mapping_store, file_id="pilot.ods", source_hash=source_hash,
            association=self.association, groups=groups, decisions={"mcl:chassis": self.part["id"]},
            expected_hash=source_hash, expected_version=expected_version,
            expected_association=self.association.id, expected_association_version=self.association.version,
            actor="mapping reviewer", request_key=request_key or f"map:{source_hash}")
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
        self.assertIn("matched_correspondence", {row["Status"] for row in matched["differences"]
            if row["DifferenceType"] == "ambiguous_correspondence"})
        self.assertEqual(matched["comparison"]["AmbiguousCount"], 0)
        proposal = self.service.decide(comparison_key=matched["comparison"]["ComparisonKey"], action="propose_part_change",
            difference_keys=[modification["DifferenceKey"]], actor="engineering reviewer", reason="Propose reviewed material substitution",
            request_key="propose-substitution")
        self.assertEqual(proposal["comparison"]["ModificationCount"], 1)
        self.assertEqual(self.registry.get_part(self.part["id"])["engineeringRevision"], "A")


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
