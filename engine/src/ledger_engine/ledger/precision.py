"""Numbers Beancount rounds: one load warning per line (BL-0123; owner, 2026-11-10, "Follow Beancount (Recommended)",
`design/inbox/answered/20261110-numbers-beyond-28-digits-divisions.md` option 3; S0098).

Beancount 3.2.0 computes in Python's default decimal context: 28 significant digits, rounding silently. Finzytrack
loads exactly as Beancount does, so its numbers are the ones bean-check and Fava show, and it tells the user where
Beancount rounded one, with a warning in the mirror's `ledger_errors` (never one of Beancount's errors):

- **A written number** (`written`, key `WRITTEN_KEY`): a number written with more than 28 significant digits, found by
  Beancount's own lexer, one warning per line. Significant as in its value: trailing zeros do not count
  (`1.0000000000000000000000000000` is the number 1, which Beancount never changes; only its scale), leading zeros do
  not either. The warning says what Beancount uses: a negative number is cut to 28 digits
  (`-12345678901.123456789012345678` is used as `-12345678901.12345678901234568`); a positive one is kept whole, but
  every sum with it is cut; one inside an expression is computed with 28 digits.
- **A filled-in amount** (`filled_in`, key `FILLED_KEY`): a leg Beancount filled in (no cost or price on it) whose
  number differs from the exact one. The exact number is Beancount's own fill (`beancount_port.filled_amount`: the
  negated residual, then the quantize to the transaction's tolerance) of Beancount's own booked numbers, computed with
  64 digits and quantized the same way, so a cut before the quantize shows too (`1234567890123456789012345.67 USD`,
  `0.0049999 USD` and a blank leg: Beancount fills `-…45.68`, exactly it is `-…45.67`). Only a transaction whose
  weights may need more than 28 digits is computed again (`_may_round`, a bound from the numbers' digits).

**Not warned, on purpose: a number Beancount cut by dividing.** A total price (`@@`), a total cost (`{{ }}`), and a
filled-in price, cost or quantity are quotients: `10 / 7` has no exact decimal form, every tool cuts it, and Beancount,
bean-check and Fava cut it at the same 28 digits. A filled-in leg is not judged when a weight it sums holds such a cut
number: one with all 28 digits that is not written on its posting's own line (the lexer says what is), such as the
price of `7 TOK @@ 10 USD` or the cost of a lot bought at `{{100 USD}}` and sold with `{}`. Summing it, Beancount fills
`-10.00000000000000000000000000` where the exact product of its cut price is `-10.000000000000000000000000003`: noise,
since the user wrote 10. A written 28-digit number (an 18-decimal token: `-1234567890.123456789012345678 SHIB`) is
judged like any other. Limits (rare; Beancount's numbers are still shown, only the warning is missing): a `pad`
amount, a balance assertion's sum, and an expression whose result has all 28 digits.

Pure but for `of_disk`, which reads the files a real load parsed (the rebuild's load reports no bytes); the overlay load
passes the bytes it parsed itself. The load's refusal, when Beancount's arithmetic raises instead of rounding, is
`load_failure`.
"""

from __future__ import annotations

import decimal
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Final, NamedTuple

from ledger_engine.ledger import beancount_port as port

DIGITS: Final = 28
"""Beancount's precision: Python's default context (`beancount_port.BEANCOUNT_CONTEXT`), the one every load runs in."""
CHECK_DIGITS: Final = 64
"""The precision the filled-in check computes the exact amount with (the mirror's, README "Exact numbers")."""

WRITTEN_KEY: Final = "Number longer than 28 digits"
FILLED_KEY: Final = "Filled-in amount rounded to 28 digits"

_LONG_RUN: Final = re.compile(rb"[0-9][0-9,.]{%d,}" % DIGITS)
"""A run of more than 28 characters of digits, separators and points: every number with more than 28 significant
digits is in one (its digits from the first to the last are), so a file without one needs no lexing for `written`."""

_SUM: Final = decimal.Context(
    prec=CHECK_DIGITS, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow, decimal.DivisionByZero]
)
_QUANTIZE: Final = decimal.Context(prec=CHECK_DIGITS, traps=[decimal.InvalidOperation, decimal.Overflow])
_NORMAL: Final = decimal.Context(prec=10_000)
"""Wide enough to normalize a written number without cutting it below 28 digits (normalizing rounds to the context)."""


class RoundingWarning(NamedTuple):
    """A load warning in Beancount's error shape (`source`, `message`, `entry`): the mirror lists it in
    `ledger_errors`, after Beancount's own errors, with this class's name as its type."""

    source: dict[str, Any]
    message: str
    entry: port.Directive | None


def _digits(number: Decimal) -> int:
    return len(number.as_tuple().digits) if number.is_finite() else 0


def _significant(number: Decimal) -> int:
    """Digits of the value, trailing zeros not counted (`1.000` has 1)."""
    return _digits(number.normalize(_NORMAL)) if number.is_finite() else 0


