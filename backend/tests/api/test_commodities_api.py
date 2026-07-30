"""
API + integration tests for commodity classification and operating currencies.

Binds to dev-docs/commodities-and-currencies.md: the ledger's
`operating_currency` option is the single source of truth for which commodities
play a currency role, and setting it reclassifies commodities. Outcomes are
verified via a subsequent read and against the ledger file on disk, never the
response alone.
"""

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _build_config


# A ledger that mixes a currency (USD) with an investment holding (VOO) and
# declares NO operating_currency, so classification starts from the default.
_LEDGER = """\
1970-01-01 open Assets:Bank:Checking   USD
1970-01-01 open Assets:Broker          VOO
1970-01-01 open Equity:Opening-Balances

2024-01-01 * "Opening Balance"
  Assets:Bank:Checking     5000.00 USD
  Equity:Opening-Balances

2024-02-01 * "Buy VOO"
  Assets:Broker                 10 VOO
  Equity:Opening-Balances
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
def comm_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from app.main import create_app
    root = _make_root(tmp_path, _LEDGER)
    with TestClient(create_app(_build_config(root))) as client:
        client._root = root  # type: ignore[attr-defined]
        yield client


def _ledger_text(client: TestClient) -> str:
    return (client._root / "data" / "ledgers" / "main.beancount").read_text()  # type: ignore[attr-defined]


def _is_currency_map(client: TestClient) -> dict:
    resp = client.get("/api/commodities")
    assert resp.status_code == 200, resp.text
    return {c["code"]: c["is_currency"] for c in resp.json()["data"]["commodities"]}


class TestOperatingCurrencies:
    def test_empty_when_undeclared(self, comm_client):
        resp = comm_client.get("/api/commodities/operating-currencies")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["currencies"] == []

    def test_defaults_to_currency_before_whitelist(self, comm_client):
        """With no operating_currency, both USD and the VOO holding default to
        currency — we never hide a commodity we cannot classify."""
        assert _is_currency_map(comm_client) == {"USD": True, "VOO": True}

    def test_set_writes_ledger_and_round_trips(self, comm_client):
        resp = comm_client.put(
            "/api/commodities/operating-currencies",
            json={"currencies": ["USD"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["currencies"] == ["USD"]

        # Read-back reflects the new whitelist.
        resp = comm_client.get("/api/commodities/operating-currencies")
        assert resp.json()["data"]["currencies"] == ["USD"]

        # The option is durable in the root ledger file.
        assert 'option "operating_currency" "USD"' in _ledger_text(comm_client)

    def test_set_reclassifies_commodities(self, comm_client):
        """Setting the whitelist turns VOO from a currency into a holding."""
        comm_client.put(
            "/api/commodities/operating-currencies",
            json={"currencies": ["USD"]},
        )
        assert _is_currency_map(comm_client) == {"USD": True, "VOO": False}

    def test_dedupes_preserving_order(self, comm_client):
        resp = comm_client.put(
            "/api/commodities/operating-currencies",
            json={"currencies": ["USD", "INR", "USD"]},
        )
        assert resp.json()["data"]["currencies"] == ["USD", "INR"]

    def test_empty_list_clears_whitelist(self, comm_client):
        comm_client.put(
            "/api/commodities/operating-currencies",
            json={"currencies": ["USD"]},
        )
        # Clearing reverts to default-currency classification (VOO currency again).
        resp = comm_client.put(
            "/api/commodities/operating-currencies",
            json={"currencies": []},
        )
        assert resp.json()["data"]["currencies"] == []
        assert 'operating_currency' not in _ledger_text(comm_client)
        assert _is_currency_map(comm_client) == {"USD": True, "VOO": True}
