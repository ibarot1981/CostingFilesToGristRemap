"""Run Safari R1–R3 browser QA against one explicitly named disposable Grist fork.

This test-only entry point keeps the production Grist target guard untouched. It
requires an exact QA document ID, workspace ID and title, then validates those
values against Grist metadata before every write.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.grist import GristClient
from app.grist_admin import GristAdminClient
from app.grist_repository import GristSafariRepository


class IsolatedQaGristClient(GristClient):
    expected_doc_id = ""
    expected_workspace_id = ""
    expected_document_name = ""

    def validate_safari_write_target(self) -> None:
        configured_doc = os.getenv("SAFARI_MANUFACTURING_GRIST_DOC_ID", "").strip()
        configured_workspace = os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip()
        legacy_doc = os.getenv("GRIST_DOC_ID", "").strip()
        if (not self.expected_doc_id or self.doc_id != self.expected_doc_id
                or configured_doc != self.expected_doc_id
                or not self.expected_workspace_id
                or getattr(self, "safari_workspace_id", "") != self.expected_workspace_id
                or configured_workspace != self.expected_workspace_id
                or self.doc_id.casefold() in {legacy_doc.casefold(), "costing-new"}):
            raise RuntimeError("Isolated QA Grist target does not match the exact configured document and workspace.")
        admin = GristAdminClient(self.api_key, self.base_url)
        writable = admin.discover_writable_workspaces()
        if not any(item.id == self.expected_workspace_id for item in writable):
            raise RuntimeError("The isolated QA workspace is not writable by this test identity.")
        document = admin.get_document(self.expected_doc_id)
        if (document.id != self.expected_doc_id or document.name != self.expected_document_name
                or document.workspace_id != self.expected_workspace_id):
            raise RuntimeError("The isolated QA document metadata changed; refusing the write.")


def _configure(args: argparse.Namespace) -> IsolatedQaGristClient:
    load_dotenv()
    production_doc = os.getenv("SAFARI_MANUFACTURING_GRIST_DOC_ID", "").strip()
    production_workspace = os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip()
    legacy_doc = os.getenv("GRIST_DOC_ID", "").strip()
    if (args.doc_id in {production_doc, legacy_doc, "Costing-New"}
            or args.doc_id.casefold() == "costing-new"):
        raise SystemExit("Refusing a production, legacy or Costing-New target for isolated QA.")
    if not production_workspace or args.workspace_id == production_workspace:
        raise SystemExit("The isolated QA document must use a workspace different from the operational Safari workspace.")
    if not args.document_name.startswith("QA - Safari Manufacturing R1-R3"):
        raise SystemExit("The isolated QA document title must use the approved R1–R3 QA prefix.")
    if not args.workspace_id.isdigit():
        raise SystemExit("An exact numeric Grist workspace ID is required.")
    if not Path(args.costing_root).is_dir():
        raise SystemExit("COSTING_ROOT must point to an existing isolated fixture directory.")

    os.environ["SAFARI_REPOSITORY"] = "grist"
    os.environ["SAFARI_MANUFACTURING_GRIST_DOC_ID"] = args.doc_id
    os.environ["SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID"] = args.workspace_id
    os.environ["COSTING_ROOT"] = str(Path(args.costing_root).resolve())
    os.environ["SAFARI_PART_DATABASE_PATH"] = str(Path(args.part_database).resolve())
    IsolatedQaGristClient.expected_doc_id = args.doc_id
    IsolatedQaGristClient.expected_workspace_id = args.workspace_id
    IsolatedQaGristClient.expected_document_name = args.document_name

    api_key = os.getenv("GRIST_API_KEY", "").strip()
    base_url = os.getenv("GRIST_BASE_URL", "https://docs.getgrist.com").strip()
    if not api_key:
        raise SystemExit("GRIST_API_KEY is not available to this local verification process.")
    client = IsolatedQaGristClient(api_key, args.doc_id, base_url)
    client.safari_workspace_id = args.workspace_id
    client.validate_safari_write_target()
    return client


def _verify_model_code_writes(client: IsolatedQaGristClient) -> dict[str, object]:
    from app import web
    from fastapi import HTTPException

    repository = GristSafariRepository(client=client)
    web._repository = repository
    web._part_identity_registry = None
    before_codes = {item.id: (item.model_id, item.code, item.description, item.legacy_spares_only)
                    for item in repository.codes.values()}
    before_models = {item.id: (item.product_id, item.model_number) for item in repository.models.values()}
    before_products = {item.id: item.name for item in repository.products.values()}
    before_associations = deepcopy(repository.associations)
    before_code_associations = deepcopy(repository.code_associations)
    eligible_models = [item for item in repository.list_models()
                       if not item.legacy_spares_only
                       and item.product_id in repository.products
                       and repository.products[item.product_id].active]
    if not eligible_models:
        raise RuntimeError("The isolated QA fork contains no active non-legacy Product Model.")
    model = eligible_models[0]
    suffix = uuid4().hex[:8].upper()
    code_value = f"QA-R123-{suffix}"
    description_value = f"QA-only model code {suffix}"
    created = web.create_model_code(model.id, {"code": code_value, "description": description_value})
    edited_description = f"QA-only saved description {suffix}"
    updated = web.update_model_code_description(created["id"], {
        "description": edited_description,
        "expectedDescription": description_value,
    })
    try:
        web.create_model_code(model.id, {"code": f"QA-EMPTY-{suffix}", "description": "   "})
    except HTTPException as exc:
        if exc.status_code != 422 or exc.detail.get("code") != "MODEL_CODE_DESCRIPTION_REQUIRED":
            raise
    else:
        raise AssertionError("The API accepted a new Model Code without a nonblank description.")

    reloaded = GristSafariRepository(client=client)
    web._repository = reloaded
    saved = reloaded.codes.get(created["id"])
    if not saved or (saved.code, saved.description, saved.model_id) != (code_value, edited_description, model.id):
        raise AssertionError("A fresh Grist repository instance could not reload the created and edited Model Code.")
    if {item.id: (item.model_id, item.code, item.description, item.legacy_spares_only)
            for item in reloaded.codes.values() if item.id in before_codes} != before_codes:
        raise AssertionError("Creating/editing the QA Model Code changed an existing code identity or value.")
    if {item.id: (item.product_id, item.model_number) for item in reloaded.models.values()} != before_models:
        raise AssertionError("Creating/editing the QA Model Code changed Product Model identity or hierarchy.")
    if {item.id: item.name for item in reloaded.products.values()} != before_products:
        raise AssertionError("Creating/editing the QA Model Code changed Product identity.")
    if reloaded.associations != before_associations or reloaded.code_associations != before_code_associations:
        raise AssertionError("Creating/editing the QA Model Code changed workbook association records.")
    lists = web.model_codes(model.id)
    if not any(item["id"] == created["id"] and item.get("owner") is None for item in lists["available"]):
        raise AssertionError("The new unowned Model Code is not available through the existing selector eligibility rules.")
    return {
        "status": "pass",
        "verified": True,
        "documentId": client.doc_id,
        "workspaceId": client.expected_workspace_id,
        "modelId": model.id,
        "createdCodeId": created["id"],
        "createdCode": created["code"],
        "reloadedDescription": saved.description,
        "existingCodeCountPreserved": len(before_codes),
        "modelIdentityPreserved": True,
        "productIdentityPreserved": True,
        "workbookAssociationsPreserved": True,
        "blankDescriptionRejected": True,
        "eligibleInSelector": True,
    }


def _backup_qa_document(client: IsolatedQaGristClient, backup_path: str) -> dict[str, object]:
    target = Path(backup_path).resolve()
    if target.exists():
        raise SystemExit(f"Refusing to overwrite an existing QA backup: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    client.validate_safari_write_target()
    payload = client.download_document_backup()
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    with sqlite3.connect(str(target)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"The downloaded native Grist backup failed SQLite integrity_check: {integrity}")
    manifest = {
        "document_id": client.doc_id,
        "document_name": client.expected_document_name,
        "workspace_id": client.expected_workspace_id,
        "purpose": "Disposable Safari R1–R3 round 1 adapter/browser QA; retained through independent review.",
        "forked_from_document_id": os.getenv("SAFARI_QA_FORK_SOURCE_ID", "bAPdkEDn7brbqTrfVsRmXZ"),
        "backup_path": str(target),
        "backup_sha256": digest,
        "sqlite_integrity_check": integrity,
    }
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("verify-model-code", "backup", "serve"), required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--document-name", required=True)
    parser.add_argument("--costing-root", required=True)
    parser.add_argument("--part-database", required=True)
    parser.add_argument("--backup-path")
    parser.add_argument("--port", type=int, default=44321)
    args = parser.parse_args()
    client = _configure(args)
    if args.mode == "verify-model-code":
        print(json.dumps(_verify_model_code_writes(client), indent=2))
        return
    if args.mode == "backup":
        if not args.backup_path:
            raise SystemExit("--backup-path is required for backup mode.")
        print(json.dumps(_backup_qa_document(client, args.backup_path), indent=2))
        return

    from app import web
    import uvicorn

    web._repository = GristSafariRepository(client=client)
    web._part_identity_registry = None
    uvicorn.run(web.app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
