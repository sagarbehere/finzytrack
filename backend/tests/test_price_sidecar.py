"""Price sidecar load + freshness (Layer 2 valuation, Slice 0).

Specification under test (dev-docs/valuations.md §3/§6,
dev-docs/valuations-invdashboards-implementation.md Slice 0):

    A dedicated ``prices.beancount`` sidecar lives next to the root ledger but is
    deliberately NOT ``include``d in it (kept off the transaction-edit parse
    path). At export the exporter loads it and folds its ``price`` directives
    into the mirror's ``prices`` table *alongside* any prices in the main ledger.
    Because the sidecar is outside the include tree, it is added explicitly to
    the mirror's freshness fingerprint so a price fetch (which writes only the
    sidecar) still triggers a re-export.

These tests assert the outcomes: sidecar prices land; main-ledger prices still
land; the sidecar is fingerprinted; touching it makes the mirror stale.
"""

import sqlite3
import json
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader


# A main ledger that itself carries one price directive, so we can prove the
# main-ledger and sidecar prices coexist rather than one overwriting the other.
_MAIN = """\
option "operating_currency" "USD"

2020-01-01 open Assets:Broker USD,VOO
2020-01-01 open Assets:Cash USD

2020-06-01 price VOO 300.00 USD

2020-02-01 * "Buy"
  Assets:Broker  2 VOO {280.00 USD}
  Assets:Cash   -560.00 USD
"""

_SIDECAR = """\
; price sidecar — NOT included in the root
2021-01-15 price VOO 400.00 USD
2021-02-15 price VOO 410.00 USD
2021-02-15 price VTI 220.00 USD
"""


def _wire(tmp_path: Path, *, with_sidecar: bool):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(_MAIN)
    if with_sidecar:
        sidecar_path(ledger).write_text(_SIDECAR)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return ledger, db, exporter, reader


def _prices(db: Path):
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT base_currency, date, quote_number, quote_currency "
            "FROM prices ORDER BY base_currency, date"
        ).fetchall()
    finally:
        con.close()


def test_sidecar_is_keyed_to_the_ledger_filename(tmp_path):
    """Two ledgers sharing a folder must get distinct sidecars, so switching the
    active ledger switches its prices (no cross-contamination)."""
    one = tmp_path / "one.beancount"
    fake = tmp_path / "fake.beancount"
    assert sidecar_path(one).name == "one.prices.beancount"
    assert sidecar_path(fake).name == "fake.prices.beancount"
    assert sidecar_path(one) != sidecar_path(fake)


def test_sidecar_prices_are_loaded_into_the_mirror(tmp_path):
    _, db, _, _ = _wire(tmp_path, with_sidecar=True)
    rows = _prices(db)
    # The three sidecar prices are present, exact.
    assert ("VOO", "2021-01-15", "400.00", "USD") in rows
    assert ("VOO", "2021-02-15", "410.00", "USD") in rows
    assert ("VTI", "2021-02-15", "220.00", "USD") in rows


def test_main_ledger_price_still_lands_alongside_sidecar(tmp_path):
    """Sidecar prices must be additive — the main ledger's own price directive is
    not lost when the sidecar is merged."""
    _, db, _, _ = _wire(tmp_path, with_sidecar=True)
    rows = _prices(db)
    assert ("VOO", "2020-06-01", "300.00", "USD") in rows
    # Exactly the 1 main-ledger + 3 sidecar prices, no dupes, no drops.
    assert len(rows) == 4


def test_no_sidecar_leaves_only_main_ledger_prices(tmp_path):
    _, db, _, _ = _wire(tmp_path, with_sidecar=False)
    rows = _prices(db)
    assert rows == [("VOO", "2020-06-01", "300.00", "USD")]


def test_sidecar_is_recorded_in_the_freshness_fingerprint(tmp_path):
    ledger, db, _, _ = _wire(tmp_path, with_sidecar=True)
    con = sqlite3.connect(str(db))
    try:
        files = [r[0] for r in json.loads(
            con.execute("SELECT value FROM meta WHERE key='ledger_files'").fetchone()[0]
        )]
    finally:
        con.close()
    assert str(sidecar_path(ledger)) in files


def test_touching_sidecar_makes_mirror_stale(tmp_path):
    """A price fetch writes only the sidecar (never a transaction file); the
    freshness gate must still notice and re-export."""
    ledger, _, _, reader = _wire(tmp_path, with_sidecar=True)
    assert reader._needs_export() is False
    with open(sidecar_path(ledger), "a") as f:
        f.write("2021-03-15 price VOO 420.00 USD\n")
    assert reader._needs_export() is True


def test_creating_sidecar_after_a_sidecarless_build_is_not_auto_detected(tmp_path):
    """Known, deliberate limitation: the freshness gate stat()s only the files
    *recorded* at build time, and an absent sidecar is intentionally NOT recorded
    (recording a missing path would make every later stat() raise → an infinite
    rebuild loop). So the very first sidecar creation is invisible to the
    stat-based gate — the Slice-4 fetcher must trigger its own re-export after
    the first write. Once a sidecar exists at build time it IS fingerprinted, and
    subsequent edits are detected (see test_touching_sidecar_makes_mirror_stale).
    """
    ledger, _, _, reader = _wire(tmp_path, with_sidecar=False)
    assert reader._needs_export() is False
    sidecar_path(ledger).write_text(_SIDECAR)
    assert reader._needs_export() is False  # not detected by stat alone — see docstring
