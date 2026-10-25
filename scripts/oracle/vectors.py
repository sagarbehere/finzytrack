"""Checks that keep the Beancount domain vectors honest (S0024; spec design/specs/kernel/vectors.md; INV-02, BL-0002).

`run_all()` returns a list of problems (empty = ok):
- the inputs (`cases.toml`): known families and fields, unique ids under their family, a polarity on every case or
  item, citations that resolve (at least one Beancount symbol per case);
- the vectors (`vectors.json`): the pinned Beancount version, one vector per case in order, every declared polarity
  equal to Beancount's verdict, a positive and a negative vector per family, the frontend port's verdict consistent
  with its rule, and the coverage the spec claims (each named behaviour has a vector);
- freshness: the committed vectors equal a fresh run of `gen_domain_vectors.py` (the keep-honest check; ADR-0004).
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import beancount

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "vectors"
DECISIONS = REPO / "design" / "decisions"
PINNED = "3.2.0"  # ADR-0004
TXN_DATE = "2020-06-01"

FAMILIES = ("tolerance", "balance", "account", "currency", "price", "precision", "booking")
# Families whose polarity is per item (a name, a code, a query, a currency) rather than per case.
ITEMS = {"account": "names", "currency": "codes", "price": "queries", "precision": "currencies"}
COMMON = {"id", "spec", "about"}
REQUIRED = {
    "tolerance": COMMON | {"polarity", "postings"},
    "booking": COMMON | {"polarity", "postings"},
    "balance": COMMON | {"polarity", "setup", "account", "amount"},
    "account": COMMON | {"names"},
    "currency": COMMON | {"codes"},
    "price": COMMON | {"setup", "queries"},
    "precision": COMMON | {"setup", "currencies"},
}
OPTIONAL = {
    "tolerance": {"options", "setup"},
    "booking": {"options", "setup"},
    "balance": {"options", "tolerance"},
    "account": {"options"},
    "currency": set(),
    "price": {"options"},
    "precision": {"options"},
}
ITEM_FIELDS = {
    "account": {"name", "polarity"},
    "currency": {"code", "polarity"},
    "price": {"base", "quote", "date", "polarity"},
    "precision": {"currency", "polarity"},
}
POSTING_REQUIRED = {"account"}
POSTING_OPTIONAL = {"units", "cost", "price"}
POLARITIES = ("positive", "negative")

Unit = tuple[dict[str, Any], dict[str, Any]]  # (case vector, item); the item is the case itself for case-level families


# ---- citations -----------------------------------------------------------------------------------------------


def beancount_symbol_exists(dotted: str) -> bool:
    parts = dotted.split(".")
    if parts[0] != "beancount":
        return False
    for i in range(len(parts), 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        try:
            for attr in parts[i:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return False
        return True
    return False


def citation_problem(ref: str, decisions: Path) -> str | None:
    if ref.startswith("beancount:"):
        return None if beancount_symbol_exists(ref[len("beancount:"):]) else f"unknown Beancount symbol {ref!r}"
    if re.fullmatch(r"ADR-\d{4}", ref):
        return None if any(decisions.glob(f"{ref}-*.md")) else f"unknown ADR {ref!r}"
    if re.fullmatch(r"review:\S+ §\S+", ref):
        return None
    return f"unrecognised citation {ref!r} (use beancount:<module>.<symbol>, ADR-nnnn or review:<doc> §<n>)"


# ---- inputs --------------------------------------------------------------------------------------------------


def check_cases(cases: dict[str, Any], decisions: Path = DECISIONS) -> list[str]:
    problems = []
    if cases.get("beancount") != PINNED:
        problems.append(f"cases.toml: beancount must be {PINNED!r} (ADR-0004), not {cases.get('beancount')!r}")
    for key in cases:
        if key != "beancount" and key not in FAMILIES:
            problems.append(f"cases.toml: unknown family {key!r}")
    seen: set[str] = set()
    for family in FAMILIES:
        family_cases = cases.get(family, [])
        if not family_cases:
            problems.append(f"{family}: the family has no cases")
        for case in family_cases:
            cid = case.get("id", "<no id>")
            where = f"{family} {cid}"
            missing = REQUIRED[family] - case.keys()
            extra = case.keys() - REQUIRED[family] - OPTIONAL[family]
            if missing:
                problems.append(f"{where}: missing field(s) {sorted(missing)}")
            if extra:
                problems.append(f"{where}: unknown field(s) {sorted(extra)}")
            if cid in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(cid)
            if not str(cid).startswith(f"{family}/"):
                problems.append(f"{where}: the id must start with {family + '/'!r}")
            refs = case.get("spec", [])
            if not isinstance(refs, list) or not refs:
                problems.append(f"{where}: the case cites nothing (spec)")
                refs = []
            if not any(isinstance(r, str) and r.startswith("beancount:") for r in refs):
                problems.append(f"{where}: the case cites no Beancount function (beancount:<module>.<symbol>)")
            for ref in refs:
                p = citation_problem(str(ref), decisions)
                if p:
                    problems.append(f"{where}: {p}")
            if family in ITEMS:
                items = case.get(ITEMS[family], [])
                if not items:
                    problems.append(f"{where}: no {ITEMS[family]}")
                for n, item in enumerate(items):
                    if set(item) != ITEM_FIELDS[family]:
                        problems.append(f"{where} item {n}: fields must be {sorted(ITEM_FIELDS[family])}, "
                                        f"not {sorted(item)}")
                    if item.get("polarity") not in POLARITIES:
                        problems.append(f"{where} item {n}: polarity must be one of {POLARITIES}")
            elif case.get("polarity") not in POLARITIES:
                problems.append(f"{where}: polarity must be one of {POLARITIES}")
            for n, posting in enumerate(case.get("postings", [])):
                if not POSTING_REQUIRED <= posting.keys() or posting.keys() - POSTING_REQUIRED - POSTING_OPTIONAL:
                    problems.append(f"{where} posting {n}: fields must be account and optionally units, cost, price")
    return problems


# ---- verdicts and the port rule ------------------------------------------------------------------------------


def verdict(family: str, item: dict[str, Any]) -> str:
    """Beancount's answer, read from the vector's output only."""
    o = item["output"]
    if family == "tolerance":
        ok = o["errors"] == [] and o["balanced"] is True
    elif family in ("balance", "booking"):
        ok = o["errors"] == []
    elif family in ("account", "currency"):
        ok = o["valid"] is True
    elif family == "price":
        ok = o["number"] is not None
    elif family == "precision":
        ok = o["most_common"] is not None
    else:
        raise KeyError(family)
    return "positive" if ok else "negative"


def units(doc: dict[str, Any], family: str) -> list[Unit]:
    out = []
    for case in doc["families"].get(family, []):
        if family in ITEMS:
            out.extend((case, item) for item in case.get("items", []))
        else:
            out.append((case, case))
    return out


LEFT_OUT = "MISSING"  # a part the author left out for Beancount to fill; null means "not written"
BALANCE_ERROR = "Transaction does not balance"


def written_needs_booking(p: dict[str, Any]) -> bool:
    """The port's rule, from the written posting alone: some number or currency of the units, cost or price is
    left out (marked MISSING). The generator's flag must equal it."""
    u, c, pr = p["units"], p["cost"], p["price"]
    if u == LEFT_OUT or LEFT_OUT in (u["number"], u["currency"]):
        return True
    if c is not None and LEFT_OUT in (c["per"], c["total"], c["currency"]):
        return True
    return pr is not None and LEFT_OUT in (pr["number"], pr["currency"])


