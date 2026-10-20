"""
API tests for the Investments → Prices tab backend (dev-docs/investment-view.md
§10.2): the price overview, a pair's price history, and batch price edits to the
price sidecar. Writes are verified by a subsequent read and against the files
on disk, never the response alone.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.ledger_loader import sidecar_path
from tests.conftest import _build_config

TODAY = date.today()


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


# No operating_currency: an untagged holding (ACME) is classed as a currency, so
# the fetcher won't price it — the overview must say why. Dates are relative to
# today because the endpoint measures age against the real date.
_LEDGER = f"""\
2015-01-01 commodity VOO
  asset-class: "etf"
2015-01-01 commodity BOND1
  asset-class: "bond"
2015-01-01 commodity VMFXX
  asset-class: "money-market"
2015-01-01 commodity IBX
  asset-class: "i-bond"
2015-01-01 commodity GONE
  asset-class: "stock"
2015-01-01 commodity USD
  asset-class: "cash"

2015-01-01 open Assets:Broker
2015-01-01 open Assets:Bank USD
2015-01-01 open Assets:Wallet
2015-01-01 open Income:Gains USD
2015-01-01 open Equity:Opening-Balances

2020-01-01 * "Opening"
  Assets:Bank        100000.00 USD
  Equity:Opening-Balances

2020-02-01 * "Buy VOO"
  Assets:Broker   10 VOO {{300.00 USD}}
  Assets:Bank   -3000.00 USD
2020-02-01 * "Buy ACME"
  Assets:Broker   5 ACME {{20.00 USD}}
  Assets:Bank   -100.00 USD
2020-02-01 * "Buy BOND1"
  Assets:Broker   2 BOND1 {{1000.00 USD}}
  Assets:Bank   -2000.00 USD
2020-02-01 * "Buy VMFXX"
  Assets:Broker   500 VMFXX {{1.00 USD}}
  Assets:Bank   -500.00 USD
2020-02-01 * "Buy IBX"
  Assets:Broker   1000 IBX {{1.00 USD}}
  Assets:Bank   -1000.00 USD
2020-02-01 * "Buy GONE"
  Assets:Broker   4 GONE {{10.00 USD}}
  Assets:Bank   -40.00 USD
2020-01-05 * "Buy euros"
  Assets:Wallet   100.00 EUR @@ 108.00 USD
  Assets:Bank   -108.00 USD
2020-03-01 * "Sell GONE"
  Assets:Broker   -4 GONE {{10.00 USD}} @ 12.00 USD
  Assets:Bank   48.00 USD
  Income:Gains   -8.00 USD

