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


def copy_fake_ledger(data_dir: Path) -> None:
    """Copy fake.beancount from seed data into data_dir/ledgers/.

    Called during setup when the user hasn't chosen demo mode (so
    seed_data_with_currency won't run), but we still want the fake ledger
    available for troubleshooting.
    """
    logger = logging.getLogger(__name__)
    src = SEED_DATA_DIR / "ledgers" / "fake.beancount"
    if not src.exists():
        logger.warning(f"Fake ledger template not found: {src}")
        return
    dest_dir = data_dir / "ledgers"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / "fake.beancount")
    logger.info(f"Copied fake ledger → {dest_dir / 'fake.beancount'}")


def seed_data_with_currency(data_dir: Path, currency: str) -> None:
    """Copy seed data template to data/, substituting {default_currency}.

    Called by the setup wizard endpoint after the user picks a currency.
    Uses dirs_exist_ok=True so pre-existing subdirectories (e.g. backups/)
    don't cause failures.
    """
    logger = logging.getLogger(__name__)

    if not SEED_DATA_DIR.is_dir():
        raise RuntimeError(f"Seed data directory not found: {SEED_DATA_DIR}")

    shutil.copytree(SEED_DATA_DIR, data_dir, dirs_exist_ok=True)
    # Substitute {default_currency} in all .beancount files
    for bc_file in data_dir.rglob("*.beancount"):
        content = bc_file.read_text(encoding="utf-8")
        if "{default_currency}" in content:
            bc_file.write_text(content.replace("{default_currency}", currency), encoding="utf-8")
    logger.info(f"Seeded data directory → {data_dir} (currency={currency})")
