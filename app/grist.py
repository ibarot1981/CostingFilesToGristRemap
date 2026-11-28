"""Grist API client.

TODO: Confirm the production Grist base URL, document ID, and table field names
before enabling this against live costing data.
"""

from __future__ import annotations

import os
import re
from typing import Any
from pathlib import Path

import requests
from dotenv import load_dotenv

from app.exceptions import ConfigError, GristError, GristValidationError
from app.grist_admin import GristAdminClient, validate_safari_document


class GristClient:
    """Small requests-based client for Grist records."""

    def __init__(self, api_key: str, doc_id: str, base_url: str) -> None:
        self.api_key = api_key
        self.doc_id = doc_id
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_environment(cls) -> "GristClient":
        """Create a Grist client from environment variables."""
        load_dotenv()
        api_key = os.getenv("GRIST_API_KEY", "").strip()
        doc_id = os.getenv("GRIST_DOC_ID", "").strip()
        base_url = os.getenv("GRIST_BASE_URL", "https://docs.getgrist.com").strip()
        if not api_key or not doc_id:
            raise ConfigError(
                "Grist verification needs GRIST_API_KEY and GRIST_DOC_ID. "
                "Copy .env.example, set the values, then export them in this shell."
            )
        return cls(api_key=api_key, doc_id=doc_id, base_url=base_url)

    @classmethod
    def from_safari_environment(cls) -> "GristClient":
        """Create a client only from the explicitly separate Safari settings."""
        load_dotenv()
        api_key = os.getenv("GRIST_API_KEY", "").strip()
        doc_id = os.getenv("SAFARI_MANUFACTURING_GRIST_DOC_ID", "").strip()
        workspace_id = os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip()
        legacy_doc_id = os.getenv("GRIST_DOC_ID", "").strip()
        base_url = os.getenv("GRIST_BASE_URL", "https://docs.getgrist.com").strip()
        if not api_key or not doc_id or not workspace_id:
            raise ConfigError("Safari Manufacturing needs GRIST_API_KEY, SAFARI_MANUFACTURING_GRIST_DOC_ID, and SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID.")
        if doc_id.casefold() == legacy_doc_id.casefold() or doc_id.casefold() == "costing-new":
            raise GristValidationError("Safari Manufacturing cannot target the legacy Costing-New document.")
        client = cls(api_key=api_key, doc_id=doc_id, base_url=base_url)
        client.safari_workspace_id = workspace_id
        client.validate_safari_write_target()
        return client

    def validate_safari_write_target(self) -> None:
        """Revalidate explicit settings and remote identity before any Grist write."""
        configured_doc_id = os.getenv("SAFARI_MANUFACTURING_GRIST_DOC_ID", "").strip()
        configured_workspace_id = os.getenv("SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID", "").strip()
        legacy_doc_id = os.getenv("GRIST_DOC_ID", "").strip()
        workspace_id = getattr(self, "safari_workspace_id", "")
        if not configured_doc_id or configured_doc_id != self.doc_id or not workspace_id or configured_workspace_id != workspace_id:
            raise ConfigError("Safari Manufacturing write target must match the explicitly configured document and workspace IDs.")
        if self.doc_id.casefold() == legacy_doc_id.casefold() or self.doc_id.casefold() == "costing-new":
            raise GristValidationError("Safari Manufacturing cannot target the legacy Costing-New document.")
        admin = GristAdminClient(self.api_key, self.base_url)
        writable = admin.discover_writable_workspaces()
        if not any(item.id == workspace_id for item in writable):
            raise GristValidationError("The configured Safari Manufacturing workspace is not accessible and writable.")
        document = admin.get_document(self.doc_id)
        qa_document_id = os.getenv("SAFARI_QA_TARGET_DOC_ID", "").strip()
        qa_workspace_id = os.getenv("SAFARI_QA_TARGET_WORKSPACE_ID", "").strip()
        qa_label = os.getenv("SAFARI_QA_TARGET_LABEL", "").strip()
        explicit_qa = (qa_document_id == self.doc_id and qa_workspace_id == workspace_id
            and bool(qa_label) and self.doc_id not in {"bAPdkEDn7brbqTrfVsRmXZ", "cPCjjr9oCzhWC7z8vJbcky"})
        validate_safari_document(document, workspace_id=workspace_id, legacy_doc_id=legacy_doc_id,
            qa_document_id=qa_document_id if explicit_qa else None, qa_label=qa_label if explicit_qa else None)

    def fetch_table_records(self, table_id: str) -> list[dict[str, Any]]:
        """Fetch records from a Grist table and return the record fields."""
        return [record.get("fields", {}) for record in self.fetch_table_records_with_ids(table_id)]

    def fetch_table_records_with_ids(self, table_id: str) -> list[dict[str, Any]]:
        """Fetch raw Grist records, including record IDs and fields."""
        # TODO: Adjust this endpoint if your Grist deployment uses a custom API gateway.
        url = f"{self.base_url}/api/docs/{self.doc_id}/tables/{table_id}/records"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GristError(f"Could not connect to Grist: {exc}") from exc

        if response.status_code >= 400:
            raise GristError(f"Grist API returned {response.status_code}: {response.text[:500]}")

        payload = response.json()
        return payload.get("records", [])

    def fetch_table_projection(self, table_id: str, columns: list[str], filters: dict[str, list[Any]], *,
                               timeout_ms: int = 8000) -> list[dict[str, Any]]:
        """Read only selected columns and matching rows through Grist's read-only SQL API.

        This is used for immutable, version-filtered local import rows. Table and
        column identifiers must be static application identifiers; all values
        are parameterized. The caller must supply a filter so this cannot turn
        into an unbounded table scan by omission.
        """
        identifiers = [table_id, *columns, *filters.keys()]
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or "") for value in identifiers):
            raise GristValidationError("The local import query contains an invalid table or column identifier.")
        if not filters or any(not isinstance(values, list) or not values for values in filters.values()):
            return []
        selected = list(dict.fromkeys(["id", *columns]))
        clauses, args = [], []
        for column, values in filters.items():
            clauses.append(f'"{column}" IN ({", ".join("?" for _ in values)})')
            args.extend(values)
        sql = "SELECT " + ", ".join(f'"{name}"' for name in selected) + f' FROM "{table_id}" WHERE ' + " AND ".join(clauses)
        url = f"{self.base_url}/api/docs/{self.doc_id}/sql"
        try:
            response = requests.post(url, headers={"Authorization": f"Bearer {self.api_key}"},
                json={"sql": sql, "args": args, "timeout": max(1, min(int(timeout_ms), 15000))},
                timeout=(3, max(1, min(17, int(timeout_ms) / 1000 + 2))))
        except requests.RequestException as exc:
            raise GristError(f"Could not query current local import rows: {exc}") from exc
        if response.status_code >= 400:
            raise GristError(f"Grist local import query returned {response.status_code}: {response.text[:500]}")
        payload = response.json()
        records = []
        for item in payload.get("records", []):
            fields = dict(item.get("fields", item))
            record_id = item.get("id", fields.pop("id", None))
            if record_id is None:
                raise GristError("Grist local import query returned a row without its record ID.")
            else:
                fields.pop("id", None)
            records.append({"id": record_id, "fields": fields})
        return records

    def update_table_records(self, table_id: str, updates: list[dict[str, Any]]) -> None:
        """Patch Grist records with fields by record ID."""
        if not updates:
            return

        url = f"{self.base_url}/api/docs/{self.doc_id}/tables/{table_id}/records"
        grouped_updates: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for update in updates:
            fields = update["fields"]
            signature = tuple(sorted(fields.keys()))
            grouped_updates.setdefault(signature, []).append(
                {
                    "id": update["id"],
                    "fields": fields,
                }
            )

        for records in grouped_updates.values():
            try:
                response = requests.patch(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"records": records},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise GristError(f"Could not update Grist: {exc}") from exc

            if response.status_code >= 400:
                raise GristError(f"Grist API update returned {response.status_code}: {response.text[:500]}")

    def create_table_records(self, table_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create Grist records and return the created record payload."""
        if not records:
            return []

        url = f"{self.base_url}/api/docs/{self.doc_id}/tables/{table_id}/records"
        payload_records = [{"fields": record["fields"]} for record in records]
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"records": payload_records},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GristError(f"Could not create Grist records: {exc}") from exc

        if response.status_code >= 400:
            raise GristError(f"Grist API create returned {response.status_code}: {response.text[:500]}")

        return response.json().get("records", [])

    def download_document_backup(self) -> bytes:
        """Download the configured document as a native .grist SQLite file."""
        url = f"{self.base_url}/api/docs/{self.doc_id}/download"
        try:
            response = requests.get(url, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=120)
        except requests.RequestException as exc:
            raise GristError(f"Could not download a Grist document backup: {exc}") from exc
        if response.status_code >= 400:
            raise GristError(f"Grist backup download returned {response.status_code}: {response.text[:500]}")
        data = response.content
        if not data.startswith(b"SQLite format 3\x00"):
            raise GristValidationError("Grist backup did not contain a SQLite .grist document.")
        return data

    def apply_user_actions(self, actions: list[list[Any]]) -> Any:
        """Apply an ordered Grist user-action batch to the configured document."""
        if not actions:
            return None
        url = f"{self.base_url}/api/docs/{self.doc_id}/apply"
        try:
            response = requests.post(url, headers={"Authorization": f"Bearer {self.api_key}"}, json={"actions": actions}, timeout=60)
        except requests.RequestException as exc:
            raise GristError(f"Could not apply Grist user actions: {exc}") from exc
        if response.status_code >= 400:
            raise GristError(f"Grist apply returned {response.status_code}: {response.text[:500]}")
        return response.json()
