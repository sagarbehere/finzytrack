"""Write-path gate — check-then-write (dev-docs/investment-view.md §6.6).

Specification: before a durable write, the candidate ledger is validated in memory
(booking runs); if the change introduces a **booking** error ("Ambiguous matches",
"Not enough lots to reduce") the write is **refused** and the file is left untouched —
unless the caller acknowledges it. The gate is scoped to booking errors: a failing
balance *assertion* is informational and must still persist. The signal is a cheap
count comparison (booking errors before vs after), never a per-error diff.
"""

import pytest
from pathlib import Path

from beancount.parser import parser
from beancount.core.data import Transaction

from app.core.ledger_manager import LedgerManager
from app.core.backup_manager import BackupManager
from app.core.ledger_initializer import LedgerInitializer
from app.exceptions import APIError
from app import error_codes as ec


_BASE = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Broker
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Gains
2020-01-02 * "buy a"
  Assets:Broker 5 VOO {100 USD}
  Assets:Cash -500 USD
2020-01-03 * "buy b"
  Assets:Broker 5 VOO {110 USD}
  Assets:Cash -550 USD
"""

# A partial reduction with an underspecified cost — matches both lots → ambiguous.
_AMBIGUOUS_SELL = """\
2020-06-01 * "ambiguous sell"
  Assets:Broker -3 VOO {}
  Assets:Cash 600 USD
  Income:Gains
"""

# A balance assertion that will not match — a reported error, but NOT corruption.
_FAILING_ASSERTION = """\
2020-06-01 balance Assets:Cash -9999.99 USD
"""


def _manager(tmp_path, text) -> tuple[LedgerManager, Path]:
    ledger = tmp_path / "main.beancount"
    ledger.write_text(text)
    backup = BackupManager(backup_dir=tmp_path / "backups", retention_count=3)
    init = LedgerInitializer(ledger_file=str(ledger), backup_manager=backup, default_currency="USD")
    mgr = LedgerManager(ledger_file=str(ledger), backup_manager=backup, ledger_initializer=init)
    return mgr, ledger


def _parse_one(snippet: str) -> Transaction:
    entries, _errs, _opts = parser.parse_string(snippet)
    return next(e for e in entries if isinstance(e, Transaction))


def _parse_entry(snippet: str):
    entries, _errs, _opts = parser.parse_string(snippet)
    return entries[0]


def test_gate_blocks_a_booking_error_and_leaves_the_file_untouched(tmp_path):
    mgr, ledger = _manager(tmp_path, _BASE)
    before = ledger.read_text()
    entries, errors, options = mgr._parse_ledger()
    assert errors == []  # clean baseline

    bad = _parse_one(_AMBIGUOUS_SELL)
    with pytest.raises(APIError) as ei:
        mgr._write_and_export(list(entries) + [bad], errors, options)

    assert ei.value.code == ec.LEDGER_VALIDATION_FAILED
    assert ei.value.status_code == 422
    assert ei.value.details.get("introduced_by_change") is True
    # Check-then-write: nothing was persisted.
    assert ledger.read_text() == before


def test_acknowledge_bypasses_the_gate(tmp_path):
    mgr, _ledger = _manager(tmp_path, _BASE)
    entries, errors, options = mgr._parse_ledger()
    bad = _parse_one(_AMBIGUOUS_SELL)
    # With acknowledge=True the gate does not raise (the user chose to proceed).
    mgr._write_and_export(list(entries) + [bad], errors, options, acknowledge=True)


def test_failing_balance_assertion_is_not_blocked(tmp_path):
    """A balance-assertion mismatch is the user's 'does this match?' signal — it is
    reported, not corruption, so the write must still go through."""
    mgr, _ledger = _manager(tmp_path, _BASE)
    entries, errors, options = mgr._parse_ledger()
    assertion = _parse_entry(_FAILING_ASSERTION)
    # Must not raise, even though the candidate load reports a BalanceError.
    mgr._write_and_export(list(entries) + [assertion], errors, options)


def test_already_dirty_baseline_allows_a_non_worsening_write(tmp_path):
    """Baseline already has a booking error; a new write that does not add another
    booking error is allowed (cheap count signal: no increase → not blocked)."""
    mgr, _ledger = _manager(tmp_path, _BASE + _AMBIGUOUS_SELL)
    entries, errors, options = mgr._parse_ledger()
    booking_baseline = [e for e in errors if type(e).__module__.startswith("beancount.parser.booking")]
    assert len(booking_baseline) == 1  # the pre-existing ambiguous sell

    deposit = _parse_one("""\
2020-07-01 * "deposit"
  Assets:Cash 100 USD
  Income:Gains -100 USD
""")
    # No new booking error introduced → allowed despite the dirty baseline.
    mgr._write_and_export(list(entries) + [deposit], errors, options)
