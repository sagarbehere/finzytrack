"""Generate the Beancount domain vectors (S0024; spec design/specs/kernel/vectors.md; INV-02, ADR-0024, BL-0002).

Reads the hand-written inputs in `corpora/vectors/cases.toml`, runs Beancount 3.2.0 on each of them and writes what
Beancount says to `corpora/vectors/vectors.json`. The vectors are never edited by hand: a Beancount upgrade (ADR-0004)
reruns this script, and `scripts/oracle/tests/test_domain_vectors.py` fails while the committed file differs from a
fresh run.

Run in the oracle environment, from the repository root:
    uv run --project scripts/oracle --locked python scripts/oracle/gen_domain_vectors.py --write
Without `--write` it prints the vectors to stdout.

The only rule here that is ours, not Beancount's, is the frontend port's verdict (`needs_booking` and `port`):
ADR-0024 says the live indicator reports "indeterminate" for postings whose weight Beancount must book or
interpolate first. Everything else is Beancount's own output.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import beancount
import re
from beancount import loader
from beancount.core import data, getters, interpolate, prices, realization
from beancount.core.amount import CURRENCY_RE
from beancount.core.display_context import Precision
from beancount.core.number import MISSING
from beancount.core.position import CostSpec
from beancount.ops import balance as balance_ops
from beancount.parser import parser

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "vectors"
CASES = CORPUS / "cases.toml"
VECTORS = CORPUS / "vectors.json"

OPEN_DATE = "2000-01-01"
TXN_DATE = "2020-06-01"
NARRATION = "vector"
BALANCE_ERROR = "Transaction does not balance"
ABOUT = (
    "Beancount domain vectors (S0024): each vector is an input from corpora/vectors/cases.toml and what Beancount "
    "says about it. Written by scripts/oracle/gen_domain_vectors.py; spec design/specs/kernel/vectors.md. "
    "Never edit by hand."
)


class VectorError(Exception):
    """A case the generator cannot turn into a vector (its setup does not load cleanly, its entry is missing...)."""


# ---- small helpers -------------------------------------------------------------------------------------------


def num(value: Any) -> str | None:
    """A Decimal as plain text with its exponent kept (`10.50`, never `1.05E+1`); None and MISSING as None."""
    if value is None or value is MISSING:
        return None
    assert isinstance(value, Decimal), value
    return format(value, "f")


def cur(value: Any) -> str | None:
    return None if value is None or value is MISSING else str(value)


def messages(errors: list[Any]) -> list[str]:
    """Beancount's error messages, first line only (no file names or line numbers)."""
    return [str(e.message).splitlines()[0].strip() for e in errors]


def option_lines(options: list[list[str]]) -> str:
    return "".join(f'option "{k}" "{v}"\n' for k, v in options)


def posting_line(p: dict[str, str]) -> str:
    parts = [p["account"]] + [p[k] for k in ("units", "cost", "price") if p.get(k)]
    return "  " + "  ".join(parts) + "\n"


def txn_text(postings: list[dict[str, str]]) -> str:
    return f'{TXN_DATE} * "{NARRATION}"\n' + "".join(posting_line(p) for p in postings)


def with_opens(options: list[list[str]], body: str) -> str:
    """Options, then an `open` for every account the body uses but does not open, then the body."""
    head = option_lines(options)
    entries, _, _ = parser.parse_string(head + body)
    opened = {e.account for e in entries if isinstance(e, data.Open)}
    missing = sorted(getters.get_accounts(entries) - opened)
    opens = "".join(f"{OPEN_DATE} open {a}\n" for a in missing)
    return head + opens + body


def load_clean(text: str, what: str) -> tuple[list[Any], dict[str, Any]]:
    entries, errors, options_map = loader.load_string(text)
    if errors:
        raise VectorError(f"{what} does not load cleanly in Beancount: {messages(errors)}")
    return entries, options_map


def effective_tolerance_options(options_map: dict[str, Any]) -> dict[str, Any]:
    return {
        "tolerance_multiplier": num(options_map["tolerance_multiplier"]),
        "inferred_tolerance_default": {k: num(v) for k, v in sorted(options_map["inferred_tolerance_default"].items())},
        "infer_tolerance_from_cost": bool(options_map["infer_tolerance_from_cost"]),
    }