{_d(20)} price VOO 390.00 USD
"""

_SIDECAR = f"""\
2020-02-15 price GONE 11.00 USD
{_d(30)} price VOO 380.00 USD
{_d(3)} price VOO 400.00 USD
{_d(3)} price VOO 360.00 EUR
{_d(50)} price BOND1 990.00 USD
{_d(2)} price EUR 1.08 USD
{_d(2)} price VTI 250.00 USD
"""


def _make_root(tmp_path: Path) -> Path:
    for d in ["config/csv_rules", "config/xls_rules", "config/email_rules",
              "config/recipes", "data/ledgers", "data/backups", "logs"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n  default_currency: USD\n  default_unknown_account: Expenses:Unknown\n"
    )
    ledger = tmp_path / "data" / "ledgers" / "main.beancount"
    ledger.write_text(_LEDGER)
    sidecar_path(ledger).write_text(_SIDECAR)
    return tmp_path


@pytest.fixture
def prices_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from app.main import create_app
    root = _make_root(tmp_path)
    with TestClient(create_app(_build_config(root))) as client:
        client._ledger = root / "data" / "ledgers" / "main.beancount"  # type: ignore[attr-defined]
        yield client


def _ledger(client: TestClient) -> Path:
    return client._ledger  # type: ignore[attr-defined]


def _overview(client: TestClient) -> dict:
    resp = client.get("/api/prices/overview")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _rows(client: TestClient) -> dict:
    return {(r["commodity"], r["quote_currency"]): r for r in _overview(client)["rows"]}


def _history(client: TestClient, commodity: str, quote: str, **params) -> dict:
    resp = client.get("/api/prices/history",
                      params={"commodity": commodity, "quote": quote, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _points(client: TestClient, commodity: str, quote: str) -> list:
    return [(p["date"], p["number"], p["source"])
            for p in _history(client, commodity, quote, limit=500)["prices"]]


def _sidecar_lines(client: TestClient) -> list:
    text = sidecar_path(_ledger(client)).read_text()
    return [ln for ln in text.splitlines() if ln and not ln.startswith(";")]


class TestOverview:
    def test_rows(self, prices_client):
        data = _overview(prices_client)
        assert data["today"] == TODAY.isoformat()
        rows = {(r["commodity"], r["quote_currency"]): r for r in data["rows"]}
        assert set(rows) == {
            ("ACME", "USD"), ("BOND1", "USD"), ("EUR", "USD"), ("GONE", "USD"), ("IBX", "USD"),
            ("VMFXX", "USD"), ("VOO", "EUR"), ("VOO", "USD"), ("VTI", "USD"),
        }

        voo = rows[("VOO", "USD")]
        assert voo["cadence"] == "daily"
        assert voo["held"] is True
        assert voo["latest"] == {"date": _d(3), "number": "400.00", "source": "sidecar"}
        assert voo["age_days"] == 3
        assert voo["count"] == 3                     # 2 sidecar + 1 in the ledger file
        assert voo["out_of_date"] is False
        assert voo["not_fetched_reason"] is None
        # A price typed in another currency is never fetched, so never out of date.
        voo_eur = rows[("VOO", "EUR")]
        assert (voo_eur["count"], voo_eur["cadence"], voo_eur["out_of_date"]) == (1, "never", False)
        assert voo_eur["not_fetched_reason"] == "Fetched in USD"

    def test_out_of_date_follows_cadence(self, prices_client):
        rows = _rows(prices_client)
        bond = rows[("BOND1", "USD")]
        assert (bond["cadence"], bond["age_days"], bond["out_of_date"]) == ("monthly", 50, True)
        # Sold out: shows its last price, never out of date.
        gone = rows[("GONE", "USD")]
        assert (gone["held"], gone["latest"]["date"], gone["out_of_date"]) == (False, "2020-02-15", False)

    def test_not_fetched_reasons(self, prices_client):
        rows = _rows(prices_client)
        acme = rows[("ACME", "USD")]
        assert (acme["cadence"], acme["latest"], acme["count"]) == ("never", None, 0)
        assert acme["not_fetched_reason"] == "Counts as cash — add an asset class to fetch prices"
        assert acme["out_of_date"] is False
        assert rows[("VMFXX", "USD")]["not_fetched_reason"] == "Never fetched (money market)"
        # A real currency with an FX price is not told to add an asset class.
        assert rows[("EUR", "USD")]["not_fetched_reason"] == "Never fetched (currency)"
        assert rows[("VTI", "USD")]["not_fetched_reason"] == "Not used in any transaction"
        assert rows[("VTI", "USD")]["cadence"] == "never"
        ibx = rows[("IBX", "USD")]
        assert (ibx["cadence"], ibx["not_fetched_reason"]) == ("computed", "Needs an issue month")
        assert ibx["out_of_date"] is False           # not computed without an issue month


class TestHistory:
    def test_newest_first_with_source(self, prices_client):
        assert _points(prices_client, "VOO", "USD") == [
            (_d(3), "400.00", "sidecar"),
            (_d(20), "390.00", "ledger"),
            (_d(30), "380.00", "sidecar"),
        ]

    def test_paging(self, prices_client):
        page = _history(prices_client, "VOO", "USD", limit=1, offset=1)
        assert page["total"] == 3
        assert [p["date"] for p in page["prices"]] == [_d(20)]


class TestEdit:
    def test_set_update_and_remove_in_one_write(self, prices_client):
        ledger_before = _ledger(prices_client).read_bytes()
        resp = prices_client.put("/api/prices", json={
            "set": [
                {"date": _d(1), "commodity": "VOO", "number": "405.25", "quote_currency": "USD"},
                {"date": _d(3), "commodity": "VOO", "number": "401.00", "quote_currency": "USD"},
                {"date": _d(2), "commodity": "VMFXX", "number": "1.00", "quote_currency": "USD"},
            ],
            "remove": [{"date": _d(30), "commodity": "VOO", "quote_currency": "USD"}],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"added": 2, "updated": 1, "removed": 1, "total": 8}

        assert _points(prices_client, "VOO", "USD") == [
            (_d(1), "405.25", "sidecar"),
            (_d(3), "401.00", "sidecar"),
            (_d(20), "390.00", "ledger"),
        ]
        assert _rows(prices_client)[("VMFXX", "USD")]["latest"] == {
            "date": _d(2), "number": "1.00", "source": "sidecar",
        }
        assert sorted(_sidecar_lines(prices_client)) == sorted([
            "2020-02-15 price GONE 11.00 USD",
            f"{_d(50)} price BOND1 990.00 USD",
            f"{_d(3)} price VOO 360.00 EUR",
            f"{_d(3)} price VOO 401.00 USD",
            f"{_d(2)} price VMFXX 1.00 USD",
            f"{_d(1)} price VOO 405.25 USD",
            f"{_d(2)} price EUR 1.08 USD",
            f"{_d(2)} price VTI 250.00 USD",
        ])
        assert _ledger(prices_client).read_bytes() == ledger_before  # ledger file untouched

    def test_header_says_comments_are_not_kept(self, prices_client):
        prices_client.put("/api/prices", json={"set": [
            {"date": _d(1), "commodity": "VOO", "number": "405", "quote_currency": "USD"},
        ]})
        text = sidecar_path(_ledger(prices_client)).read_text()
        assert "; Edits here are kept; comments are not (every save rewrites this file)." in text.splitlines()

    def test_any_commodity_and_quote_allowed(self, prices_client):
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(1), "commodity": "BRK.B", "number": "450.10", "quote_currency": "EUR"},
        ]})
        assert resp.status_code == 200, resp.text
        assert _points(prices_client, "BRK.B", "EUR") == [(_d(1), "450.10", "sidecar")]

    def test_exponent_number_is_written_plainly(self, prices_client):
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(1), "commodity": "VOO", "number": "4.5E+2", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 200, resp.text
        assert f"{_d(1)} price VOO 450 USD" in _sidecar_lines(prices_client)

    @pytest.mark.parametrize("change", [
        {"set": [{"date": _d(20), "commodity": "VOO", "number": "1", "quote_currency": "USD"}]},
        {"remove": [{"date": _d(20), "commodity": "VOO", "quote_currency": "USD"}]},
    ])
    def test_rejects_a_price_in_the_ledger_file(self, prices_client, change):
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        resp = prices_client.put("/api/prices", json=change)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PRICE_IN_LEDGER"
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before
        assert _points(prices_client, "VOO", "USD")[1] == (_d(20), "390.00", "ledger")

    def test_rejects_removing_a_missing_price(self, prices_client):
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        resp = prices_client.put("/api/prices", json={"remove": [
            {"date": _d(5), "commodity": "VOO", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PRICE_NOT_FOUND"
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before

    @pytest.mark.parametrize("body", [
        {},
        {"set": [{"date": _d(1), "commodity": "VOO", "number": "0", "quote_currency": "USD"}]},
        {"set": [{"date": _d(1), "commodity": "VOO", "number": "-3", "quote_currency": "USD"}]},
        {"set": [{"date": _d(1), "commodity": "voo", "number": "3", "quote_currency": "USD"}]},
        {"set": [{"date": _d(1), "commodity": "VOO", "number": "3", "quote_currency": "USD"}] * 2},
        {"set": [{"date": _d(3), "commodity": "VOO", "number": "3", "quote_currency": "USD"}],
         "remove": [{"date": _d(3), "commodity": "VOO", "quote_currency": "USD"}]},
    ])
    def test_rejects_invalid_batches(self, prices_client, body):
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        resp = prices_client.put("/api/prices", json=body)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before

    def test_parse_check_blocks_a_bad_render(self, prices_client, monkeypatch):
        from app.core.ledger_manager import LedgerManager
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        monkeypatch.setattr(
            LedgerManager, "_render_price_sidecar", staticmethod(lambda prices: "not beancount\n"),
        )
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(1), "commodity": "VOO", "number": "405", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PRICE_SIDECAR_INVALID"
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before


class TestEditGuards:
    def test_rejects_a_date_after_tomorrow(self, prices_client):
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(-2), "commodity": "VOO", "number": "1", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before

    def test_tomorrow_is_allowed(self, prices_client):
        # One day of slack for a browser ahead of the server's date.
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(-1), "commodity": "VOO", "number": "1", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 200, resp.text
        assert _points(prices_client, "VOO", "USD")[0] == (_d(-1), "1", "sidecar")

    def test_replace_false_refuses_a_taken_date(self, prices_client):
        before = sidecar_path(_ledger(prices_client)).read_bytes()
        # Move the 30-days-ago price onto the 3-days-ago date, which has one.
        resp = prices_client.put("/api/prices", json={
            "set": [{"date": _d(3), "commodity": "VOO", "number": "380.00",
                     "quote_currency": "USD", "replace": False}],
            "remove": [{"date": _d(30), "commodity": "VOO", "quote_currency": "USD"}],
        })
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "PRICE_EXISTS"
        assert err["details"]["prices"] == [
            {"date": _d(3), "commodity": "VOO", "quote_currency": "USD", "number": "400.00"},
        ]
        assert sidecar_path(_ledger(prices_client)).read_bytes() == before

    def test_replace_false_on_a_free_date_saves(self, prices_client):
        resp = prices_client.put("/api/prices", json={
            "set": [{"date": _d(29), "commodity": "VOO", "number": "380.00",
                     "quote_currency": "USD", "replace": False}],
            "remove": [{"date": _d(30), "commodity": "VOO", "quote_currency": "USD"}],
        })
        assert resp.status_code == 200, resp.text
        assert [p[0] for p in _points(prices_client, "VOO", "USD")] == [_d(3), _d(20), _d(29)]

    def test_a_price_file_with_unreadable_lines_is_never_rewritten(self, prices_client):
        path = sidecar_path(_ledger(prices_client))
        path.write_text(path.read_text() + "2026-10-01 price VOO 4OO USD\n")
        before = path.read_bytes()
        resp = prices_client.put("/api/prices", json={"set": [
            {"date": _d(1), "commodity": "VOO", "number": "405", "quote_currency": "USD"},
        ]})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PRICE_SIDECAR_INVALID"
        assert path.read_bytes() == before


_OPERATING_LEDGER = """\
option "operating_currency" "USD"

