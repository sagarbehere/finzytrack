"""Compare the legacy app's actual change with the intended change, and tag every divergence (ADR-0014).

Classes: I2 (glob-included entries copied into the root), I3 (plugin- or pad-generated entries persisted), I10
(elided amounts or `{}` baked in; comments lost), M28 (metadata contract: legacy machinery keys instead of v0.4's),
FIX-L3-02 (`@@` total price written as a rounded unit price; a lot's date or label dropped), FIX-L3-05 (the written
decimal scale changed, `10` → `10.00`), FIX-L3-15 (typed metadata written as strings).
An entry the legacy wrote differently is paired with the entry it should have been, and tagged with the smallest
set of classes whose erasers make the two equal ("I10+M28"). Anything else is UNEXPLAINED (an unexpected change)
or NOT_APPLIED (an intended change that did not happen); either blocks promotion until explained.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.number import MISSING
from beancount.core.position import CostSpec

from model import Expected
from semantic_diff import LedgerSet, canonical_key, diff

BLOCKING = frozenset({"UNEXPLAINED", "NOT_APPLIED"})
# ADR-0026: what the legacy app writes that v0.4 does not, and what v0.4 writes instead.
M28_KEYS = frozenset({"id", "content_hash", "source_account", "external_id", "external_id_type", "source_rule",
                      "status", "import-batch", "import-key", "txn-date", "original", "statement-amount"})
M28_RENAMES = {"interest_rate": "interest-rate", "maturity_date": "maturity-date", "interest_account": "interest-account",
               "dividend_of": "dividend-of", "fetch_symbol": "fetch-symbol", "issue_date": "issue-date"}
CENT = Decimal("0.01")

Pair = tuple[Any, Any]


def _map_meta(entry: Any, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
    entry = entry._replace(meta=fn(dict(entry.meta or {})))
    if isinstance(entry, data.Transaction):
        entry = entry._replace(postings=[p._replace(meta=fn(dict(p.meta or {})) or None) for p in entry.postings])
    return entry


def _m28(meta: dict[str, Any]) -> dict[str, Any]:
    return {M28_RENAMES.get(k, k): v for k, v in meta.items() if k not in M28_KEYS}


def erase_m28(x: Any, y: Any) -> Pair:
    x, y = _map_meta(x, _m28), _map_meta(y, _m28)
    if isinstance(x, data.Transaction) and isinstance(y, data.Transaction):
        # v0.4 writes `memo` only when it differs from the narration; the legacy app writes it whenever given.
        drop = lambda e: _map_meta(e, lambda m: {k: v for k, v in m.items() if not (k == "memo" and v == e.narration)})  # noqa: E731
        x, y = drop(x), drop(y)
    return x, y


def _stringify(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if k in ("filename", "lineno") else str(v)) for k, v in meta.items()}


def erase_l3_15(x: Any, y: Any) -> Pair:
    return _map_meta(x, _stringify), _map_meta(y, _stringify)


def _postings_pairwise(x: Any, y: Any, fn: Callable[[Any, Any], Pair]) -> Pair:
    if not (isinstance(x, data.Transaction) and isinstance(y, data.Transaction)) or len(x.postings) != len(y.postings):
        return x, y
    pairs = [fn(px, py) if px.account == py.account else (px, py) for px, py in zip(x.postings, y.postings)]
    return x._replace(postings=[p for p, _ in pairs]), y._replace(postings=[q for _, q in pairs])


def _total(p: Any) -> Any:
    if p.price is None or p.price is MISSING or p.units is MISSING or p.units.number is MISSING \
            or p.price.number is MISSING:
        return p
    return p._replace(price=Amount((p.units.number * p.price.number).quantize(CENT), p.price.currency))


def _keep_lot(px: Any, py: Any) -> Pair:
    """The legacy edit payload drops a lot's date and label (and a cost with no date entirely)."""
    if isinstance(px.cost, CostSpec) and px.cost != py.cost and (
            py.cost is None or all(_unset(b) or a == b for a, b in zip(px.cost, py.cost))):
        py = py._replace(cost=px.cost)
    return _total(px), _total(py)


def erase_l3_02(x: Any, y: Any) -> Pair:
    return _postings_pairwise(x, y, _keep_lot)


def _same_number(a: Any, b: Any) -> bool:
    return isinstance(a, Decimal) and isinstance(b, Decimal) and a == b


