import os
import unittest
from unittest import mock

from typer.testing import CliRunner

import app.cli as cli
from app.grist_admin import GristWorkspace


class _SetupClient:
    base_url = "https://grist.test"

    def __init__(self, workspaces):
        self.workspaces = workspaces
        self.document_lists = []
        self.creates = []

    def discover_writable_workspaces(self):
        return self.workspaces

    def list_documents(self, workspace_id):
        self.document_lists.append(workspace_id)
        return []


class CliTests(unittest.TestCase):
    def test_existing_version_option_remains_available(self) -> None:
        result = CliRunner().invoke(cli.app, ["--version"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("costing-ods-remap", result.output)

    def test_safari_setup_dry_run_does_not_create_a_document(self) -> None:
        client = _SetupClient([GristWorkspace("ws1", "ERP", "org1", True)])
        with mock.patch.object(cli.GristAdminClient, "from_environment", return_value=client):
            with mock.patch.dict(os.environ, {"SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID": ""}, clear=False):
                result = CliRunner().invoke(cli.app, ["safari-setup"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("PLAN ONLY", result.output)
        self.assertIn("Document action: planned", result.output)
        self.assertEqual(client.document_lists, ["ws1"])
        self.assertEqual(client.creates, [])

    def test_safari_setup_requires_selection_when_multiple_workspaces_are_writable(self) -> None:
        client = _SetupClient([
            GristWorkspace("ws1", "DataEntry", "org1", True),
            GristWorkspace("ws2", "Work", "org1", True),
        ])
        with mock.patch.object(cli.GristAdminClient, "from_environment", return_value=client):
            with mock.patch.dict(os.environ, {"SAFARI_MANUFACTURING_GRIST_WORKSPACE_ID": ""}, clear=False):
                result = CliRunner().invoke(cli.app, ["safari-setup"])

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Workspace selection required", result.output)
        self.assertEqual(client.document_lists, [])
        self.assertEqual(client.creates, [])


if __name__ == "__main__":
    unittest.main()