2015-01-01 open Assets:Broker
2015-01-01 open Assets:Bank USD
2015-01-01 open Equity:Opening-Balances

2020-01-01 * "Opening"
  Assets:Bank        10000.00 USD
  Equity:Opening-Balances

2020-02-01 * "Buy VOO"
  Assets:Broker   10 VOO {300.00 USD}
  Assets:Bank   -3000.00 USD
2020-02-01 * "Buy VTI"
  Assets:Broker   5 VTI {200.00 USD}
  Assets:Bank   -1000.00 USD
"""


class TestOverviewWithOperatingCurrency:
    """The common case: operating currencies declared, holdings untagged."""

    @pytest.fixture
    def client(self, tmp_path):
        from app.main import create_app
        root = _make_root(tmp_path)
        ledger = root / "data" / "ledgers" / "main.beancount"
        ledger.write_text(_OPERATING_LEDGER)
        sidecar_path(ledger).write_text(
            f"{_d(10)} price VOO 400.00 USD\n{_d(2)} price VOO 360.00 EUR\n"
        )
        with TestClient(create_app(_build_config(root))) as c:
            yield c

    def test_untagged_holdings_are_fetched_daily(self, client):
        rows = _rows(client)
        assert set(rows) == {("VOO", "EUR"), ("VOO", "USD"), ("VTI", "USD")}
        voo = rows[("VOO", "USD")]
        assert (voo["cadence"], voo["held"], voo["age_days"], voo["out_of_date"]) == ("daily", True, 10, True)
        assert voo["not_fetched_reason"] is None
        # Held, no price yet: a row in the currency it was bought in, out of date.
        vti = rows[("VTI", "USD")]
        assert (vti["latest"], vti["count"], vti["out_of_date"]) == (None, 0, True)
        assert rows[("VOO", "EUR")]["out_of_date"] is False


def test_a_typed_number_keeps_its_trailing_zeros(prices_client):
    resp = prices_client.put("/api/prices", json={"set": [
        {"date": _d(1), "commodity": "VOO", "number": "712.50", "quote_currency": "USD"},
    ]})
    assert resp.status_code == 200, resp.text
    assert f"{_d(1)} price VOO 712.50 USD" in _sidecar_lines(prices_client)
    assert _points(prices_client, "VOO", "USD")[0] == (_d(1), "712.50", "sidecar")
