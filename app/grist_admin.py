"""Guarded Grist administration for the Safari Manufacturing document."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable

import requests
from dotenv import load_dotenv

from app.exceptions import (
    ConfigError,
    GristConnectivityError,
    GristDuplicateNameError,
    GristPermissionError,
    GristValidationError,
    GristWorkspaceSelectionRequired,
)


SAFARI_DOCUMENT_NAME = "Safari Manufacturing"


@dataclass(frozen=True)
class GristOrganization:
    id: str
    name: str


@dataclass(frozen=True)
class GristWorkspace:
    id: str
    name: str
    organization_id: str
    writable: bool
    documents: tuple["GristDocument", ...] = ()


@dataclass(frozen=True)
class GristDocument:
    id: str
    name: str
    workspace_id: str | None = None
    organization_id: str | None = None


@dataclass(frozen=True)
class DocumentEnsureResult:
    document: GristDocument | None
    workspace: GristWorkspace
    action: str
    exact_matches: tuple[GristDocument, ...] = ()


class GristAdminClient:
    def __init__(self, api_key: str, base_url: str, *, session: Any = requests) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._workspace_cache: dict[str, GristWorkspace] = {}

    @classmethod
    def from_environment(cls) -> "GristAdminClient":
        load_dotenv()
        api_key = os.getenv("GRIST_API_KEY", "").strip()
        base_url = os.getenv("GRIST_BASE_URL", "https://docs.getgrist.com").strip()
        if not api_key:
            raise ConfigError("Safari Manufacturing setup needs GRIST_API_KEY.")
        return cls(api_key=api_key, base_url=base_url)

    def list_organizations(self) -> list[GristOrganization]:
        payload = self._request("GET", "/api/orgs")
        return [GristOrganization(str(item.get("id")), str(item.get("name") or item.get("domain") or item.get("id"))) for item in _items(payload, "orgs", "organizations") if item.get("id") is not None]

    def list_workspaces(self, organization_id: str | None = None) -> list[GristWorkspace]:
        path = f"/api/orgs/{organization_id}/workspaces" if organization_id else "/api/workspaces"
        payload = self._request("GET", path)
        result: list[GristWorkspace] = []
        for item in _items(payload, "workspaces"):
            workspace_id = item.get("id") or item.get("workspaceId")
            if workspace_id is None:
                continue
            org_id = str(item.get("orgId") or item.get("organizationId") or organization_id or "")
            writable = _is_writable(item)
            documents = tuple(_document(doc, str(workspace_id), org_id) for doc in item.get("docs", []) if isinstance(doc, dict) and doc.get("id") is not None)
            workspace = GristWorkspace(str(workspace_id), str(item.get("name") or workspace_id), org_id, writable, documents)
            self._workspace_cache[workspace.id] = workspace
            result.append(workspace)
        return result

    def discover_writable_workspaces(self) -> list[GristWorkspace]:
        organizations = self.list_organizations()
        workspaces: list[GristWorkspace] = []
        if organizations:
            for organization in organizations:
                workspaces.extend(self.list_workspaces(organization.id))
        else:
            workspaces = self.list_workspaces()
        unique: dict[str, GristWorkspace] = {item.id: item for item in workspaces if item.writable}
        return sorted(unique.values(), key=lambda item: (item.organization_id, item.name.casefold(), item.id))

    def list_documents(self, workspace_id: str) -> list[GristDocument]:
        cached = self._workspace_cache.get(workspace_id)
        if cached is not None and cached.documents:
            return list(cached.documents)
        try:
            payload = self._request("GET", f"/api/workspaces/{workspace_id}/docs")
            return [_document(item, workspace_id, cached.organization_id if cached else None) for item in _items(payload, "docs", "documents") if item.get("id") is not None]
        except GristConnectivityError as exc:
            # Some Grist deployments expose documents only embedded in the
            # organization workspace listing. Refresh that listing before
            # treating a missing workspace-doc route as a real connectivity
            # failure.
            if "returned 404" not in str(exc) or cached is None or not cached.organization_id:
                raise
            for workspace in self.list_workspaces(cached.organization_id):
                if workspace.id == workspace_id:
                    return list(workspace.documents)
            raise

    def get_document(self, document_id: str) -> GristDocument:
        payload = self._request("GET", f"/api/docs/{document_id}")
        item = payload.get("doc", payload) if isinstance(payload, dict) else {}
        return _document(item, None)

    def create_document(self, workspace_id: str, name: str = SAFARI_DOCUMENT_NAME) -> GristDocument:
        payload = self._request("POST", f"/api/workspaces/{workspace_id}/docs", json={"name": name})
        item = payload.get("doc", payload) if isinstance(payload, dict) else {}
        if isinstance(item, dict) and isinstance(item.get("items"), str):
            item = item["items"]
        if isinstance(item, dict):
            document = _document(item, workspace_id)
        else:
            # The documented endpoint returns the new document ID as a JSON
            # string rather than a document object.
            document = GristDocument(id=str(item or ""), name=name, workspace_id=workspace_id)
        if not document.id or document.name != name:
            raise GristValidationError("Grist returned an invalid created-document payload.")
        return document

    def list_tables(self, document_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/api/docs/{document_id}/tables")
        tables = [dict(item) for item in _items(payload, "tables")]
        enriched: list[dict[str, Any]] = []
        for table in tables:
            if not isinstance(table.get("columns"), list):
                table["columns"] = self.list_columns(document_id, str(table.get("id", "")))
            enriched.append(table)
        return enriched

    def list_columns(self, document_id: str, table_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/api/docs/{document_id}/tables/{table_id}/columns")
        return [dict(item) for item in _items(payload, "columns")]

    def create_table(self, document_id: str, table: dict[str, Any]) -> dict[str, Any]:
        payload = self._request("POST", f"/api/docs/{document_id}/tables", json={"tables": [_table_payload(table)]})
        items = _items(payload, "tables")
        return items[0] if items else payload

    def add_columns(self, document_id: str, table_id: str, columns: list[dict[str, Any]]) -> None:
        if not columns:
            return
        self._request("POST", f"/api/docs/{document_id}/tables/{table_id}/columns", json={"columns": [_column_payload(column) for column in columns]})

    def update_columns(self, document_id: str, table_id: str, columns: list[dict[str, Any]]) -> None:
        if not columns:
            return
        self._request("PUT", f"/api/docs/{document_id}/tables/{table_id}/columns", json={"columns": [_column_payload(column) for column in columns]})

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            response = self.session.request(method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise GristConnectivityError(f"Could not connect to Grist at {self.base_url}: {exc}") from exc
        if response.status_code in (401, 403):
            raise GristPermissionError(f"Grist denied the {method} operation ({response.status_code}).")
        if response.status_code == 409:
            raise GristDuplicateNameError("Grist rejected the operation because the name already exists.")
        if response.status_code >= 400:
            raise GristConnectivityError(f"Grist API returned {response.status_code} for {method} {path}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GristConnectivityError("Grist returned a non-JSON response.") from exc
        return payload if isinstance(payload, dict) else {"items": payload}


def ensure_safari_document(
    client: GristAdminClient,
    *,
    workspace_id: str | None = None,
    apply: bool = False,
    legacy_doc_id: str | None = None,
    on_uncertain_create: Callable[[], list[GristDocument]] | None = None,
) -> DocumentEnsureResult:
    workspaces = client.discover_writable_workspaces()
    if workspace_id:
        selected = next((item for item in workspaces if item.id == workspace_id), None)
        if selected is None:
            raise GristValidationError("The explicitly selected workspace is not accessible and writable.")
    elif not workspaces:
        raise GristPermissionError("The authenticated Grist account has no writable workspace.")
    elif len(workspaces) != 1:
        choices = ", ".join(f"{item.name} ({item.id}, organization {item.organization_id})" for item in workspaces)
        raise GristWorkspaceSelectionRequired(f"Select SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID; writable workspace discovery returned {len(workspaces)} choices: {choices}")
    else:
        selected = workspaces[0]
    if not selected.writable:
        raise GristPermissionError("The selected workspace is not writable.")
    matches = tuple(item for item in client.list_documents(selected.id) if item.name == SAFARI_DOCUMENT_NAME)
    _reject_legacy(legacy_doc_id or os.getenv("GRIST_DOC_ID", ""), [item.id for item in matches])
    if len(matches) > 1:
        raise GristDuplicateNameError("Multiple exact-name Safari Manufacturing documents exist; refusing to choose one.")
    if matches:
        document = _validate_document(matches[0], selected)
        return DocumentEnsureResult(document, selected, "reused", matches)
    if not apply:
        return DocumentEnsureResult(None, selected, "planned", ())
    try:
        document = client.create_document(selected.id, SAFARI_DOCUMENT_NAME)
    except GristConnectivityError:
        retry_matches = tuple((on_uncertain_create or (lambda: client.list_documents(selected.id)))())
        exact = tuple(item for item in retry_matches if item.name == SAFARI_DOCUMENT_NAME)
        if len(exact) == 1:
            document = _validate_document(exact[0], selected)
            return DocumentEnsureResult(document, selected, "reused-after-uncertain-create", exact)
        if len(exact) > 1:
            raise GristDuplicateNameError("Multiple exact-name Safari Manufacturing documents appeared after an uncertain create; refusing to choose one.")
        raise
    _reject_legacy(legacy_doc_id or os.getenv("GRIST_DOC_ID", ""), [document.id])
    try:
        metadata = client.get_document(document.id)
        if metadata.id != document.id:
            raise GristValidationError("Grist returned document metadata for a different document ID.")
        validated = _validate_document(metadata, selected)
    except GristConnectivityError:
        retry_matches = tuple(client.list_documents(selected.id))
        exact = tuple(item for item in retry_matches if item.name == SAFARI_DOCUMENT_NAME)
        if len(exact) == 1 and exact[0].id == document.id:
            validated = _validate_document(exact[0], selected)
        elif exact:
            raise GristDuplicateNameError("Document metadata could not be confirmed after creation; exact-name matches do not uniquely identify the returned document ID.")
        else:
            raise
    return DocumentEnsureResult(validated, selected, "created", (validated,))


def _reject_legacy(doc_id: str, new_ids: list[str]) -> None:
    legacy = doc_id.strip()
    if not legacy:
        return
    if legacy.casefold() == "costing-new" or legacy in new_ids:
        raise GristValidationError("Safari Manufacturing writes cannot target the legacy Costing-New document ID.")


def validate_safari_document(document: GristDocument, *, workspace_id: str, legacy_doc_id: str | None = None) -> GristDocument:
    _reject_legacy(legacy_doc_id or os.getenv("GRIST_DOC_ID", ""), [document.id])
    if not document.id:
        raise GristValidationError("Target document metadata did not include a document ID.")
    if document.name != SAFARI_DOCUMENT_NAME:
        raise GristValidationError(f"Target document must be named exactly {SAFARI_DOCUMENT_NAME!r}.")
    if not document.workspace_id or document.workspace_id != workspace_id:
        raise GristValidationError("Target document is not in the selected workspace.")
    return document


def _validate_document(document: GristDocument, workspace: GristWorkspace) -> GristDocument:
    return validate_safari_document(document, workspace_id=workspace.id, legacy_doc_id=os.getenv("GRIST_DOC_ID", ""))


def _items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    value = payload.get("items")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _document(item: dict[str, Any], workspace_id: str | None, organization_id: str | None = None) -> GristDocument:
    workspace = item.get("workspace") if isinstance(item.get("workspace"), dict) else {}
    organization = item.get("organization") if isinstance(item.get("organization"), dict) else {}
    actual_workspace_id = item.get("workspaceId") or item.get("workspace_id") or workspace.get("id") or workspace_id
    actual_organization_id = item.get("orgId") or item.get("organizationId") or organization.get("id") or organization_id
    return GristDocument(id=str(item.get("id") or item.get("docId") or ""), name=str(item.get("name") or ""), workspace_id=str(actual_workspace_id) if actual_workspace_id is not None else None, organization_id=str(actual_organization_id) if actual_organization_id is not None else None)


def _column_payload(column: dict[str, Any]) -> dict[str, Any]:
    fields = dict(column.get("fields") or {})
    if column.get("type") and "type" not in fields:
        fields["type"] = column["type"]
    fields.setdefault("label", str(column.get("id", "")))
    return {"id": str(column.get("id", "")), "fields": fields}


def _table_payload(table: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in table.items() if key != "columns"}
    payload["columns"] = [_column_payload(column) for column in table.get("columns", [])]
    return payload


def _is_writable(item: dict[str, Any]) -> bool:
    if "writable" in item:
        return bool(item["writable"])
    if "isWritable" in item:
        return bool(item["isWritable"])
    access = str(item.get("access") or item.get("permission") or "").casefold()
    return access in {"owner", "owners", "edit", "editor", "editors", "write", "admin"}
