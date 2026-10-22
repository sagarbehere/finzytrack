"""API tests for statement import (Investments → Import statement; dev-docs/cas-import.md).

parse → preview → commit through the real app, with the PDF parser replaced by a
synthetic statement (no real CAS — personal data). Every write is verified by reading
the ledger (and price sidecar) back, never the response alone.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Generator

import pytest
from beancount.core import data
from fastapi.testclient import TestClient

from app import error_codes as ec
from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.statement_import import store
from app.services.statement_import import types as T
from app.services.statement_import.parsers.cas import StatementParseError
from tests.conftest import _build_config
from tests.statement_builders import Stmt

_LEDGER = """\
option "operating_currency" "INR"

2000-01-01 open Assets:Broker
2000-01-01 open Assets:Bank
2000-01-01 open Equity:Funding
2000-01-01 open Income:Dividends
2000-01-01 open Income:Gains
  income-type: "capital-gains"
2000-01-01 open Expenses:Taxes
"""

MAPPING = {
    "holding_accounts": {"ARN-1": "Assets:Broker"},
    "cash_fallback_account": "Equity:Funding",
    "dividend_account": "Income:Dividends",
    "taxes_account": "Expenses:Taxes",
    "gains_account": "Income:Gains",
}


def _statement(extra_sell: bool = False) -> T.StatementData:
    s = Stmt(start=date(2010, 1, 1), end=date(2025, 6, 30) if extra_sell else date(2024, 12, 31))
    a = s.fund("A", name="DSP Flexi Cap Fund - Regular Plan - Growth", isin="INF740K01029", amfi="100080",
               nav="55.5", nav_date=date(2024, 12, 30))
    bfund = s.fund("B", name="DSP Mid Cap Fund - Regular Plan - IDCW", isin="INF740K01110", amfi="104482")
    s.row(a, date(2015, 1, 1), T.KIND_PURCHASE, amount="10000.00", units="1000.000", nav="10")
    s.row(a, date(2016, 1, 1), T.KIND_SWITCH_OUT, amount="-1500.00", units="-100.000", nav="15")
    s.row(bfund, date(2016, 1, 1), T.KIND_SWITCH_IN, amount="1500.00", units="75.000", nav="20")
    s.row(bfund, date(2017, 3, 1), T.KIND_DIVIDEND_REINVEST, amount="150.00", units="7.000", nav="21.4286")
    if extra_sell:
        s.row(a, date(2025, 2, 1), T.KIND_REDEMPTION, amount="-5000.00", units="-100.000", nav="50")
    return s.build()


def _make_root(tmp_path: Path, ledger: str = _LEDGER, files: dict = None) -> Path:
    for d in ["config/csv_rules", "config/xls_rules", "config/email_rules",
              "config/recipes", "data/ledgers", "data/backups", "logs"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n  default_currency: INR\n  default_unknown_account: Expenses:Unknown\n"
    )
    (tmp_path / "data" / "ledgers" / "main.beancount").write_text(ledger)
    for name, text in (files or {}).items():
        (tmp_path / "data" / "ledgers" / name).write_text(text)
    return tmp_path


def _client(root: Path) -> TestClient:
    from app.main import create_app
    c = TestClient(create_app(_build_config(root)))
    c._ledger = root / "data" / "ledgers" / "main.beancount"  # type: ignore[attr-defined]
    return c


@pytest.fixture
def statement(monkeypatch):
    """The statement the (patched) parser returns; tests may replace ``holder[0]``."""
    holder = [_statement()]

    def fake_parse(content, password):
        if password == "wrong":
            raise StatementParseError(ec.CAS_WRONG_PASSWORD, "Wrong password.")
        return holder[0]

    monkeypatch.setattr("app.api.routers.statement_import.parse_cas_pdf", fake_parse)
    store.clear()
    return holder


@pytest.fixture
def client(tmp_path: Path, statement) -> Generator[TestClient, None, None]:
    with _client(_make_root(tmp_path)) as c:
        yield c


def _parse(c: TestClient, password: str = "pw"):
    return c.post("/api/investments/statement/parse", files={"file": ("cas.pdf", b"%PDF-1.4", "application/pdf")},
                  data={"password": password})


def _preview(c, token, mapping=MAPPING, corrections=()):
    return c.post("/api/investments/statement/preview",
                  json={"token": token, "mapping": mapping, "corrections": list(corrections)})


def _commit(c, token, preview_hash, mapping=MAPPING):
    return c.post("/api/investments/statement/commit",
                  json={"token": token, "mapping": mapping, "corrections": [], "expected_preview_hash": preview_hash})


def _import(c, mapping=MAPPING):
    token = _parse(c).json()["data"]["token"]
    prev = _preview(c, token, mapping).json()["data"]
    assert prev["blocking"] == []
    res = _commit(c, token, prev["preview_hash"], mapping)
    assert res.status_code == 200, res.json()
    return token, prev, res.json()["data"]


def _entries(path: Path):
    entries, errors, _ = load_ledger_checked(str(path))
    assert errors == [], [str(e.message) for e in errors]
    return entries


def _txns(entries, import_id=None):
    return [e for e in entries if isinstance(e, data.Transaction)
            and (import_id is None or e.meta.get("stmt_import") == import_id)]


def test_parse_reports_what_the_mapping_screen_needs(client):
    res = _parse(client)
    assert res.status_code == 200
    d = res.json()["data"]
    assert d["token"]
    assert (d["source"], d["variant"], d["row_count"]) == ("cas", "detailed", 4)
    assert [x["key"] for x in d["distributors"]] == ["ARN-1"]
    assert (d["needs_dividend_account"], d["needs_taxes_account"], d["needs_gains_account"], d["needs_cash_account"]) == (
        True, False, True, True)
    assert d["ledger_files"] == [str(Path(client._ledger).resolve())]
    # Nothing chosen before: only the metadata-matched gains account is pre-selected.
    assert d["preselected"]["holding_accounts"] == {}
    assert d["preselected"]["cash_fallback_account"] is None
    assert d["preselected"]["gains_account"] == "Income:Gains"


def test_wrong_password_is_a_typed_error(client):
    res = _parse(client, password="wrong")
    assert (res.status_code, res.json()["error"]["code"]) == (422, ec.CAS_WRONG_PASSWORD)


def test_import_writes_everything_in_one_go_and_reads_back(client):
    token, prev, res = _import(client)
    assert (res["transactions"], res["balances"], res["commodities_created"], res["prices"]) == (3, 2, 2, 2)
    assert res["failed_balance_checks"] == []

    entries = _entries(client._ledger)
    txns = _txns(entries, res["import_id"])
    assert sorted(t.narration for t in txns) == sorted(t["narration"] for t in prev["transactions"])
    assert all(t.meta["folio"] == "F1" and t.meta["advisor"] == "ARN-1" and t.meta["stmt_id"] for t in txns)
    balances = sorted((b.account, b.amount.currency, b.amount.number, b.tolerance)
                      for b in entries if isinstance(b, data.Balance))
    codes = {f["security_key"]: f["commodity"] for f in prev["funds"]}
    assert balances == sorted([("Assets:Broker", codes["A"], Decimal("900.000"), Decimal("0.001")),
                               ("Assets:Broker", codes["B"], Decimal("82.000"), Decimal("0.001"))])
    comm = {e.currency: e.meta for e in entries if isinstance(e, data.Commodity)}
    assert comm[codes["A"]]["fetch_symbol"] == "amfi:100080"
    assert comm[codes["A"]]["isin"] == "INF740K01029"
    assert comm[codes["A"]]["asset-class"] == "mutual-fund"
    record = [e for e in entries if isinstance(e, data.Custom) and e.type == "statement-import"]
    assert [r.values[0].value for r in record] == [res["import_id"]]

    prices, perr, _ = load_ledger_checked(str(sidecar_path(client._ledger)))
    assert perr == []
    assert sorted((p.currency, p.amount.number) for p in prices if isinstance(p, data.Price)) == sorted(
        [(codes["A"], Decimal("55.5")), (codes["B"], Decimal("10"))])

    listed = client.get("/api/investments/statement/imports").json()["data"]["imports"]
    assert [(i["import_id"], i["transactions"], i["source"]) for i in listed] == [(res["import_id"], 3, "cas")]


def test_reimport_adds_only_new_rows_and_preselects_earlier_choices(client, statement):
    _import(client)
    statement[0] = _statement(extra_sell=True)
    parsed = _parse(client).json()["data"]
    assert parsed["preselected"]["holding_accounts"] == {"ARN-1": "Assets:Broker"}
    assert parsed["preselected"]["cash_fallback_account"] == "Equity:Funding"
    prev = _preview(client, parsed["token"]).json()["data"]
    assert [t["kind"] for t in prev["transactions"]] == ["sell"]
    assert prev["summary"]["already_imported"] == 4
    res = _commit(client, parsed["token"], prev["preview_hash"]).json()["data"]
    assert len(_txns(_entries(client._ledger))) == 4
    assert res["balances"] == 2  # one per fund for the new statement end
    assert res["failed_balance_checks"] == []


def test_commit_refuses_a_stale_preview(client):
    token = _parse(client).json()["data"]["token"]
    prev = _preview(client, token).json()["data"]
    client.post("/api/accounts", json={"name": "Assets:Other", "open_date": "2000-01-01"})
    # A ledger change that alters the plan (an earlier write of the same import).
    token2 = _parse(client).json()["data"]["token"]
    prev2 = _preview(client, token2).json()["data"]
    _commit(client, token2, prev2["preview_hash"])
    res = _commit(client, token, prev["preview_hash"])
    assert (res.status_code, res.json()["error"]["code"]) == (409, ec.PREVIEW_STALE)
    assert len(_txns(_entries(client._ledger))) == 3


def test_commit_refuses_while_something_blocks(client):
    token = _parse(client).json()["data"]["token"]
    mapping = {**MAPPING, "holding_accounts": {}}
    prev = _preview(client, token, mapping).json()["data"]
    assert [b["code"] for b in prev["blocking"]] == ["ACCOUNT_REQUIRED"]
    res = _commit(client, token, prev["preview_hash"], mapping)
    assert (res.status_code, res.json()["error"]["code"]) == (422, ec.STATEMENT_NOT_READY)
    assert _txns(_entries(client._ledger)) == []


def test_unknown_token_has_expired(client):
    res = _preview(client, "nope")
    assert (res.status_code, res.json()["error"]["code"]) == (410, ec.STATEMENT_EXPIRED)


def test_undo_removes_one_import_as_a_unit(client):
    _token, _prev, res = _import(client)
    undo = client.delete(f"/api/investments/statement/imports/{res['import_id']}")
    assert undo.status_code == 200
    assert (undo.json()["data"]["removed_transactions"], undo.json()["data"]["removed_balances"]) == (3, 2)
    entries = _entries(client._ledger)
    assert _txns(entries) == []
    assert [e for e in entries if isinstance(e, (data.Balance, data.Custom))] == []
    assert client.get("/api/investments/statement/imports").json()["data"]["imports"] == []
    missing = client.delete("/api/investments/statement/imports/cas-nope")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, ec.STATEMENT_IMPORT_NOT_FOUND)


def test_import_can_go_to_an_included_file(tmp_path, statement):
    root = _make_root(tmp_path, ledger=_LEDGER + '\ninclude "cas.beancount"\n', files={"cas.beancount": ""})
    with _client(root) as c:
        cas_file = str((root / "data" / "ledgers" / "cas.beancount").resolve())
        parsed = _parse(c).json()["data"]
        assert parsed["ledger_files"] == [str(c._ledger.resolve()), cas_file]
        mapping = {**MAPPING, "target_file": cas_file}
        prev = _preview(c, parsed["token"], mapping).json()["data"]
        assert prev["target_file"] == cas_file
        _commit(c, parsed["token"], prev["preview_hash"], mapping)
        entries = _entries(c._ledger)
        written = [e for e in entries if isinstance(e, (data.Transaction, data.Balance, data.Commodity, data.Custom))]
        assert len(written) == 3 + 2 + 2 + 1
        assert {str(Path(e.meta["filename"]).resolve()) for e in written} == {cas_file}
        assert "stmt_import" not in c._ledger.read_text()


def test_hand_entered_holding_is_replaced_in_the_same_write(tmp_path, statement):
    hand = _LEDGER + """
