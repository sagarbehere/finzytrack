"""Pad↔Balance pairing tests (export + engine).

The pairing rule, per Beancount semantics, applies to BOTH the read path
(``SqliteReader.get_balance_directives``) and the write/delete path
(``BeancountEngine._find_pad_before_balance_entry``). Both must agree on
which Pad feeds which Balance — otherwise the UI's "has_pad" / "delete
also pad" controls disagree with what actually happens on disk.

Rule:
    Walk entries in source order. Maintain a per-account ``candidate_pad``.
    On Pad(account, ...): set/overwrite ``candidate_pad[account]``.
    On Balance(account, ...): the candidate (if any) feeds this balance;
    clear it.
"""
import sqlite3
from pathlib import Path

import pytest
from beancount import loader
from beancount.core import data as bd

from app.core.beancount_engine import BeancountEngine
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader


def _export(tmp_path: Path, ledger_text: str) -> tuple[Path, list, list]:
    """Write *ledger_text* to a temp file, export, return (db_path, entries, errors)."""
    ledger_path = tmp_path / "main.beancount"
    ledger_path.write_text(ledger_text)
    db_path = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db_path))
    entries, errors, options = loader.load_file(str(ledger_path))
    exporter.export_full_sync(entries, errors, options)
    return db_path, entries, errors


def _balance_rows(db_path: Path, account: str):
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT date, pad_date, pad_source_account FROM balance_assertions "
        "WHERE account = ? ORDER BY date",
        (account,),
    ).fetchall()
    con.close()
    return rows


# ── Export-time pairing (stamps balance_assertions.pad_*) ───────────────────


class TestExportPadPairing:
    def test_single_pad_pairs_with_next_balance(self, tmp_path):
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
"""
        db_path, _, errors = _export(tmp_path, ledger)
        assert not errors
        rows = _balance_rows(db_path, "Assets:Cash")
        assert rows == [("2020-02-01", "2020-01-01", "Equity:Opening-Balances")]

    def test_multi_pad_only_most_recent_pairs(self, tmp_path):
        """Per Beancount: 'If there is more than one pad directive between
        two balance assertions, only the most recent one is used.'
        """
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
"""
        db_path, _, errors = _export(tmp_path, ledger)
        rows = _balance_rows(db_path, "Assets:Cash")
        assert len(rows) == 1
        # Only the second pad pairs; the first is silently overridden.
        assert rows[0] == ("2020-02-01", "2020-01-15", "Equity:Opening-Balances")

    def test_pad_consumed_only_by_first_following_balance(self, tmp_path):
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
2020-03-01 balance Assets:Cash  600 USD
"""
        db_path, _, errors = _export(tmp_path, ledger)
        rows = _balance_rows(db_path, "Assets:Cash")
        assert rows == [
            ("2020-02-01", "2020-01-01", "Equity:Opening-Balances"),
            ("2020-03-01", None, None),
        ]

    def test_pad_with_transaction_between_still_pairs(self, tmp_path):
        """Beancount allows Transactions (and any other entry) between Pad
        and Balance — the Pad still feeds the Balance. The strict
        "immediately preceding" check would have missed this.
        """
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 * "Payday"
  Assets:Cash      50 USD
  Income:Salary   -50 USD
2020-02-01 balance Assets:Cash  500 USD
"""
        db_path, _, errors = _export(tmp_path, ledger)
        rows = _balance_rows(db_path, "Assets:Cash")
        assert rows == [("2020-02-01", "2020-01-01", "Equity:Opening-Balances")]

    def test_pad_pairing_does_not_cross_accounts(self, tmp_path):
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Assets:Bank USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Bank  100 USD
2020-03-01 balance Assets:Cash  500 USD
"""
        db_path, _, errors = _export(tmp_path, ledger)
        bank = _balance_rows(db_path, "Assets:Bank")
        cash = _balance_rows(db_path, "Assets:Cash")
        # The pad is for Cash, so the Bank balance must not pick it up.
        assert bank == [("2020-02-01", None, None)]
        # The Cash balance is the first balance for Cash after the pad, so it
        # consumes the pad even though a Bank balance landed between them.
        assert cash == [("2020-03-01", "2020-01-01", "Equity:Opening-Balances")]


# ── Reader-side: get_balance_directives reflects the pairing ────────────────


class TestReaderPadPairing:
    def test_reader_returns_paired_pad_from_export(self, tmp_path):
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
2020-03-01 balance Assets:Cash  600 USD
"""
        db_path, _, _ = _export(tmp_path, ledger)
        reader = SqliteReader(
            sqlite_path=db_path,
            ledger_file=tmp_path / "main.beancount",
            exporter=SQLiteExporter(str(db_path)),
        )
        directives = reader.get_balance_directives("Assets:Cash")
        assert len(directives) == 2
        assert directives[0].has_pad is True
        # The most recent pre-balance pad must win, not the earliest.
        assert directives[0].pad_source_account == "Equity:Opening-Balances"
        # The second balance has no pad (the only pad was consumed already).
        assert directives[1].has_pad is False
        assert directives[1].pad_source_account is None