def the_transaction(entries: list[Any]) -> Any | None:
    found = [e for e in entries if isinstance(e, data.Transaction) and e.narration == NARRATION]
    if len(found) > 1:
        raise VectorError("more than one vector transaction")
    return found[0] if found else None


# ---- the written postings (the frontend port's input) and the port's verdict ----------------------------------


def units_of(units: Any) -> dict[str, Any] | None:
    """A booked Amount as {number, currency}."""
    if units is None or units is MISSING:
        return None
    return {"number": num(units.number), "currency": cur(units.currency)}


LEFT_OUT = "MISSING"  # a part the author left out for Beancount to fill (Beancount's MISSING); null = not written


def w_num(value: Any) -> str | None:
    return LEFT_OUT if value is MISSING else num(value)


def w_cur(value: Any) -> str | None:
    return LEFT_OUT if value is MISSING else cur(value)


def written_units(units: Any) -> dict[str, Any] | str:
    if units is MISSING:
        return LEFT_OUT
    return {"number": w_num(units.number), "currency": w_cur(units.currency)}


def written_cost(cost: Any) -> dict[str, Any] | None:
    if cost is None:
        return None
    assert isinstance(cost, CostSpec), cost
    return {
        "per": w_num(cost.number_per),
        "total": w_num(cost.number_total),
        "currency": w_cur(cost.currency),
        "date": cost.date.isoformat() if cost.date else None,
        "label": cost.label,
        "merge": bool(cost.merge),
    }


def written_postings(case: dict[str, Any], parsed: Any) -> list[dict[str, Any]]:
    """The postings as written: Beancount's parse, except the price number, which is the written text (the parser
    divides a `@@` total by the units and turns a negative price into its absolute value, with an error)."""
    out = []
    for given, p in zip(case["postings"], parsed.postings, strict=True):
        price = None
        if p.price is not None:
            text = given["price"].strip()
            total = text.startswith("@@")
            number = LEFT_OUT if p.price.number is MISSING else text[2 if total else 1:].split()[0]
            price = {"number": number, "currency": w_cur(p.price.currency), "total": total}
        out.append({
            "account": p.account,
            "units": written_units(p.units),
            "cost": written_cost(p.cost),
            "price": price,
            "needs_booking": posting_needs_booking(p),
        })
    return out


def posting_needs_booking(p: Any) -> bool:
    """ADR-0024: the posting's weight is unknown until Beancount books or interpolates it, that is when any number
    or currency of its units, cost or price is left out (`{}` is a cost with every part left out)."""
    if p.units is None or p.units is MISSING or MISSING in (p.units.number, p.units.currency):
        return True
    c = p.cost
    if c is not None and MISSING in (c.number_per, c.number_total, c.currency):
        return True
    return p.price is not None and MISSING in (p.price.number, p.price.currency)


def needs_booking(parsed: Any) -> bool:
    return any(posting_needs_booking(p) for p in parsed.postings)


# ---- families --------------------------------------------------------------------------------------------------


def booked_posting(p: Any) -> dict[str, Any]:
    cost = None
    if p.cost is not None:
        cost = {"number": num(p.cost.number), "currency": cur(p.cost.currency),
                "date": p.cost.date.isoformat() if p.cost.date else None, "label": p.cost.label}
    price = None if p.price is None else {"number": num(p.price.number), "currency": cur(p.price.currency)}
    return {"account": p.account, "units": units_of(p.units),
            "cost": cost, "price": price,
            "interpolated": bool(p.meta and interpolate.AUTOMATIC_META in p.meta)}


def transaction_case(case: dict[str, Any]) -> tuple[dict[str, Any], Any, Any, list[Any], dict[str, Any]]:
    options = case.get("options", [])
    setup = case.get("setup", "")
    load_clean(with_opens(options, setup), f"{case['id']}: setup")
    text = with_opens(options, setup + txn_text(case["postings"]))
    parsed_entries, _, _ = parser.parse_string(text)
    parsed = the_transaction(parsed_entries)
    if parsed is None:
        raise VectorError(f"{case['id']}: the transaction does not parse")
    entries, errors, options_map = loader.load_string(text)
    booked = the_transaction(entries)
    inp = {"options": options, "ledger": text, "postings": written_postings(case, parsed),
           "tolerance_options": effective_tolerance_options(options_map)}
    return inp, parsed, booked, errors, options_map


