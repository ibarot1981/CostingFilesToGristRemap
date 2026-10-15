"""Safe, repeatable local Safari Manufacturing launcher.

The launcher reuses only services that identify this checkout through health
responses. It never terminates a listener or starts Vite on a fallback port.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


HOST = "127.0.0.1"
API_PORT = 4321
UI_PORT = 4320
UI_TITLE = "<title>Safari Manufacturing ERP</title>"
READY_TIMEOUT_SECONDS = 30
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class LauncherError(RuntimeError):
    pass


def normalize_root(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path))).rstrip("\\/")


def port_is_listening(port: int, *, host: str = HOST, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def _read_url(url: str, timeout: float = 1.0) -> tuple[int, bytes]:
    with urlopen(url, timeout=timeout) as response:
        return int(response.status), response.read(2_000_000)


def api_matches_project(port: int, project_root: str | Path) -> bool:
    try:
        status, body = _read_url(f"http://{HOST}:{port}/api/health")
        health = json.loads(body.decode("utf-8"))
        # costingRoot identifies workbook data. Only projectRoot proves which
        # checkout is serving this API; older health responses fail closed.
        reported_root = health.get("projectRoot")
        return (status == 200 and health.get("status") == "ok" and isinstance(reported_root, str)
            and bool(reported_root.strip()) and normalize_root(reported_root) == normalize_root(project_root))
    except (OSError, ValueError, URLError, json.JSONDecodeError):
        return False


def ui_matches_project(port: int, project_root: str | Path) -> bool:
    """Require both the expected UI and its proxy to the matching project API."""
    try:
        status, body = _read_url(f"http://{HOST}:{port}/")
        return (status == 200 and UI_TITLE.encode("ascii") in body
            and api_matches_project(port, project_root))
    except (OSError, URLError):
        return False


def startup_plan(*, api_busy: bool, api_matches: bool, ui_busy: bool, ui_matches: bool) -> dict[str, str]:
    """Plan safe starts/reuse; fail before launching anything on foreign ports."""
    if api_busy and not api_matches:
        raise LauncherError(
            f"Port {API_PORT} is occupied by an API without a matching projectRoot identity. "
            f"Inspect it and, if it is an older instance, restart this checkout's API yourself; no process was stopped."
        )
    if ui_busy and not ui_matches:
        raise LauncherError(
            f"Port {UI_PORT} does not serve this project's UI and matching API proxy. "
            f"Inspect it and restart this checkout's UI yourself; no process was stopped."
        )
    return {"api": "reuse" if api_busy else "start", "ui": "reuse" if ui_busy else "start"}


def wait_until(check, description: str, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.35)
    raise LauncherError(f"{description} did not become ready within {timeout:g} seconds. Check its console for errors.")


def _start_api(project_root: Path) -> None:
    powershell = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    subprocess.Popen([powershell, "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File",
        str(project_root / "run-web.ps1")], cwd=str(project_root), creationflags=NEW_CONSOLE)


def _start_ui(project_root: Path) -> None:
    powershell = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    command = "npm.cmd run dev -- --host 127.0.0.1 --port 4320 --strictPort"
    subprocess.Popen([powershell, "-NoProfile", "-NoExit", "-Command", command],
        cwd=str(project_root / "ui"), creationflags=NEW_CONSOLE)


def launch(project_root: str | Path, *, start_api=_start_api, start_ui=_start_ui) -> dict[str, str]:
    root = Path(project_root).resolve()
    if not (root / ".venv" / "Scripts" / "python.exe").is_file():
        raise LauncherError(f"Virtual environment not found under {root}. Run setup.bat first.")
    if not (root / "ui" / "package.json").is_file():
        raise LauncherError(f"UI package.json not found under {root / 'ui'}.")

    api_busy = port_is_listening(API_PORT)
    ui_busy = port_is_listening(UI_PORT)
    plan = startup_plan(api_busy=api_busy, api_matches=api_matches_project(API_PORT, root) if api_busy else False,
        ui_busy=ui_busy, ui_matches=ui_matches_project(UI_PORT, root) if ui_busy else False)

    if plan["api"] == "start":
        start_api(root)
        wait_until(lambda: api_matches_project(API_PORT, root), f"Project API at http://{HOST}:{API_PORT}")
    if plan["ui"] == "start":
        start_ui(root)
    wait_until(lambda: ui_matches_project(UI_PORT, root),
        f"Project UI and proxy to its API at http://{HOST}:{UI_PORT}")
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    options = parser.parse_args(argv)
    try:
        plan = launch(options.project_root)
    except LauncherError as exc:
        print(f"Safari Manufacturing did not start: {exc}", file=sys.stderr)
        return 1
    print(f"Safari Manufacturing is ready at http://{HOST}:{UI_PORT}/ (API {HOST}:{API_PORT}).")
    print(f"API: {plan['api']}; UI: {plan['ui']}. Repeated launch reuses healthy project services.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