class _Lexed:
    """Each file's written numbers by line, lexed once, when first asked (Beancount's own lexer)."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self._files = files
        self._lines: dict[str, dict[int, list[port.WrittenNumber]]] = {}

    def lines(self, name: str) -> dict[int, list[port.WrittenNumber]]:
        known = self._lines.get(name)
        if known is not None:
            return known
        got: dict[int, list[port.WrittenNumber]] = {}
        data = self._files.get(name)
        for number in port.written_numbers(data) if data is not None else ():
            got.setdefault(number.lineno, []).append(number)
        self._lines[name] = got
        return got

    def on_line(self, meta: Mapping[str, Any] | None, number: Decimal) -> bool:
        """Whether `number` is written, as a number by itself, on the line `meta` names (sign aside)."""
        if not meta:
            return False
        name, lineno = meta.get("filename"), meta.get("lineno")
        if not isinstance(name, str) or not isinstance(lineno, int):
            return False
        target = number.copy_abs()
        return any(w.value.copy_abs() == target for w in self.lines(name).get(lineno, ()))


def _part(w: port.WrittenNumber) -> str:
    if not w.alone:
        return f"{w.text}, in an expression Beancount computes with 28 digits"
    if w.value.is_signed():
        with decimal.localcontext(port.BEANCOUNT_CONTEXT):
            used = -w.value.copy_abs()  # Beancount's parser negates the written magnitude in its context
        return f"{w.text} as written, used as {format(used, 'f')}"
    return f"{w.text}, kept as written, but every sum with it is cut to 28 digits"


def written(files: Mapping[str, bytes], lexed: _Lexed | None = None) -> list[RoundingWarning]:
    """One warning per line holding a written number of more than 28 significant digits, by file name, then line."""
    lexed = lexed or _Lexed(files)
    out: list[RoundingWarning] = []
    for name in sorted(files):
        if _LONG_RUN.search(files[name]) is None:
            continue
        for lineno, numbers in sorted(lexed.lines(name).items()):
            long = [w for w in numbers if _significant(w.value) > DIGITS]
            if not long:
                continue
            message = (
                f"{WRITTEN_KEY}: {'; '.join(_part(w) for w in long)}. Beancount computes with 28 significant digits, "
                "as bean-check and Fava do."
            )
            out.append(RoundingWarning({"filename": name, "lineno": lineno}, message, None))
    return out


def _weight_numbers(posting: port.Posting) -> list[Decimal] | None:
    """The numbers a booked posting's weight is made of: its units, and its per-unit cost (or else its price); None when
    one is not a number (a failed booking)."""
    units: object = getattr(posting.units, "number", None)
    if not isinstance(units, Decimal):
        return None
    if posting.cost is not None:
        per: object = getattr(posting.cost, "number", None)  # a booked `Cost` (a `CostSpec` has none)
    elif posting.price is not None:
        per = getattr(posting.price, "number", None)
    else:
        return [units]
    return [units, per] if isinstance(per, Decimal) else None


def _may_round(weights: Sequence[list[Decimal]]) -> bool:
    """Whether summing these weights may need more than 28 digits (a bound, never too small): from the highest digit a
    weight can have (a product's is at most one above the sum of its factors') and the count of terms, down to the
    lowest digit any weight has."""
    high: int | None = None
    low: int | None = None
    for numbers in weights:
        if not all(n.is_finite() for n in numbers):
            return True
        top = sum(n.adjusted() for n in numbers) + len(numbers) - 1
        bottom = sum(int(n.as_tuple().exponent) for n in numbers)
        high = top if high is None else max(high, top)
        low = bottom if low is None else min(low, bottom)
    if high is None or low is None:
        return False
    return high + len(weights).bit_length() - low + 1 > DIGITS


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
    """A leg Beancount filled in, with no cost or price: its number and currency."""
    if not port.is_interpolated(p) or p.cost is not None or p.price is not None or p.units is None:
        return None
    number: object = p.units.number
    return (number, p.units.currency) if isinstance(number, Decimal) else None


def filled_in(
    entries: Sequence[port.Directive], options: port.OptionsMap, lexed: _Lexed | None = None
) -> list[RoundingWarning]:
    """One warning per filled-in leg whose exact amount differs from the one Beancount computed, in entry order."""
    lexed = lexed or _Lexed({})
    out: list[RoundingWarning] = []
    for entry in entries:
        if not isinstance(entry, port.Transaction):
            continue
        legs = [(p, filled) for p in entry.postings if (filled := _filled(p)) is not None]
        if not legs:
            continue
        others = [p for p in entry.postings if not port.is_interpolated(p)]
        weights = [_weight_numbers(p) for p in others]
        if any(w is None for w in weights):
            continue  # a posting without a number: a booking Beancount already reported
        known = [w for w in weights if w is not None]
        if not _may_round(known):
            continue  # every sum and product fits in 28 digits: Beancount's fill is exact
        if any(
            _digits(n) == DIGITS and not lexed.on_line(p.meta, n)
            for p, ws in zip(others, known, strict=True)
            for n in ws
        ):
            continue  # a number Beancount cut by dividing feeds the sum (the module's docstring)
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


def warnings(
    files: Mapping[str, bytes], entries: Sequence[port.Directive], options: port.OptionsMap
) -> list[RoundingWarning]:
    """Every warning of a load: the written numbers of `files` (each parsed file's bytes), then the filled-ins."""
    lexed = _Lexed(files)
    return written(files, lexed) + filled_in(entries, options, lexed)


def of_disk(entries: Sequence[port.Directive], options: port.OptionsMap) -> list[RoundingWarning]:
    """The warnings of a real load (`beancount_port.real_load`): the files it parsed (`options["include"]`) read again.
    The rebuild reads them before it observes the set again, so a change in between is a moved disk; a file gone or
    unreadable meanwhile raises `OSError`, which the rebuild answers as a moved disk too."""
    files: dict[str, bytes] = {}
    for name in options["include"]:
        with open(str(name), "rb") as f:
            files[str(name)] = f.read()
    return warnings(files, entries, options)
