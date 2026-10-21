"""
API tests for the Setup & Metadata backend (dev-docs/investment-view.md §10.3):
the recognized vocabulary, the metadata review, the batch commodity-metadata
upsert, and account-metadata key removal. Writes are verified by a subsequent
read and against the ledger file on disk, never the response alone.
"""

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _build_config


# No operating_currency: VOO is held at cost but untagged, so it is classed as
# a currency — the review must still find it as a holding (cost-held signal).
_LEDGER = """\
2015-01-01 commodity VMFXX
  name: "Vanguard Federal Money Market Fund"
  note: "keep me"

1970-01-01 open Assets:Bank:Checking   USD
1970-01-01 open Assets:Bank:CD1        USD
  account-type: "cd"
1970-01-01 open Assets:Broker
1970-01-01 open Income:Dividends:VOO   USD
  income-type: "dividend"
1970-01-01 open Income:Salary          USD
1970-01-01 open Equity:Opening-Balances

2024-01-01 * "Opening Balance"
  Assets:Bank:Checking     5000.00 USD
  Assets:Bank:CD1          1000.00 USD
  Equity:Opening-Balances

2024-02-01 * "Buy VOO"
  Assets:Broker            10 VOO {400.00 USD}
  Assets:Bank:Checking   -4000.00 USD

2024-03-01 * "Dividend"
  Assets:Bank:Checking      12.00 USD
  Income:Dividends:VOO     -12.00 USD
"""


def _make_root(tmp_path: Path, ledger_text: str) -> Path:
    for d in ["config/csv_rules", "config/xls_rules", "config/email_rules",
              "config/recipes", "data/ledgers", "data/backups", "logs"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n  default_currency: USD\n  default_unknown_account: Expenses:Unknown\n"
    )
    (tmp_path / "data" / "ledgers" / "main.beancount").write_text(ledger_text)
    return tmp_path


@pytest.fixture
def meta_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from app.main import create_app
    root = _make_root(tmp_path, _LEDGER)
    with TestClient(create_app(_build_config(root))) as client:
        client._root = root  # type: ignore[attr-defined]
        yield client


def _ledger_text(client: TestClient) -> str:
    return (client._root / "data" / "ledgers" / "main.beancount").read_text()  # type: ignore[attr-defined]


def _backup_count(client: TestClient) -> int:
    return sum(1 for p in (client._root / "data" / "backups").rglob("*") if p.is_file())  # type: ignore[attr-defined]


def _review(client: TestClient) -> dict:
    resp = client.get("/api/metadata/review")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _items(review: dict) -> dict:
    return {(i["scope"], i["item"]): i for i in review["items"]}


def _commodity(client: TestClient, code: str) -> dict:
    resp = client.get("/api/commodities")
    assert resp.status_code == 200, resp.text
    return next(c for c in resp.json()["data"]["commodities"] if c["code"] == code)


def _account_meta(client: TestClient, name: str) -> dict:
    resp = client.get("/api/accounts")
    assert resp.status_code == 200, resp.text
    meta = next(a for a in resp.json()["data"]["accounts"] if a["name"] == name)["metadata"]
    # The accounts API also returns the parser-stamped source location.
    return {k: v for k, v in meta.items() if k not in ("filename", "lineno")}


class TestVocabulary:
    def test_lists_recognized_keys_per_scope(self, meta_client):
        resp = meta_client.get("/api/metadata/vocabulary")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        keys = {(f["scope"], f["key"]) for f in data["fields"]}
        assert keys == {
            ("commodity", "asset-class"), ("commodity", "name"), ("commodity", "fetch_symbol"),
            ("commodity", "issue_date"), ("commodity", "denomination"),
            ("account", "account-type"), ("account", "interest_rate"),
            ("account", "maturity_date"), ("account", "interest_account"),
            ("income-account", "income-type"), ("income-account", "dividend_of"),
        }
        assert data["currency_asset_classes"] == ["cash", "currency"]

    def test_commodity_and_account_asset_classes_differ(self, meta_client):
        fields = meta_client.get("/api/metadata/vocabulary").json()["data"]["fields"]
        by = {(f["scope"], f["key"]): f for f in fields}
        assert by[("commodity", "asset-class")]["values"] == [
            "cash", "currency", "stock", "etf", "mutual-fund", "money-market",
            "bond", "cd", "i-bond", "crypto",
        ]
        assert by[("account", "account-type")]["values"] == ["checking", "savings", "cd", "bond", "cash"]
        assert by[("account", "maturity_date")]["requires_key"] == "account-type"
        assert by[("account", "maturity_date")]["requires_values"] == ["cd", "bond"]
        # Interest rate is shown for any account that can earn interest.
        assert by[("account", "interest_rate")]["requires_key"] == "account-type"
        assert by[("account", "interest_rate")]["requires_values"] == ["checking", "savings", "cd", "bond"]


