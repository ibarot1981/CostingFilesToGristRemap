"""Exercise assembly persistence against one explicitly selected isolated Grist document.

This script intentionally requires the exact document/workspace IDs and an
explicit write acknowledgement. Never point it at production or Costing-New.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.grist import GristClient
from app.grist_admin import GristAdminClient, SAFARI_DOCUMENT_NAME, validate_safari_document
from app.grist_parts import GristPartFeatures, GristPartRegistry
from app.part_identity import PartIdentityError
from app.schema import SCHEMA_VERSION, plan_schema


class LoseMetadataResponseOnce:
    """Raise after one Grist metadata row commits to verify create recovery."""

    def __init__(self, client):
        self.client = client
        self.lost = False

    def __getattr__(self, name):
        return getattr(self.client, name)

    def create_table_records(self, table, records):
        result = self.client.create_table_records(table, records)
        if table == "PartMetadataVersion" and not self.lost:
            self.lost = True
            raise TimeoutError("isolated persistence check: simulated response loss after Grist commit")
        return result


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-doc-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--journal-path", required=True, help="New isolated SQLite coordinator journal shared with the app browser run.")
    parser.add_argument("--allow-isolated-grist-writes", action="store_true")
    return parser.parse_args()


def create_args(*, kind, request_key, description, shortcode):
    expected_name = f"{shortcode} — {description}"
    payload = {"scope": "global", "targetId": "global", "description": description, "variant": "",
        "expectedName": expected_name, "reason": "Isolated assembly persistence verification", "identityKind": kind}
    args = {"scope_type": "global", "target_id": "global", "target_label": "Safari Manufacturing",
        "description": description, "variant": "", "expected_name": expected_name,
        "actor": "Codex isolated verification", "reason": payload["reason"], "request_key": request_key,
        "revision_assertion": "A"}
    if kind == "assembly":
        args["request_payload"] = payload
    return payload, args


def main():
    options = parse_args()
    if not options.allow_isolated_grist_writes:
        raise SystemExit("Refusing Grist writes without --allow-isolated-grist-writes.")
    load_dotenv(Path(".env"))
    if options.expected_doc_id in {"", "costing-new", os.getenv("GRIST_DOC_ID", "").strip()}:
        raise SystemExit("Refusing a blank, Costing-New, or legacy Costing-New document ID.")
    if options.expected_doc_id != os.getenv("SAFARI_MANUFACTURING_GRIST_DOC_ID", "").strip():
        raise SystemExit("SAFARI_MANUFACTURING_GRIST_DOC_ID must match --expected-doc-id exactly.")
    if options.workspace_id != os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip():
        raise SystemExit("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID must match --workspace-id exactly.")

    client = GristClient.from_safari_environment()
    if client.doc_id != options.expected_doc_id:
        raise SystemExit("The configured Safari client targets a different document.")
    admin = GristAdminClient.from_environment()
    document = admin.get_document(options.expected_doc_id)
    validate_safari_document(document, workspace_id=options.workspace_id, legacy_doc_id=os.getenv("GRIST_DOC_ID", ""))
    plan = plan_schema(admin, document.id, workspace_id=options.workspace_id, document_name=document.name,
        legacy_doc_id=os.getenv("GRIST_DOC_ID", ""))
    if plan.create_tables or plan.add_columns or plan.update_columns or plan.schema_version != SCHEMA_VERSION:
        raise SystemExit("The isolated document must already have the reviewed, complete v15 schema; no schema writes are performed by this script.")

    for table in ("ProductPart", "PartRegistryCoordinator", "PartRegistryRequest"):
        if client.fetch_table_records_with_ids(table):
            raise SystemExit(f"The isolated document is not fresh: {table} already contains rows. Create a new isolated document for another run.")

    journal_path = Path(options.journal_path).expanduser().resolve()
    if journal_path.exists():
        raise SystemExit("The isolated SQLite journal already exists; choose a new path to avoid mixing verification runs.")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex[:10]
    request = lambda label: f"isolated-assembly-{run_id}-{label}"
    shortcode = f"T{run_id[:3].upper()}"
    registry = GristPartRegistry(client, path=journal_path)
    registry.bind_coordinator(actor="Codex isolated verification", reason="Bind the new isolated Grist document for persistence checks.", request_key=request("bind"))
    registry.set_shortcode(scope_type="global", target_id="global", target_label="Safari Manufacturing",
        shortcode=shortcode, actor="Codex isolated verification", reason="Unique temporary prefix for the isolated verification.", request_key=request("shortcode"))

    _, part_args = create_args(kind="part", request_key=request("part"), description="Leaf Part", shortcode=shortcode)
    part = registry.create_part(**part_args)["part"]

    root_payload, root_args = create_args(kind="assembly", request_key=request("root"), description="Root Assembly", shortcode=shortcode)
    interrupted_registry = GristPartRegistry(LoseMetadataResponseOnce(client), path=journal_path)
    try:
        interrupted_registry.create_assembly(**root_args)
        raise AssertionError("The injected response loss did not occur.")
    except TimeoutError as error:
        if "simulated response loss" not in str(error):
            raise

    restarted = GristPartRegistry(client, path=journal_path)
    recovered_payload = restarted.prepare_create_recovery(root_args["request_key"], root_payload, identity_kind="assembly")
    if not recovered_payload or recovered_payload.get("identityKind") != "assembly":
        raise AssertionError("Restart could not recover the reserved assembly request from its journal.")
    root = restarted.create_assembly(**root_args)["part"]
    _, nested_args = create_args(kind="assembly", request_key=request("nested"), description="Nested Assembly", shortcode=shortcode)
    nested = restarted.create_assembly(**nested_args)["part"]

    features = GristPartFeatures(restarted)
    features.add_component(parent_part_id=root["id"], child_part_id=nested["id"], quantity=2, uom="each",
            actor="Codex isolated verification", reason="Root contains a nested assembly.", request_key=request("root-child"))
    features.add_component(parent_part_id=nested["id"], child_part_id=part["id"], quantity=3.5, uom="set",
        actor="Codex isolated verification", reason="Nested assembly contains an existing Part.", request_key=request("nested-child"))
    try:
        features.add_component(parent_part_id=nested["id"], child_part_id=root["id"], quantity=1, uom="each",
            actor="Codex isolated verification", reason="Expected cycle rejection.", request_key=request("cycle"))
        raise AssertionError("The isolated cycle check accepted an indirect assembly cycle.")
    except PartIdentityError as error:
        if error.code != "PART_COMPONENT_CYCLE":
            raise
    try:
        features.finalize_revision(part_id=root["id"], actor="Codex isolated verification",
            reason="Expected child finalization gate.", request_key=request("early-finish"))
        raise AssertionError("The isolated finalization check accepted a draft child assembly.")
    except PartIdentityError as error:
        if error.code != "PART_CHILD_REVISION_DRAFT":
            raise
    features.finalize_revision(part_id=part["id"], actor="Codex isolated verification", reason="Finalize leaf.", request_key=request("finish-part"))
    features.finalize_revision(part_id=nested["id"], actor="Codex isolated verification", reason="Finalize nested assembly.", request_key=request("finish-nested"))
    features.finalize_revision(part_id=root["id"], actor="Codex isolated verification", reason="Finalize root assembly.", request_key=request("finish-root"))

    # Simulate a lost local reservation row while retaining the shared writer
    # coordinator and counter; restore it from Grist's request row.
    with restarted._database() as database:
        database.execute("DELETE FROM part_registry_reservations WHERE request_key=?", (root_args["request_key"],))
    after_journal_loss = GristPartRegistry(client, path=journal_path)
    restored = after_journal_loss.prepare_create_recovery(root_args["request_key"], root_payload, identity_kind="assembly")
    root_retry = after_journal_loss.create_assembly(**root_args)
    if root_retry.get("idempotent") is not True or restored.get("identityKind") != "assembly":
        raise AssertionError("Grist-backed reservation recovery did not return the original published assembly.")

    after_restart = GristPartRegistry(client, path=journal_path)
    root_readback = after_restart.get_part(root["id"])
    nested_readback = after_restart.get_part(nested["id"])
    part_readback = after_restart.get_part(part["id"])
    root_components = GristPartFeatures(after_restart).components(root["id"])
    nested_components = GristPartFeatures(after_restart).components(nested["id"])
    if (part["partNumber"], root["partNumber"], nested["partNumber"]) != ("SM-P-000001", "SM-A-000001", "SM-A-000002"):
        raise AssertionError("The isolated Part and Assembly sequences did not remain independent.")
    if any(not row or row.get("publishStatus") != "published" for row in (root_readback, nested_readback, part_readback)):
        raise AssertionError("Grist read-back did not confirm all three published identities after restart.")
    if len(root_components) != 1 or root_components[0].get("childIdentityKind") != "assembly" or len(nested_components) != 1:
        raise AssertionError("Grist read-back did not preserve the nested pinned composition.")
    if root_components[0].get("relationshipStatus") != "available" or nested_components[0].get("relationshipStatus") != "available":
        raise AssertionError("A persisted child revision could not be resolved after restart.")

    print(json.dumps({
        "documentId": document.id,
        "journalPath": str(journal_path),
        "workspaceId": document.workspace_id,
        "documentName": document.name,
        "schemaVersion": plan.schema_version,
        "result": "pass",
        "simulatedCommittedResponseLossRecovered": True,
        "localReservationRecoveredFromGristRequest": True,
        "readBackAfterRegistryRestart": True,
        "partNumber": part["partNumber"],
        "rootAssemblyNumber": root["partNumber"],
        "nestedAssemblyNumber": nested["partNumber"],
        "rootChildRevision": root_components[0]["childRevisionLabel"],
        "nestedChildRevision": nested_components[0]["childRevisionLabel"],
        "cycleAndFinalizationLocks": "verified",
        "businessWrites": "isolated test document only",
    }, indent=2))


if __name__ == "__main__":
    main()
