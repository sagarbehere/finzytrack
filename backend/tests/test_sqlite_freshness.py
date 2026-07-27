"""
SQLite-mirror freshness gate.

Specification under test (dev-docs/multi-user.md §5 "Scale ceiling"; the
"fix the unconditional rebuild" work):

    The mirror is rebuilt *only when stale*. A build records, in the mirror's
    ``meta`` table, the export version and a per-file (mtime_ns, size)
    fingerprint of the ledger's whole include tree. The single freshness gate
    (`SqliteReader._needs_export`) reports stale iff the DB is missing, the
    build is incomplete, the export version drifted, or any recorded file
    changed — using cheap stat()s, no hashing. `ensure_fresh()` (used at
    startup) rebuilds only on a stale verdict.

These tests assert the *outcomes*: fresh → no rebuild; each trigger → rebuild.
"""

import sqlite3
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked
from app.services.sqlite_exporter import SQLiteExporter, EXPORT_VERSION
from app.services.sqlite_reader import SqliteReader

FIXTURE = Path(__file__).parent / "fixtures" / "small_ledger.beancount"


def _build(ledger: Path, db: Path) -> tuple[SQLiteExporter, SqliteReader]:
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return exporter, reader


@pytest.fixture
def wired(tmp_path):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(FIXTURE.read_text())
    db = tmp_path / "ledger.db"
    exporter, reader = _build(ledger, db)
    return ledger, db, exporter, reader


class TestFreshnessGate:
    def test_fresh_mirror_is_not_stale(self, wired):
        _, _, _, reader = wired
        assert reader._needs_export() is False

    def test_export_version_drift_triggers_rebuild(self, wired):
        _, db, _, reader = wired
        con = sqlite3.connect(str(db))
        con.execute("UPDATE meta SET value=? WHERE key='export_version'", ("bogus:0",))
        con.commit()
        con.close()
        assert reader._needs_export() is True

    def test_incomplete_build_triggers_rebuild(self, wired):
        _, db, _, reader = wired
        con = sqlite3.connect(str(db))
        con.execute("UPDATE meta SET value='0' WHERE key='build_complete'")
        con.commit()
        con.close()
        assert reader._needs_export() is True

    def test_missing_meta_table_triggers_rebuild(self, wired):
        _, db, _, reader = wired
        con = sqlite3.connect(str(db))
        con.execute("DROP TABLE meta")
        con.commit()
        con.close()
        assert reader._needs_export() is True

    def test_ledger_change_triggers_rebuild(self, wired):
        ledger, _, _, reader = wired
        # Appending changes both size and mtime.
        with open(ledger, "a") as f:
            f.write("\n2024-06-01 open Assets:Extra USD\n")
        assert reader._needs_export() is True

    def test_included_file_change_is_detected(self, tmp_path):
        """The fingerprint covers the whole include tree — editing an *included*
        file (which need not touch the root) must be detected. This is the
        latent bug the old root-only mtime check missed."""
        root = tmp_path / "root.beancount"
        child = tmp_path / "child.beancount"
        child.write_text("2024-01-02 open Expenses:Food USD\n")
        root.write_text('include "child.beancount"\n2024-01-01 open Assets:Bank USD\n')
        db = tmp_path / "ledger.db"
        _, reader = _build(root, db)
        assert reader._needs_export() is False
        # Modify only the child; the root is untouched.
        with open(child, "a") as f:
            f.write("2024-01-03 open Expenses:Rent USD\n")
        assert reader._needs_export() is True


class TestEnsureFresh:
    def test_ensure_fresh_skips_rebuild_when_fresh(self, wired):
        _, _, exporter, reader = wired
        calls = []
        orig = exporter.export_full_sync
        exporter.export_full_sync = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
        reader.ensure_fresh()
        assert calls == [], "ensure_fresh rebuilt an already-fresh mirror"

    def test_ensure_fresh_rebuilds_when_stale(self, wired):
        ledger, db, exporter, reader = wired
        con = sqlite3.connect(str(db))
        con.execute("UPDATE meta SET value=? WHERE key='export_version'", ("bogus:0",))
        con.commit()
        con.close()
        assert reader._needs_export() is True
        reader.ensure_fresh()
        # After recovery the mirror is current again.
        assert reader._needs_export() is False
