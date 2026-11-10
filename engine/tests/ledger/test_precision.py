"""Numbers Beancount rounds: the load follows Beancount and warns on each line (BL-0123; owner, 2026-11-10, "Follow
Beancount (Recommended)", `design/inbox/answered/20261110-numbers-beyond-28-digits-divisions.md` option 3; S0098).

Beancount 3.2.0 computes in Python's default decimal context, 28 significant digits. The load keeps exactly
Beancount's numbers (the same as bean-check and Fava) and adds one warning per line where Beancount rounded one:
a number written with more than 28 significant digits, and an amount Beancount filled in whose exact value differs
from the one it computed. A number Beancount divides (`@@`, `{{ }}`, a filled-in price, cost or quantity) is cut in
every tool and never warns. Every case is judged twice, through the overlay load (the open, the copy) and through
Beancount's own load with `precision.of_disk` (the rebuild), and both must say the same.

The must-not-pass cases are written the ways numbers really appear in a ledger: signs, thousands separators,
trailing and leading zeros, expressions, digits in strings, comments, dates and account names, prices, costs,
balance tolerances, metadata.
"""

from __future__ import annotations

import decimal
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import precision
from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP

pytestmark = [pytest.mark.inv("INV-02"), pytest.mark.inv("INV-22")]

OPEN = (
    "2020-01-01 open Assets:Wallet\n2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n"
    "2020-01-01 open Equity:Opening\n2020-01-01 open Expenses:Fees\n"
)
FIRST = OPEN.count("\n") + 1  # the line the first transaction starts on

SHIB = "12345678901.123456789012345678"  # an 18-decimal token: 29 significant digits
WRITTEN = precision.WRITTEN_KEY
FILLED = precision.FILLED_KEY


def loaded(tmp_path: Path, body: str, name: str = "main.beancount") -> Loaded:
    root = tmp_path / name
    root.write_text(OPEN + body, encoding="utf-8")
    out = load(str(root), {}, DESKTOP)
    assert isinstance(out, Loaded), out
    return out


def warned(tmp_path: Path, body: str) -> list[tuple[int, str]]:
    """(line, key) of each warning, after checking that the overlay load and Beancount's own load (the rebuild's
    path, `precision.of_disk`) give the same warnings, and that no warning is among Beancount's errors."""
    got = loaded(tmp_path, body)
    entries, _, options = port.real_load(str(tmp_path / "main.beancount"))
    seen = [(w.source, w.message) for w in got.warnings]
    assert [(w.source, w.message) for w in precision.of_disk(entries, options)] == seen
    assert not any(isinstance(e, precision.RoundingWarning) for e in got.errors)
    out: list[tuple[int, str]] = []
    for w in got.warnings:
        assert isinstance(w, precision.RoundingWarning)
        assert w.source["filename"] == str(tmp_path / "main.beancount")
        key = WRITTEN if w.message.startswith(WRITTEN) else FILLED if w.message.startswith(FILLED) else w.message
        out.append((int(w.source["lineno"]), key))
    return out


def txn(*postings: str) -> str:
    return '2021-01-01 * "x"\n' + "".join(f"  {p}\n" for p in postings)


# ---- the context the load runs in ------------------------------------------------------------------------------------


def test_the_load_runs_in_beancounts_28_digit_context() -> None:
    assert precision.DIGITS == decimal.DefaultContext.prec == decimal.getcontext().prec == 28


def test_the_load_leaves_the_context_as_it_found_it(tmp_path: Path) -> None:
    before = decimal.getcontext().copy()
    loaded(tmp_path, txn(f"Assets:Wallet  {SHIB} SHIB", "Equity:Opening  -0.5 SHIB", "Assets:Bank  0.25 USD @ 2 SHIB"))
    after = decimal.getcontext()
    assert (after.prec, after.rounding, after.traps, after.Emax) == (
        before.prec,
        before.rounding,
        before.traps,
        before.Emax,
    )


# ---- written numbers -------------------------------------------------------------------------------------------------


def test_a_written_negative_number_is_rounded_as_beancount_rounds_it_and_warned(tmp_path: Path) -> None:
    body = txn(f"Assets:Wallet  {SHIB} SHIB", f"Equity:Opening  -{SHIB} SHIB")
    assert warned(tmp_path, body) == [(FIRST + 1, WRITTEN), (FIRST + 2, WRITTEN)]
    got = loaded(tmp_path, body)
    legs = [
        getattr(p.units, "number", None) for e in got.entries if isinstance(e, port.Transaction) for p in e.postings
    ]
    assert legs == [Decimal(SHIB), Decimal("-12345678901.12345678901234568")]  # Beancount's own value, kept
    entries, _, _ = port.real_load(str(tmp_path / "main.beancount"))
    assert legs == [
        getattr(p.units, "number", None) for e in entries if isinstance(e, port.Transaction) for p in e.postings
    ]
    message = got.warnings[1].message
    assert f"-{SHIB}" in message  # the number as written, so the user can find it