def port_problems(case: dict[str, Any]) -> list[str]:
    o, where, problems = case["output"], f"tolerance {case['id']}", []
    postings = case["input"]["postings"]
    if o["needs_booking"] != any(p["needs_booking"] for p in postings):
        problems.append(f"{where}: needs_booking is not the OR of the postings' flags")
    for n, p in enumerate(postings):
        if written_needs_booking(p) != p["needs_booking"]:
            problems.append(f"{where} posting {n}: needs_booking is {p['needs_booking']}, "
                            f"the written posting says {written_needs_booking(p)}")
    refused = [e for e in o["errors"] if not e.startswith(BALANCE_ERROR)]
    expected = ("indeterminate" if o["needs_booking"] else "not-pinned" if refused
                else "balanced" if o["balanced"] else "unbalanced")
    if o["port"] != expected:
        problems.append(f"{where}: port verdict {o['port']!r}, the rule gives {expected!r}")
    said_unbalanced = any(e.startswith(BALANCE_ERROR) for e in o["errors"])
    if o["balanced"] is not None and o["balanced"] == said_unbalanced:
        problems.append(f"{where}: balanced={o['balanced']} disagrees with Beancount's own validation errors")
    if o["dropped"] and not o["needs_booking"]:
        problems.append(f"{where}: Beancount dropped a transaction whose weights are known")
    return problems