class TestReview:
    def test_items_and_unset_keys(self, meta_client):
        review = _review(meta_client)
        items = _items(review)
        assert set(items) == {
            ("commodity", "USD"), ("commodity", "VMFXX"), ("commodity", "VOO"),
            ("account", "Assets:Bank:Checking"), ("account", "Assets:Bank:CD1"),
            ("income-account", "Income:Dividends:VOO"), ("income-account", "Income:Salary"),
        }
        # Untagged VOO is classed as a currency (no operating_currency) but is
        # held at cost, so it is reviewed as a holding.
        assert items[("commodity", "VOO")]["is_currency"] is True
        assert items[("commodity", "VOO")]["unset"] == ["asset-class"]
        # USD looks like a currency → listed, never flagged.
        assert items[("commodity", "USD")]["unset"] == []
        # VMFXX is declared but never held at cost or classed → not flagged.
        assert items[("commodity", "VMFXX")]["unset"] == []
        assert items[("account", "Assets:Bank:Checking")]["unset"] == ["account-type"]
        # Tagging an account as a CD makes its CD fields relevant.
        assert items[("account", "Assets:Bank:CD1")]["unset"] == ["interest_rate", "maturity_date"]
        assert items[("income-account", "Income:Dividends:VOO")]["unset"] == ["dividend_of"]
        assert items[("income-account", "Income:Salary")]["unset"] == ["income-type"]
        # Summary: only open, in-use items missing their main key. CD1 has its
        # type (only refinements unset); Income:Salary has no postings.
        assert review["summary"] == {"commodity": 1, "account": 1, "income-account": 0}
        assert review["has_operating_currencies"] is False

    def test_operating_currencies_are_not_listed(self, meta_client):
        meta_client.put("/api/commodities/operating-currencies", json={"currencies": ["USD"]})
        review = _review(meta_client)
        codes = {i["item"] for i in review["items"] if i["scope"] == "commodity"}
        assert codes == {"VMFXX", "VOO"}
        assert review["has_operating_currencies"] is True


    def test_summary_skips_closed_and_unused_accounts(self, tmp_path):
        from app.main import create_app
        ledger = _LEDGER + """
2024-01-01 open Assets:Bank:Old        USD
2024-01-02 * "Old"
  Assets:Bank:Old            1.00 USD
  Equity:Opening-Balances
2024-06-01 * "Close out"
  Assets:Bank:Old           -1.00 USD
  Equity:Opening-Balances
2024-06-02 close Assets:Bank:Old
2024-01-01 open Assets:Bank:Placeholder USD
"""
        root = _make_root(tmp_path, ledger)
        with TestClient(create_app(_build_config(root))) as client:
            review = _review(client)
        items = _items(review)
        # Both are still listed as "not set"...
        assert items[("account", "Assets:Bank:Old")]["unset"] == ["account-type"]
        assert items[("account", "Assets:Bank:Old")]["closed"] is True
        assert items[("account", "Assets:Bank:Placeholder")]["unset"] == ["account-type"]
        # ...but neither prompts a banner.
        assert review["summary"]["account"] == 1

    def test_summary_skips_unused_commodity(self, meta_client):
        """A declared-but-never-posted holding is listed but doesn't prompt."""
        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "OLDFUND", "set": {"asset-class": "stock"}},
                        {"code": "VOO", "set": {"asset-class": "etf"}}],
        })
        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "OLDFUND", "remove": ["asset-class"], "set": {"name": "Sold long ago"}}],
        })
        review = _review(meta_client)
        assert _items(review)[("commodity", "OLDFUND")]["unset"] == []  # untagged, no cost, classed currency
        meta_client.put("/api/commodities/operating-currencies", json={"currencies": ["USD"]})
        review = _review(meta_client)
        # Now a holding (not an operating currency) with no asset-class, but never used.
        assert _items(review)[("commodity", "OLDFUND")]["unset"] == ["asset-class"]
        assert review["summary"]["commodity"] == 0

    def test_commodity_tagged_as_currency_is_not_flagged(self, meta_client):
        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VOO", "set": {"asset-class": "currency"}}],
        })
        review = _review(meta_client)
        assert _items(review)[("commodity", "VOO")]["unset"] == []
        assert review["summary"]["commodity"] == 0

    def test_non_operating_currency_tagged_currency_is_not_flagged(self, meta_client):
        """With operating_currency USD, EUR is classed as a holding; once the
        user tags it `currency` it is no longer offered for an asset class."""
        meta_client.put("/api/commodities/operating-currencies", json={"currencies": ["USD"]})
        meta_client.put("/api/accounts/Assets:Bank:Checking", json={"currencies": ["USD", "EUR"]})
        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "EUR", "set": {"name": "Euro"}}],
        })
        items = _items(_review(meta_client))
        assert items[("commodity", "EUR")]["unset"] == ["asset-class"]

        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "EUR", "set": {"asset-class": "currency"}}],
        })
        items = _items(_review(meta_client))
        assert items[("commodity", "EUR")]["unset"] == []
        assert items[("commodity", "EUR")]["is_currency"] is False