def _scale(px: Any, py: Any) -> Pair:
    """Numerically equal amounts written at another scale (`10` → `10.00`)."""
    for field_ in ("units", "price"):
        a, b = getattr(px, field_), getattr(py, field_)
        if not (_unset(a) or _unset(b)) and _same_number(a.number, b.number) and a.currency == b.currency:
            py = py._replace(**{field_: a})
    return px, py


def erase_l3_05(x: Any, y: Any) -> Pair:
    return _postings_pairwise(x, y, _scale)


def _unset(v: Any) -> bool:
    return v is MISSING or v is None


def _bake(px: Any, py: Any) -> Pair:
    """Where the intended posting left something for Beancount to fill (elided units, `{}`), accept what the
    legacy app baked in."""
    if px.units is MISSING or (px.units is not None and px.units.number is MISSING):
        py = py._replace(units=px.units)
    if isinstance(px.cost, CostSpec) and isinstance(py.cost, CostSpec) and px.cost != py.cost:
        if all(_unset(a) or a == b for a, b in zip(px.cost, py.cost)):
            py = py._replace(cost=px.cost)
    return px, py


def erase_i10(x: Any, y: Any) -> Pair:
    return _postings_pairwise(x, y, _bake)


ERASERS: dict[str, Callable[[Any, Any], Pair]] = {
    "I10": erase_i10, "M28": erase_m28, "FIX-L3-02": erase_l3_02, "FIX-L3-05": erase_l3_05, "FIX-L3-15": erase_l3_15,
}


def _same_slot(x: Any, y: Any) -> bool:
    return type(x) is type(y) and x.date == y.date


def explain(x: Any, y: Any) -> str | None:
    """The smallest set of classes whose erasers make intended entry *x* and written entry *y* equal, or None."""
    if not _same_slot(x, y):
        return None
    for size in range(1, len(ERASERS) + 1):
        for combo in itertools.combinations(ERASERS, size):
            a, b = x, y
            for name in combo:
                a, b = ERASERS[name](a, b)
            if canonical_key(a) == canonical_key(b):
                return "+".join(sorted(combo))
    return None


@dataclass(frozen=True)
class Divergence:
    cls: str
    file: str
    intended: str | None  # canonical key of the entry that should be there (or was there), if any
    written: str | None  # canonical key of the entry the legacy app wrote, if any
    note: str = ""


@dataclass
class Report:
    divergences: list[Divergence]

    @property
    def verdict(self) -> str:
        if not self.divergences:
            return "MATCH"
        return "UNEXPLAINED" if any(d.cls in BLOCKING for d in self.divergences) else "EXPECTED_DIVERGENCE"

    def class_counts(self) -> Counter[str]:
        return Counter(d.cls for d in self.divergences)


def _take(counter: Counter[str], key: str) -> bool:
    if counter[key] > 0:
        counter[key] -= 1
        return True
    return False


def _duplicate_of_glob_entry(before: LedgerSet, after: LedgerSet, k: str, y: Any) -> tuple[str, str] | None:
    """(key, extra classes) of the entry a glob-included file still holds that root entry *y* duplicates."""
    held = [(c, e) for g in sorted(before.glob_files) if g in after.files for c, e in after.files[g].by_key.items()]
    if any(c == k for c, _ in held):
        return k, ""
    for c, e in held:
        if (cls := explain(e, y)) is not None:
            return c, cls
    return None


