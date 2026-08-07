"""cash_instruments compute — interest attribution + effective yield.

Specification (dev-docs/metadata-conventions.md "Interest attribution",
investment-dashboards.md §5): interest is identified by `income-type: "interest"`
on the income account (metadata, NOT an `Income:Interest` name); each interest
amount is attributed to the instrument that earned it — linked income account →
structural counterpart → reduced/closed leg at maturity → unattributed guardrail;
effective yield = interest ÷ time-weighted average balance, annualized; graceful
with no metadata.
"""

import asyncio
from pathlib import Path

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.compute.functions.cash_instruments import CashInstrumentsFunction


def _run(tmp_path, ledger_text, to):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(ledger_text)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == [], errors
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    rows = asyncio.run(CashInstrumentsFunction(reader).execute(to=to, currency="*"))
    return {r["account"]: r for r in rows}


_FUND = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Cash USD
"""


def test_compounding_no_link_structural(tmp_path):
    """Interest credited to the CD itself → attributed structurally, no metadata."""
    led = _FUND + """\
2020-01-01 open Assets:CD USD
  asset-class: "cd"
2020-01-01 open Income:Interest:CD USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:CD 1000.00 USD
  Assets:Cash -1000.00 USD
2021-01-01 * "interest"
  Assets:CD 50.00 USD
  Income:Interest:CD -50.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Assets:CD"]["interest_earned"] == "50.00"
    assert abs(float(rows["Assets:CD"]["effective_yield"]) - 0.05) < 1e-3
    assert not any(r["degraded"] for r in rows.values())


def test_linked_paid_out_to_savings(tmp_path):
    """Interest paid OUT to savings, but the FD declares its interest_account →
    attributed to the FD via the link (the case the link exists for)."""
    led = _FUND + """\
2020-01-01 open Assets:Savings USD
2020-01-01 open Assets:FD USD
  asset-class: "cd"
  interest_account: "Income:Interest:FD"
2020-01-01 open Income:Interest:FD USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:FD 1000.00 USD
  Assets:Cash -1000.00 USD
2021-01-01 * "interest paid to savings"
  Assets:Savings 50.00 USD
  Income:Interest:FD -50.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Assets:FD"]["interest_earned"] == "50.00"
    assert abs(float(rows["Assets:FD"]["effective_yield"]) - 0.05) < 1e-3
    # Savings merely received the cash — it earned nothing.
    assert rows["Assets:Savings"]["interest_earned"] is None
    assert not any(r["degraded"] for r in rows.values())


def test_maturity_into_tagged_savings_reduced_leg(tmp_path):
    """FD matures into a savings account that is ALSO tagged an investment; the
    interest attributes to the FD (the reduced/closed leg), not the receiver."""
    led = _FUND + """\
2020-01-01 open Assets:Savings USD
  asset-class: "savings"
2020-01-01 open Assets:FD USD
  asset-class: "cd"
2020-01-01 open Income:Interest:FD USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:FD 1000.00 USD
  Assets:Cash -1000.00 USD
2021-01-01 * "FD matured"
  Assets:Savings 1050.00 USD
  Assets:FD -1000.00 USD
  Income:Interest:FD -50.00 USD
2021-01-01 close Assets:FD
"""
    rows = _run(tmp_path, led, "2021-06-01")
    assert rows["Assets:FD"]["interest_earned"] == "50.00"
    # Closed → balance 0, but the row (and its realized yield) still surfaces.
    assert rows["Assets:FD"]["balance"] == "0"
    assert abs(float(rows["Assets:FD"]["effective_yield"]) - 0.05) < 1e-3
    assert rows["Assets:Savings"]["interest_earned"] is None
    assert not any(r["degraded"] for r in rows.values())


def test_paid_out_no_link_unattributed(tmp_path):
    """Interest paid to a non-investment account with no link → unattributed
    (surfaced, never dropped onto the receiving account); graceful degradation."""
    led = _FUND + """\
2020-01-01 open Assets:Checking USD
2020-01-01 open Assets:FD USD
  asset-class: "cd"
2020-01-01 open Income:Interest:FD USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:FD 1000.00 USD
  Assets:Cash -1000.00 USD
