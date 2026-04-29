"""Logging setup for the CLI."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_dir: Path = Path("logs")) -> None:
    """Configure file logging for troubleshooting CLI runs."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "costing_ods_remap.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
