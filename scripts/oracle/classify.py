"""Compare the legacy app's actual change with the intended change, and tag every divergence (ADR-0014).

Classes: I2 (glob-included entries copied into the root), I3 (plugin- or pad-generated entries persisted), I10
(elided amounts or `{}` baked in as Beancount booked them; comments lost), M28 (metadata contract: legacy machinery
keys instead of v0.4's, on entries the mutator creates), FIX-L3-02 (a `@@` total written as exactly the legacy's rounded
unit price; a lot's label dropped), FIX-L3-05 (the written decimal scale changed,
`10` → `10.00`), FIX-L3-15 (typed metadata written as strings).

An entry the legacy wrote differently is paired with the entry it should have been, and tagged with the smallest set
of classes whose erasers make the two equal ("I10+M28"). Each pairing allows only the erasers that can apply to it:
an untargeted entry may differ only by what booking filled in (checked against Beancount's own booking); M28 applies
only to entries the mutator creates. A new Beancount error after the write is never expected. Anything else is
UNEXPLAINED (an unexpected change) or NOT_APPLIED (an intended change that did not happen); either blocks promotion.

The parsed comparison cannot see a change of money that leaves every entry as written: Beancount sorts entries by
date, kind and line number, across files, so a reprint that moves lines can make a reduction take another lot and
change a realized gain (S0122 verifier F1). `booked` (BL-0275) therefore compares Beancount's booked result of the
intended ledger with the legacy's: the lots each reduction took, the amounts booking filled in, generated entries,
and each account's end balance per currency. Its rules for each class are in its docstring.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from beancount import loader
from beancount.core import data
from beancount.core.number import MISSING
from beancount.core.position import Cost, CostSpec
from beancount.parser import printer

from model import Expected, intended_entries
from semantic_diff import LedgerSet, LoadFacts, at_kind, book, booked_of, canonical_key, diff, strip_location

BLOCKING = frozenset({"UNEXPLAINED", "NOT_APPLIED"})
# ADR-0026: what the legacy app writes that v0.4 does not, and what v0.4 writes instead.
LEGACY_KEYS = frozenset({"id", "content_hash", "source_account", "external_id", "external_id_type", "source_rule",
                         "status", "import-batch"})
V04_KEYS = frozenset({"import-key", "txn-date", "original", "statement-amount"})
M28_RENAMES = {"interest_rate": "interest-rate", "maturity_date": "maturity-date", "interest_account": "interest-account",
               "dividend_of": "dividend-of", "fetch_symbol": "fetch-symbol", "issue_date": "issue-date"}


def legacy_unit_price(units: Decimal, total: Decimal) -> Decimal:
    """The unit price the legacy commit endpoint writes for `units @@ total`: the fewest decimal places (2 to 28) with
    |units × price − total| < 0.005, else the full quotient (legacy app/api/routers/importer/transaction.py). Used only
    to recognise that rule's output, never as a tolerance of ours (INV-02)."""
    units = abs(units)
    raw = total / units
    for places in range(2, 29):
        q = raw.quantize(Decimal(10) ** -places)
        if abs(units * q - total) < Decimal("0.005"):
            return q
    return raw


@dataclass(frozen=True)
class Ctx:
    """What an eraser may know about the intended entry *x*: its booked form and its `@@` postings."""
    booked: Any | None = None
    totals: dict[int, Decimal] = field(default_factory=dict)  # posting index → its `@@` total as written


Pair = tuple[Any, Any]


def _unset(v: Any) -> bool:  # identity only: Beancount's Amount.__eq__ fails on None and MISSING
    return v is None or v is MISSING


def _map_meta(entry: Any, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
    entry = entry._replace(meta=fn(dict(entry.meta or {})))
    if isinstance(entry, data.Transaction):
        entry = entry._replace(postings=[p._replace(meta=fn(dict(p.meta or {})) or None) for p in entry.postings])
    return entry


def _all_keys(entry: Any) -> set[str]:
    keys = set(entry.meta or {})
    for p in getattr(entry, "postings", ()):
        keys |= set(p.meta or {})
    return keys


def erase_m28(x: Any, y: Any, ctx: Ctx) -> Pair:
    """v0.4 keys the legacy app did not write are dropped from *x*; legacy machinery keys v0.4 does not write are
    dropped from *y*; the renamed vocabulary is compared under its new name. A key both sides carry stays compared."""
    on_x, on_y = _all_keys(x), _all_keys(y)
    x = _map_meta(x, lambda m: {k: v for k, v in m.items() if not (k in V04_KEYS and k not in on_y)})
    y = _map_meta(y, lambda m: {M28_RENAMES.get(k, k): v for k, v in m.items() if not (k in LEGACY_KEYS and k not in on_x)})
    if isinstance(x, data.Transaction) and isinstance(y, data.Transaction):
        # v0.4 writes `memo` only when it differs from the narration; the legacy app writes it whenever given.
        y = _map_meta(y, lambda m: {k: v for k, v in m.items() if not (k == "memo" and v == y.narration and "memo" not in (x.meta or {}))})
    return x, y


def _stringify(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if k in ("filename", "lineno") else str(v)) for k, v in meta.items()}


