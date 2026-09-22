"""Create the read-only first-pass catalog report used by the ERP UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog import most_recent, scan_costing_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/first_pass_costing_catalog.json"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    summary, files = scan_costing_root(args.root, workers=args.workers)
    payload = {
        "summary": summary.to_dict(),
        "recent_files": [item.to_dict() for item in most_recent(files, 40)],
        "external_reference_files": [
            item.to_dict() for item in files if item.external_reference_count
        ],
        "unreadable_files": [item.to_dict() for item in files if not item.readable],
        "files": [item.to_dict() for item in files],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
