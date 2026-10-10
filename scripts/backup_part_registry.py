"""Create and verify a consistent SQLite backup of the local Part registry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "state" / "safari_parts.sqlite3"


def backup(database: Path, destination: Path | None = None) -> Path:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Part registry database does not exist: {database}")
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = database.parent / "backups" / f"safari_parts_{stamp}.sqlite3"
    destination = destination.expanduser().resolve()
    if destination == database:
        raise ValueError("Backup destination must differ from the active Part registry database.")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite an existing backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(database, timeout=30)
    target = sqlite3.connect(destination)
    try:
        check = source.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("The active Part registry failed SQLite quick_check.")
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError("The backup failed SQLite integrity_check.")
    except Exception:
        target.close()
        source.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(os.getenv("SAFARI_PART_DATABASE_PATH", DEFAULT_DATABASE)))
    parser.add_argument("--destination", type=Path, help="Optional backup file path; defaults to state/backups with a UTC timestamp.")
    arguments = parser.parse_args()
    saved = backup(arguments.database, arguments.destination)
    print(f"Verified Part registry backup: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
