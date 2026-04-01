"""Seed data utilities for first-run setup."""

import logging
import shutil
import sys
from pathlib import Path

# Seed data location differs between dev (backend/resources/seed_data) and
# packaged app (sys._MEIPASS/backend/seed_data).
_SEED_DATA_DIR_DEV = Path(__file__).parents[2] / "resources" / "seed_data"
_SEED_DATA_DIR_FROZEN = Path(getattr(sys, '_MEIPASS', '')) / "backend" / "seed_data"
SEED_DATA_DIR = _SEED_DATA_DIR_FROZEN if getattr(sys, 'frozen', False) else _SEED_DATA_DIR_DEV


def seed_data_with_currency(data_dir: Path, currency: str) -> None:
    """Copy seed data template to data/, substituting {default_currency}.

    Called by the setup wizard endpoint after the user picks a currency.
    The data/ directory must not already exist.
    """
    logger = logging.getLogger(__name__)

    if data_dir.exists():
        entries = list(data_dir.iterdir())
        if entries:
            logger.error(f"Data directory {data_dir} already exists and is not empty: {[e.name for e in entries]}")
            raise RuntimeError(f"Data directory {data_dir} already exists and is not empty")
        # Empty directory — remove so copytree can create it
        data_dir.rmdir()

    if not SEED_DATA_DIR.is_dir():
        raise RuntimeError(f"Seed data directory not found: {SEED_DATA_DIR}")

    shutil.copytree(SEED_DATA_DIR, data_dir)
    # Substitute {default_currency} in all .beancount files
    for bc_file in data_dir.rglob("*.beancount"):
        content = bc_file.read_text(encoding="utf-8")
        if "{default_currency}" in content:
            bc_file.write_text(content.replace("{default_currency}", currency), encoding="utf-8")
    logger.info(f"Seeded data directory → {data_dir} (currency={currency})")
