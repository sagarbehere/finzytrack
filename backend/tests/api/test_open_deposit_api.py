"""
API tests for Open Deposit (POST /api/investments/open-deposit): a savings, CD or
bond account, optionally with its own interest income account and first deposit,
written in one ledger write — or previewed with dry_run. Writes are verified by
reading the ledger and the accounts API back, never the response alone.
"""

from datetime import date
from pathlib import Path
from typing import Generator

import pytest
from beancount.core import data
from fastapi.testclient import TestClient

from app.core.ledger_loader import load_ledger_checked
from tests.conftest import _build_config

_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 open Assets:Bank:Savings USD
  account-type: "savings"
2020-01-01 open Assets:Bank:Euro EUR
2023-06-01 open Assets:Bank:Late USD
2020-01-01 open Assets:Bank:CD-Old USD
  account-type: "cd"
  interest_account: "Income:Interest:Shared"
2020-01-01 open Income:Interest:Shared USD
  income-type: "interest"
2020-01-01 open Income:Other USD
2027-01-01 open Income:Interest:Later USD
2020-01-01 open Assets:Bank:Old USD
2024-01-01 close Assets:Bank:Old
2020-01-01 open Income:Interest:Closed USD
2024-01-01 close Income:Interest:Closed
2020-01-01 open Liabilities:Card USD
2020-01-01 open Equity:Opening-Balances

2020-01-02 * "Opening"
  Assets:Bank:Savings  50000.00 USD
  Equity:Opening-Balances
2023-06-02 * "Opening late"
  Assets:Bank:Late  100.00 USD
  Equity:Opening-Balances
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
    (tmp_path / "data" / "ledgers" / "main.beancount").write_text(_LEDGER)
    return tmp_path


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from app.main import create_app
    root = _make_root(tmp_path)
    with TestClient(create_app(_build_config(root))) as c:
        c._ledger = root / "data" / "ledgers" / "main.beancount"  # type: ignore[attr-defined]
        yield c


def _ledger(c: TestClient) -> Path:
    return c._ledger  # type: ignore[attr-defined]


def _entries(c: TestClient):
    entries, errors, _ = load_ledger_checked(str(_ledger(c)))
    assert errors == []
    return entries


def _open(c: TestClient, name: str) -> data.Open:
    return next(e for e in _entries(c) if isinstance(e, data.Open) and e.account == name)


def _meta(e) -> dict:
    return {k: v for k, v in e.meta.items() if k not in ("filename", "lineno")}


def _account(c: TestClient, name: str) -> dict:
    accounts = c.get("/api/accounts").json()["data"]["accounts"]
    return next(a for a in accounts if a["name"] == name)


CD = {
    "account": "Assets:Bank:CD-2026", "account_type": "cd", "currency": "USD",
    "open_date": "2026-01-15", "interest_rate": "4.50%", "maturity_date": "2027-01-15",
}


def _post(c: TestClient, **over):
    return c.post("/api/investments/open-deposit", json={**CD, **over})


