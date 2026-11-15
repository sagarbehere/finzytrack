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

Step 2b (`outside_spans`, S0122) then compares every byte outside the intended change's spans, which step 2 cannot
see (an untargeted `@@` re-written as Beancount's per-unit price, a comment, the layout): each file that differs gets
step-2b divergences (`Divergence.step == "2b"`) classed I10 (comments lost: `;` comments anywhere, org headings),
FIX-L3-02 (a `@@` total written as the per-unit price on an unchanged entry), FORMAT (the legacy printer's layout:
whitespace inside lines, blank lines, line ends, entries in another order that Beancount sorts the same (by date,
then kind, ties kept), and option, plugin and include lines moved in their own order; ADR-0014 amendment of
2026-11-15), or UNEXPLAINED for each run of other changed lines.
"""

from __future__ import annotations

import itertools
import re
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

from model import Expected
from semantic_diff import LedgerSet, LoadFacts, booked_of, canonical_key, diff, parse_file, strip_location

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
    step: str = "2"  # "2": the entry-level compare; "2b": a byte outside the intended change (outside_spans)
    text: str = ""  # step 2b only: the old and new lines that differ (repo corpora; a T-local report drops it)


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
    roles = _Roles()
    u_rem: dict[str, Counter[str]] = {}
    u_add: dict[str, Counter[str]] = {}
    n_rem: dict[str, Counter[str]] = {}
    n_add: dict[str, Counter[str]] = {}
    for f in files:
        a, e = actual.files.get(f), expected.files.get(f)
        a_rem, a_add = (a.removed, a.added) if a else (Counter(), Counter())
        e_rem, e_add = (e.removed, e.added) if e else (Counter(), Counter())
        u_rem[f], u_add[f], n_rem[f], n_add[f] = a_rem - e_rem, a_add - e_add, e_rem - a_rem, e_add - a_add
        for k in (a_add & e_add).elements():  # intended entries written exactly as intended
            roles.new(f, k, "C" if k in expected.created else "T")

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
    for f in files:  # entries the legacy app wrote that were not intended
        for k in list(u_add[f].elements()):
            if not _take(u_add[f], k):
                continue
            moved_from = next((g for g in files if g != f and u_rem[g][k] > 0), None)
            if moved_from is not None:
                _take(u_rem[moved_from], k)
                glob_to_root = moved_from in before.glob_files and f == before.root
                out.append(Divergence("I2" if glob_to_root else "UNEXPLAINED", f, k, k, f"moved from {moved_from}"))
                roles.new(f, k, "X")
                roles.old(moved_from, k, "X")
                continue
            if k in before_facts.generated:
                out.append(Divergence("I3", f, None, k, "generated by a plugin or a pad"))
                roles.new(f, k, "X")
                continue
            y = after.entry(k)
            to_root = f == before.root
            if to_root:  # I2: a glob-included entry duplicated into the root (the glob file still holds it)
                dup = next(((c, "") for c, _ in held_glob if c == k), None) or next(
                    ((c, cls) for c, e in held_glob
                     if (cls := explain(e, y, UNTARGETED, Ctx(booked_of(after_facts, e)))) is not None), None)
                if dup is not None:
                    out.append(Divergence(_tag("I2", dup[1]), f, dup[0], k, "a glob-included entry duplicated into the root"))
                    roles.new(f, k, "X")
                    continue
            # Where the written entry may come from: an intended entry of this file, an untargeted entry of this
            # file, or (I2) an intended entry of a glob-included file that the legacy app wrote into the root.
            pools: list[tuple[Counter[str], str | None, Callable[[str], tuple[tuple[str, ...], Ctx]], str]] = [
                (n_add[f], None, intended_ctx, "intended"), (u_rem[f], None, lambda c: untargeted_ctx(c, before_facts), "pair")]
            if to_root:
                pools += [(n_add[g], "I2", intended_ctx, "glob") for g in sorted(before.glob_files) if g in n_add]
            match = None
            for pool, tag, ctx_of, kind in pools:
                for cand in [c for c in pool if pool[c] > 0]:
                    allowed, ctx = ctx_of(cand)
                    cls = "" if cand == k else explain(entry(cand), y, allowed, ctx)
                    if cls is not None and (cls or tag):
                        match = (pool, cand, _tag(tag, cls), kind)
                        break
                if match:
                    break
            if match:
                pool, cand, cls, kind = match
                _take(pool, cand)
                out.append(Divergence(cls, f, cand, k))
                if kind == "intended":
                    roles.new(f, k, "C" if cand in expected.created else "T")
                elif kind == "pair":
                    roles.pair(f, cand, k)
                else:
                    roles.new(f, k, "X")
                continue
            was = next((c for c in u_rem[f] if u_rem[f][c] > 0 and _same_slot(entry(c), y)), None)
            if was is not None:
                _take(u_rem[f], was)
                out.append(Divergence("UNEXPLAINED", f, was, k, "an entry that was not targeted changed"))
                roles.pair(f, was, k)
            else:
                out.append(Divergence("UNEXPLAINED", f, None, k, "written but not intended"))
                roles.new(f, k, "X")
    for f in files:  # what is left over
        out += [Divergence("UNEXPLAINED", f, k, None, "removed but not intended") for k in u_rem[f].elements()]
        for k in u_rem[f].elements():
            roles.old(f, k, "X")
        # I2: the legacy app never rewrites a glob-included file, so an intended change inside one is not applied.
        unwritten_glob = f in before.glob_files and f not in actual.files
        cls, why = ("I2", "glob-included file not rewritten") if unwritten_glob else ("NOT_APPLIED", "")
        out += [Divergence(cls, f, k, None, why or "intended removal did not happen") for k in n_rem[f].elements()]
        out += [Divergence(cls, f, k, None, why or "intended entry was not written") for k in n_add[f].elements()]
    for f in actual.header_changed:
        out.append(Divergence("UNEXPLAINED", f, None, None, "options, plugins or includes changed"))
    out += outside_spans(before, after, expected, roles, set(actual.header_changed))
    return Report(out + new_errors(before_facts, after_facts, before.root))


# --- step 2b: every byte outside the intended change's spans (review:D1 §3 step 2b; BL-0045, S0122) ---------------

STEP2B_ERASERS = ("I10", "FIX-L3-02", "FORMAT")  # the classes a byte outside the intended change may carry
_HEADER_WORDS = frozenset({"option", "plugin", "include"})
Role = str | tuple[str, int]  # "T" target / its replacement, "C" created, "X" accounted by step 2, ("P", pair id)


class _Roles:
    """What the entry-level compare said about each entry instance, by file and canonical key, so that step 2b
    compares only the bytes step 2 has not already accounted for."""

    def __init__(self) -> None:
        self._old: dict[str, dict[str, list[Role]]] = {}
        self._new: dict[str, dict[str, list[Role]]] = {}
        self._pairs = 0

    def old(self, f: str, key: str, role: Role) -> None:
        self._old.setdefault(f, {}).setdefault(key, []).append(role)

    def new(self, f: str, key: str, role: Role) -> None:
        self._new.setdefault(f, {}).setdefault(key, []).append(role)

    def pair(self, f: str, old_key: str, new_key: str) -> None:
        """An untargeted entry the legacy wrote differently: its bytes are explained by its step-2 class."""
        self._pairs += 1
        self.old(f, old_key, ("P", self._pairs))
        self.new(f, new_key, ("P", self._pairs))

    def of(self, f: str, side: str) -> dict[str, list[Role]]:
        return {k: list(v) for k, v in (self._old if side == "old" else self._new).get(f, {}).items()}


def _lines(text: str) -> list[str]:
    """The text's lines with their line ends, numbered as Beancount numbers them (only `\\n` ends a line)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _entry_stop(lines: list[str], first: int) -> int:
    """Fava's `find_entry_lines`, as the engine's `source_edit.locate`: an entry's lines run from its first line to
    the first blank line, line of whitespace only, or line whose first character is not whitespace."""
    stop = first + 1
    while stop < len(lines) and lines[stop].strip() and lines[stop][0].isspace():
        stop += 1
    return stop


def _split_comment(line: str) -> tuple[str, str, str]:
    """(code, comment, line end). A comment starts at a `;` outside a string; a line starting with `*` (an org-mode
    heading, which Beancount skips) is a comment as a whole."""
    eol = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    body = line[: len(line) - len(eol)]
    if body.startswith("*"):
        return "", body, eol
    in_string = escaped = False
    for i, ch in enumerate(body):
        if in_string:
            escaped, in_string = (False, True) if escaped else (ch == "\\", ch != '"')
        elif ch == '"':
            in_string = True
        elif ch == ";":
            return body[:i], body[i:], eol
    return body, "", eol


def _spaced(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class _Unit:
    first: int  # its first line, from 1
    lines: tuple[str, ...]
    key: str | None  # an entry's canonical key; None for a line outside every entry
    role: Role  # entries: T, C, X, E (unchanged key) or a pair; other lines: L
    parts: tuple[tuple[str, str, str], ...] = ()  # each line split once: (code, comment, line end)
    order: tuple[Any, int] | None = None  # an entry's place in Beancount's sort: its date, then its kind


def _units(pf: Any, text: str, side: str, roles: dict[str, list[Role]], targets: Counter[str]) -> list[_Unit]:
    """The file as entries (Fava's lines of each entry the parser found) and the lines between them, each with its
    role: on the old side the first `targets[key]` instances of a key are the intended change's targets; on the new
    side an instance of a target key that step 2 did not account for is the target left in its place."""
    lines = _lines(text)
    starts = {int(e.meta["lineno"]): e for e in pf.entries}
    targets = Counter(targets)
    out: list[_Unit] = []
    i = 0
    while i < len(lines):
        entry = starts.get(i + 1)
        if entry is None:
            out.append(_Unit(i + 1, (lines[i],), None, "L", (_split_comment(lines[i]),)))
            i += 1
            continue
        stop = _entry_stop(lines, i)
        key = canonical_key(entry)
        role: Role
        if side == "old" and targets[key] > 0:
            targets[key] -= 1
            role = "T"
        elif roles.get(key):
            role = roles[key].pop(0)
        else:
            role = "T" if side == "new" and key in targets else "E"
        out.append(_Unit(i + 1, tuple(lines[i:stop]), key, role, tuple(_split_comment(x) for x in lines[i:stop]),
                         (entry.date, data.SORT_ORDER.get(type(entry), 0))))
        i = stop
    return out


def _comments(units: list[_Unit], fmt: bool) -> Counter[str]:
    """The comments of the compared bytes (lines between entries, unchanged and paired entries)."""
    out: Counter[str] = Counter()
    for u in units:
        if u.role in ("T", "C", "X"):
            continue
        for _, comment, _ in u.parts:
            if comment:
                out[_spaced(comment) if fmt else comment] += 1
    return out


Token = tuple[Any, int, int, tuple[Any, int] | None]  # (compared value, first line, last line, sort place)


def _tokens(units: list[_Unit], erasers: frozenset[str], lost: Counter[str] | None, totals: set[str],
            header_changed: bool) -> list[Token]:
    """The compared value of each unit under the erasers. I10 drops the comments in *lost* (old side only); FORMAT
    compares lines with their whitespace (indentation included) collapsed and drops blank lines and line ends; FIX-L3-02 masks the price of
    an unchanged entry the old text wrote with `@@` (its key, so its per-unit price, is unchanged)."""
    fmt = "FORMAT" in erasers
    lost = Counter(lost) if lost is not None and "I10" in erasers else None

    def line_value(part: tuple[str, str, str], mask: bool) -> str | None:
        code, comment, eol = part
        key = _spaced(comment) if fmt else comment
        if comment and lost is not None and lost[key] > 0:
            lost[key] -= 1
            comment, code = "", code.rstrip()
            if not code.strip():
                return None
        if mask and code[:1].isspace() and "@" in code:
            code = re.sub(r"\s@@?\s.*$", " @ ·", code)
        if not fmt:
            return code + comment + eol
        spaced = _spaced(code)  # indentation is whitespace too: where it means something, the parse (the key) differs
        return spaced + (" " if comment and spaced.strip() else "") + (key if comment else "")

    out: list[Token] = []
    for u in units:
        last = u.first + len(u.lines) - 1
        if isinstance(u.role, str) and u.role in ("T", "C", "X"):
            out.append((u.role, u.first, last, u.order))
        elif u.role == "L":
            line, part = u.lines[0], u.parts[0]
            code = part[0]
            if not line.strip():
                if not fmt:
                    out.append((("B", line), u.first, last, None))
                continue
            header = bool(code[:1].strip()) and code.split()[0] in _HEADER_WORDS
            value = None if header and header_changed else line_value(part, False)
            # A line dropped as accounted for (a lost comment line, a header line step 2 reported) is "K": like a
            # removed entry, it may take its blank separator with it (_without).
            out.append((("H" if header else "L", value), u.first, last, None) if value is not None else ("K", u.first, last, None))
        else:
            mask = "FIX-L3-02" in erasers and u.role == "E" and u.key in totals
            values = tuple(v for part in u.parts if (v := line_value(part, mask)) is not None)
            if u.role == "E":
                out.append((("E", values), u.first, last, u.order))
            else:  # a pair: its text is its step-2 class's; only the comments it kept are compared
                kept = tuple(c for v in values if (c := _split_comment(v)[1]))
                out.append((("P", u.role[1], kept), u.first, last, u.order))
    return out


def _without(tokens: list[Token], drop: tuple[str, ...], blank: bool) -> list[Token]:
    """*tokens* without the inserted or removed entries step 2 accounted for; with *blank*, each also takes one blank
    line next to it (the one after it, else the one before), as an inserted entry brings its separator."""
    out: list[Token] = []
    pending = False
    for t in tokens:
        if t[0] in drop:
            if pending and blank and out and isinstance(out[-1][0], tuple) and out[-1][0][0] == "B":
                out.pop()
            pending = blank
            continue
        if pending and isinstance(t[0], tuple) and t[0][0] == "B":
            pending = False
            continue
        if pending and out and isinstance(out[-1][0], tuple) and out[-1][0][0] == "B":
            out.pop()
        pending = False
        out.append(t)
    if pending and out and isinstance(out[-1][0], tuple) and out[-1][0][0] == "B":
        out.pop()
    return out


def _sides(old: list[Token], new: list[Token], blank: bool) -> tuple[list[Token], list[Token]]:
    return _without(old, ("X", "K"), blank), _without(new, ("C", "X", "K"), blank)


def _layout(tokens: list[Token]) -> list[Token]:
    """FORMAT's view of order: Beancount sorts a file's entries by date, then kind, then line, so entries between two
    other lines may stand in any order that keeps each same-date, same-kind pair in its order. Each run of entries is
    sorted that way (stably); an intended replacement's place among them is layout too. Beancount reads the `option`,
    `plugin` and `include` lines in their file order wherever they stand among the entries, so they are compared in
    their own order, first."""
    header = [t for t in tokens if _is_header(t)]
    out: list[Token] = []
    run: list[Token] = []
    for t in tokens:
        if t[0] == "T" or _is_header(t):
            continue
        if t[3] is None:
            out += sorted(run, key=_place)
            run = []
            out.append(t)
        else:
            run.append(t)
    return header + out + sorted(run, key=_place)


def _is_header(t: Token) -> bool:
    return isinstance(t[0], tuple) and t[0][0] == "H"


def _place(t: Token) -> Any:
    return t[3]


def _fits(old: list[Token], new: list[Token]) -> bool:
    """Every compared byte is the old one, in order, and every intended replacement stands where a target stood."""
    def split(ts: list[Token]) -> tuple[list[Any], set[int]]:
        rest: list[Any] = []
        slots: set[int] = set()
        for t in ts:
            if t[0] == "T":
                slots.add(len(rest))
            else:
                rest.append(t[0])
        return rest, slots

    (o, o_slots), (n, n_slots) = split(old), split(new)
    return o == n and n_slots <= o_slots


Run = tuple[tuple[int, int] | None, tuple[int, int] | None, int]  # old lines, new lines (first, last), tokens


def _residue(old: list[Token], new: list[Token]) -> list[Run]:
    """The differences that remain, per changed run: its old and new lines (None on a side that has none) and how
    many compared units differ; a target that is simply gone (replaced by nothing) is inside its span."""
    import difflib

    sm = difflib.SequenceMatcher(None, [t[0] for t in old], [t[0] for t in new], autojunk=False)
    out: list[Run] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "delete" and all(t[0] == "T" for t in old[i1:i2])):
            continue
        out.append(((old[i1][1], old[i2 - 1][2]) if i1 < i2 else None, (new[j1][1], new[j2 - 1][2]) if j1 < j2 else None,
                    (i2 - i1) + (j2 - j1)))
    return out


_NOTES = {"I10": "comment(s) lost outside the intended change",
          "FIX-L3-02": "a `@@` total written as a unit price outside the intended change",
          "FORMAT": "whitespace, blank lines or line ends changed outside the intended change"}


_MOVED = "; entries in another order that Beancount sorts the same"


def _file_of(s: LedgerSet, f: str) -> Any | None:
    if f in s.files:
        return s.files[f]
    path = s.base / f
    return parse_file(path)[0] if path.is_file() else None


class _FileCheck:
    """Step 2b on one file whose text changed: the units of both sides, their tokens under each set of erasers
    (computed once per set), and the divergences."""

    def __init__(self, f: str, old_pf: Any | None, old_text: str, new_pf: Any | None, new_text: str, expected: Expected,
                 roles: _Roles, header_changed: bool) -> None:
        targets = expected.files[f].removed if f in expected.files else Counter()
        self.f, self.old_text, self.new_text, self.header_changed = f, old_text, new_text, header_changed
        self.old_units = _units(old_pf, old_text, "old", roles.of(f, "old"), targets) if old_pf else []
        self.new_units = _units(new_pf, new_text, "new", roles.of(f, "new"), targets) if new_pf else []
        self.totals = {u.key for u in self.old_units if u.role == "E" and u.key and any("@@" in x[0] for x in u.parts)}
        self.cache: dict[frozenset[str], tuple[list[Token], list[Token], int]] = {}

    def sides(self, erasers: frozenset[str], blank: bool, layout: bool = True) -> tuple[list[Token], list[Token], int]:
        if erasers not in self.cache:
            fmt = "FORMAT" in erasers
            old_c, new_c = _comments(self.old_units, fmt), _comments(self.new_units, fmt)
            lost = old_c - new_c if not new_c - old_c else Counter()  # the legacy loses comments; it never writes one
            self.cache[erasers] = (_tokens(self.old_units, erasers, lost, self.totals, self.header_changed),
                                   _tokens(self.new_units, erasers, None, self.totals, self.header_changed),
                                   sum(lost.values()))
        o, n, lost_n = self.cache[erasers]
        o, n = _sides(o, n, blank)
        if layout and "FORMAT" in erasers:
            o, n = _layout(o), _layout(n)
        return o, n, lost_n

    def fits(self, erasers: frozenset[str], layout: bool = True) -> int | None:
        """The number of comments lost when *erasers* explain the file, else None."""
        for blank in (False, True):
            o, n, lost = self.sides(erasers, blank, layout)
            if _fits(o, n):
                return lost
        return None

    def residue(self, erasers: frozenset[str]) -> tuple[list[Run], int]:
        best: tuple[list[Run], int] | None = None
        for blank in (False, True):
            o, n, lost = self.sides(erasers, blank)
            r = _residue(o, n)
            if best is None or sum(x[2] for x in r) < sum(x[2] for x in best[0]):
                best = (r, lost)
        assert best is not None
        return best

    def unmatched(self, erasers: frozenset[str]) -> int:
        """Compared units without an equal on the other side, order aside: linear, where difflib on two sides that
        share almost nothing (FORMAT taken out of a reprinted file) is quadratic (minutes on a demo case)."""
        o, n, _ = self.sides(erasers, True)
        a, b = Counter(t[0] for t in o if t[0] != "T"), Counter(t[0] for t in n if t[0] != "T")
        return sum(((a - b) + (b - a)).values())

    def explained(self) -> tuple[tuple[str, ...], int] | None:
        """The smallest set of erasers that explains the file, and the comments it lost; None if none does."""
        every = frozenset(STEP2B_ERASERS)
        if self.fits(every) is None:
            return None
        # The erasers each fit needs: those without which the rest does not fit (fits grow with the erasers).
        needed = tuple(c for c in STEP2B_ERASERS if self.fits(every - {c}) is None)
        lost = self.fits(frozenset(needed))
        if lost is not None:
            return needed, lost
        for size in range(len(STEP2B_ERASERS) + 1):
            for combo in itertools.combinations(STEP2B_ERASERS, size):
                lost = self.fits(frozenset(combo))
                if lost is not None:
                    return combo, lost
        return None

    def note(self, c: str, lost: int, moved: bool = False) -> Divergence:
        return Divergence(c, self.f, None, None, (f"{lost} " if c == "I10" else "") + _NOTES[c] + (_MOVED if moved else ""), "2b")

    def divergences(self) -> list[Divergence]:
        found = self.explained()
        if found is not None:
            combo, lost = found
            moved = "FORMAT" in combo and self.fits(frozenset(combo), layout=False) is None
            return [self.note(c, lost, c == "FORMAT" and moved) for c in combo]
        every = frozenset(STEP2B_ERASERS)
        runs, lost = self.residue(every)
        size = self.unmatched(every)
        # An eraser that removed differences is reported beside what remains.
        out = [self.note(c, lost) for c in STEP2B_ERASERS if self.unmatched(every - {c}) > size]
        old_lines, new_lines = _lines(self.old_text), _lines(self.new_text)

        def show(sign: str, lines: list[str], r: tuple[int, int] | None) -> str:
            return "".join(f"{sign} {x}" for x in lines[r[0] - 1:r[1]][:5]) if r else ""

        for o_range, n_range, _ in runs:
            where = ", ".join(f"{side} lines {r[0]}-{r[1]}" for side, r in (("old", o_range), ("new", n_range)) if r)
            out.append(Divergence("UNEXPLAINED", self.f, None, None, f"outside the intended change: {where} differ", "2b",
                                  show("-", old_lines, o_range) + show("+", new_lines, n_range)))
        return out


def outside_spans(before: LedgerSet, after: LedgerSet, expected: Expected, roles: _Roles,
                  header_changed: set[str]) -> list[Divergence]:
    """Step 2b (review:D1 §3; the contract's `OUTSIDE_SPANS`): in each file, the legacy's new bytes equal the old bytes
    everywhere outside the intended change's spans, or each difference is a step-2b divergence.

    The spans are the targets' lines in the old text (Fava's entry lines, as the engine locates them) and, for an
    entry the mutator creates, its place: anywhere between units, with one blank line as its separator. Bytes step 2
    already accounted for are not compared again: a replacement, a created, moved, copied (I2) or generated (I3)
    entry, an entry removed or written unintended (UNEXPLAINED), the text of an untargeted entry the legacy wrote
    differently (its step-2 class; its comments are still compared), and the option, plugin and include lines of a
    file whose header step 2 reported. Everything else (unchanged entries, the lines between entries) must be byte
    for byte the old text.

    A file that differs is classed with the smallest set of STEP2B_ERASERS that makes the two sides equal; if none
    does, each remaining run of changed lines is UNEXPLAINED, beside the erasers that removed differences.
    - I10: comments lost (`;` comments anywhere, org-mode headings), unless the new side has a comment the old lacks
      (the legacy never writes one). A lost comment line may take one blank line next to it.
    - FIX-L3-02: a `@@` total written as Beancount's per-unit price on an unchanged entry.
    - FORMAT: whitespace inside lines (indentation included), blank lines, line ends; entries between two other lines
      standing in another order that Beancount sorts the same (by date, then kind; two entries of one date and kind
      keep their order; the intended replacement's place among them included); option, plugin and include lines
      moved among the entries in their own order.
    None of them covers a changed comment, a re-spelled number (`1,000.00` → `1000.00`), a swapped tie, a comment or
    other line moved, or a `pushtag`/`pushmeta` block folded into its entries.

    Limits: the place of an entry the mutator creates is not checked (the model does not route inserts; the engine's
    `route_insert` does). Beancount also breaks ties between files by line number, which a reprint changes; FORMAT
    does not see that. With duplicates of one untargeted key of which one changed, the pairing takes the first
    instance, so it may report an UNEXPLAINED run more, never fewer."""
    out: list[Divergence] = []
    created = {f for f, d in expected.files.items() if any(k in expected.created for k in d.added)}
    for f in sorted(set(before.files) | set(after.files)):
        old_pf, new_pf = _file_of(before, f), _file_of(after, f)
        old_text, new_text = (old_pf.text if old_pf else ""), (new_pf.text if new_pf else "")
        if old_text == new_text:
            continue
        if f in created and old_text and not old_text.endswith("\n"):
            old_text += "\n"  # an insert at the end of a file without a final newline brings one (rule 3)
        out += _FileCheck(f, old_pf, old_text, new_pf, new_text, expected, roles, f in header_changed).divergences()
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