# ── Engine-side: _find_pad_before_balance_entry agrees with reader ──────────


class TestEnginePadDetection:
    """The engine's pad-finder must agree with the reader's pairing so the
    UI's 'delete also pad' / 'update pad' controls actually affect the same
    pad the reader reported.
    """

    def _entries(self, tmp_path: Path, ledger: str) -> list:
        ledger_path = tmp_path / "main.beancount"
        ledger_path.write_text(ledger)
        entries, errors, _ = loader.load_file(str(ledger_path))
        # Beancount itself flags overridden pads as "Unused Pad entry" — that's
        # informative, not fatal, and confirms the "only most recent pad
        # applies" semantic we mirror. Skip those for the purposes of
        # asserting "no real errors."
        real_errors = [e for e in errors if "Unused Pad" not in (getattr(e, "message", "") or "")]
        assert not real_errors, real_errors
        return entries

    def test_pad_immediately_preceding_balance_is_found(self, tmp_path):
        entries = self._entries(tmp_path, """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
""")
        balance_idx = next(i for i, e in enumerate(entries) if isinstance(e, bd.Balance))
        pad_idx = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_idx, "Assets:Cash"
        )
        assert pad_idx is not None
        assert isinstance(entries[pad_idx], bd.Pad)
        assert entries[pad_idx].account == "Assets:Cash"

    def test_pad_with_transaction_between_is_still_found(self, tmp_path):
        """Closing the pre-existing read/delete asymmetry: the strict
        ``entries[balance_idx - 1]`` check would miss this. The walk-based
        algorithm finds it because no Balance for Cash sits between.
        """
        entries = self._entries(tmp_path, """
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 * "Payday"
  Assets:Cash      50 USD
  Income:Salary   -50 USD
2020-02-01 balance Assets:Cash  500 USD
""")
        balance_idx = next(i for i, e in enumerate(entries) if isinstance(e, bd.Balance))
        pad_idx = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_idx, "Assets:Cash"
        )
        assert pad_idx is not None
        assert isinstance(entries[pad_idx], bd.Pad)

    def test_pad_consumed_by_earlier_balance_is_not_reused(self, tmp_path):
        """For the second balance, no pad should be returned — the first
        balance already consumed it.
        """
        entries = self._entries(tmp_path, """
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
2020-02-15 * "Payday"
  Assets:Cash      100 USD
  Income:Salary   -100 USD
2020-03-01 balance Assets:Cash  600 USD
""")
        balance_indices = [i for i, e in enumerate(entries) if isinstance(e, bd.Balance)]
        assert len(balance_indices) == 2
        pad_for_first = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_indices[0], "Assets:Cash"
        )
        pad_for_second = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_indices[1], "Assets:Cash"
        )
        assert pad_for_first is not None
        assert pad_for_second is None

    def test_most_recent_of_multiple_pads_is_returned(self, tmp_path):
        entries = self._entries(tmp_path, """
2020-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 pad Assets:Cash Equity:Opening-Balances
2020-02-01 balance Assets:Cash  500 USD
""")
        balance_idx = next(i for i, e in enumerate(entries) if isinstance(e, bd.Balance))
        pad_idx = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_idx, "Assets:Cash"
        )
        # The second pad — same source, but date 2020-01-15.
        from datetime import date as _date
        assert entries[pad_idx].date == _date(2020, 1, 15)

    def test_engine_and_export_agree_on_pad_attribution(self, tmp_path):
        """Spec-level: whatever pad the reader reports must also be the
        pad the engine identifies for delete/update. Otherwise the UI's
        'delete also pad' is a lie.
        """
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary
2020-01-01 open Equity:Opening-Balances

2020-01-01 pad Assets:Cash Equity:Opening-Balances
2020-01-15 * "Payday"
  Assets:Cash      50 USD
  Income:Salary   -50 USD
2020-02-01 balance Assets:Cash  500 USD
"""
        db_path, entries, _ = _export(tmp_path, ledger)
        reader = SqliteReader(
            sqlite_path=db_path,
            ledger_file=tmp_path / "main.beancount",
            exporter=SQLiteExporter(str(db_path)),
        )
        directives = reader.get_balance_directives("Assets:Cash")
        assert directives[0].has_pad is True
        reported_source = directives[0].pad_source_account

        balance_idx = next(i for i, e in enumerate(entries) if isinstance(e, bd.Balance))
        pad_idx = BeancountEngine._find_pad_before_balance_entry(
            entries, balance_idx, "Assets:Cash"
        )
        assert pad_idx is not None
        assert entries[pad_idx].source_account == reported_source
