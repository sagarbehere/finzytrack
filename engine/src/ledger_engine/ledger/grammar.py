"""Account and currency grammar, and tolerances, as Beancount decides them (ADR-0024; INV-02; EP-03).

The grammar is Beancount's parser, not `beancount.core.account.is_valid` (which refuses names that load) nor
`amount.CURRENCY_RE` (which accepts codes the parser refuses): a name is an account when a probe ledger that opens it
parses with no error and opens exactly that name, under the ledger's own root names; a code is a currency when it
parses, exactly, both as a `commodity` directive and as the base of a `price` directive. The tolerances are
Beancount's own calls on booked entries, through `beancount_port`. The domain vectors pin all of it
(`engine/tests/ledger/test_vectors.py`). An account token on a line of a file is found by Beancount's lexer
(`account_token`, S0115).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NewType

from ledger_engine.ledger import beancount_port as port

AccountName = NewType("AccountName", str)
Currency = NewType("Currency", str)

PROBE_DATE = "2000-01-01"
ROOT_OPTIONS = ("name_assets", "name_liabilities", "name_equity", "name_income", "name_expenses")


@dataclass(frozen=True, slots=True)
class Invalid:
    """Refused by Beancount's parser; `errors` are its messages (first lines), empty when it parsed the text as
    something other than exactly the name or code."""

    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccountRoots:
    """The five root names of a ledger (`option "name_assets"` and the others)."""

    assets: str
    liabilities: str
    equity: str
    income: str
    expenses: str


# @concept account-roots
def account_roots(options: port.OptionsMap) -> AccountRoots:
    """The root names from a load's options map."""
    return AccountRoots(*(str(options[key]) for key in ROOT_OPTIONS))


def _messages(errors: list[port.BeancountError]) -> tuple[str, ...]:
    return tuple(str(e.message).splitlines()[0].strip() for e in errors)


def _root_options(roots: AccountRoots) -> str:
    values = (roots.assets, roots.liabilities, roots.equity, roots.income, roots.expenses)
    return "".join(f'option "{key}" "{value}"\n' for key, value in zip(ROOT_OPTIONS, values, strict=True))


# @concept account-grammar
def parse_account(text: str, roots: AccountRoots) -> AccountName | Invalid:
    entries, errors, _ = port.parse_string(f"{_root_options(roots)}{PROBE_DATE} open {text}\n".encode())
    opened = [e.account for e in entries if isinstance(e, port.Open)]
    if not errors and opened == [text]:
        return AccountName(text)
    return Invalid(_messages(errors))


@dataclass(frozen=True, slots=True)
class TokenAt:
    """Where one token stands on a line: `line[start:end]`."""

    start: int
    end: int


def _tokens(line: str) -> list[tuple[str, bytes]]:
    return [(t.kind, t.text) for t in port.lex((line + "\n").encode()) if t.kind != "EOL"]


def account_token(line: str, old: str, new: str) -> TokenAt | Invalid:
    """Where `old` stands on `line` (one line, without its line ending) as an account token, as Beancount's lexer reads
    the line, so that writing `new` there changes that one token into the account `new` and nothing else the lexer
    reads (S0115, `replace_token`; ADR-0009: never a regex over the file). Each place `old` is written is tried and the
    line lexed again: a place inside a comment, a string or a longer name changes another token or none, so it is not
    the token. Invalid when `old` is not exactly one account token of the line, `new` equals it, or `new` would not be
    read as one account token there."""
    before = _tokens(line)
    if old == new or [text for kind, text in before if kind == "ACCOUNT"].count(old.encode()) != 1:
        return Invalid(())
    want = [
        ("ACCOUNT", new.encode()) if (kind, text) == ("ACCOUNT", old.encode()) else (kind, text)
        for kind, text in before
    ]
    hits: list[int] = []
    at = line.find(old)
    while at >= 0:
        if _tokens(line[:at] + new + line[at + len(old) :]) == want:
            hits.append(at)
        at = line.find(old, at + 1)
    if len(hits) != 1:
        return Invalid(())
    return TokenAt(hits[0], hits[0] + len(old))


def account_root(account: str) -> str:
    """The first component of an account name, as written (Beancount's `account.root(1, name)`, without the port:
    the separator is the grammar's `:`)."""
    return account.split(":", 1)[0]


# @concept currency-grammar
def parse_currency(text: str) -> Currency | Invalid:
    as_commodity, commodity_errors, _ = port.parse_string(f"{PROBE_DATE} commodity {text}\n".encode())
    as_price, price_errors, _ = port.parse_string(f"{PROBE_DATE} price {text} 1.00 USD\n".encode())
    commodities = [e.currency for e in as_commodity if isinstance(e, port.Commodity)]
    priced = [e.currency for e in as_price if isinstance(e, port.Price)]
    if not commodity_errors and not price_errors and commodities == [text] and priced == [text]:
        return Currency(text)
    return Invalid(_messages(commodity_errors) + _messages(price_errors))


# @concept balance-check
def transaction_balance(transaction: port.Transaction, options: port.OptionsMap) -> port.TransactionCheck:
    """Beancount's balance check of a booked transaction: inferred tolerances, residual, balanced."""
    return port.check_transaction(transaction.postings, options)


@dataclass(frozen=True, slots=True)
class AssertionCheck:
    """A balance assertion against the ledger: Beancount's tolerance for it, the actual balance of the account and
    its children at the start of its day, and actual minus asserted."""

    tolerance: Decimal
    actual: Decimal
    difference: Decimal


def balance_assertion(
    entries: Sequence[port.Directive], assertion: port.Balance, options: port.OptionsMap
) -> AssertionCheck:
    """`entries` is the loaded list that holds `assertion`."""
    actual = port.assertion_balance(entries, assertion)
    asserted = assertion.amount.number
    return AssertionCheck(
        tolerance=port.balance_tolerance(assertion, options),
        actual=actual,
        difference=actual - (Decimal(0) if asserted is None else asserted),
    )