def erase_l3_15(x: Any, y: Any, ctx: Ctx) -> Pair:
    return _map_meta(x, _stringify), _map_meta(y, _stringify)


def _postings_pairwise(x: Any, y: Any, fn: Callable[[int, Any, Any], Pair]) -> Pair:
    if not (isinstance(x, data.Transaction) and isinstance(y, data.Transaction)) or len(x.postings) != len(y.postings):
        return x, y
    pairs = [fn(i, px, py) if px.account == py.account else (px, py) for i, (px, py) in enumerate(zip(x.postings, y.postings))]
    return x._replace(postings=[p for p, _ in pairs]), y._replace(postings=[q for _, q in pairs])


def erase_l3_02(x: Any, y: Any, ctx: Ctx) -> Pair:
    def fix(i: int, px: Any, py: Any) -> Pair:
        if i in ctx.totals and not (_unset(px.price) or _unset(py.price) or _unset(px.units) or _unset(py.units)) \
                and px.units == py.units and px.price.currency == py.price.currency \
                and py.price.number == legacy_unit_price(px.units.number, ctx.totals[i]):
            py = py._replace(price=px.price)
        if isinstance(px.cost, CostSpec) and isinstance(py.cost, CostSpec) and px.cost.label is not None \
                and py.cost == px.cost._replace(label=None):
            py = py._replace(cost=px.cost)  # the legacy edit payload carries no lot label; everything else kept
        return px, py
    return _postings_pairwise(x, y, fix)


def erase_l3_05(x: Any, y: Any, ctx: Ctx) -> Pair:
    """Numerically equal amounts written at another scale (`10` → `10.00`)."""
    def fix(i: int, px: Any, py: Any) -> Pair:
        for name in ("units", "price"):
            a, b = getattr(px, name), getattr(py, name)
            if not (_unset(a) or _unset(b)) and isinstance(a.number, Decimal) and isinstance(b.number, Decimal) \
                    and a.number == b.number and a.currency == b.currency:
                py = py._replace(**{name: a})
        return px, py
    return _postings_pairwise(x, y, fix)


def _as_spec(cost: Cost) -> CostSpec:
    return CostSpec(cost.number, None, cost.currency, cost.date, cost.label, False)


def erase_i10(x: Any, y: Any, ctx: Ctx) -> Pair:
    """Where *x* left something for Beancount to fill (elided units, `{}`), accept what the legacy app wrote only if
    it is what Beancount booked. Without a booked form (an intended new entry), only elided units may be filled; the
    new-error check then catches a wrong number."""
    if not isinstance(x, data.Transaction):
        return x, y
    booked = ctx.booked if isinstance(ctx.booked, data.Transaction) and len(ctx.booked.postings) == len(x.postings) else None

    def fix(i: int, px: Any, py: Any) -> Pair:
        pb = booked.postings[i] if booked is not None else None
        if px.units is MISSING or (not _unset(px.units) and px.units.number is MISSING):
            if pb is None or (not _unset(pb.units) and not _unset(py.units) and pb.units == py.units):
                py = py._replace(units=px.units)
        if isinstance(px.cost, CostSpec) and px.cost != py.cost and isinstance(py.cost, CostSpec) \
                and pb is not None and isinstance(pb.cost, Cost) and _as_spec(pb.cost) == py.cost:
            py = py._replace(cost=px.cost)
        return px, py
    return _postings_pairwise(x, y, fix)


