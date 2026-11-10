"""Numbers Beancount rounds: one load warning per line (BL-0123; owner, 2026-11-10, "Follow Beancount (Recommended)",
`design/inbox/answered/20261110-numbers-beyond-28-digits-divisions.md` option 3; S0098).

Beancount 3.2.0 computes in Python's default decimal context: 28 significant digits, rounding silently. Finzytrack
loads exactly as Beancount does, so its numbers are the ones bean-check and Fava show, and it tells the user where
Beancount rounded one, with a warning in the load's errors (the mirror's `ledger_errors`):

- **A written number** (`written`, key `WRITTEN_KEY`): a number written with more than 28 significant digits (trailing
  zeros count, as Python counts them; leading zeros do not), found by Beancount's own lexer, one warning per line. Its
  negation, and any sum or product with it, keeps only 28 digits: `-12345678901.123456789012345678` loads as
  `-12345678901.12345678901234568`. A positive one is kept whole but rounds in every sum, so it warns too.
- **A filled-in amount** (`filled_in`, key `FILLED_KEY`): a leg Beancount filled in (no cost or price on it) whose
  exact value differs from Beancount's. The exact value is Beancount's own fill (`beancount_port.filled_amount`) of
  Beancount's own booked numbers, computed with 64 digits; only a fill of exactly 28 digits can differ (a rounded
  result has all 28; a coarser quantize gives the exact sum's own quantized value).

**Not warned, on purpose:** a number Beancount divides. A total price (`@@`), a total cost (`{{ }}`), and a filled-in
price, cost or quantity are quotients: `10 / 7` has no exact decimal form, every tool cuts it, and Beancount, bean-check
and Fava cut it at the same 28 digits, so there is nothing to explain. A fill is not judged when a number of the other
postings has exactly 28 digits (the shape of such a cut quotient): `7 TOK @@ 10 USD` with a blank leg fills
`-10.00000000000000000000000000`, where the exact product of Beancount's cut price is `-10.000000000000000000000000003`.
Limits (rare; Beancount's numbers are still shown, only the warning is missing): the product of a written 28-digit
number, a `pad` amount, and a balance assertion's sum. The load's own refusal, when Beancount's arithmetic raises
instead of rounding, is `load_failure`.

Pure except `of_disk`, which reads the files a real load parsed (the rebuild's load reports no bytes); the overlay load
passes the bytes it parsed itself.
"""

from __future__ import annotations

import decimal
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Final, NamedTuple

from ledger_engine.ledger import beancount_port as port

DIGITS: Final = 28
"""Beancount's precision: Python's default context, the one every load runs in (`decimal.DefaultContext.prec`)."""
CHECK_DIGITS: Final = 64
"""The precision the filled-in check computes the exact amount with (the mirror's, README "Exact numbers")."""

WRITTEN_KEY: Final = "Number longer than 28 digits"
FILLED_KEY: Final = "Filled-in amount rounded to 28 digits"

_LONG_RUN: Final = re.compile(rb"[0-9][0-9,.]{%d,}" % DIGITS)
"""A run of more than 28 characters of digits, separators and points: every number with more than 28 significant
digits is in one (its digits from the first to the last are), so a file without one needs no lexing."""

_SUM: Final = decimal.Context(
    prec=CHECK_DIGITS, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow, decimal.DivisionByZero]
)
_QUANTIZE: Final = decimal.Context(prec=CHECK_DIGITS, traps=[decimal.InvalidOperation, decimal.Overflow])


class RoundingWarning(NamedTuple):
    """A load warning in Beancount's error shape (`source`, `message`, `entry`): the mirror lists it in
    `ledger_errors`, after Beancount's own errors, with this class's name as its type."""

    source: dict[str, Any]
    message: str
    entry: port.Directive | None


def may_round(data: bytes) -> bool:
    """Whether `data` may hold a number with more than 28 significant digits (a superset: `written` decides)."""
    return _LONG_RUN.search(data) is not None


def _digits(number: Decimal) -> int:
    return len(number.as_tuple().digits) if number.is_finite() else 0


