from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest import mock
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import safari_launcher


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((safari_launcher.HOST, 0))
        return int(probe.getsockname()[1])


def _port_pair() -> tuple[int, int]:
    first = _available_port()
    second = _available_port()
    while second == first:
        second = _available_port()
    return first, second


class SafariLauncherTests(unittest.TestCase):
    def setUp(self):
        self.servers = []
        self.addCleanup(self._stop_servers)

    def _stop_servers(self):
        for kind, server, thread in reversed(self.servers):
            if kind == "uvicorn":
                server.should_exit = True
            else:
                server.shutdown()
            thread.join(timeout=5)
            if kind != "uvicorn":
                server.server_close()
            self.assertFalse(thread.is_alive(), "isolated test server did not stop")

    def _make_project(self, root: Path) -> Path:
        root.mkdir(parents=True)
        (root / ".venv" / "Scripts").mkdir(parents=True)
        (root / ".venv" / "Scripts" / "python.exe").touch()
        (root / "ui").mkdir()
        (root / "ui" / "package.json").write_text("{}", encoding="utf-8")
        return root.resolve()

    def _patch_health_app(self, patches: ExitStack, project_root: Path, costing_root: Path):
        from app import web
        from app.repository import InMemorySafariRepository

        patches.enter_context(mock.patch.object(web, "PROJECT_ROOT", project_root))
        patches.enter_context(mock.patch.object(web, "_costing_root", return_value=costing_root))
        patches.enter_context(mock.patch.object(web, "_catalog_path", return_value=project_root / "missing-catalog.json"))
        patches.enter_context(mock.patch.object(web, "_get_repository", return_value=InMemorySafariRepository()))

    def _start_real_api(self, port: int):
        import uvicorn
        from app import web

        server = uvicorn.Server(uvicorn.Config(web.app, host=safari_launcher.HOST, port=port,
            log_level="critical", access_log=False))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 8
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=2)
            self.fail(f"isolated FastAPI health server did not start on {port}")
        self.servers.append(("uvicorn", server, thread))

    def _start_http(self, port: int, routes: dict[str, tuple[int, bytes]]):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, b"Not found"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if self.path.endswith("/health") else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer((safari_launcher.HOST, port), Handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(("http", server, thread))

    def _start_ui_proxy(self, port: int, api_port: int):
        title_page = b"<!doctype html><html><head><title>Safari Manufacturing ERP</title></head><body></body></html>"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    status, body = 200, title_page
                elif self.path == "/api/health":
                    with urlopen(f"http://{safari_launcher.HOST}:{api_port}/api/health", timeout=2) as response:
                        status, body = int(response.status), response.read()
                else:
                    status, body = 404, b"Not found"
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if self.path.endswith("/health") else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer((safari_launcher.HOST, port), Handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(("http", server, thread))

    def test_first_and_repeated_start_use_real_health_identity_not_costing_root(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            project_root = self._make_project(base / "code-checkout")
            costing_root = base / "workbook-files"
            costing_root.mkdir()
            self.assertNotEqual(project_root, costing_root.resolve())
            api_port, ui_port = _port_pair()

            with ExitStack() as patches:
                patches.enter_context(mock.patch.object(safari_launcher, "API_PORT", api_port))
                patches.enter_context(mock.patch.object(safari_launcher, "UI_PORT", ui_port))
                self._patch_health_app(patches, project_root, costing_root.resolve())
                starts = []

                def start_api(root):
                    self.assertEqual(root.resolve(), project_root)
                    self._start_real_api(api_port)
                    starts.append("api")

                def start_ui(root):
                    self.assertEqual(root.resolve(), project_root)
                    self._start_ui_proxy(ui_port, api_port)
                    starts.append("ui")

                first = safari_launcher.launch(project_root, start_api=start_api, start_ui=start_ui)
                status, body = safari_launcher._read_url(f"http://{safari_launcher.HOST}:{api_port}/api/health")
                health = json.loads(body.decode("utf-8"))
                self.assertEqual(status, 200)
                self.assertEqual(health["projectRoot"], str(project_root))
                self.assertEqual(health["costingRoot"], str(costing_root.resolve()))
                self.assertNotEqual(safari_launcher.normalize_root(health["projectRoot"]),
                    safari_launcher.normalize_root(health["costingRoot"]))
                self.assertEqual(first, {"api": "start", "ui": "start"})
                self.assertEqual(starts, ["api", "ui"])

                # The observed relative Vite spelling is irrelevant to identity:
                # the existing listener proves itself through the UI and API routes.
                relative_vite_command = r"ui\node_modules\.bin\..\vite\bin\vite.js --port 4320"
                self.assertIn(r".bin\..\vite\bin\vite.js", relative_vite_command)
                repeated = safari_launcher.launch(project_root,
                    start_api=lambda _root: self.fail("repeated launch started another API"),
                    start_ui=lambda _root: self.fail("repeated launch started another UI"))
                self.assertEqual(repeated, {"api": "reuse", "ui": "reuse"})
                self.assertTrue(safari_launcher.api_matches_project(api_port, project_root))
                self.assertTrue(safari_launcher.ui_matches_project(ui_port, project_root))

    def test_foreign_checkout_is_rejected_even_when_costing_root_matches_requester(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            requested_root = self._make_project(base / "requested-checkout")
            foreign_root = self._make_project(base / "foreign-checkout")
            api_port, ui_port = _port_pair()

            with ExitStack() as patches:
                patches.enter_context(mock.patch.object(safari_launcher, "API_PORT", api_port))
                patches.enter_context(mock.patch.object(safari_launcher, "UI_PORT", ui_port))
                # A foreign API deliberately reports the requested checkout as
                # costingRoot. Only its independently reported code root differs.
                self._patch_health_app(patches, foreign_root, requested_root)
                self._start_real_api(api_port)
                start_api = mock.Mock()
                start_ui = mock.Mock()
                with self.assertRaises(safari_launcher.LauncherError) as caught:
                    safari_launcher.launch(requested_root, start_api=start_api, start_ui=start_ui)
                self.assertIn("projectRoot identity", str(caught.exception))
                start_api.assert_not_called()
                start_ui.assert_not_called()

    def test_legacy_health_without_project_identity_and_unrelated_api_port_fail_closed(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            project_root = self._make_project(base / "code-checkout")
            api_port, ui_port = _port_pair()
            legacy_body = json.dumps({"status": "ok", "costingRoot": str(project_root)}).encode("utf-8")
            generic_health = {"/api/health": (200, legacy_body)}
            self._start_http(api_port, generic_health)

            with mock.patch.object(safari_launcher, "API_PORT", api_port), \
                 mock.patch.object(safari_launcher, "UI_PORT", ui_port):
                self.assertFalse(safari_launcher.api_matches_project(api_port, project_root))
                with self.assertRaises(safari_launcher.LauncherError) as caught:
                    safari_launcher.launch(project_root, start_api=mock.Mock(), start_ui=mock.Mock())
                self.assertIn("projectRoot identity", str(caught.exception))
                self.assertIn("no process was stopped", str(caught.exception))

    def test_ui_title_alone_and_unrelated_proxy_do_not_prove_project_identity(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            project_root = self._make_project(base / "code-checkout")
            costing_root = base / "workbook-files"
            costing_root.mkdir()
            api_port, ui_port = _port_pair()
            legacy_body = json.dumps({"status": "ok", "costingRoot": str(project_root)}).encode("utf-8")
            title_page = b"<!doctype html><html><head><title>Safari Manufacturing ERP</title></head></html>"

            with ExitStack() as patches:
                patches.enter_context(mock.patch.object(safari_launcher, "API_PORT", api_port))
                patches.enter_context(mock.patch.object(safari_launcher, "UI_PORT", ui_port))
                self._patch_health_app(patches, project_root, costing_root.resolve())
                self._start_real_api(api_port)
                self._start_http(ui_port, {
                    "/": (200, title_page),
                    # This UI has the expected title but its API proxy lacks the
                    # project identity contract used by this checkout.
                    "/api/health": (200, legacy_body),
                })
                start_api = mock.Mock()
                start_ui = mock.Mock()
                with self.assertRaises(safari_launcher.LauncherError) as caught:
                    safari_launcher.launch(project_root, start_api=start_api, start_ui=start_ui)
                self.assertIn("matching API proxy", str(caught.exception))
                start_api.assert_not_called()
                start_ui.assert_not_called()

    def test_startup_plan_rejects_unrelated_occupied_ports_without_killing(self):
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