2000-01-01 commodity MYDSP
  isin: "INF740K01029"
2023-01-01 * "Opening holding MYDSP"
  Assets:Broker   900.000 MYDSP {11.00 INR}
  Equity:Funding  -9900.00 INR
"""
    with _client(_make_root(tmp_path, ledger=hand)) as c:
        token = _parse(c).json()["data"]["token"]
        assert [b["code"] for b in _preview(c, token).json()["data"]["blocking"]] == ["HAND_ENTRIES"]
        mapping = {**MAPPING, "hand_entries": {"A": "replace"}}
        prev = _preview(c, token, mapping).json()["data"]
        assert prev["removed_hand_entries"] == 1
        res = _commit(c, token, prev["preview_hash"], mapping).json()["data"]
        assert res["removed_hand_entries"] == 1
        entries = _entries(c._ledger)
        assert [t.narration for t in _txns(entries) if "stmt_import" not in t.meta] == []
        assert next(f for f in prev["funds"] if f["security_key"] == "A")["commodity"] == "MYDSP"


def test_dividend_account_can_be_tagged_by_the_import(client):
    _import(client, {**MAPPING, "tag_dividend_account": True})
    opens = {e.account: e.meta for e in _entries(client._ledger) if isinstance(e, data.Open)}
    assert opens["Income:Dividends"]["income-type"] == "dividend"


def test_ai_import_hands_a_cas_over_without_calling_the_model(client, monkeypatch):
    monkeypatch.setattr("app.api.routers.importer.llm_parse.is_cas_pdf", lambda *_a, **_k: True)
    res = client.post("/api/import/llm-parse", files={"file": ("cas.pdf", b"%PDF-1.4", "application/pdf")},
                      data={"account": "Assets:Bank", "currency": "INR", "pdf_password": "pw"})
    assert (res.status_code, res.json()["error"]["code"]) == (422, ec.STATEMENT_IS_CAS)


def test_a_fund_left_out_never_reaches_the_ledger(client):
    mapping = {**MAPPING, "excluded_funds": [{"folio": "F1", "security_key": "B"}]}
    token = _parse(client).json()["data"]["token"]
    prev = _preview(client, token, mapping).json()["data"]
    assert next(f for f in prev["funds"] if f["security_key"] == "B")["excluded"] is True
    res = _commit(client, token, prev["preview_hash"], mapping).json()["data"]
    entries = _entries(client._ledger)
    b_code = next(f["commodity"] for f in prev["funds"] if f["security_key"] == "B")
    touched = {p.units.currency for t in _txns(entries) for p in t.postings if p.units}
    assert b_code not in touched
    assert b_code not in {e.currency for e in entries if isinstance(e, data.Commodity)}
    assert [b.amount.currency for b in entries if isinstance(b, data.Balance)] == [
        next(f["commodity"] for f in prev["funds"] if f["security_key"] == "A")]
    assert res["balances"] == 1