@pytest.mark.parametrize(
    "posting",
    [
        "Assets:Wallet  12,345,678,901.123456789012345678 SHIB",  # thousands separators
        "Assets:Wallet  1.0000000000000000000000000000 SHIB",  # 29 digits, all but one trailing zeros
        "Assets:Wallet  12345678901234567890123456789 SHIB",  # a whole number of 29 digits
        f"Assets:Wallet  ({SHIB} + 1) SHIB",  # inside an expression
        f"Assets:Wallet  1 SHIB @ {SHIB} USD",  # a price
        f"Assets:Wallet  1 SHIB {{{SHIB} USD}}",  # a cost
    ],
)
def test_every_written_form_of_a_long_number_warns(tmp_path: Path, posting: str) -> None:
    # no leg left blank: Beancount could not fill one in for such a number (`test_load_failure.py`)
    assert warned(tmp_path, txn(posting, "Equity:Opening  -1 SHIB")) == [(FIRST + 1, WRITTEN)]


def test_long_numbers_outside_transactions_warn(tmp_path: Path) -> None:
    body = (
        f"2021-01-02 balance Assets:Wallet  {SHIB} SHIB\n"
        f"2021-01-03 price SHIB  {SHIB} USD\n"
        f'2021-01-04 custom "budget" Assets:Wallet {SHIB} SHIB\n'
    )
    assert warned(tmp_path, body) == [(FIRST, WRITTEN), (FIRST + 1, WRITTEN), (FIRST + 2, WRITTEN)]


def test_two_long_numbers_on_one_line_are_one_warning(tmp_path: Path) -> None:
    body = txn(f"Assets:Wallet  {SHIB} SHIB @ {SHIB} USD", "Equity:Opening  -1 USD")
    assert warned(tmp_path, body) == [(FIRST + 1, WRITTEN)]


def test_an_included_file_is_warned_with_its_own_name(tmp_path: Path) -> None:
    (tmp_path / "inc.beancount").write_text(txn(f"Assets:Wallet  {SHIB} SHIB", "Equity:Opening  -1 SHIB"))
    root = tmp_path / "main.beancount"
    root.write_text(OPEN + 'include "inc.beancount"\n', encoding="utf-8")
    out = load(str(root), {}, DESKTOP)
    assert isinstance(out, Loaded)
    assert [(w.source["filename"], w.source["lineno"]) for w in out.warnings] == [(str(tmp_path / "inc.beancount"), 2)]


@pytest.mark.parametrize(
    "body",
    [
        # 28 significant digits, the most Beancount keeps: never rounded
        txn("Assets:Wallet  1234567890.123456789012345678 SHIB", "Equity:Opening  -1234567890.123456789012345678 SHIB"),
        txn("Assets:Wallet  1234567890123456789012345678 SHIB", "Equity:Opening"),
        # long digit runs that are no number
        '2021-01-01 * "Order 12345678901234567890123456789012" "ref 0000000000000000000000000000001"\n'
        "  Assets:Bank  1.00 USD\n  Equity:Opening\n",
        "; 12345678901234567890123456789012345 in a comment\n",
        "2020-01-01 open Assets:Acct12345678901234567890123456789\n",
        '2021-01-01 note Assets:Bank "12345678901234567890123456789012"\n',
        txn("Assets:Bank  1.00 USD", "Equity:Opening", '  memo: "12345678901234567890123456789012"'),
        # leading zeros are not significant
        txn("Assets:Wallet  0.0000000000000000000000000000000001 SHIB", "Equity:Opening"),
        txn("Assets:Wallet  -000000000000000000000000000000012.5 SHIB", "Equity:Opening"),
        # everyday numbers, prices, costs, tolerances and metadata
        txn("Assets:Broker  10 VTI {123.45 USD}", "Assets:Bank  -1234.50 USD"),
        txn("Assets:Broker  10 VTI @ 123.45 USD", "Assets:Bank"),
        "2021-01-02 balance Assets:Bank  0.00 ~ 0.01 USD\n",
        "2021-01-02 price VTI  123.456789 USD\n",
        txn("Assets:Bank  1.00 USD", "Equity:Opening", "  rate: 1.234567890123456789012345678"),
    ],
)
def test_numbers_beancount_does_not_round_never_warn(tmp_path: Path, body: str) -> None:
    assert warned(tmp_path, body) == []


# ---- amounts Beancount filled in -------------------------------------------------------------------------------------


def test_a_filled_in_amount_beancount_rounded_warns_with_the_exact_amount(tmp_path: Path) -> None:
    # 23 digits x 9 digits: the weight needs 32 digits; Beancount keeps 28 (USD has no tolerance here: nothing to
    # quantize to), so the filled-in leg is cut.
    body = txn("Assets:Broker  12345.123456789012345678 ETH @ 1234.56789 USD", "Assets:Bank")
    assert warned(tmp_path, body) == [(FIRST + 2, FILLED)]
    message = loaded(tmp_path, body).warnings[0].message
    assert "-15240893.01783751714678763908 USD" in message  # what Beancount filled in
    assert "-15240893.01783751714678763907942 USD" in message  # the exact amount