def compare(before: LedgerSet, after: LedgerSet, expected: Expected, generated: frozenset[str]) -> Report:
    actual = diff(before, after)
    files = sorted(set(actual.files) | set(expected.files))
    u_rem: dict[str, Counter[str]] = {}
    u_add: dict[str, Counter[str]] = {}
    n_rem: dict[str, Counter[str]] = {}
    n_add: dict[str, Counter[str]] = {}
    for f in files:
        a, e = actual.files.get(f), expected.files.get(f)
        a_rem, a_add = (a.removed, a.added) if a else (Counter(), Counter())
        e_rem, e_add = (e.removed, e.added) if e else (Counter(), Counter())
        u_rem[f], u_add[f], n_rem[f], n_add[f] = a_rem - e_rem, a_add - e_add, e_rem - a_rem, e_add - a_add

    def entry(key: str) -> Any:
        if key in expected.entries:
            return expected.entries[key]
        for s in (after, before):
            try:
                return s.entry(key)
            except KeyError:
                pass
        raise KeyError(key)

    out: list[Divergence] = []
    for f in files:  # entries the legacy app wrote that were not intended
        for k in list(u_add[f].elements()):
            if not _take(u_add[f], k):
                continue
            moved_from = next((g for g in files if g != f and u_rem[g][k] > 0), None)
            if moved_from is not None:
                _take(u_rem[moved_from], k)
                glob_to_root = moved_from in before.glob_files and f == before.root
                out.append(Divergence("I2" if glob_to_root else "UNEXPLAINED", f, k, k, f"moved from {moved_from}"))
                continue
            if k in generated:
                out.append(Divergence("I3", f, None, k, "generated by a plugin or a pad"))
                continue
            to_root = f == before.root
            y = entry(k)
            dup = _duplicate_of_glob_entry(before, after, k, y) if to_root else None
            if dup is not None:
                cand, cls = dup
                out.append(Divergence("+".join(sorted({"I2", *filter(None, cls.split("+"))})), f, cand, k,
                                      "a glob-included entry duplicated into the root"))
                continue
            # Where the written entry may come from: an intended entry of this file, an untargeted entry of this
            # file, or (I2) an intended entry of a glob-included file that the legacy app wrote into the root.
            pools = [(n_add[f], None), (u_rem[f], None)]
            if to_root:
                pools += [(n_add[g], "I2") for g in sorted(before.glob_files) if g in n_add]
            match = None
            for pool, tag in pools:
                for cand in list(pool):
                    if pool[cand] <= 0:
                        continue
                    cls = "" if cand == k else explain(entry(cand), y)
                    if cls is not None and (cls or tag):
                        match = (pool, cand, "+".join(sorted(filter(None, {tag, *cls.split("+")}))))
                        break
                if match:
                    break
            if match:
                pool, cand, cls = match
                _take(pool, cand)
                out.append(Divergence(cls, f, cand, k))
                continue
            was = next((c for c in u_rem[f] if u_rem[f][c] > 0 and _same_slot(entry(c), y)), None)
            if was is not None:
                _take(u_rem[f], was)
                out.append(Divergence("UNEXPLAINED", f, was, k, "an entry that was not targeted changed"))
            else:
                out.append(Divergence("UNEXPLAINED", f, None, k, "written but not intended"))
    for f in files:  # what is left over
        out += [Divergence("UNEXPLAINED", f, k, None, "removed but not intended") for k in u_rem[f].elements()]
        # I2: the legacy app never rewrites a glob-included file, so an intended change inside one is not applied.
        unwritten_glob = f in before.glob_files and f not in actual.files
        cls, why = ("I2", "glob-included file not rewritten") if unwritten_glob else ("NOT_APPLIED", "")
        out += [Divergence(cls, f, k, None, why or "intended removal did not happen") for k in n_rem[f].elements()]
        out += [Divergence(cls, f, k, None, why or "intended entry was not written") for k in n_add[f].elements()]
    for f in actual.header_changed:
        out.append(Divergence("UNEXPLAINED", f, None, None, "options, plugins or includes changed"))
    for f, n in actual.comments_lost.items():
        out.append(Divergence("I10", f, None, None, f"{n} comment line(s) lost"))
    return Report(out)


def _priced(entries: list[Any]) -> bool:
    return any(p.cost is not None or p.price is not None for e in entries for p in getattr(e, "postings", ()))


def refused(before: LedgerSet, after: LedgerSet, expected: Expected, generated: frozenset[str], mutator: str,
            code: str | None) -> Report:
    """The legacy app refused the intent. One divergence for the refusal (FIX-L3-02 when its validator refuses an
    edit with a price or cost, since it ignores their weights), plus anything that changed anyway."""
    targets = [before.entry(k) for f in expected.files.values() for k in f.removed]
    if mutator == "update_transaction" and code == "TRANSACTION_INVALID" and _priced(targets + list(expected.entries.values())):
        first = Divergence("FIX-L3-02", before.root, None, None, "refused: the legacy validator ignores price and cost weights")
    else:
        first = Divergence("NOT_APPLIED", before.root, None, None, f"the legacy app refused ({code})")
    return Report([first] + compare(before, after, Expected(), generated).divergences)
