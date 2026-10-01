"""Safe LibreOffice link refresh against a disposable ODS copy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote
from xml.etree import ElementTree as ET


class LibreOfficeRefreshError(RuntimeError):
    """A linked-workbook refresh could not be completed safely."""


@dataclass(frozen=True)
class RefreshEvidence:
    temporary_path: Path
    original_sha256: str
    refreshed_copy_sha256: str
    linked_sources: list[dict[str, str]]
    checked_at: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].casefold()


def _external_source_urls(ods_path: Path) -> tuple[set[str], list[str]]:
    try:
        with zipfile.ZipFile(ods_path) as package:
            content = ET.fromstring(package.read("content.xml"))
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise LibreOfficeRefreshError(f"Could not inspect ODS external references: {exc}") from exc
    local_urls: set[str] = set()
    unsupported: list[str] = []
    for element in content.iter():
        for attribute, value in element.attrib.items():
            attr_name = _local_name(attribute)
            if attr_name not in {"formula", "href", "url"}:
                continue
            for match in re.findall(r"file:///[^\]\\\s\"']+", value, re.IGNORECASE):
                local_urls.add(match.split("#", 1)[0])
            if attr_name in {"formula", "href", "url"}:
                remote = re.findall(r"https?://[^\]\\\s\"']+", value, re.IGNORECASE)
                unsupported.extend(item for item in remote if "www.w3.org/" not in item.casefold() and "openoffice.org/" not in item.casefold())
    return local_urls, sorted(set(unsupported))


def _url_path(url: str) -> Path:
    # All allowed paths are local Windows ODS files (file:///C:/...).
    remainder = unquote(url[len("file:///"):]).replace("/", os.sep)
    if not re.match(r"^[A-Za-z]:", remainder):
        raise LibreOfficeRefreshError(f"Only local drive-letter ODS links are supported: {url}")
    return Path(remainder).resolve()


def linked_ods_sources(workbook_path: Path, allowed_root: Path) -> list[Path]:
    """Find and validate every local ODS workbook linked by the selected file."""
    root = allowed_root.resolve()
    urls, unsupported = _external_source_urls(workbook_path)
    if unsupported:
        raise LibreOfficeRefreshError(
            "External web links are not refreshed automatically; review them in LibreOffice first."
        )
    sources: set[Path] = set()
    for url in urls:
        source = _url_path(url)
        if source.suffix.casefold() != ".ods":
            raise LibreOfficeRefreshError(f"Unsupported linked source type: {source.name}")
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise LibreOfficeRefreshError(
                f"Linked source is outside the configured Costing root: {source}"
            ) from exc
        if not source.is_file():
            raise LibreOfficeRefreshError(f"Linked ODS source was not found: {source}")
        sources.add(source)
    if not sources:
        raise LibreOfficeRefreshError("No local ODS dependencies were found to refresh.")
    return sorted(sources, key=lambda path: str(path).casefold())


def _libreoffice_program() -> tuple[Path, Path]:
    configured = os.getenv("LIBREOFFICE_PROGRAM", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        Path(r"C:\Program Files\LibreOffice\program"),
        Path(r"C:\Program Files (x86)\LibreOffice\program"),
    ])
    for directory in candidates:
        # The .com launcher forwards command-line flags reliably on Windows;
        # soffice.exe can route to an already-running interactive instance.
        soffice = directory / "soffice.com"
        if not soffice.is_file():
            soffice = directory / "soffice.exe"
        python = directory / "python.exe"
        if soffice.is_file() and python.is_file():
            return soffice, python
    raise LibreOfficeRefreshError(
        "LibreOffice with its UNO Python runtime is not installed or LIBREOFFICE_PROGRAM is not configured."
    )


def _property_script() -> str:
    return r'''import json, sys, time, traceback
import uno
from com.sun.star.beans import PropertyValue

def prop(name, value):
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item

port, document_url = sys.argv[1], sys.argv[2]
local_context = uno.getComponentContext()
resolver = local_context.ServiceManager.createInstanceWithContext(
    "com.sun.star.bridge.UnoUrlResolver", local_context)
remote_context = None
last_error = None
for _ in range(60):
    try:
        remote_context = resolver.resolve(
            "uno:socket,host=127.0.0.1,port=%s;urp;StarOffice.ComponentContext" % port)
        break
    except Exception as exc:
        last_error = exc
        time.sleep(0.5)
if remote_context is None:
    print(json.dumps({"ok": False, "error": "LibreOffice UNO did not start: %s" % last_error}))
    sys.exit(2)

desktop = remote_context.ServiceManager.createInstanceWithContext(
    "com.sun.star.frame.Desktop", remote_context)
document = None
outcome = {"ok": False, "error": "LibreOffice refresh did not run"}
try:
    args = (
        prop("Hidden", True),
        prop("ReadOnly", False),
        # FULL_UPDATE updates document links on load even if LibreOffice would
        # otherwise ask for confirmation. Remote links are rejected before
        # opening this workbook copy.
        prop("UpdateDocMode", 3),
        prop("MacroExecutionMode", 0),
    )
    document = desktop.loadComponentFromURL(document_url, "_blank", 0, args)
    if document is None:
        raise RuntimeError("LibreOffice could not open the temporary workbook copy")
    document.calculateAll()
    document.store()
    document.close(True)
    document = None
    outcome = {"ok": True, "refresh_mode": "FULL_UPDATE", "formulas_recalculated": True}
except Exception as exc:
    outcome = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
finally:
    if document is not None:
        try:
            document.close(True)
        except Exception:
            pass
    try:
        desktop.terminate()
    except Exception:
        pass
print(json.dumps(outcome))
sys.exit(0 if outcome.get("ok") else 3)
'''


def _remove_runtime_directory(path: Path | None) -> None:
    """Retry profile cleanup because the LibreOffice process can release files asynchronously on Windows."""
    if path is None:
        return
    for attempt in range(8):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 7:
                return
            time.sleep(0.25)


@contextmanager
def refreshed_ods_copy(
    workbook_path: Path,
    allowed_root: Path,
    timeout_seconds: int = 180,
) -> Iterator[RefreshEvidence]:
    """Refresh a temporary sibling copy; source workbooks are never saved."""
    workbook_path = workbook_path.resolve()
    sources = linked_ods_sources(workbook_path, allowed_root)
    original_hash = _sha256(workbook_path)
    source_hashes_before = {path: _sha256(path) for path in sources}
    temporary_path = workbook_path.with_name(
        f".{workbook_path.stem}.refresh-{uuid.uuid4().hex}.ods"
    )
    shutil.copy2(workbook_path, temporary_path)
    runtime_path: Path | None = None
    try:
        soffice, uno_python = _libreoffice_program()
        with tempfile.TemporaryDirectory(prefix="safari-lo-refresh-", ignore_cleanup_errors=True) as runtime:
            runtime_path = Path(runtime)
            profile_path = runtime_path / "profile"
            profile_path.mkdir()
            script_path = runtime_path / "refresh_links.py"
            script_path.write_text(_property_script(), encoding="utf-8")
            port_socket = socket.socket()
            try:
                port_socket.bind(("127.0.0.1", 0))
                port = port_socket.getsockname()[1]
            finally:
                port_socket.close()
            profile_url = profile_path.as_uri()
            accept = f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager"
            command = [
                str(soffice), "--headless", "--norestore", "--nodefault",
                "--nofirststartwizard", f"-env:UserInstallation={profile_url}", accept,
            ]
            with (runtime_path / "soffice.log").open("wb") as server_log:
                server = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                    cwd=str(soffice.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
                try:
                    completed = subprocess.run(
                        [str(uno_python), str(script_path), str(port), temporary_path.as_uri()],
                        cwd=str(soffice.parent),
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        check=False,
                    )
                    try:
                        result = json.loads(completed.stdout.strip().splitlines()[-1])
                    except (json.JSONDecodeError, IndexError) as exc:
                        details = completed.stderr.strip() or completed.stdout.strip() or "No diagnostic output."
                        raise LibreOfficeRefreshError(f"LibreOffice refresh helper failed: {details}") from exc
                    if completed.returncode != 0 or not result.get("ok"):
                        raise LibreOfficeRefreshError(result.get("error", "LibreOffice refresh failed."))
                except subprocess.TimeoutExpired as exc:
                    raise LibreOfficeRefreshError(
                        f"LibreOffice external-link refresh timed out after {timeout_seconds} seconds."
                    ) from exc
                finally:
                    if server.poll() is None:
                        if os.name == "nt":
                            subprocess.run(
                                ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                check=False,
                            )
                        else:
                            server.terminate()
                        try:
                            server.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)

        source_hashes_after = {path: _sha256(path) for path in sources}
        original_hash_after = _sha256(workbook_path)
        if original_hash_after != original_hash:
            raise LibreOfficeRefreshError("The selected source workbook changed during temporary refresh.")
        if source_hashes_after != source_hashes_before:
            raise LibreOfficeRefreshError("A linked ODS source changed during temporary refresh.")
        evidence = RefreshEvidence(
            temporary_path=temporary_path,
            original_sha256=original_hash,
            refreshed_copy_sha256=_sha256(temporary_path),
            linked_sources=[
                {
                    "path": str(path.relative_to(allowed_root.resolve())).replace("\\", "/"),
                    "sha256": source_hashes_before[path],
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
                }
                for path in sources
            ],
            checked_at=datetime.now().astimezone().isoformat(),
        )
        yield evidence
    finally:
        _remove_runtime_directory(runtime_path)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
