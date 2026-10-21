"""Renaming an account updates every reference to it in the ledger — including
metadata on *other* entries (e.g. an instrument's `interest_account` on its
`open`) and `custom "budget"` directives — not just postings and transaction
metadata. Mutations are verified via a subsequent read."""

from beancount.core import data
from beancount.loader import load_string

from app.core.beancount_engine import BeancountEngine


def _accounts(client):
    return {a["name"]: a for a in client.get("/api/accounts").json()["data"]["accounts"]}


def _budgets(client):
    return client.get("/api/budgets", params={"history": "true"}).json()["data"]["budgets"]


def test_rename_updates_budgets_and_metadata_on_other_accounts(test_client):
    assert test_client.post("/api/budgets", json={
        "date": "2026-01-01", "account": "Expenses:Food",
        "interval": "monthly", "amount": "500", "currency": "USD",
    }).status_code == 200
    assert test_client.put("/api/accounts/Assets:Bank:Checking", json={"metadata": {
        "linked": "Expenses:Food",                 # the account itself
        "linked-sub": "Expenses:Food:Snacks",      # a sub-account of it
        "remark": "Expenses:Food is the big one",  # mentions it, isn't it
        "other": "Expenses:FoodCourt",             # a different account
    }}).status_code == 200

    resp = test_client.put("/api/accounts/Expenses:Food", json={"new_name": "Expenses:Groceries"})
    assert resp.status_code == 200

    budgets = _budgets(test_client)
    assert [b["account"] for b in budgets] == ["Expenses:Groceries"]

    meta = _accounts(test_client)["Assets:Bank:Checking"]["metadata"]
    assert meta["linked"] == "Expenses:Groceries"
    assert meta["linked-sub"] == "Expenses:Groceries:Snacks"
    assert meta["remark"] == "Expenses:Food is the big one"
    assert meta["other"] == "Expenses:FoodCourt"


def test_engine_rewrites_account_values_in_any_entry():
    entries, errors, _ = load_string("""
2020-01-01 open Assets:CD USD
  interest_account: "Income:Interest:CD"
2020-01-01 open Income:Interest:CD USD
2020-01-01 open Expenses:Food USD
2020-01-01 commodity VOO
  note: "Income:Interest:CD"
2026-01-01 custom "budget" Expenses:Food "monthly" 500 USD
2026-01-01 custom "budget" "Expenses" "monthly" 900 USD
""")
    assert errors == []
    engine = BeancountEngine()

    renamed = engine.update_account(entries, "Income:Interest:CD", new_name="Income:Interest:Valley:CD")
    cd = next(e for e in renamed if isinstance(e, data.Open) and e.account == "Assets:CD")
    assert cd.meta["interest_account"] == "Income:Interest:Valley:CD"
    voo = next(e for e in renamed if isinstance(e, data.Commodity))
    assert voo.meta["note"] == "Income:Interest:Valley:CD"
    # The parser's source location survives the rewrite (multi-file write routing).
    assert (cd.meta["filename"], cd.meta["lineno"]) == next(
        (e.meta["filename"], e.meta["lineno"]) for e in entries if isinstance(e, data.Open) and e.account == "Assets:CD"
    )

    renamed = engine.update_account(entries, "Expenses:Food", new_name="Expenses:Groceries")
    budgets = [e.values[0].value for e in renamed if isinstance(e, data.Custom)]
    assert budgets == ["Expenses:Groceries", "Expenses"]   # the root budget is another account
