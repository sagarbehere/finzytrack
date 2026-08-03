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

# The single placeholder the bundled ledgers carry for the user's primary
# currency; substituted at seed time (and at seed-content refresh time — the
# same helper is reused there so the substitution lives in exactly one place).
CURRENCY_PLACEHOLDER = "{default_currency}"


def substitute_currency(content: str, currency: str) -> str:
    """Replace the ``{default_currency}`` placeholder with the user's chosen
    currency. The one substitution rule, shared by first-run seeding and the
    seed-content refresh so a ledger's recorded provenance hash and its written
    bytes always agree. Files without the placeholder pass through unchanged."""
    return content.replace(CURRENCY_PLACEHOLDER, currency)


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

    # The fake ledger ships with a price sidecar (prices.beancount) that lives
    # next to it but is NOT `include`d (dev-docs/valuations.md §3). Carry it
    # along so the demo's investment holdings can be valued.
    # (seed_data_with_currency copies the whole tree, so it needs no such
    # special-case; this troubleshooting-only path copies files individually.)
    prices_src = SEED_DATA_DIR / "ledgers" / "prices.beancount"
    if prices_src.exists():
        shutil.copy2(prices_src, dest_dir / "prices.beancount")
        logger.info(f"Copied price sidecar → {dest_dir / 'prices.beancount'}")


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
        # newline="" on both sides: read the file's real bytes and write them back
        # untranslated. The seed refresh compares a delivered ledger's on-disk hash
        # against the hash of the substituted *bundle* bytes, so translating
        # newlines here (Windows) would make every launch see a ledger that
        # differs from what we recorded delivering.
        # Path.read_text() only accepts `newline` on Python 3.13+; open() has
        # taken it since forever. Use open() so this works on our documented
        # 3.11+ floor — Ubuntu 24.04 ships 3.12, where the kwarg form raises
        # TypeError and breaks the setup wizard.
        with bc_file.open("r", encoding="utf-8", newline="") as fh:
            content = fh.read()
        if CURRENCY_PLACEHOLDER in content:
            bc_file.write_text(
                substitute_currency(content, currency), encoding="utf-8", newline=""
            )
    logger.info(f"Seeded data directory → {data_dir} (currency={currency})")
