"""Per-mutator adapters: one intent → the legacy app's HTTP request (review:C DEP-11).

Each adapter builds the payload the v0.3 frontend would send. The payloads are lossy by design where the old
frontend was (review:D1 §12.8): amounts and metadata travel as strings, a lot needs a date to survive, `@@` is sent
as a total. The divergences this causes are tagged by the classifiers, not fixed here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from beancount.core import data
from beancount.core.number import MISSING

from model import parse_entries

SOURCE_ROOTS = ("Assets:", "Liabilities:")


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    body: dict[str, Any]


def _unset(v: Any) -> bool:  # identity only: Beancount's Amount.__eq__ fails on None and MISSING
    return v is None or v is MISSING


def _num(v: Any) -> str | None:
    return None if _unset(v) else format(v, "f")


def _posting(p: Any) -> dict[str, Any]:
    units, cost, price = p.units, p.cost, p.price
    out: dict[str, Any] = {"account": p.account, "amount": None, "currency": None}
    if not _unset(units):
        out["amount"], out["currency"] = _num(units.number), (None if units.currency is MISSING else units.currency)
    if cost is not None:
        out["cost_amount"], out["cost_currency"] = _num(cost.number_per), None if cost.currency is MISSING else cost.currency
        out["cost_date"] = None if _unset(cost.date) else cost.date.isoformat()
    if not _unset(price):
        out["price_amount"], out["price_currency"], out["price_type"] = _num(price.number), price.currency, "@"
    meta = {k: str(v) for k, v in (p.meta or {}).items() if k not in ("filename", "lineno")}
    if meta:
        out["posting_meta"] = meta
    return out


def _header(t: Any) -> dict[str, Any]:
    meta = {k: str(v) for k, v in t.meta.items() if k not in ("filename", "lineno", "memo")}
    return {"date": t.date.isoformat(), "flag": t.flag, "payee": t.payee or "", "narration": t.narration or "",
            "memo": t.meta.get("memo"), "tags": sorted(t.tags), "links": sorted(t.links),
            "postings": [_posting(p) for p in t.postings], "meta": meta}


def _fill_elided(t: Any, booked: Any | None) -> Any:
    """The v0.3 frontend showed, and sent back, the booked amount of an elided posting."""
    if booked is None or len(booked.postings) != len(t.postings):
        return t
    return t._replace(postings=[b._replace(meta=p.meta) if _unset(p.units) and p.account == b.account else p
                                for p, b in zip(t.postings, booked.postings)])


def update_transaction(params: dict[str, Any], booked: dict[str, Any]) -> Request:
    (t,) = parse_entries(params["entry"])
    if not isinstance(t, data.Transaction):
        raise ValueError("update_transaction takes one transaction")
    t = _fill_elided(t, booked.get(params["txn_id"]))
    return Request("PUT", "/api/ledger/transactions", {"transactions": [{"id": params["txn_id"], **_header(t)}]})


def rename_account(params: dict[str, Any], booked: dict[str, Any]) -> Request:
    return Request("PUT", "/api/accounts/" + urllib.parse.quote(params["old"], safe=""), {"new_name": params["new"]})


def _anchor(t: Any) -> str:
    """The posting the statement line belongs to: the one carrying `import-key`, else the first asset/liability."""
    for p in t.postings:
        if p.meta and "import-key" in p.meta:
            return p.account
    return next(p.account for p in t.postings if p.account.startswith(SOURCE_ROOTS))


def import_append(params: dict[str, Any], booked: dict[str, Any]) -> Request:
    txns = []
    for text in params["entries"]:
        for t in parse_entries(text):
            body = _header(t)
            if any(p["amount"] is None for p in body["postings"]):
                raise ValueError("the old commit endpoint needs every amount; elide nothing in import intents")
            txns.append({**body, "source_account": _anchor(t)})
    return Request("POST", "/api/import/commit", {"transactions": txns})


# Every adapter takes (case params, booked transactions by legacy id).
ADAPTERS = {"update_transaction": update_transaction, "rename_account": rename_account, "import_append": import_append}


def send(port: int, req: Request, timeout: float = 120.0) -> tuple[int, dict[str, Any]]:
    """(HTTP status, JSON body) of the request against the legacy app on *port*."""
    http = urllib.request.Request(f"http://127.0.0.1:{port}{req.path}", method=req.method,
                                  data=json.dumps(req.body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(http, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
