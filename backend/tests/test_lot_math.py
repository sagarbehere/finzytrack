"""lot_math — FIFO consumption and tax-neutral lot conversion (exact Decimal)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.services.lot_math import InsufficientUnits, LotPiece, consume_fifo, convert_lots, unit_cost


def _lots():
    return [LotPiece(D("10"), D("12"), "INR", date(2020, 2, 1)), LotPiece(D("10"), D("10"), "INR", date(2020, 1, 1))]


def test_fifo_takes_the_oldest_lot_first_and_splits_the_last():
    lots = _lots()
    taken = consume_fifo(lots, D("15"))
    assert taken == [LotPiece(D("10"), D("10"), "INR", date(2020, 1, 1)), LotPiece(D("5"), D("12"), "INR", date(2020, 2, 1))]
    assert lots == [LotPiece(D("5"), D("12"), "INR", date(2020, 2, 1))]


def test_fifo_refuses_and_leaves_lots_untouched():
    lots = _lots()
    with pytest.raises(InsufficientUnits):
        consume_fifo(lots, D("20.001"))
    assert sum(lot.units for lot in lots) == D("20")


def test_conversion_keeps_dates_total_cost_and_exact_units():
    pieces = [LotPiece(D("1.001"), D("10"), "INR", date(2020, 1, 1)), LotPiece(D("2.002"), D("11"), "INR", date(2020, 2, 1))]
    out = convert_lots(pieces, D("1.000"), D("0.001"))
    assert sum(p.units for p in out) == D("1.000")
    assert [p.date for p in out] == [date(2020, 1, 1), date(2020, 2, 1)]
    basis_before = sum(p.units * p.cost for p in pieces)
    basis_after = sum(p.units * p.cost for p in out)
    assert abs(basis_after - basis_before) < D("0.0000001")


def test_conversion_folds_a_lot_that_rounds_to_nothing_into_the_rest():
    pieces = [LotPiece(D("0.010"), D("10"), "INR", date(2020, 1, 1)), LotPiece(D("100.000"), D("10"), "INR", date(2020, 2, 1))]
    out = convert_lots(pieces, D("1.000"), D("0.001"))
    assert [p.date for p in out] == [date(2020, 2, 1)]
    assert abs(out[0].units * out[0].cost - D("1000.10")) < D("0.0000001")


def test_unit_cost_has_ten_places():
    assert unit_cost(D("99995.00"), D("943.972")) == D("105.9300487726")