2021-01-01 * "interest paid to checking, no link"
  Assets:Checking 50.00 USD
  Income:Interest:FD -50.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Assets:FD"]["interest_earned"] is None          # not misattributed
    assert rows["Assets:FD"]["effective_yield"] is None          # graceful blank
    # Unattributed interest surfaces keyed by the income account that booked it, and
    # drills into those postings (naming where an interest_account link would fix it).
    unatt = rows["Income:Interest:FD"]
    assert unatt["interest_earned"] == "50.00"
    assert unatt["degraded"] is True
    assert unatt["filter_account"] == "Income:Interest:FD"
    assert rows["Assets:FD"]["filter_account"] == "Assets:FD"   # real rows link too
    assert rows["Assets:Checking"]["interest_earned"] is None


def test_unattributed_interest_split_per_income_account(tmp_path):
    """Unattributed interest is keyed by the income account that booked it, so two
    paid-out CDs with their own income accounts surface as two drillable rows — not
    one lumped total — each pointing at where a link would fix the gap."""
    led = _FUND + """\
2020-01-01 open Assets:Checking USD
2020-01-01 open Assets:FD1 USD
  asset-class: "cd"
2020-01-01 open Assets:FD2 USD
  asset-class: "cd"
2020-01-01 open Income:Interest:FD1 USD
  income-type: "interest"
2020-01-01 open Income:Interest:FD2 USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:FD1 1000.00 USD
  Assets:FD2 2000.00 USD
  Assets:Cash -3000.00 USD
2021-01-01 * "FD1 interest paid out, no link"
  Assets:Checking 40.00 USD
  Income:Interest:FD1 -40.00 USD
2021-01-01 * "FD2 interest paid out, no link"
  Assets:Checking 90.00 USD
  Income:Interest:FD2 -90.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Income:Interest:FD1"]["interest_earned"] == "40.00"
    assert rows["Income:Interest:FD1"]["filter_account"] == "Income:Interest:FD1"
    assert rows["Income:Interest:FD2"]["interest_earned"] == "90.00"
    assert rows["Income:Interest:FD2"]["degraded"] is True


def test_shared_income_account_compounding_splits_per_cd(tmp_path):
    """A shared Income:Interest:TermDeposits still attributes per-CD when interest is
    compounded (credited to each CD) — the counterpart identifies it, no migration."""
    led = _FUND + """\
2020-01-01 open Assets:CD1 USD
  asset-class: "cd"
2020-01-01 open Assets:CD2 USD
  asset-class: "cd"
2020-01-01 open Income:Interest:TermDeposits USD
  income-type: "interest"
2020-01-01 * "fund"
  Assets:CD1 1000.00 USD
  Assets:CD2 2000.00 USD
  Assets:Cash -3000.00 USD
2021-01-01 * "CD1 interest"
  Assets:CD1 40.00 USD
  Income:Interest:TermDeposits -40.00 USD
2021-01-01 * "CD2 interest"
  Assets:CD2 90.00 USD
  Income:Interest:TermDeposits -90.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Assets:CD1"]["interest_earned"] == "40.00"
    assert rows["Assets:CD2"]["interest_earned"] == "90.00"
    assert not any(r["degraded"] for r in rows.values())


def test_untagged_income_account_is_not_interest(tmp_path):
    """Interest is identified by income-type metadata, NOT the account name. An
    income account named `Income:Interest:CD` but left untagged is invisible to
    attribution — no interest earned, no yield, and nothing goes to Unattributed
    (it isn't interest at all). One tag on the account's open turns it on."""
    led = _FUND + """\
2020-01-01 open Assets:CD USD
  asset-class: "cd"
2020-01-01 open Income:Interest:CD USD
2020-01-01 * "fund"
  Assets:CD 1000.00 USD
  Assets:Cash -1000.00 USD
2021-01-01 * "interest, but income account untagged"
  Assets:CD 50.00 USD
  Income:Interest:CD -50.00 USD
"""
    rows = _run(tmp_path, led, "2021-01-01")
    assert rows["Assets:CD"]["interest_earned"] is None    # name doesn't count
    assert rows["Assets:CD"]["effective_yield"] is None
    assert not any(r["degraded"] for r in rows.values())   # not interest → not a gap