def gen_tolerance(case: dict[str, Any]) -> dict[str, Any]:
    inp, parsed, booked, errors, options_map = transaction_case(case)
    out: dict[str, Any] = {"errors": messages(errors), "needs_booking": needs_booking(parsed)}
    out["dropped"] = booked is None  # Beancount refused the whole transaction (no booked postings to weigh)
    out["tolerances"] = out["residual"] = out["balanced"] = None
    out["interpolated"] = []
    if booked is not None:
        # The validation path (beancount.ops.validation.validate_check_transaction_balances): booked postings.
        tolerances = interpolate.infer_tolerances(booked.postings, options_map)
        residual = interpolate.compute_residual(booked.postings)
        out["tolerances"] = {"by_currency": {k: num(v) for k, v in sorted(tolerances.items())},
                             "default": num(tolerances.default)}
        out["residual"] = {pos.units.currency: num(pos.units.number)
                           for pos in sorted(residual, key=lambda x: x.units.currency)}
        out["balanced"] = bool(residual.is_small(tolerances))
        out["interpolated"] = [booked_posting(p) for p in booked.postings
                               if p.meta and interpolate.AUTOMATIC_META in p.meta]
    if booked is None and not out["needs_booking"]:
        raise VectorError(f"{case['id']}: Beancount dropped a transaction whose weights are known; "
                          "the port verdict would be undefined")
    refused = [e for e in out["errors"] if not e.startswith(BALANCE_ERROR)]
    if out["needs_booking"]:
        out["port"] = "indeterminate"
    elif refused:
        out["port"] = "not-pinned"  # Beancount refuses this entry for another reason; the contract refuses the write
    else:
        out["port"] = "balanced" if out["balanced"] else "unbalanced"
    return {"input": inp, "output": out}


def gen_booking(case: dict[str, Any]) -> dict[str, Any]:
    inp, _, booked, errors, _ = transaction_case(case)
    out = {"errors": messages(errors),
           "booked": [booked_posting(p) for p in booked.postings] if booked is not None else None}
    return {"input": inp, "output": out}


def gen_balance(case: dict[str, Any]) -> dict[str, Any]:
    options = case.get("options", [])
    setup = case.get("setup", "")
    load_clean(with_opens(options, setup), f"{case['id']}: setup")
    number, currency = case["amount"].split()
    tol = f" ~ {case['tolerance']}" if case.get("tolerance") else ""
    line = f"{TXN_DATE} balance {case['account']}  {number}{tol} {currency}\n"
    text = with_opens(options, setup + line)
    entries, errors, options_map = loader.load_string(text)
    idx = [i for i, e in enumerate(entries) if isinstance(e, data.Balance) and e.account == case["account"]
           and e.date.isoformat() == TXN_DATE]
    if len(idx) != 1:
        raise VectorError(f"{case['id']}: expected one balance entry")
    entry = entries[idx[0]]
    real = realization.realize(entries[: idx[0]])
    acct = realization.get(real, case["account"])
    actual = (realization.compute_balance(acct, leaf_only=False).get_currency_units(currency).number
              if acct is not None else Decimal(0))
    out = {"errors": messages(errors), "tolerance": num(balance_ops.get_balance_tolerance(entry, options_map)),
           "actual": num(actual), "difference": num(actual - entry.amount.number)}
    inp = {"options": options, "account": case["account"], "amount": case["amount"],
           "tolerance": case.get("tolerance"), "ledger": text}
    return {"input": inp, "output": out}


def probe_errors(text: str) -> tuple[list[Any], list[str]]:
    entries, errors, _ = parser.parse_string(text)
    return entries, messages(errors)