def balance_problems(case: dict[str, Any]) -> list[str]:
    """The recorded tolerance and difference must explain Beancount's own verdict on the assertion."""
    o = case["output"]
    failed = any(e.startswith("Balance failed") for e in o["errors"])
    over = abs(Decimal(o["difference"])) > Decimal(o["tolerance"])
    if failed != over and (failed or not o["errors"]):
        return [f"balance {case['id']}: difference {o['difference']} and tolerance {o['tolerance']} "
                f"do not explain Beancount's verdict"]
    return []


# ---- coverage: each behaviour the spec names has a vector ----------------------------------------------------


def _d(x: str | None) -> Decimal | None:
    return None if x is None else Decimal(x)


def _wp(case: dict[str, Any]) -> list[dict[str, Any]]:
    return case["input"]["postings"]


def _cost_shape(p: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    c = p["cost"]
    return (None, None, None, None) if c is None else (c["per"], c["total"], c["currency"], c["date"])


def _at_boundary(o: dict[str, Any]) -> bool:
    if not o["balanced"] or not o["residual"]:
        return False
    tol = o["tolerances"]
    return any(Decimal(r) != 0 and abs(Decimal(r)) == Decimal(tol["by_currency"].get(c, tol["default"]))
               for c, r in o["residual"].items())


def _zero_tol_fail(o: dict[str, Any]) -> bool:
    if o["balanced"] is not False:
        return False
    tol = o["tolerances"]
    return any(c not in tol["by_currency"] and Decimal(tol["default"]) == 0 for c in o["residual"])


def _price_lines(ledger: str) -> list[tuple[str, str, str]]:
    return re.findall(r"^(\d{4}-\d\d-\d\d) price (\S+) \S+ (\S+)$", ledger, flags=re.M)


def _forward(case: dict[str, Any], base: str, quote: str) -> list[str]:
    return [d for d, b, q in _price_lines(case["input"]["ledger"]) if (b, q) == (base, quote)]


def _lines_on(case: dict[str, Any], date: str | None) -> list[tuple[int, str, str, Decimal]]:
    """(line index, base, quote, number) of the price directives written on `date`."""
    out = []
    for n, line in enumerate(case["input"]["ledger"].splitlines()):
        m = re.fullmatch(r"(\d{4}-\d\d-\d\d) price (\S+) (\S+) (\S+)", line)
        if m and m.group(1) == date:
            out.append((n, m.group(2), m.group(4), Decimal(m.group(3))))
    return out


def _inverted_wins_before_forward(case: dict[str, Any], item: dict[str, Any]) -> bool:
    """The result is 1/x of a reverse-direction line although a forward line comes later in the file that day."""
    o, b, q = item["output"], item["input"]["base"], item["input"]["quote"]
    if o["number"] is None:
        return False
    lines = _lines_on(case, o["date"])
    rev = [(n, x) for n, lb, lq, x in lines if (lb, lq) == (q, b) and x != 0 and Decimal(1) / x == Decimal(o["number"])]
    fwd = [n for n, lb, lq, _ in lines if (lb, lq) == (b, q)]
    return bool(rev and fwd and max(fwd) > max(n for n, _ in rev))


def _inverted_twice(case: dict[str, Any], item: dict[str, Any]) -> bool:
    """The result equals neither a written line of that day nor its inverse: the point went through 1/(1/x)."""
    o, b, q = item["output"], item["input"]["base"], item["input"]["quote"]
    if o["number"] is None:
        return False
    got = Decimal(o["number"])
    lines = _lines_on(case, o["date"])
    fwd = [x for _, lb, lq, x in lines if (lb, lq) == (b, q)]
    rev = [x for _, lb, lq, x in lines if (lb, lq) == (q, b)]
    return bool(fwd) and got not in fwd and all(x == 0 or Decimal(1) / x != got for x in rev)


def _has_plugin(case: dict[str, Any]) -> bool:
    return 'plugin "beancount.plugins.implicit_prices"' in case["input"]["ledger"]


def _accounts_in(ledger: str) -> set[str]:
    return set(re.findall(r"^\s+([A-Z][^\s]*:[^\s]+)\s", ledger, flags=re.M))


def _booking_method(case: dict[str, Any]) -> str:
    """The method Beancount uses for the reducing posting's account: its open line, else the option, else STRICT."""
    ledger = case["input"]["ledger"]
    account = next(p["account"] for p in _wp(case) if p["cost"] is not None)
    on_open = re.findall(rf'^\d{{4}}-\d\d-\d\d open {re.escape(account)} "([A-Z_]+)"', ledger, flags=re.M)
    option = re.findall(r'^option "booking_method" "([A-Z_]+)"', ledger, flags=re.M)
    return (on_open or option or ["STRICT"])[-1]


def _later_lot(case: dict[str, Any]) -> bool:
    return any(d > TXN_DATE for d in re.findall(r'^(\d{4}-\d\d-\d\d) \*', case["input"]["ledger"], flags=re.M))


def _uses_method(method: str) -> Callable[[dict[str, Any], dict[str, Any]], bool]:
    return lambda c, i: _booking_method(c) == method


Pred = Callable[[dict[str, Any], dict[str, Any]], bool]
COVERAGE: dict[str, dict[str, Pred]] = {
    "tolerance": {
        "an elided leg: indeterminate, filled by Beancount": lambda c, i: (
            i["output"]["port"] == "indeterminate" and bool(i["output"]["interpolated"])
            and any(p["units"] == LEFT_OUT for p in _wp(c))),
        "a {} reduction: indeterminate": lambda c, i: i["output"]["port"] == "indeterminate" and any(
            _cost_shape(p) == ("MISSING", None, "MISSING", None) for p in _wp(c)),
        "a {USD} reduction: indeterminate": lambda c, i: i["output"]["port"] == "indeterminate" and any(
            _cost_shape(p) == ("MISSING", None, "USD", None) for p in _wp(c)),
        "a {date} reduction: indeterminate": lambda c, i: i["output"]["port"] == "indeterminate" and any(
            _cost_shape(p)[:3] == ("MISSING", None, "MISSING") and _cost_shape(p)[3] is not None for p in _wp(c)),
        "a cost with no currency: indeterminate": lambda c, i: i["output"]["port"] == "indeterminate" and any(
            _cost_shape(p)[2] == "MISSING" and _cost_shape(p)[0] not in (None, "MISSING") for p in _wp(c)),
        "a cost with only a total: indeterminate": lambda c, i: i["output"]["port"] == "indeterminate" and any(
            _cost_shape(p)[0] == "MISSING" and _cost_shape(p)[1] not in (None, "MISSING") for p in _wp(c)),
        "a refused entry has no port verdict": lambda c, i: i["output"]["port"] == "not-pinned",
        "a negative price is served as written": lambda c, i: any(
            p["price"] is not None and p["price"]["number"].startswith("-") for p in _wp(c)),
        "a residual equal to the tolerance balances": lambda c, i: _at_boundary(i["output"]),
        "an unbalanced transaction": lambda c, i: i["output"]["balanced"] is False,
        "integers infer no tolerance": lambda c, i: _zero_tol_fail(i["output"]),
        "tolerance_multiplier option": lambda c, i: (
            c["input"]["tolerance_options"]["tolerance_multiplier"] != "0.5"),
        "inferred_tolerance_default for one currency": lambda c, i: any(
            k != "*" for k in c["input"]["tolerance_options"]["inferred_tolerance_default"]),
        "inferred_tolerance_default *": lambda c, i: (
            "*" in c["input"]["tolerance_options"]["inferred_tolerance_default"]),
        "infer_tolerance_from_cost": lambda c, i: c["input"]["tolerance_options"]["infer_tolerance_from_cost"],
        "a @@ total price": lambda c, i: any(p["price"] is not None and p["price"]["total"] for p in _wp(c)),
        "a {{}} total cost": lambda c, i: any(
            p["cost"] is not None and p["cost"]["total"] not in (None, "MISSING")
            and p["cost"]["per"] != "MISSING" for p in _wp(c)),
        "cost and price on one posting": lambda c, i: any(p["cost"] and p["price"] for p in _wp(c)),
        "Beancount drops the transaction": lambda c, i: i["output"]["dropped"] is True,
    },
    "balance": {
        "an explicit ~ tolerance": lambda c, i: "~" in c["input"]["ledger"] and verdict("balance", i) == "positive",
        "an integer assertion has zero tolerance": lambda c, i: i["output"]["tolerance"] == "0" and any(
            e.startswith("Balance failed") for e in i["output"]["errors"]),
        "a difference equal to the tolerance passes": lambda c, i: (
            verdict("balance", i) == "positive" and _d(i["output"]["difference"]) != 0
            and abs(_d(i["output"]["difference"])) == _d(i["output"]["tolerance"])),
        "a transaction on the assertion's date is not counted": lambda c, i: (
            re.search(rf"^{TXN_DATE} \*", c["input"]["ledger"], flags=re.M) is not None),
        "a parent account sums its children": lambda c, i: verdict("balance", i) == "positive" and any(
            a.startswith(c["input"]["account"] + ":") for a in _accounts_in(c["input"]["ledger"])),
        "tolerance_multiplier option": lambda c, i: any(k == "tolerance_multiplier" for k, _ in c["input"]["options"]),
    },
    "account": {
        "Assets:Retirement:401k is valid": lambda c, i: (
            i["input"]["name"] == "Assets:Retirement:401k" and i["output"]["valid"]),
        "Café is valid": lambda c, i: "Café" in i["input"]["name"] and i["output"]["valid"],
        "日本 is valid": lambda c, i: "日本" in i["input"]["name"] and i["output"]["valid"],
        "an underscore is invalid": lambda c, i: "_" in i["input"]["name"] and not i["output"]["valid"],
        "a custom root is valid with its option": lambda c, i: bool(c["input"]["options"]) and i["output"]["valid"]
        and i["input"]["name"].split(":")[0] not in ("Assets", "Liabilities", "Equity", "Income", "Expenses"),
        "a custom root is invalid without its option": lambda c, i: not c["input"]["options"]
        and not i["output"]["valid"] and i["output"]["errors"][:1] == [f"Invalid account name: {i['input']['name']}"]
        and i["input"]["name"].split(":")[0] not in ("Assets", "Liabilities", "Equity", "Income", "Expenses"),
        "valid although account.is_valid says no": lambda c, i: (
            i["output"]["valid"] and not i["output"]["core_is_valid"]),
        "more text after a name is invalid even when it parses": lambda c, i: (
            " " in i["input"]["name"] and not i["output"]["valid"] and i["output"]["errors"] == []),
        "invalid although account.is_valid says yes": lambda c, i: (
            not i["output"]["valid"] and i["output"]["core_is_valid"]),
    },
    "currency": {
        "a single capital letter is valid": lambda c, i: len(i["input"]["code"]) == 1 and i["output"]["valid"],
        "a slash-prefixed future is valid": lambda c, i: i["input"]["code"].startswith("/") and i["output"]["valid"],
        "a slash code without a letter is invalid": lambda c, i: (
            i["input"]["code"].startswith("/") and not i["output"]["valid"]),
        "a trailing special character is invalid": lambda c, i: (
            i["input"]["code"][-1:] in "._-'" and not i["output"]["valid"]),
        "more text after a code is invalid even when it parses": lambda c, i: (
            " " in i["input"]["code"] and not i["output"]["valid"] and i["output"]["commodity_errors"] == []),
        "invalid although CURRENCY_RE matches": lambda c, i: (
            not i["output"]["valid"] and i["output"]["currency_re_matches"]),
        "lower case is invalid": lambda c, i: (" " not in i["input"]["code"]
                                               and i["input"]["code"] != i["input"]["code"].upper()
                                               and not i["output"]["valid"]),
    },
    "price": {
        "a price on the query date counts": lambda c, i: (
            i["output"]["date"] is not None and i["output"]["date"] == i["input"]["date"]),
        "before the first price there is none": lambda c, i: i["output"]["number"] is None and any(
            d > i["input"]["date"] for d in _forward(c, i["input"]["base"], i["input"]["quote"])),
        "a later price is excluded for an earlier date": lambda c, i: (
            i["input"]["date"] != "latest" and i["output"]["number"] is not None and any(
                d > i["input"]["date"] for d in _forward(c, i["input"]["base"], i["input"]["quote"]))),
        "the latest price includes future dates": lambda c, i: (
            i["input"]["date"] == "latest" and i["output"]["date"] is not None and i["output"]["date"] > TXN_DATE),
        "an inverse lookup": lambda c, i: i["output"]["number"] is not None and not _forward(
            c, i["input"]["base"], i["input"]["quote"]) and bool(_forward(c, i["input"]["quote"], i["input"]["base"])),
        "the inverted point wins wherever it is in the file": lambda c, i: _inverted_wins_before_forward(c, i),
        "a point inverted twice": lambda c, i: _inverted_twice(c, i),
        "same-day duplicates": lambda c, i: (
            i["output"]["number"] is not None and len(_forward(c, i["input"]["base"], i["input"]["quote"]))
            != len(set(_forward(c, i["input"]["base"], i["input"]["quote"])))),
        "another quote currency gives none": lambda c, i: i["output"]["number"] is None and any(
            b == i["input"]["base"] and q != i["input"]["quote"] for _, b, q in _price_lines(c["input"]["ledger"])),
        "base equals quote": lambda c, i: i["input"]["base"] == i["input"]["quote"],
        "implicit prices with the plugin": lambda c, i: _has_plugin(c) and i["output"]["number"] is not None,
        "no implicit prices without the plugin": lambda c, i: (
            not _has_plugin(c) and not _price_lines(c["input"]["ledger"]) and i["output"]["number"] is None),
    },
    "precision": {
        "most common differs from maximum": lambda c, i: (
            i["output"]["most_common"] is not None and i["output"]["most_common"] != i["output"]["maximum"]),
        "integers count as 0 digits": lambda c, i: i["output"]["most_common"] == 0,
        "an unseen currency has none": lambda c, i: i["output"]["most_common"] is None,
    },
    "booking": {
        **{f"method {m}": _uses_method(m)
           for m in ("STRICT", "STRICT_WITH_SIZE", "NONE", "AVERAGE", "FIFO", "LIFO", "HIFO")},
        "STRICT refuses an ambiguous reduction": lambda c, i: (
            _booking_method(c) == "STRICT" and any(e.startswith("Ambiguous") for e in i["output"]["errors"])),
        "a lot dated after the sale is not used": lambda c, i: _later_lot(c) and verdict("booking", i) == "positive",
    },
}


# ---- the vectors file ----------------------------------------------------------------------------------------


def check_vectors(doc: dict[str, Any], cases: dict[str, Any]) -> list[str]:
    problems = []
    if doc.get("beancount") != PINNED:
        problems.append(f"vectors.json: written by Beancount {doc.get('beancount')!r}, the pin is {PINNED!r}")
    if beancount.__version__ != PINNED:
        problems.append(f"the oracle environment runs Beancount {beancount.__version__}, the pin is {PINNED!r}")
    if set(doc.get("families", {})) != set(FAMILIES):
        problems.append(f"vectors.json: families must be {sorted(FAMILIES)}")
        return problems
    for family in FAMILIES:
        vecs = doc["families"][family]
        if [v.get("id") for v in vecs] != [c.get("id") for c in cases.get(family, [])]:
            problems.append(f"{family}: the vectors do not match the cases (ids or order)")
        by_id = {c.get("id"): c for c in cases.get(family, [])}
        for v in vecs:
            c = by_id.get(v.get("id"), {})
            if family in ITEMS and len(v.get("items", [])) != len(c.get(ITEMS[family], [])):
                problems.append(f"{family} {v['id']}: the vector's items do not match the case's")
        seen = set()
        for case, item in units(doc, family):
            said = verdict(family, item)
            seen.add(item.get("polarity"))
            if item.get("polarity") != said:
                where = f"{family} {case['id']}" + (f" {item['input']}" if family in ITEMS else "")
                problems.append(f"{where}: declared {item.get('polarity')} but Beancount says {said}")
        for pol in POLARITIES:
            if pol not in seen:
                problems.append(f"{family}: the family has no {pol} vector")
        if family == "tolerance":
            for case in vecs:
                problems.extend(port_problems(case))
        if family == "balance":
            for case in vecs:
                problems.extend(balance_problems(case))
        for name, pred in COVERAGE[family].items():
            if not any(pred(case, item) for case, item in units(doc, family)):
                problems.append(f"{family}: coverage: no vector for {name!r}")
    return problems


def run_all(corpus: Path = CORPUS, fresh: bool = True, decisions: Path = DECISIONS) -> list[str]:
    cases = tomllib.loads((corpus / "cases.toml").read_text(encoding="utf-8"))
    problems = check_cases(cases, decisions)
    text = (corpus / "vectors.json").read_text(encoding="utf-8")
    problems += check_vectors(json.loads(text), cases)
    if fresh:
        import gen_domain_vectors

        try:
            if gen_domain_vectors.generate(corpus / "cases.toml") != text:
                problems.append("vectors.json differs from a fresh run of gen_domain_vectors.py; regenerate it "
                                "(a hand edit, a case changed without regenerating, or a Beancount change)")
        except gen_domain_vectors.VectorError as exc:
            problems.append(f"generator: {exc}")
    return problems