class TestCommodityMetadataUpsert:
    def test_creates_directive_dated_first_use(self, meta_client):
        resp = meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VOO", "set": {"asset-class": "etf", "name": "Vanguard S&P 500"}}],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] == ["VOO"]

        voo = _commodity(meta_client, "VOO")
        assert voo["asset_class"] == "etf"
        assert voo["name"] == "Vanguard S&P 500"
        assert voo["is_currency"] is False
        assert "2024-02-01 commodity VOO" in _ledger_text(meta_client)
        assert _items(_review(meta_client))[("commodity", "VOO")]["unset"] == []

    def test_updates_existing_keeping_other_keys(self, meta_client):
        resp = meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VMFXX", "set": {"asset-class": "money-market"}, "remove": ["note"]}],
        })
        assert resp.status_code == 200, resp.text

        vmfxx = _commodity(meta_client, "VMFXX")
        assert vmfxx["asset_class"] == "money-market"
        assert vmfxx["name"] == "Vanguard Federal Money Market Fund"
        assert "note" not in vmfxx["metadata"]
        assert _items(_review(meta_client))[("commodity", "VMFXX")]["metadata"] == {
            "name": "Vanguard Federal Money Market Fund", "asset-class": "money-market",
        }
        text = _ledger_text(meta_client)
        assert text.count("commodity VMFXX") == 1
        assert "2015-01-01 commodity VMFXX" in text

    def test_batch_is_one_write(self, meta_client):
        before = _backup_count(meta_client)
        resp = meta_client.put("/api/commodities/metadata", json={
            "changes": [
                {"code": "VOO", "set": {"asset-class": "etf"}},
                {"code": "VMFXX", "set": {"asset-class": "money-market"}},
            ],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] == ["VOO", "VMFXX"]
        assert _backup_count(meta_client) == before + 1
        assert _commodity(meta_client, "VOO")["asset_class"] == "etf"
        assert _commodity(meta_client, "VMFXX")["asset_class"] == "money-market"

    def test_no_change_does_not_write(self, meta_client):
        before_text = _ledger_text(meta_client)
        before = _backup_count(meta_client)
        resp = meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VOO", "remove": ["asset-class"]}],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] == []
        assert _backup_count(meta_client) == before
        assert _ledger_text(meta_client) == before_text

    def test_custom_value_is_written(self, meta_client):
        meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VOO", "set": {"asset-class": "reit"}}],
        })
        assert _commodity(meta_client, "VOO")["asset_class"] == "reit"

    @pytest.mark.parametrize("key", ["Asset-class", "filename", "lineno", "has space", "1x"])
    def test_invalid_key_is_rejected(self, meta_client, key):
        before_text = _ledger_text(meta_client)
        resp = meta_client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VOO", "set": {key: "x"}}],
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert _ledger_text(meta_client) == before_text