ERASERS: dict[str, Callable[[Any, Any, Ctx], Pair]] = {
    "I10": erase_i10, "M28": erase_m28, "FIX-L3-02": erase_l3_02, "FIX-L3-05": erase_l3_05, "FIX-L3-15": erase_l3_15,
}
UNTARGETED = ("I10",)  # a full rewrite may only re-print what booking filled in
REPLACED = ("I10", "FIX-L3-02", "FIX-L3-05", "FIX-L3-15")  # an entry the legacy payload carried
CREATED = REPLACED + ("M28",)  # an entry the mutator creates
OWN_MONEY = frozenset({"FIX-L3-02"})  # classes whose cause changes the entry's own booked money (`booked`)


def _same_slot(x: Any, y: Any) -> bool:
    return type(x) is type(y) and x.date == y.date


def explain(x: Any, y: Any, allowed: tuple[str, ...] = tuple(ERASERS), ctx: Ctx = Ctx()) -> str | None:
    """The smallest set of allowed classes whose erasers make intended entry *x* and written entry *y* equal."""
    if not _same_slot(x, y):
        return None
    for size in range(1, len(allowed) + 1):
        for combo in itertools.combinations(allowed, size):
            a, b = x, y
            for name in combo:
                a, b = ERASERS[name](a, b, ctx)
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
    check: str = "parsed"  # "parsed" (the entries as written), "booked" (an entry's booked money) or "balance"


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


def _tag(*parts: str | None) -> str:
    return "+".join(sorted({c for p in parts if p for c in p.split("+")}))


def new_errors(before: LoadFacts, after: LoadFacts, root: str) -> list[Divergence]:
    """Beancount errors after the write that were not there before: never an expected divergence."""
    return [Divergence("UNEXPLAINED", root, None, None, f"new Beancount error: {k.split('|', 1)[0]}")
            for k in (after.errors - before.errors).elements()]


def compare(before: LedgerSet, after: LedgerSet, expected: Expected, before_facts: LoadFacts,
            after_facts: LoadFacts) -> Report:
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
        """An intended entry, else the source's instance, else the written one: the booked form of an untargeted
        entry is looked up by the source's file and line, so the source instance must win over the copy's."""
        if key in expected.entries:
            return expected.entries[key]
        for s in (before, after):
            try:
                return s.entry(key)
            except KeyError:
                pass
        raise KeyError(key)

    def intended_ctx(key: str) -> tuple[tuple[str, ...], Ctx]:
        allowed = CREATED if key in expected.created else REPLACED
        return allowed, Ctx(None, expected.totals.get(key, {}))

    def untargeted_ctx(key: str, facts: LoadFacts) -> tuple[tuple[str, ...], Ctx]:
        return UNTARGETED, Ctx(booked_of(facts, entry(key)))

    held_glob = [(c, e) for g in sorted(before.glob_files) if g in after.files for c, e in after.files[g].by_key.items()]
    out: list[Divergence] = []
    pairs: list[tuple[str, str]] = []  # (intended, written): one entry, written differently with a class
    standins: Counter[str] = Counter()  # persisted generated entries (I3)
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
            if k in before_facts.generated:
                out.append(Divergence("I3", f, None, k, "generated by a plugin or a pad"))
                standins[k] += 1
                continue
            y = after.entry(k)
            to_root = f == before.root
            if to_root:  # I2: a glob-included entry duplicated into the root (the glob file still holds it)
                dup = next(((c, "") for c, _ in held_glob if c == k), None) or next(
                    ((c, cls) for c, e in held_glob
                     if (cls := explain(e, y, UNTARGETED, Ctx(booked_of(after_facts, e)))) is not None), None)
                if dup is not None:
                    out.append(Divergence(_tag("I2", dup[1]), f, dup[0], k, "a glob-included entry duplicated into the root"))
                    continue
            # Where the written entry may come from: an intended entry of this file, an untargeted entry of this
            # file, or (I2) an intended entry of a glob-included file that the legacy app wrote into the root.
            pools: list[tuple[Counter[str], str | None, Callable[[str], tuple[tuple[str, ...], Ctx]]]] = [
                (n_add[f], None, intended_ctx), (u_rem[f], None, lambda c: untargeted_ctx(c, before_facts))]
            if to_root:
                pools += [(n_add[g], "I2", intended_ctx) for g in sorted(before.glob_files) if g in n_add]
            match = None
            for pool, tag, ctx_of in pools:
                for cand in [c for c in pool if pool[c] > 0]:
                    allowed, ctx = ctx_of(cand)
                    cls = "" if cand == k else explain(entry(cand), y, allowed, ctx)
                    if cls is not None and (cls or tag):
                        match = (pool, cand, _tag(tag, cls))
                        break
                if match:
                    break
            if match:
                pool, cand, cls = match
                _take(pool, cand)
                out.append(Divergence(cls, f, cand, k))
                if cand != k and not OWN_MONEY & set(cls.split("+")):
                    pairs.append((cand, k))
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
    out += new_errors(before_facts, after_facts, before.root)
    return Report(out + booked(before, expected, before_facts, after_facts, pairs, standins))


