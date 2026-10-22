"""The casparser wrapper (dev-docs/cas-import.md §3): its exceptions become our error
codes, and its ``CASData`` maps onto the neutral ``StatementData`` — without carrying
PAN, email, phone or address. No real CAS is used (personal data)."""

from datetime import date
from decimal import Decimal

import casparser
import pytest
from casparser.exceptions import CASIntegrityError, CASParseError, HeaderParseError, IncorrectPasswordError
from casparser.types import CASData

from app import error_codes as ec
from app.services.statement_import.parsers import cas as cas_parser
from app.services.statement_import.parsers.cas import StatementParseError, _to_statement, is_cas_pdf, parse_cas_pdf


def _cas(folios) -> CASData:
    return CASData.model_validate({
        "statement_period": {"from": "01-Jan-2010", "to": "21-Oct-2026"},
        "folios": folios,
        "investor_info": {"name": "Solo Investor", "email": "x@example.com", "address": "Street 1", "mobile": "+91-55501"},
        "cas_type": "DETAILED",
        "file_type": "CAMS",
        "parse_warnings": ["a warning"],
    })


def _scheme(name, isin, amfi="100", advisor="ARN-1", txns=(), close="10.000"):
    return {
        "scheme": name, "advisor": advisor, "rta_code": "X1", "rta": "CAMS", "type": "EQUITY",
        "isin": isin, "amfi": amfi, "open": "0", "close": close, "close_calculated": close,
        "valuation": {"date": "2026-10-19", "nav": "12.5", "cost": "100", "value": "125"},
        "transactions": list(txns),
    }


def _txn(d, kind, amount, units, desc="Purchase"):
    return {"date": d, "description": desc, "amount": amount, "units": units, "nav": "10", "balance": units, "type": kind}


def test_wrong_password_unrecognized_integrity_and_unreadable_map_to_codes(monkeypatch):
    for exc, code in [
        (IncorrectPasswordError("x"), ec.CAS_WRONG_PASSWORD),
        (CASParseError("x"), ec.CAS_UNRECOGNIZED),
        (HeaderParseError("x"), ec.CAS_UNRECOGNIZED),
        (CASIntegrityError("x"), ec.CAS_INTEGRITY),
        (RuntimeError("x"), ec.CAS_PARSE_FAILED),
    ]:
        def boom(*_a, _e=exc, **_k):
            raise _e
        monkeypatch.setattr(casparser, "read_cas_pdf", boom)
        with pytest.raises(StatementParseError) as info:
            parse_cas_pdf(b"%PDF", "pw")
        assert info.value.code == code


def test_not_a_pdf_is_not_recognised():
    with pytest.raises(StatementParseError) as info:
        parse_cas_pdf(b"hello", "")
    assert info.value.code == ec.CAS_UNRECOGNIZED
    assert is_cas_pdf(b"hello", None) is False


def test_maps_folios_schemes_and_rows_without_personal_data(monkeypatch):
    cas = _cas([
        {"folio": "123 / 45", "amc": "DSP Mutual Fund", "PAN": "AAAAA1111A", "name": "First Holder", "schemes": [
            _scheme("DSP Flexi Cap Fund - Regular Plan - Growth (Non", "INF740K01029",
                    txns=[_txn("2020-01-01", "PURCHASE", "100.00", "10.000"),
                          _txn("2020-01-01", "PURCHASE", "100.00", "10.000")]),
        ]},
        {"folio": "999", "amc": "HDFC Mutual Fund", "PAN": "BBBBB2222B", "name": "Second Holder", "schemes": [
            _scheme("HDFC Liquid Fund - Direct Plan - Growth", None, amfi=None, advisor=None, close="0"),
        ]},
    ])
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *_a, **_k: cas)
    stmt = parse_cas_pdf(b"%PDF", "pw")

    assert (stmt.source, stmt.variant, stmt.issuer) == ("cas", "detailed", "CAMS")
    assert (stmt.period_start, stmt.period_end) == (date(2010, 1, 1), date(2026, 10, 21))
    assert [(i.key, i.name) for i in stmt.investors] == [("I1", "First Holder"), ("I2", "Second Holder")]
    assert [(a.id, a.institution, a.investor) for a in stmt.accounts] == [
        ("123/45", "DSP Mutual Fund", "I1"), ("999", "HDFC Mutual Fund", "I2")]
    assert [s.name for s in stmt.securities] == ["DSP Flexi Cap Fund - Regular Plan - Growth",
                                                 "HDFC Liquid Fund - Direct Plan - Growth"]
    assert stmt.securities[1].key == "CAMS:X1"  # no ISIN → RTA code
    assert [p.advisor for p in stmt.positions] == ["ARN-1", None]
    assert len({r.id for r in stmt.rows}) == 2  # identical rows still get distinct ids
    assert stmt.rows[0].units == Decimal("10.000")
    assert stmt.warnings == ["a warning"]
    flat = repr(stmt)
    for personal in ("AAAAA1111A", "BBBBB2222B", "x@example.com", "Street 1", "+91-55501"):
        assert personal not in flat


def test_single_investor_without_folio_name_uses_the_statement_name():
    stmt = _to_statement(_cas([{"folio": "1", "amc": "DSP Mutual Fund", "PAN": "AAAAA1111A",
                                "schemes": [_scheme("DSP X Fund", "INF1")]}]))
    assert [i.name for i in stmt.investors] == ["Solo Investor"]


def test_row_ids_are_stable_across_statements():
    one = _to_statement(_cas([{"folio": "1", "amc": "DSP Mutual Fund", "schemes": [
        _scheme("DSP X Fund", "INF1", txns=[_txn("2020-01-01", "PURCHASE", "100.00", "10.000")])]}]))
    two = _to_statement(_cas([{"folio": "1", "amc": "DSP Mutual Fund", "schemes": [
        _scheme("DSP X Fund", "INF1", txns=[_txn("2020-01-01", "PURCHASE", "100.00", "10.000"),
                                           _txn("2021-01-01", "PURCHASE", "50.00", "5.000")])]}]))
    assert one.rows[0].id == two.rows[0].id


def test_depository_statements_are_refused(monkeypatch):
    class Nsdl:  # NSDL/CDSL statements parse into a shape without folios
        pass
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *_a, **_k: Nsdl())
    with pytest.raises(StatementParseError) as info:
        cas_parser.parse_cas_pdf(b"%PDF", "pw")
    assert info.value.code == ec.CAS_UNRECOGNIZED