class TestAccountMetadataRemoval:
    def test_remove_metadata_deletes_key(self, meta_client):
        resp = meta_client.put("/api/accounts/Assets:Bank:CD1", json={
            "remove_metadata": ["account-type"],
        })
        assert resp.status_code == 200, resp.text
        assert "account-type" not in _account_meta(meta_client, "Assets:Bank:CD1")
        assert "account-type" not in _ledger_text(meta_client).split("Assets:Bank:CD1")[1].split("open")[0]

    def test_metadata_without_key_keeps_it(self, meta_client):
        """Omitting a key from `metadata` keeps it — only `remove_metadata` deletes."""
        meta_client.put("/api/accounts/Assets:Bank:CD1", json={"metadata": {"interest_rate": "4.5%"}})
        assert _account_meta(meta_client, "Assets:Bank:CD1") == {
            "account-type": "cd", "interest_rate": "4.5%",
        }

    def test_recognized_date_is_written_as_date(self, meta_client):
        meta_client.put("/api/accounts/Assets:Bank:CD1", json={
            "metadata": {"maturity_date": "2027-01-14"},
        })
        assert "maturity_date: 2027-01-14\n" in _ledger_text(meta_client)
        assert _account_meta(meta_client, "Assets:Bank:CD1")["maturity_date"] == "2027-01-14"


def test_update_keeps_directive_in_its_included_file(tmp_path: Path):
    from app.main import create_app
    root = _make_root(tmp_path, 'include "commodities.beancount"\n\n' + _LEDGER.replace(
        '2015-01-01 commodity VMFXX\n  name: "Vanguard Federal Money Market Fund"\n  note: "keep me"\n', ''))
    included = root / "data" / "ledgers" / "commodities.beancount"
    included.write_text('2015-01-01 commodity VMFXX\n  name: "Vanguard Federal Money Market Fund"\n')
    with TestClient(create_app(_build_config(root))) as client:
        resp = client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VMFXX", "set": {"asset-class": "money-market"}}],
        })
        assert resp.status_code == 200, resp.text
        assert _commodity(client, "VMFXX")["asset_class"] == "money-market"
    assert 'asset-class: "money-market"' in included.read_text()
    assert "commodity VMFXX" not in (root / "data" / "ledgers" / "main.beancount").read_text()

    with TestClient(create_app(_build_config(root))) as client:
        resp = client.put("/api/commodities/metadata", json={
            "changes": [{"code": "VMFXX", "remove": ["asset-class"]}],
        })
        assert resp.status_code == 200, resp.text
        assert _commodity(client, "VMFXX")["asset_class"] is None
    assert "asset-class" not in included.read_text()
    assert "commodity VMFXX" in included.read_text()


def test_create_account_writes_recognized_date_as_date(meta_client):
    resp = meta_client.post("/api/accounts", json={
        "name": "Assets:Bank:CD2", "open_date": "2025-01-01", "currencies": ["USD"],
        "metadata": {"account-type": "cd", "maturity_date": "2026-01-01"},
    })
    assert resp.status_code == 201, resp.text
    assert "maturity_date: 2026-01-01\n" in _ledger_text(meta_client)
    assert _account_meta(meta_client, "Assets:Bank:CD2") == {
        "account-type": "cd", "maturity_date": "2026-01-01",
    }


def test_review_reports_the_currency_a_holding_was_bought_in(meta_client):
    """`cost_currency` lets the Price symbol editor offer a fund lookup only for
    holdings bought in INR. Never-at-cost commodities report none."""
    items = _items(_review(meta_client))
    assert items[("commodity", "VOO")]["cost_currency"] == "USD"
    assert items[("commodity", "VMFXX")]["cost_currency"] is None
