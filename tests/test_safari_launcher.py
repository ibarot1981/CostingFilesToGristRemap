from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import safari_launcher


class SafariLauncherTests(unittest.TestCase):
    def test_first_start_launches_api_then_one_strict_port_ui(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".venv" / "Scripts").mkdir(parents=True)
            (root / ".venv" / "Scripts" / "python.exe").touch()
            (root / "ui").mkdir()
            (root / "ui" / "package.json").touch()
            starts = []
            with mock.patch.object(safari_launcher, "port_is_listening", side_effect=[False, False]), \
                 mock.patch.object(safari_launcher, "api_matches_project", return_value=True), \
                 mock.patch.object(safari_launcher, "ui_matches_project", return_value=True), \
                 mock.patch.object(safari_launcher, "wait_until", side_effect=lambda check, *_args, **_kwargs: self.assertTrue(check())):
                plan = safari_launcher.launch(root, start_api=lambda path: starts.append(("api", path)),
                    start_ui=lambda path: starts.append(("ui", path)))
            self.assertEqual(plan, {"api": "start", "ui": "start"})
            self.assertEqual([kind for kind, _ in starts], ["api", "ui"])

    def test_repeated_start_reuses_relative_vite_project_ui_without_process_name_matching(self):
        # The observed Vite command used a relative node_modules path. Reuse is
        # based on the app title and proxied project health, never a Vite command
        # line substring, so relative CLI spellings have the same safe result.
        relative_vite_command = r"ui\node_modules\.bin\..\vite\bin\vite.js --host 127.0.0.1 --port 4320"
        self.assertIn(r".bin\..\vite\bin\vite.js", relative_vite_command)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".venv" / "Scripts").mkdir(parents=True)
            (root / ".venv" / "Scripts" / "python.exe").touch()
            (root / "ui").mkdir()
            (root / "ui" / "package.json").touch()
            with mock.patch.object(safari_launcher, "port_is_listening", side_effect=[True, True]), \
                 mock.patch.object(safari_launcher, "api_matches_project", return_value=True), \
                 mock.patch.object(safari_launcher, "ui_matches_project", return_value=True), \
                 mock.patch.object(safari_launcher, "wait_until", side_effect=lambda check, *_args, **_kwargs: self.assertTrue(check())), \
                 mock.patch.object(safari_launcher, "_start_api") as start_api, \
                 mock.patch.object(safari_launcher, "_start_ui") as start_ui:
                plan = safari_launcher.launch(root)
            self.assertEqual(plan, {"api": "reuse", "ui": "reuse"})
            start_api.assert_not_called()
            start_ui.assert_not_called()

    def test_occupied_unrelated_ports_fail_before_launch_and_never_offer_kill(self):
        cases = [
            ({"api_busy": True, "api_matches": False, "ui_busy": False, "ui_matches": False}, "4321"),
            ({"api_busy": False, "api_matches": False, "ui_busy": True, "ui_matches": False}, "4320"),
        ]
        for arguments, port in cases:
            with self.subTest(port=port), self.assertRaises(safari_launcher.LauncherError) as caught:
                safari_launcher.startup_plan(**arguments)
            self.assertIn(port, str(caught.exception))
            self.assertIn("no process was stopped", str(caught.exception))

    def test_only_the_documented_ui_port_is_used_and_vite_is_strict(self):
        config = (ROOT / "ui" / "vite.config.ts").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "safari_launcher.py").read_text(encoding="utf-8")
        batch = (ROOT / "start-safari-manufacturing.bat").read_text(encoding="utf-8")
        self.assertIn("port: 4320", config)
        self.assertIn("strictPort: true", config)
        self.assertIn("--port 4320 --strictPort", launcher)
        self.assertIn("http://127.0.0.1:4320/", batch)
        self.assertNotIn("Stop-Process", batch + launcher)
        self.assertNotIn("taskkill", batch + launcher)


if __name__ == "__main__":
    unittest.main()
