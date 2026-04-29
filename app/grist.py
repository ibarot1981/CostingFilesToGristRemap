"""Grist API client.

TODO: Confirm the production Grist base URL, document ID, and table field names
before enabling this against live costing data.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from app.exceptions import ConfigError, GristError


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
