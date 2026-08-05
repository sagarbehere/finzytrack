"""The Beancount → SQLite boundary contract.

Two shipped bugs had the same shape: an object crossed from Beancount into
SQLite (or into JSON on the way there) without coercion, sqlite3 refused to
bind it, and the whole export aborted — leaving the app unable to open the
ledger at all.

  - `open Assets:Broker AAPL "FIFO"` → a `Booking` enum (v3; v2 used a string)
  - metadata holding a dict keyed by a class object → json.dumps refused it

Testing directive-by-directive only catches the forms someone thought to write
down. These tests assert the *contract* instead: every value the exporter binds
must be a type sqlite3 accepts, for every ledger we have. A new Beancount
version returning a new object type fails here rather than in a user's log.

See also `tests/fixtures/v3_kitchen_sink.beancount`, which exercises the whole
directive surface — Beancount's own `bean-example` generator emits none of the
booking-method, pad, note, document, custom or query forms where both bugs
lived.
"""

import sqlite3
from pathlib import Path

import pytest
from beancount import loader

from app.services.sqlite_exporter import SQLiteExporter

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Types sqlite3 binds natively. Decimal is deliberately absent: money is stored
# as TEXT (dev-docs/money-types.md), so a Decimal reaching a bind call means a
# money value escaped its `str()` conversion — a bug in its own right.
BINDABLE = (str, int, float, bytes, type(None))

LEDGERS = [
    "v3_kitchen_sink.beancount",
    "small_ledger.beancount",
    "edge_cases.beancount",
]


class _AuditingConnection:
    """Delegates to a real connection, recording every bound parameter."""

    def __init__(self, con, bindings):
        self._con = con
        self._bindings = bindings

    def _record(self, sql, params):
        table = "?"
        if "INSERT INTO" in sql:
            table = sql.split("INSERT INTO", 1)[1].split("(", 1)[0].split()[0]
        for i, value in enumerate(params):
            self._bindings.append((table, i, value))

    def execute(self, sql, params=()):
        self._record(sql, params)
        return self._con.execute(sql, params)

    def executemany(self, sql, rows):
        rows = list(rows)
        for row in rows:
            self._record(sql, row)
        return self._con.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._con, name)


def _export_recording_bindings(ledger_path, db_path):
    """Run a full export, returning every (table, position, value) bound."""
    entries, errors, options = loader.load_file(str(ledger_path))
    assert not errors, f"fixture must parse cleanly: {[str(e.message) for e in errors]}"

    bindings = []
    exporter = SQLiteExporter(str(db_path))
    real_open = exporter._open_connection
    exporter._open_connection = lambda: _AuditingConnection(real_open(), bindings)
    exporter.export_full_sync(
        entries, errors, options, ledger_file=str(ledger_path)
    )
    return bindings


@pytest.mark.parametrize("ledger_name", LEDGERS)
def test_every_bound_value_is_bindable(ledger_name, tmp_path):
    """No value reaching SQLite may be a type sqlite3 cannot bind.

    This is the assertion that would have caught `Booking.FIFO` before a user
    did. It is deliberately about *types*, not about any particular directive.
    """
    bindings = _export_recording_bindings(
        FIXTURES_DIR / ledger_name, tmp_path / "ledger.db"
    )
    assert bindings, "export bound nothing — the audit hook is not wired up"

    offenders = [
        (table, i, type(value).__name__, repr(value)[:80])
        for table, i, value in bindings
        if not isinstance(value, BINDABLE)
    ]
    assert not offenders, (
        "values reached SQLite that sqlite3 cannot bind — each would abort the "
        f"whole export: {offenders[:5]}"
    )


@pytest.mark.parametrize("ledger_name", LEDGERS)
def test_export_completes_and_populates_tables(ledger_name, tmp_path):
    """The export runs to completion — no sub-export silently exports nothing."""
    db_path = tmp_path / "ledger.db"
    _export_recording_bindings(FIXTURES_DIR / ledger_name, db_path)

    con = sqlite3.connect(str(db_path))
    accounts = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    postings = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    con.close()

    assert accounts > 0
    assert postings > 0


class TestKitchenSinkCoverage:
    """The fixture must keep exercising the surface it claims to cover.

    Without these, a well-meaning edit could quietly drop the booking methods
    or the pad/custom/query directives and the contract test above would still
    pass — against a ledger that no longer proves anything.
    """

    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "ledger.db"
        _export_recording_bindings(
            FIXTURES_DIR / "v3_kitchen_sink.beancount", db_path
        )
        con = sqlite3.connect(str(db_path))
        yield con
        con.close()

    def test_every_booking_method_is_exported_as_text(self, db):
        """All six v3 booking methods, stored as the text the ledger carries."""
        rows = dict(
            db.execute(
                "SELECT name, booking FROM accounts WHERE booking IS NOT NULL"
            ).fetchall()
        )
        assert rows == {
            "Assets:Broker:Strict": "STRICT",
            "Assets:Broker:StrictSize": "STRICT_WITH_SIZE",
            "Assets:Broker:Fifo": "FIFO",
            "Assets:Broker:Lifo": "LIFO",
            "Assets:Broker:Hifo": "HIFO",
            "Assets:Broker:Average": "AVERAGE",
            "Assets:Broker:NoBooking": "NONE",
        }

    @pytest.mark.parametrize(
        "table,minimum",
        [
            ("accounts", 17),
            ("commodities", 4),
            ("prices", 4),
            ("balance_assertions", 2),
            ("pad_directives", 1),
            ("notes", 1),
            ("events", 2),
            ("documents", 1),
            ("custom_directives", 3),
            ("stored_queries", 1),
            ("lots", 1),
        ],
    )
    def test_directive_surface_stays_covered(self, db, table, minimum):
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count >= minimum, (
            f"{table} has {count} rows, expected at least {minimum} — the "
            "kitchen-sink fixture lost coverage it is supposed to provide"
        )