# --- the booked comparison (BL-0275) ------------------------------------------------------------------------------

def _num(n: Any) -> str:
    return format(n.normalize(), "f") if isinstance(n, Decimal) and n != 0 else ("0" if isinstance(n, Decimal) else repr(n))


def _amount(a: Any) -> tuple[str, str]:
    return ("", "") if _unset(a) else (_num(a.number), str(a.currency))


def _lot(c: Any) -> tuple[str, ...] | None:
    return None if c is None else (_num(c.number), str(c.currency), str(c.date), str(c.label))


Money = tuple[Any, tuple[tuple[Any, ...], ...]]


def money(entry: Any) -> Money:
    """What a booked transaction does to the money: its date and, per posting, the account, the units and the lot
    it holds or reduces (number, currency, date, label). Amounts compare by value (`10` = `10.00`). Prices, flags,
    text and metadata are left to the parsed comparison."""
    return entry.date, tuple(sorted(((p.account, *_amount(p.units), _lot(p.cost)) for p in entry.postings), key=repr))


def _monies(entries: Any, keys_at: dict[tuple[str, str, int], str], keep: Counter[str]) -> Counter[Money]:
    """The money of every booked transaction: a generated one always (no file holds it), a parsed one while its
    key has room in *keep*."""
    room, seen, out = Counter(keep), set(), Counter()
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        loc = at_kind(e)
        k = keys_at.get(loc) if loc not in seen else None
        seen.add(loc)
        if k is not None:
            if room[k] <= 0:
                continue
            room[k] -= 1
        out[money(e)] += 1
    return out


def _balances(monies: Counter[Money]) -> Counter[tuple[str, str]]:
    out: Counter[tuple[str, str]] = Counter()
    for (_, postings), n in monies.items():
        for account, number, currency, _ in postings:
            if currency and number[:1] in "-0123456789":  # an amount booking could not fill has no number
                out[(account, currency)] += Decimal(number) * n
    return out


def _show(m: Money) -> str:
    def lot(c: tuple[str, ...] | None) -> str:
        return "" if c is None else " {" + f"{c[0]} {c[1]}, {c[2]}" + ("" if c[3] == "None" else f', "{c[3]}"') + "}"
    date, postings = m
    return f"{date} " + "; ".join(f"{a} {n} {c}{lot(cost)}" for a, n, c, cost in postings)