class TestCreate:
    def test_deposit_only(self, client):
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["accounts_created"] == ["Assets:Bank:CD-2026"]
        cd = _open(client, "Assets:Bank:CD-2026")
        assert (cd.date.isoformat(), cd.currencies) == ("2026-01-15", ["USD"])
        assert _meta(cd) == {
            "account-type": "cd", "interest_rate": "4.50%",
            "maturity_date": date(2027, 1, 15),
        }
        assert "maturity_date: 2027-01-15\n" in _ledger(client).read_text()   # a date, not a string
        assert _account(client, "Assets:Bank:CD-2026")["metadata"]["account-type"] == "cd"

    def test_with_new_income_account_and_first_deposit(self, client):
        resp = _post(client, income_account={"mode": "new", "account": "Income:Interest:CD-2026"},
                     first_deposit={"from_account": "Assets:Bank:Savings", "amount": "10000.00"})
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["accounts_created"] == ["Assets:Bank:CD-2026", "Income:Interest:CD-2026"]
        assert body["warnings"] == []

        assert _meta(_open(client, "Assets:Bank:CD-2026"))["interest_account"] == "Income:Interest:CD-2026"
        inc = _open(client, "Income:Interest:CD-2026")
        assert (_meta(inc), inc.currencies) == ({"income-type": "interest"}, ["USD"])

        txns = [e for e in _entries(client) if isinstance(e, data.Transaction)
                and any(p.account == "Assets:Bank:CD-2026" for p in e.postings)]
        assert len(txns) == 1
        t = txns[0]
        assert t.date.isoformat() == "2026-01-15"
        assert sorted((p.account, str(p.units)) for p in t.postings) == [
            ("Assets:Bank:CD-2026", "10000.00 USD"), ("Assets:Bank:Savings", "-10000.00 USD"),
        ]
        assert {"id", "content_hash", "source_account"} <= set(t.meta)    # same IDs as Register
        cur = {x["currency"]: x["balance"] for x in _account(client, "Assets:Bank:CD-2026")["currencies"]}
        assert cur == {"USD": "10000.00"}

    def test_existing_interest_account_is_linked(self, client):
        resp = _post(client, account_type="savings", maturity_date=None, account="Assets:Bank:HYSA",
                     income_account={"mode": "existing", "account": "Income:Other"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["warnings"] == [
            "Income:Other has no income type of interest, so its interest is not matched to accounts.",
        ]
        assert _meta(_open(client, "Assets:Bank:HYSA"))["interest_account"] == "Income:Other"
        assert resp.json()["data"]["accounts_created"] == ["Assets:Bank:HYSA"]

    def test_existing_income_account_already_linked_is_not_linked_again(self, client):
        resp = _post(client, income_account={"mode": "existing", "account": "Income:Interest:Shared"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["warnings"] == [
            "Income:Interest:Shared is already linked to Assets:Bank:CD-Old, so it is not linked to the new account.",
        ]
        assert "interest_account" not in _meta(_open(client, "Assets:Bank:CD-2026"))


class TestDryRun:
    def test_previews_without_writing(self, client):
        before = _ledger(client).read_bytes()
        resp = _post(client, dry_run=True,
                     income_account={"mode": "new", "account": "Income:Interest:CD-2026"},
                     first_deposit={"from_account": "Assets:Bank:Savings", "amount": "10000.00",
                                    "date": "2026-01-16"})
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert (body["created"], body["accounts_created"]) == (False, [])
        preview = body["preview"]
        for line in ['2026-01-15 open Assets:Bank:CD-2026', 'account-type: "cd"', 'interest_rate: "4.50%"',
                     "maturity_date: 2027-01-15", 'interest_account: "Income:Interest:CD-2026"',
                     "2026-01-15 open Income:Interest:CD-2026", 'income-type: "interest"',
                     '2026-01-16 * "Opening deposit to Assets:Bank:CD-2026"']:
            assert line in preview
        assert "content_hash" not in preview and "id:" not in preview
        assert _ledger(client).read_bytes() == before

    def test_preview_matches_what_is_written(self, client):
        kwargs = dict(income_account={"mode": "new", "account": "Income:Interest:CD-2026"},
                      first_deposit={"from_account": "Assets:Bank:Savings", "amount": "10000.00"})
        preview = _post(client, dry_run=True, **kwargs).json()["data"]["preview"]
        written = _post(client, **kwargs).json()["data"]["preview"]
        assert preview == written


class TestRejects:
    @pytest.mark.parametrize("over,status,code", [
        ({"account": "Assets:Bank:Savings"}, 409, "ACCOUNT_ALREADY_EXISTS"),
        ({"account": "Income:CD"}, 422, "INVALID_ACCOUNT_FORMAT"),
        ({"account_type": "savings"}, 422, "VALIDATION_ERROR"),               # savings with a maturity date
        ({"maturity_date": "2025-01-01"}, 422, "VALIDATION_ERROR"),           # before the open date
        ({"income_account": {"mode": "new", "account": "Income:Other"}}, 409, "ACCOUNT_ALREADY_EXISTS"),
        ({"income_account": {"mode": "existing", "account": "Income:Nope"}}, 404, "ACCOUNT_NOT_FOUND"),
        ({"first_deposit": {"from_account": "Assets:Nope", "amount": "1"}}, 404, "ACCOUNT_NOT_FOUND"),
        ({"first_deposit": {"from_account": "Assets:Bank:Euro", "amount": "1"}}, 422, "VALIDATION_ERROR"),
        ({"first_deposit": {"from_account": "Assets:Bank:Savings", "amount": "1", "date": "2026-01-01"}},
         422, "VALIDATION_ERROR"),                                            # before the open date
        ({"first_deposit": {"from_account": "Assets:Bank:Savings", "amount": "0"}}, 422, "VALIDATION_ERROR"),
        ({"first_deposit": {"from_account": "Assets:Bank:Savings", "amount": "1", "date": "2027-01-15"}},
         422, "VALIDATION_ERROR"),                                            # on/after maturity
        ({"first_deposit": {"from_account": "Assets:Bank:Old", "amount": "1"}}, 422, "ACCOUNT_ALREADY_CLOSED"),
        ({"income_account": {"mode": "existing", "account": "Income:Interest:Closed"}}, 422, "ACCOUNT_ALREADY_CLOSED"),
        ({"currency": "EUR", "income_account": {"mode": "existing", "account": "Income:Interest:Shared"}},
         422, "VALIDATION_ERROR"),                                            # income account doesn't allow EUR
        ({"income_account": {"mode": "existing", "account": "Income:Interest:Later"}},
         422, "VALIDATION_ERROR"),                                            # income account opens later
    ])
    def test_rejected_and_nothing_written(self, client, over, status, code):
        before = _ledger(client).read_bytes()
        for dry in (True, False):
            resp = _post(client, dry_run=dry, **over)
            assert resp.status_code == status, resp.text
            assert resp.json()["error"]["code"] == code
        assert _ledger(client).read_bytes() == before

    def test_first_deposit_before_the_source_account_opens(self, client):
        """A posting before an account's open is a ledger error the write check
        doesn't block (it blocks booking errors), so Open Deposit refuses it."""
        before = _ledger(client).read_bytes()
        resp = _post(client, open_date="2022-01-01", maturity_date="2023-01-01",
                     first_deposit={"from_account": "Assets:Bank:Late", "amount": "50"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert resp.json()["error"]["details"]["field"] == "first_deposit_date"
        assert _ledger(client).read_bytes() == before


class TestPreviewHash:
    KW = dict(income_account={"mode": "existing", "account": "Income:Other"})

    def test_write_with_the_previewed_hash_succeeds(self, client):
        h = _post(client, dry_run=True, **self.KW).json()["data"]["preview_hash"]
        resp = _post(client, expected_preview_hash=h, **self.KW)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["preview_hash"] == h
        assert _meta(_open(client, "Assets:Bank:CD-2026"))["interest_account"] == "Income:Other"

    def test_write_that_would_differ_from_the_preview_is_refused(self, client):
        """After the preview, another account links Income:Other, so the write
        would no longer add the link the user saw: nothing is written."""
        h = _post(client, dry_run=True, **self.KW).json()["data"]["preview_hash"]
        upd = client.put("/api/accounts/Assets:Bank:Savings",
                         json={"metadata": {"interest_account": "Income:Other"}})
        assert upd.status_code == 200, upd.text
        before = _ledger(client).read_bytes()
        resp = _post(client, expected_preview_hash=h, **self.KW)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "PREVIEW_STALE"
        assert _ledger(client).read_bytes() == before


def test_first_deposit_from_a_liabilities_account(client):
    resp = _post(client, first_deposit={"from_account": "Liabilities:Card", "amount": "500.00"})
    assert resp.status_code == 200, resp.text
    t = next(e for e in _entries(client) if isinstance(e, data.Transaction)
             and any(p.account == "Assets:Bank:CD-2026" for p in e.postings))
    assert sorted((p.account, str(p.units)) for p in t.postings) == [
        ("Assets:Bank:CD-2026", "500.00 USD"), ("Liabilities:Card", "-500.00 USD"),
    ]


def test_multi_file_ledger_writes_to_the_root_and_leaves_included_files(tmp_path):
    """New entries go to the root file (as Accounts → create does); an included
    file keeps exactly its own entries (every write re-prints all files, so its
    formatting may change)."""
    from app.main import create_app
    root = _make_root(tmp_path)
    ledger = root / "data" / "ledgers" / "main.beancount"
    accounts_file = root / "data" / "ledgers" / "accounts.beancount"
    accounts_file.write_text(_LEDGER.replace('option "operating_currency" "USD"\n', ""))
    ledger.write_text('option "operating_currency" "USD"\ninclude "accounts.beancount"\n')
    def own_entries(path):
        from beancount.parser import parser
        entries, _e, _o = parser.parse_file(str(path))
        return sorted(f"{type(e).__name__} {e.date} {getattr(e, 'account', getattr(e, 'narration', ''))}"
                      for e in entries)
    included_before = own_entries(accounts_file)
    with TestClient(create_app(_build_config(root))) as c:
        resp = c.post("/api/investments/open-deposit", json={
            **CD, "first_deposit": {"from_account": "Assets:Bank:Savings", "amount": "100.00"},
        })
        assert resp.status_code == 200, resp.text
    root_text = ledger.read_text()
    assert "open Assets:Bank:CD-2026" in root_text
    assert "Opening deposit to Assets:Bank:CD-2026" in root_text
    assert own_entries(accounts_file) == included_before
    assert "CD-2026" not in accounts_file.read_text()