def written(files: Mapping[str, bytes]) -> list[RoundingWarning]:
    """One warning per line holding a written number of more than 28 significant digits, by file name, then line."""
    out: list[RoundingWarning] = []
    for name in sorted(files):
        data = files[name]
        if not may_round(data):
            continue
        lines: dict[int, list[str]] = {}
        for lineno, text, value in port.written_numbers(data):
            if _digits(value) > DIGITS:
                lines.setdefault(lineno, []).append(text)
        for lineno, texts in sorted(lines.items()):
            message = (
                f"{WRITTEN_KEY}: {', '.join(texts)}. Beancount computes with 28 significant digits, so it may round "
                "this number and the amounts computed from it, as bean-check and Fava do."
            )
            out.append(RoundingWarning({"filename": name, "lineno": lineno}, message, None))
    return out


def _numbers(posting: port.Posting) -> list[Decimal]:
    """The units, the per-unit cost and the price of a booked posting: the numbers its weight is made of."""
    numbers: list[object] = [getattr(posting.units, "number", None)]
    if posting.cost is not None:
        numbers.append(getattr(posting.cost, "number", None))  # a booked `Cost` (a `CostSpec` has none: not judged)
    if posting.price is not None:
        numbers.append(getattr(posting.price, "number", None))
    return [n for n in numbers if isinstance(n, Decimal)]


def _exact(others: Sequence[port.Posting], currency: str, options: port.OptionsMap) -> Decimal | None:
    """Beancount's fill of `currency` with 64 digits; None when even that needs more digits."""
    try:
        with decimal.localcontext(_SUM):
            weight = port.fill_weight(others, currency)
        with decimal.localcontext(_QUANTIZE):
            return port.quantize_fill(others, currency, weight, options)
    except decimal.DecimalException:
        return None


def _filled(p: port.Posting) -> tuple[Decimal, str] | None:
    """A leg Beancount filled in, with no cost or price, whose number has all 28 digits: its number and currency."""
    if not port.is_interpolated(p) or p.cost is not None or p.price is not None or p.units is None:
        return None
    number: object = p.units.number
    if not isinstance(number, Decimal) or _digits(number) != DIGITS:
        return None
    return number, p.units.currency


def filled_in(entries: Sequence[port.Directive], options: port.OptionsMap) -> list[RoundingWarning]:
    """One warning per filled-in leg whose exact amount differs from the one Beancount computed, in entry order."""
    out: list[RoundingWarning] = []
    for entry in entries:
        if not isinstance(entry, port.Transaction):
            continue
        legs = [(p, filled) for p in entry.postings if (filled := _filled(p)) is not None]
        if not legs:
            continue
        others = [p for p in entry.postings if not port.is_interpolated(p)]
        numbers = [_numbers(p) for p in others]
        if any(not ns for ns in numbers) or any(_digits(n) == DIGITS for ns in numbers for n in ns):
            continue  # a posting without a number (a failed booking), or a cut quotient feeds the sum
        for leg, (got, cur) in legs:
            exact = _exact(others, cur, options)
            if exact is not None and exact == got:
                continue
            told = (
                f"The exact amount is {format(exact, 'f')} {cur}."
                if exact is not None
                else f"The exact amount has more than {CHECK_DIGITS} significant digits."
            )
            message = (
                f"{FILLED_KEY}: {format(got, 'f')} {cur}. {told} Beancount, bean-check and Fava use the rounded amount."
            )
            meta = leg.meta or {}
            source = {"filename": meta.get("filename", entry.meta.get("filename")), "lineno": meta.get("lineno")}
            out.append(RoundingWarning(source, message, entry))
    return out


def of_disk(entries: Sequence[port.Directive], options: port.OptionsMap) -> list[RoundingWarning]:
    """The warnings of a real load (`beancount_port.real_load`): the files it parsed (`options["include"]`) read again.
    The rebuild reads them before it observes the set again, so a change in between is a moved disk."""
    files: dict[str, bytes] = {}
    for name in options["include"]:
        with open(str(name), "rb") as f:
            data = f.read()
        if may_round(data):
            files[str(name)] = data
    return written(files) + filled_in(entries, options)