def booked(before: LedgerSet, expected: Expected, before_facts: LoadFacts, after_facts: LoadFacts,
           pairs: list[tuple[str, str]], standins: Counter[str]) -> list[Divergence]:
    """Beancount's booked result of the intended ledger against the legacy's (INV-02: its booking, not ours).

    The reference is the intended ledger booked by Beancount (`model.intended_entries`, then `semantic_diff.book`,
    the loader's own steps): untargeted entries at their lines, a replacement at its target's, an import at the end
    of its file. Both sides keep the entries the parsed comparison holds to be the same: an entry with the same key
    on both sides, and each pair it tagged with a class (an intended or untargeted entry and the legacy's version of
    it, e.g. I10 or M28). Each such entry must book the same: the same lots taken (cost number, currency, date,
    label) and the same amounts, including what booking fills in, such as a realized gain; generated entries (pads,
    plugins) must come out the same too. Every difference is UNEXPLAINED, one per entry (`check` "booked"), and one
    per account and currency whose end balance differs (`check` "balance", the same money summed per account).

    Classes and booked differences, case by case: an entry the parsed comparison reports on one side only is left
    out, since its own money is that report's (a removed, unintended or not-applied entry, blocking already; I2's
    duplicate of a glob-included entry, whose doubled postings are that class's cause). So is a pair tagged
    FIX-L3-02 (`OWN_MONEY`): its cause changes that entry's own booked money (a lot's date or label dropped, a `@@`
    total rounded to a unit price, which moves an amount booking fills in); limit: another change to that one
    entry's booked money is not seen, its effect on any other entry is. An entry I3 tagged (a generated entry the
    legacy persisted) counts in place of the one the load would generate: it explains a booked difference only when
    the load no longer generates that entry. No class explains a difference on any other entry: a reduction that
    takes another lot, or a pad that fills another amount, because of an expected divergence elsewhere is
    UNEXPLAINED. I10 explains nothing here: a baked lot books the same, or the difference is a change of meaning."""
    if before_facts.parsed is None:
        raise ValueError("the booked comparison needs the source's parse (load_facts(keep_parsed=True))")
    intended = intended_entries(before_facts.parsed, before, expected)
    ref_keys = {at_kind(e): canonical_key(e) for e in intended}
    reference, _ = book(intended, before_facts.parsed)
    common = Counter(ref_keys.values()) & Counter(after_facts.keys_at.values())
    keep_ref = common + Counter(i for i, _ in pairs)
    keep_new = common + Counter(w for _, w in pairs) + standins
    ref = _monies(reference, ref_keys, keep_ref)
    new = _monies(after_facts.booked, after_facts.keys_at, keep_new)
    only_ref, only_new = list((ref - new).elements()), list((new - ref).elements())
    out = []
    for r in only_ref:
        accounts = {p[0] for p in r[1]}  # its twin, for the note: the same date and an account in common
        twin = next((n for n in only_new if n[0] == r[0] and accounts & {p[0] for p in n[1]}), None)
        if twin is not None:
            only_new.remove(twin)
            what = "took another lot" if [p[:3] for p in twin[1]] == [p[:3] for p in r[1]] else "booked other amounts"
            out.append(Divergence("UNEXPLAINED", before.root, None, None,
                                  f"booked: an entry {what}: {_show(r)} -> {_show(twin)}", "booked"))
        else:
            out.append(Divergence("UNEXPLAINED", before.root, None, None, f"booked: missing: {_show(r)}", "booked"))
    out += [Divergence("UNEXPLAINED", before.root, None, None, f"booked: not intended: {_show(n)}", "booked")
            for n in only_new]
    b_ref, b_new = _balances(ref), _balances(new)
    for account, currency in sorted(set(b_ref) | set(b_new)):
        x, y = b_ref[(account, currency)], b_new[(account, currency)]
        if x != y:
            out.append(Divergence("UNEXPLAINED", before.root, None, None,
                                  f"balance: {account} {currency}: {_num(x)} -> {_num(y)}", "balance"))
    return out


def balances(entries: list[Any], before: LedgerSet) -> bool:
    """Whether Beancount itself accepts the entries next to the ledger's own `open` lines for their accounts
    (currency constraints and booking methods included): its weights and tolerances, not ours (INV-02). An account
    the ledger never opens, or a reduction with no lots to reduce, counts as not accepted (the safe side)."""
    accounts = {p.account for e in entries for p in getattr(e, "postings", ())}
    opens = [e for f in before.files.values() for e in f.by_key.values() if isinstance(e, data.Open) and e.account in accounts]
    text = "\n".join(printer.format_entry(strip_location(e)) for e in opens + list(entries))
    _, errors, _ = loader.load_string(text)
    return not errors


def _priced(entries: list[Any]) -> bool:
    return any(p.cost is not None or p.price is not None for e in entries for p in getattr(e, "postings", ()))


def refused(before: LedgerSet, after: LedgerSet, expected: Expected, before_facts: LoadFacts, after_facts: LoadFacts,
            mutator: str, code: str | None) -> Report:
    """The legacy app refused the intent. One divergence for the refusal, plus anything that changed anyway.
    FIX-L3-02 only when the legacy validator refuses a priced or cost edit that Beancount itself accepts (the legacy
    validator sums units and ignores price and cost weights)."""
    targets = [before.entry(k) for f in expected.files.values() for k in f.removed]
    intended = list(expected.entries.values())
    if mutator == "update_transaction" and code == "TRANSACTION_INVALID" and _priced(targets + intended) \
            and intended and balances(intended, before):
        first = Divergence("FIX-L3-02", before.root, None, None, "refused: the legacy validator ignores price and cost weights")
    else:
        first = Divergence("NOT_APPLIED", before.root, None, None, f"the legacy app refused ({code})")
    return Report([first] + compare(before, after, Expected(), before_facts, after_facts).divergences)
