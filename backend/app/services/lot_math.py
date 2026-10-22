"""Lot arithmetic shared by the investment assembler and statement import.

Exact ``Decimal`` math (dev-docs/money-types.md). A lot is (units, per-unit cost,
cost currency, acquisition date); every reduction names the lot's explicit
``{cost, date}`` so Beancount books it deterministically (investment-view.md §6.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import List, Sequence

# Per-unit cost precision for costs we compute (amount ÷ units, merger carry-over).
# Ten places keeps units × cost within a millionth of a paisa of the true basis, so
# the transaction's 2-decimal cash/zero leg tolerance always absorbs the rest.
COST_QUANTUM = Decimal("0.0000000001")


class InsufficientUnits(Exception):
    def __init__(self, held: Decimal, wanted: Decimal):
        super().__init__(f"only {held} units held, {wanted} needed")
        self.held = held
        self.wanted = wanted


@dataclass(frozen=True)
class LotPiece:
    units: Decimal
    cost: Decimal
    currency: str
    date: date


def unit_cost(amount: Decimal, units: Decimal) -> Decimal:
    """Per-unit cost of buying ``units`` for ``amount``."""
    return (amount / units).quantize(COST_QUANTUM, rounding=ROUND_HALF_EVEN)


def consume_fifo(lots: List[LotPiece], units: Decimal) -> List[LotPiece]:
    """Remove ``units`` from ``lots`` oldest first (Indian mutual fund capital-gains
    rules use FIFO per folio). Mutates ``lots`` in place and returns the consumed
    pieces. Raises ``InsufficientUnits`` (and leaves ``lots`` unchanged) when they
    don't cover the reduction — never guesses a cost."""
    held = sum((lot.units for lot in lots), Decimal("0"))
    if units > held:
        raise InsufficientUnits(held, units)
    lots.sort(key=lambda lot: (lot.date, lot.cost))
    consumed: List[LotPiece] = []
    remaining = units
    while remaining > 0:
        first = lots[0]
        take = first.units if first.units <= remaining else remaining
        consumed.append(LotPiece(take, first.cost, first.currency, first.date))
        if take == first.units:
            lots.pop(0)
        else:
            lots[0] = LotPiece(first.units - take, first.cost, first.currency, first.date)
        remaining -= take
    return consumed


def convert_lots(consumed: Sequence[LotPiece], new_units: Decimal, quantum: Decimal) -> List[LotPiece]:
    """Carry lots into another security for a tax-neutral conversion (scheme merger,
    face-value change): each lot keeps its acquisition date and its total cost; its
    units scale by ``new_units / old_units``, rounded to ``quantum`` with the last lot
    absorbing the rounding so the new units total exactly ``new_units``."""
    old_total = sum((p.units for p in consumed), Decimal("0"))
    if old_total <= 0 or new_units <= 0:
        raise ValueError("a conversion needs units on both sides")
    out: List[LotPiece] = []
    assigned = Decimal("0")
    for i, p in enumerate(consumed):
        if i == len(consumed) - 1:
            units = new_units - assigned
        else:
            units = (p.units * new_units / old_total).quantize(quantum, rounding=ROUND_HALF_EVEN)
        if units <= 0:
            continue  # its share rounds to zero units; its basis is carried below
        assigned += units
        out.append(LotPiece(units, p.units * p.cost, p.currency, p.date))  # cost = basis for now
    if not out:
        raise ValueError("a conversion needs units on both sides")
    # Rounding may leave the total a quantum off (when the last share rounded to zero):
    # settle the difference on the largest piece so the units are exact.
    diff = new_units - sum((p.units for p in out), Decimal("0"))
    if diff:
        big = max(range(len(out)), key=lambda i: out[i].units)
        b = out[big]
        out[big] = LotPiece(b.units + diff, b.cost, b.currency, b.date)
    # Every consumed lot's basis must survive; a skipped (zero-unit) lot's basis goes to the last piece.
    total_basis = sum((p.units * p.cost for p in consumed), Decimal("0"))
    carried = sum((p.cost for p in out), Decimal("0"))
    if out and carried != total_basis:
        last = out[-1]
        out[-1] = LotPiece(last.units, last.cost + (total_basis - carried), last.currency, last.date)
    return [LotPiece(p.units, (p.cost / p.units).quantize(COST_QUANTUM, rounding=ROUND_HALF_EVEN), p.currency, p.date)
            for p in out]