def gen_account(case: dict[str, Any]) -> dict[str, Any]:
    from beancount.core import account as account_mod

    options = case.get("options", [])
    items = []
    for item in case["names"]:
        name = item["name"]
        entries, errs = probe_errors(option_lines(options) + f"{OPEN_DATE} open {name}\n")
        opened = [e.account for e in entries if isinstance(e, data.Open)]
        valid = not errs and opened == [name]
        items.append({"polarity": item["polarity"], "input": {"name": name},
                      "output": {"valid": valid, "errors": errs, "core_is_valid": bool(account_mod.is_valid(name))}})
    return {"input": {"options": options}, "items": items}


def gen_currency(case: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in case["codes"]:
        code = item["code"]
        e1, errs1 = probe_errors(f"{OPEN_DATE} commodity {code}\n")
        e2, errs2 = probe_errors(f"{OPEN_DATE} price {code} 1.00 USD\n")  # the price directive's base currency
        ok1 = not errs1 and [e.currency for e in e1 if isinstance(e, data.Commodity)] == [code]
        ok2 = not errs2 and [e.currency for e in e2 if isinstance(e, data.Price)] == [code]
        items.append({"polarity": item["polarity"], "input": {"code": code},
                      "output": {"valid": ok1 and ok2, "commodity_errors": errs1, "price_base_errors": errs2,
                                 "currency_re_matches": re.fullmatch(CURRENCY_RE, code) is not None}})
    return {"input": {}, "items": items}


def gen_price(case: dict[str, Any]) -> dict[str, Any]:
    options = case.get("options", [])
    text = with_opens(options, case["setup"])
    entries, _ = load_clean(text, f"{case['id']}: ledger")
    price_map = prices.build_price_map(entries)
    items = []
    for q in case["queries"]:
        date = None if q["date"] == "latest" else datetime.date.fromisoformat(q["date"])
        found_date, number = prices.get_price(price_map, (q["base"], q["quote"]), date)
        items.append({"polarity": q["polarity"], "input": {"base": q["base"], "quote": q["quote"], "date": q["date"]},
                      "output": {"date": found_date.isoformat() if found_date else None, "number": num(number)}})
    return {"input": {"options": options, "ledger": text}, "items": items}


def gen_precision(case: dict[str, Any]) -> dict[str, Any]:
    options = case.get("options", [])
    text = with_opens(options, case["setup"])
    _, options_map = load_clean(text, f"{case['id']}: ledger")
    contexts = options_map["dcontext"].ccontexts
    items = []
    for item in case["currencies"]:
        c = item["currency"]
        ctx = contexts.get(c)  # .get: the mapping is a defaultdict, indexing would create the currency
        common = ctx.get_fractional(Precision.MOST_COMMON) if ctx is not None else None
        maximum = ctx.get_fractional(Precision.MAXIMUM) if ctx is not None else None
        items.append({"polarity": item["polarity"], "input": {"currency": c},
                      "output": {"most_common": common, "maximum": maximum}})
    return {"input": {"options": options, "ledger": text}, "items": items}


GENERATORS = {
    "tolerance": gen_tolerance,
    "balance": gen_balance,
    "account": gen_account,
    "currency": gen_currency,
    "price": gen_price,
    "precision": gen_precision,
    "booking": gen_booking,
}
COMMON = ("id", "spec", "about")


def build(cases: dict[str, Any]) -> dict[str, Any]:
    families: dict[str, list[dict[str, Any]]] = {}
    for family, gen in GENERATORS.items():
        out = []
        for case in cases.get(family, []):
            vector = {k: case[k] for k in COMMON if k in case}
            if "polarity" in case:
                vector["polarity"] = case["polarity"]
            vector.update(gen(case))
            out.append(vector)
        families[family] = out
    return {"about": ABOUT, "beancount": beancount.__version__,
            "generator": "scripts/oracle/gen_domain_vectors.py", "cases": "corpora/vectors/cases.toml",
            "families": families}


def generate(cases_path: Path = CASES) -> str:
    cases = tomllib.loads(cases_path.read_text(encoding="utf-8"))
    return json.dumps(build(cases), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="write corpora/vectors/vectors.json")
    args = ap.parse_args(argv)
    text = generate()
    if args.write:
        VECTORS.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
