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
  negated residual, then the quantize to the tolerance it infers) of the postings as written, computed with 64 digits:
  Beancount's booked numbers, but a negative written number it cut to 28 digits put back as written (its cut also
  coarsens the tolerance Beancount infers; S0098 round 2, finance M2). A cut before the quantize shows too
  (`1234567890123456789012345.67 USD`, `0.0049999 USD` and a blank leg: Beancount fills `-…45.68`, exactly it is
  `-…45.67`). Only a transaction whose
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
from os import path
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


class Lexed:
    """Written numbers by file and line, each line lexed alone and once, only when asked (Beancount's own lexer): a
    long digit run's line for `written`, a candidate posting's line for `filled_in`, never a whole file (S0098 round 2,
    finance M1: a whole-file lex cost a third of a 54k load). `lexed` counts the lines lexed. A line lexed alone reads
    as Beancount reads it, but for a line inside a string that spans lines: a long digit run there may warn
    (a narration of several lines holding a number of more than 28 digits; rare, and only advice)."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self._files = files
        self._split: dict[str, list[bytes]] = {}
        self._lines: dict[tuple[str, int], list[port.WrittenNumber]] = {}
        self.lexed = 0

    def _text(self, name: str) -> list[bytes]:
        known = self._split.get(name)
        if known is None:
            data = self._files.get(name)
            known = [] if data is None else data.split(b"\n")  # Beancount counts lines by `\n` only
            self._split[name] = known
        return known

    def line(self, name: str, lineno: int) -> list[port.WrittenNumber]:
        """The numbers written on line `lineno` (from 1) of file `name`, numbered as that line."""
        key = (name, lineno)
        known = self._lines.get(key)
        if known is not None:
            return known
        text = self._text(name)
        got: list[port.WrittenNumber] = []
        if 1 <= lineno <= len(text):
            self.lexed += 1
            got = [port.WrittenNumber(lineno, w.text, w.value, w.alone) for w in port.written_numbers(text[lineno - 1])]
        self._lines[key] = got
        return got

    def long_lines(self, name: str) -> list[int]:
        """The lines of `name` holding a run of more than 28 digits, separators and points (in order, once each)."""
        data = self._files.get(name, b"")
        out: list[int] = []
        lineno, at = 1, 0
        for match in _LONG_RUN.finditer(data):
            lineno += data.count(b"\n", at, match.start())
            at = match.start()
            if not out or out[-1] != lineno:
                out.append(lineno)
        return out

    def as_written(self, meta: Mapping[str, Any] | None, number: Decimal) -> Decimal | None:
        """The number as written on the line `meta` names, when `number` is one written there (sign aside) or
        Beancount's 28-digit negation of one (S0098 round 2, finance M2: `-12345678901.123456789012345678` is stored as
        `-12345678901.12345678901234568`), with `number`'s sign; None when it is written nowhere on that line."""
        if not meta:
            return None
        name, lineno = meta.get("filename"), meta.get("lineno")
        if not isinstance(name, str) or not isinstance(lineno, int):
            return None
        target = number.copy_abs()
        beancount = port.BEANCOUNT_CONTEXT.copy()  # a copy: the shared constant collects no flags
        for w in self.line(name, lineno):
            written = w.value.copy_abs()
            if written == target or beancount.plus(written) == target:
                return written.copy_sign(number)
        return None


def _part(w: port.WrittenNumber) -> str:
    if not w.alone:
        return f"{w.text}, in an expression Beancount computes with 28 digits"
    if w.value.is_signed():
        with decimal.localcontext(port.BEANCOUNT_CONTEXT):
            used = -w.value.copy_abs()  # Beancount's parser negates the written magnitude in its context
        return f"{w.text} as written, used as {format(used, 'f')}"
    return f"{w.text}, kept as written, but every sum with it is cut to 28 digits"


def written(files: Mapping[str, bytes], lexed: Lexed | None = None) -> list[RoundingWarning]:
    """One warning per line holding a written number of more than 28 significant digits, by file name, then line."""
    lexed = lexed or Lexed(files)
    out: list[RoundingWarning] = []
    for name in sorted(files):
        for lineno in lexed.long_lines(name):
            long = [w for w in lexed.line(name, lineno) if _significant(w.value) > DIGITS]
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


def _exact(written: Sequence[port.Posting], currency: str, options: port.OptionsMap) -> Decimal | None:
    """Beancount's fill of `currency` with 64 digits, from the postings as written (the weight and the tolerance it
    infers both: a number Beancount cut also coarsens the tolerance it infers); None when 64 digits are too few."""
    try:
        with decimal.localcontext(_SUM):
            weight = port.fill_weight(written, currency)
        with decimal.localcontext(_QUANTIZE):
            return port.quantize_fill(written, currency, weight, options)
    except decimal.DecimalException:
        return None


def _as_written(p: port.Posting, lexed: Lexed) -> port.Posting:
    """`p` with its units as written, when Beancount stored a negative written number cut to 28 digits."""
    number: object = getattr(p.units, "number", None)
    if not isinstance(number, Decimal) or _digits(number) != DIGITS:
        return p
    written = lexed.as_written(p.meta, number)
    return p if written is None or written == number else port.with_units_number(p, written)


def _filled(p: port.Posting) -> tuple[Decimal, str] | None:
    """A leg Beancount filled in, with no cost or price: its number and currency."""
    if not port.is_interpolated(p) or p.cost is not None or p.price is not None or p.units is None:
        return None
    number: object = p.units.number
    return (number, p.units.currency) if isinstance(number, Decimal) else None


def filled_in(
    entries: Sequence[port.Directive], options: port.OptionsMap, lexed: Lexed | None = None
) -> list[RoundingWarning]:
    """One warning per filled-in leg whose exact amount differs from the one Beancount computed, in entry order."""
    lexed = lexed or Lexed({})
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
            _digits(n) == DIGITS and lexed.as_written(p.meta, n) is None
            for p, ws in zip(others, known, strict=True)
            for n in ws
        ):
            continue  # a number Beancount cut by dividing feeds the sum (the module's docstring)
        exact_others = [_as_written(p, lexed) for p in others]  # a written number Beancount negated at 28 digits
        for leg, (got, cur) in legs:
            exact = _exact(exact_others, cur, options)
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
    files: Mapping[str, bytes],
    entries: Sequence[port.Directive],
    options: port.OptionsMap,
    lexed: Lexed | None = None,
) -> list[RoundingWarning]:
    """Every warning of a load: the written numbers of `files` (each parsed file's bytes), then the filled-ins."""
    lexed = lexed or Lexed(files)
    return written(files, lexed) + filled_in(entries, options, lexed)


def of_disk(
    entries: Sequence[port.Directive], options: port.OptionsMap, parsed: Sequence[str]
) -> list[RoundingWarning]:
    """The warnings of a real load (`beancount_port.real_load`): the files it parsed as ledger text (`parsed`, as the
    overlay's warnings see the files it parsed; never what a plugin added to `options["include"]`), read again. A path
    that is no regular file now holds no number and is skipped (a file gone since the load is a moved disk the
    rebuild's next observation sees). The rebuild reads them before it observes the set again, so a change in between
    is a moved disk; a file it cannot read raises `OSError`, which the rebuild answers as a moved disk too."""
    files: dict[str, bytes] = {}
    for name in parsed:
        if not path.isfile(name):
            continue
        with open(name, "rb") as f:
            files[name] = f.read()
    return warnings(files, entries, options)