def test_a_filled_in_amount_that_needs_more_than_64_digits_still_warns(tmp_path: Path) -> None:
    # Two weights far apart: about 1.5E+52 with no decimals, and about 1.2E-28 with 54 decimals. Their exact sum has
    # more digits than the 64 the check computes with. No USD amount is written, so nothing is quantized.
    body = txn(
        "Assets:Broker  123456789012345678901234567 ETH @ 123456789012345678901234567 USD",
        "Assets:Wallet  0.123456789012345678901234567 SHIB @ 0.000000000000000000000000001 USD",
        "Assets:Bank",
    )
    assert warned(tmp_path, body) == [(FIRST + 3, FILLED)]
    assert "more than 64" in loaded(tmp_path, body).warnings[0].message


@pytest.mark.parametrize(
    "body",
    [
        # Divisions: Beancount cuts a quotient at 28 digits, as every tool must; the filled-in leg that sums it is not
        # a rounding to warn about. A total price, integer and decimal, uneven and even; a total cost.
        txn("Assets:Broker  7 TOK @@ 10 USD", "Assets:Bank"),
        txn("Assets:Broker  7 TOK @@ 10.00 USD", "Assets:Bank"),
        txn("Assets:Broker  3 TOK @@ 10 USD", "Assets:Bank"),
        txn("Assets:Broker  8 TOK @@ 10 USD", "Assets:Bank"),
        txn("Assets:Broker  7 VTI {{100 USD}}", "Assets:Bank"),
        txn("Assets:Broker  3 VTI {{10.00 USD}}", "Assets:Bank"),
        # a filled-in quantity, cost or price is itself a quotient
        txn("Assets:Broker  TOK @ 3 USD", "Assets:Bank  -10.00 USD"),
        txn("Assets:Broker  7 TOK @ USD", "Assets:Bank  -10.00 USD"),
        # Beancount quantizes the filled-in leg to the currency's tolerance: the exact sum gives the same amount
        txn("Assets:Broker  12345.123456789012345678 ETH @ 1234.56789 USD", "Assets:Bank  -1.00 USD", "Equity:Opening"),
        # a sum of 28 and 1 digits: 1E+28 exactly, which Beancount keeps (another scale, the same number)
        txn("Assets:Wallet  9999999999999999999999999999 X", "Assets:Bank  1 X", "Equity:Opening"),
        # everyday filled-in legs
        txn("Expenses:Fees  12.34 USD", "Assets:Bank  0.66 USD", "Equity:Opening"),
        txn("Expenses:Fees  12.34 USD", "Expenses:Fees  1.5 EUR", "Equity:Opening"),
    ],
)
def test_filled_in_amounts_beancount_did_not_round_never_warn(tmp_path: Path, body: str) -> None:
    assert warned(tmp_path, body) == []


def test_the_fill_the_check_reproduces_is_beancounts_own(tmp_path: Path) -> None:
    """INV-02: `port.filled_amount` (residual, then quantize, as Beancount's `interpolate_group` fills a leg without
    cost or price), run in Beancount's own context, gives exactly the number Beancount filled in."""
    shapes = [
        txn("Expenses:Fees  12.34 USD", "Assets:Bank  0.66 USD", "Equity:Opening"),
        txn("Expenses:Fees  12.34 USD", "Expenses:Fees  1.5 EUR", "Equity:Opening"),
        txn("Assets:Broker  12345.123456789012345678 ETH @ 1234.56789 USD", "Assets:Bank"),
        txn("Assets:Broker  12345.123456789012345678 ETH @ 1234.56789 USD", "Assets:Bank  -1.00 USD", "Equity:Opening"),
        txn("Assets:Broker  7 TOK @@ 10 USD", "Assets:Bank"),
        txn("Assets:Broker  7 VTI {{100 USD}}", "Assets:Bank"),
        txn("Assets:Broker  10 VTI {1.005 USD}", "Assets:Bank  -0.05 USD", "Equity:Opening"),
    ]
    checked = 0
    for i, body in enumerate(shapes):
        got = loaded(tmp_path, body, f"m{i}.beancount")
        for entry in got.entries:
            if not isinstance(entry, port.Transaction):
                continue
            others = [p for p in entry.postings if not port.is_interpolated(p)]
            for p in entry.postings:
                if port.is_interpolated(p) and p.cost is None and p.price is None:
                    assert p.units is not None
                    filled = port.filled_amount(others, p.units.currency, got.options)
                    assert (filled, str(filled)) == (p.units.number, str(p.units.number)), body
                    checked += 1
    assert checked == 8
