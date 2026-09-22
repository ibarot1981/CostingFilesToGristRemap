"""Safe, read-only filesystem catalog for the Costing Explorer."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

from app.catalog import scan_costing_file
from app.repository import sha256_file


class PathSafetyError(ValueError):
    code = "PATH_OUTSIDE_ROOT"


@dataclass(frozen=True)
class FileNode:
    id: str
    name: str
    type: str
    relative_path: str
    extension: str
    size_bytes: int | None
    modified_at: str | None
    candidate_classification: str
    mapping_status: str = "unmapped"
    readable: bool | None = None
    parse_error: str | None = None
    sheet_count: int | None = None
    external_reference_count: int | None = None
    external_link_warning: bool = False
    content_hash: str | None = None
    read_state: str = "uninspected"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["relativePath"] = payload["relative_path"]
        payload["sizeBytes"] = payload["size_bytes"]
        payload["modifiedAt"] = payload["modified_at"]
        payload["candidateClassification"] = payload["candidate_classification"]
        payload["mappingStatus"] = payload["mapping_status"]
        payload["sheetCount"] = payload["sheet_count"]
        payload["externalReferenceCount"] = payload["external_reference_count"]
        payload["externalLinkWarning"] = payload["external_link_warning"]
        payload["contentHash"] = payload["content_hash"]
        payload["readState"] = payload["read_state"]
        return payload


_ABSOLUTE_PATTERN = re.compile(r"^(?:[A-Za-z]:|[\\/]{2}|[\\/])")


class FilesystemCatalog:
    """Resolve user-supplied relative IDs and inspect files without writing."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise FileNotFoundError(f"Costing root does not exist: {self.root}")

    def resolve(self, relative_path: str = "", *, require_exists: bool = True) -> Path:
        if "\x00" in relative_path:
            raise PathSafetyError("Path contains a NUL character")
        decoded = relative_path
        for _ in range(3):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        if _ABSOLUTE_PATTERN.match(decoded) or PureWindowsPath(decoded).is_absolute() or PureWindowsPath(decoded).drive:
            raise PathSafetyError("Absolute paths are not accepted")
        parts = re.split(r"[\\/]", decoded)
        if any(part in {"..", "."} for part in parts if part):
            raise PathSafetyError("Relative traversal segments are not accepted")
        candidate = self.root / Path(*[part for part in parts if part])
        # Inspect the caller's lexical path before resolve() can traverse a
        # junction/reparse point and erase that component from the path.
        self._reject_reparse_points(candidate)
        candidate = candidate.resolve(strict=False)
        root_key = str(self.root).replace("/", "\\").casefold().rstrip("\\")
        candidate_key = str(candidate).replace("/", "\\").casefold().rstrip("\\")
        if candidate_key != root_key and not candidate_key.startswith(root_key + "\\"):
            raise PathSafetyError("Path must remain inside the configured costing root")
        self._reject_reparse_points(candidate)
        if require_exists and not candidate.exists():
            raise FileNotFoundError(f"Path not found below costing root: {relative_path}")
        return candidate

    def list_children(self, relative_path: str = "") -> list[FileNode]:
        directory = self.resolve(relative_path)
        if not directory.is_dir():
            raise NotADirectoryError(relative_path)
        children: list[FileNode] = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            self._reject_reparse_points(child)
            children.append(self._node(child))
        return children

    def walk_files(self, *, query: str = "", extension: str | None = ".ods") -> list[FileNode]:
        needle = query.strip().casefold()
        files: list[FileNode] = []
        for directory, child_dirs, child_files in os.walk(self.root, followlinks=False):
            directory_path = Path(directory)
            self._reject_reparse_points(directory_path)
            child_dirs[:] = [item for item in child_dirs if not self._is_reparse(directory_path / item)]
            for name in child_files:
                child = directory_path / name
                if self._is_reparse(child):
                    continue
                if extension and child.suffix.casefold() != extension.casefold():
                    continue
                relative = self._relative(child)
                if needle and needle not in relative.casefold():
                    continue
                files.append(self._node(child))
        return sorted(files, key=lambda item: item.relative_path.casefold())

    def inspect(self, relative_path: str) -> FileNode:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        node = self._node(path)
        if path.suffix.casefold() != ".ods":
            return node
        try:
            scanned = scan_costing_file(path, self.root)
            read_state = "readable" if scanned.readable else ("encrypted" if "encrypted" in (scanned.error or "").casefold() else "parse_error")
            classification = node.candidate_classification
            costing_sheets = {sheet.strip().casefold() for sheet in scanned.sheets}
            if (
                classification == "generated_output"
                and "export" in path.name.casefold()
                and {"cost log", "total summary"}.issubset(costing_sheets)
            ):
                classification = "costing_candidate"
            return FileNode(
                **{**asdict(node), "readable": scanned.readable, "parse_error": scanned.error,
                   "sheet_count": len(scanned.sheets), "external_reference_count": scanned.external_reference_count,
                   "external_link_warning": scanned.external_reference_count > 0, "content_hash": sha256_file(path), "read_state": read_state,
                   "candidate_classification": classification}
            )
        except (OSError, ValueError) as exc:
            return FileNode(**{**asdict(node), "readable": False, "parse_error": f"{type(exc).__name__}: {exc}", "read_state": "parse_error"})

    def _node(self, path: Path) -> FileNode:
        relative = self._relative(path)
        is_dir = path.is_dir()
        stat = path.stat()
        return FileNode(
            id=relative,
            name=path.name,
            type="directory" if is_dir else "file",
            relative_path=relative,
            extension="" if is_dir else path.suffix.casefold(),
            size_bytes=None if is_dir else stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            candidate_classification=self.classify(path),
        )

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    @staticmethod
    def classify(path: Path) -> str:
        lower = path.name.casefold()
        if path.is_dir():
            return "directory"
        if path.suffix.casefold() != ".ods":
            return "unsupported"
        if any(token in lower for token in ("archive", "old", "backup")):
            return "archive"
        if any(token in lower for token in ("template", "master", "database", "db")):
            return "master_or_template"
        if any(token in lower for token in ("output", "generated", "export")):
            return "generated_output"
        return "costing_candidate"

    def _reject_reparse_points(self, candidate: Path) -> None:
        try:
            relative_parts = candidate.relative_to(self.root).parts
        except ValueError as exc:
            raise PathSafetyError("Resolved path is outside the costing root") from exc
        current = self.root
        for part in relative_parts:
            current = current / part
            if self._is_reparse(current):
                raise PathSafetyError("Reparse points and symlinks are not accepted in the costing path")

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            return False
        attributes = getattr(metadata, "st_file_attributes", 0)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)
