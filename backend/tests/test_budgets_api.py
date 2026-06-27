"""/api/budgets CRUD (§6.3). Mutations verified via a subsequent read;
assertions on error codes/status, not message strings."""

from app import error_codes as ec


def _create(client, **over):
    body = {"date": "2026-01-01", "account": "Expenses:Food",
            "interval": "monthly", "amount": "500", "currency": "USD"}
    body.update(over)
    return client.post("/api/budgets", json=body)


def _history(client):
    return client.get("/api/budgets", params={"history": "true"}).json()["data"]["budgets"]


# ── Create + read-back ───────────────────────────────────────────────────────


def test_create_budget_round_trips(test_client):
    resp = _create(test_client)
    assert resp.status_code == 200
    created = resp.json()["data"]["budget"]
    assert created["account"] == "Expenses:Food"
    assert created["amount"] == "500"

    # Verify via a subsequent read.
    budgets = _history(test_client)
    match = [b for b in budgets if b["account"] == "Expenses:Food" and b["amount"] == "500"]
    assert len(match) == 1
    assert match[0]["id"] == created["id"]


def test_effective_set_picks_latest_as_of(test_client):
    _create(test_client, date="2026-01-01", amount="500")
    _create(test_client, date="2026-07-01", amount="720")

    # As of mid-year → the Jan directive (500) is effective.
    eff = test_client.get("/api/budgets", params={"as_of": "2026-03-01"}).json()["data"]["budgets"]
    food = [b for b in eff if b["account"] == "Expenses:Food"]
    assert len(food) == 1 and food[0]["amount"] == "500"

    # As of after the raise → 720.
    eff2 = test_client.get("/api/budgets", params={"as_of": "2026-08-01"}).json()["data"]["budgets"]
    food2 = [b for b in eff2 if b["account"] == "Expenses:Food"]
    assert len(food2) == 1 and food2[0]["amount"] == "720"


# ── Update ───────────────────────────────────────────────────────────────────


def test_update_budget_round_trips(test_client):
    created = _create(test_client).json()["data"]["budget"]
    resp = test_client.put(f"/api/budgets/{created['id']}", json={
        "date": "2026-01-01", "account": "Expenses:Food",
        "interval": "monthly", "amount": "650", "currency": "USD",
    })
    assert resp.status_code == 200
    budgets = _history(test_client)
    food = [b for b in budgets if b["account"] == "Expenses:Food"]
    assert len(food) == 1 and food[0]["amount"] == "650"


def test_update_unknown_id_is_404(test_client):
    resp = test_client.put("/api/budgets/deadbeefdeadbeef", json={
        "date": "2026-01-01", "account": "Expenses:Food",
        "interval": "monthly", "amount": "650", "currency": "USD",
    })
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == ec.BUDGET_NOT_FOUND


# ── Delete ───────────────────────────────────────────────────────────────────


def test_delete_budget_round_trips(test_client):
    created = _create(test_client).json()["data"]["budget"]
    resp = test_client.delete(f"/api/budgets/{created['id']}")
    assert resp.status_code == 200
    budgets = _history(test_client)
    assert not [b for b in budgets if b["id"] == created["id"]]


def test_delete_unknown_id_is_404(test_client):
    resp = test_client.delete("/api/budgets/deadbeefdeadbeef")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == ec.BUDGET_NOT_FOUND


# ── Validation ───────────────────────────────────────────────────────────────


def test_bad_interval_is_validation_error(test_client):
    resp = _create(test_client, interval="fortnightly")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == ec.BUDGET_VALIDATION_ERROR


def test_bad_amount_is_validation_error(test_client):
    resp = _create(test_client, amount="notanumber")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == ec.BUDGET_VALIDATION_ERROR


def test_multi_currency_side_by_side(test_client):
    _create(test_client, account="Expenses:Phone", amount="50", currency="USD")
    _create(test_client, account="Expenses:Phone", amount="1500", currency="INR")
    eff = test_client.get("/api/budgets", params={"account": "Expenses:Phone"}).json()["data"]["budgets"]
    by_curr = {b["currency"]: b["amount"] for b in eff}
    assert by_curr == {"USD": "50", "INR": "1500"}
