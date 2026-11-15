"""Expected-change models: what each S9 mutator is meant to change, as per-file entry multisets.

Each model implements the `intent` column of its row in the per-mutator intent table
(design/specs/write-path/intent-table.toml), which is the authority. Where the table is not settled (a row waiting
for the owner), the model refuses the case as non-comparable instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from beancount.core import account as bc_account
from beancount.core import data

from semantic_diff import FileDiff, LedgerSet, Parsed, canonical_key, location, parse_entries_with_totals


# Harness mutator → its row in the intent table (scripts/tests/test_intent_table.py checks both sides).
INTENT_ROWS = {"update_transaction": "txn.edit", "rename_account": "account.rename", "import_append": "import.commit"}


class NonComparable(Exception):
    """The intent cannot be expressed against this ledger, or its intended change is not decided yet."""


@dataclass
class Expected:
    """The intended change: per file, the entries removed and added (a multiset of keys), and where each goes. An
    entry that replaces another takes its place (its file and first line); a created entry is appended to its file,
    `offset` lines into the appended text. The places matter only to Beancount's order (`intended_entries`)."""
    files: dict[str, FileDiff] = field(default_factory=dict)
    entries: dict[str, Any] = field(default_factory=dict)  # key → entry, for the intended new entries
    created: set[str] = field(default_factory=set)  # keys of entries the mutator creates (not replacements)
    totals: dict[str, dict[int, Decimal]] = field(default_factory=dict)  # key → {posting: `@@` total}
    at: dict[tuple[str, int], str | None] = field(default_factory=dict)  # (file, line) removed → key put there
    appended: list[tuple[str, str, int]] = field(default_factory=list)  # (file, key, line offset), in order

    def add(self, rel: str, entry: Any, *, created: bool = False, totals: dict[int, Decimal] | None = None,
            at: int | None = None, offset: int = 0) -> None:
        """Add *entry* to *rel*: at line *at* (the place of an entry it replaces), else appended at *offset*."""
        k = canonical_key(entry)
        self.files.setdefault(rel, FileDiff()).added[k] += 1
        self.entries.setdefault(k, entry)
        self.totals.setdefault(k, totals or {})
        if created:
            self.created.add(k)
        if at is not None:
            self.at[(rel, at)] = k
        else:
            self.appended.append((rel, k, offset))

    def remove(self, rel: str, key: str, n: int = 1, *, at: tuple[int, ...] = ()) -> None:
        """Remove *n* instances of *key* from *rel*, found at the lines *at*."""
        self.files.setdefault(rel, FileDiff()).removed[key] += n
        for line in at:
            self.at.setdefault((rel, line), None)


def parse_intent(text: str) -> tuple[list[Any], dict[str, dict[int, Decimal]]]:
    """The intent's entries and, per entry key, the postings it writes with a total price."""
    entries, errors, _, totals = parse_entries_with_totals(text, "<intent>")
    if errors or not entries:
        raise ValueError(f"intent text does not parse: {[e.message for e in errors] or 'no entries'}")
    return entries, totals


def parse_entries(text: str) -> list[Any]:
    return parse_intent(text)[0]


def find_by_legacy_id(before: LedgerSet, txn_id: str) -> tuple[str, str, Any]:
    """(file, key, entry) of the one transaction whose legacy `id` is *txn_id*."""
    hits = [(rel, k, e) for rel, f in before.files.items() for k, e in f.by_key.items()
            if isinstance(e, data.Transaction) and e.meta.get("id") == txn_id
            for _ in range(f.keys[k])]
    if len(hits) != 1:
        raise NonComparable(f"{len(hits)} transactions carry the legacy id {txn_id!r}; the old app addresses exactly one")
    return hits[0]


def update_transaction(before: LedgerSet, txn_id: str, new_text: str) -> Expected:
    """Replace the transaction with legacy id *txn_id* by *new_text*, in the file it lives in."""
    rel, key, target = find_by_legacy_id(before, txn_id)
    (new,), totals = parse_intent(new_text)
    exp = Expected()
    line = target.meta["lineno"]
    exp.remove(rel, key, at=(line,))
    exp.add(rel, new, totals=totals[canonical_key(new)], at=line)  # Fava's splice: the replacement takes its place
    return exp


def _accounts(before: LedgerSet) -> set[str]:
    return {e.account for f in before.files.values() for e in f.by_key.values() if isinstance(e, data.Open)}


def _names(value: Any, acct: str) -> bool:
    return isinstance(value, str) and (value == acct or value.startswith(acct + ":"))


def renamed(entry: Any, old: str, new: str) -> Any:
    """The entry with A (*old*) and every `A:…` subaccount re-pointed to B (*new*): account tokens, and metadata and
    `custom` string values that are exactly one of those names (ADR-0049 amendment, intent table `account.rename`)."""
    def r(name: Any) -> Any:
        return new + name[len(old):] if _names(name, old) else name

    def meta(m: dict[str, Any] | None) -> dict[str, Any] | None:
        return m if not m else {k: (v if k in ("filename", "lineno") else r(v)) for k, v in m.items()}

    entry = entry._replace(meta=meta(entry.meta))
    if isinstance(entry, (data.Open, data.Close, data.Balance, data.Note, data.Document)):
        return entry._replace(account=r(entry.account))
    if isinstance(entry, data.Pad):
        return entry._replace(account=r(entry.account), source_account=r(entry.source_account))
    if isinstance(entry, data.Transaction):
        return entry._replace(postings=[p._replace(account=r(p.account), meta=meta(p.meta)) for p in entry.postings])
    if isinstance(entry, data.Custom):
        return entry._replace(values=[v._replace(value=r(v.value)) if v.dtype in (bc_account.TYPE, str) else v
                                      for v in entry.values])
    return entry


def rename_account(before: LedgerSet, old: str, new: str) -> Expected:
    """Rename A and its subtree: every reference to each renamed account (intent table `account.rename`)."""
    accounts = _accounts(before)
    if old not in accounts:
        raise NonComparable(f"{old} is not opened in this ledger")
    targets = {new + a[len(old):] for a in accounts if _names(a, old)}
    if targets & accounts:
        raise NonComparable(f"{sorted(targets & accounts)} exist: that is a merge, its own mutator (ADR-0049)")
    exp = Expected()
    for rel, f in before.files.items():
        for k, e in f.by_key.items():
            r = renamed(e, old, new)
            if canonical_key(r) != k:
                exp.remove(rel, k, f.keys[k], at=f.lines[k])
                for line in f.lines[k]:  # a rename edits each entry where it stands
                    exp.add(rel, r, totals=f.totals.get(k), at=line)
    return exp


def import_append(before: LedgerSet, texts: list[str]) -> Expected:
    """Append the imported entries to the root file."""
    exp = Expected()
    for f in before.files.values():
        if any(isinstance(e, data.Custom) and e.type == "fava-option" and e.values and e.values[0].value == "insert-entry"
               for e in f.by_key.values()):
            raise NonComparable("the ledger routes new entries with insert-entry; the intended file is not modelled yet")
    pieces = []  # (entry, its own text, its `@@` totals): v0.4 inserts each entry as one text
    for text in texts:
        entries, totals = parse_intent(text)
        lines = text.splitlines(keepends=True)
        starts = [e.meta["lineno"] - 1 for e in entries] + [len(lines)]
        for e, first, stop in zip(entries, starts, starts[1:]):
            own = "".join(lines[first:stop]).rstrip("\n") + "\n"
            pieces.append((e, own, totals[canonical_key(e)]))
    # v0.4 orders inserts at one point by date, Fava's order of kinds, then text (the engine's
    # `source_edit.insert_key`), and puts each after a blank line (`semantic_diff._planned`).
    pieces.sort(key=lambda p: (p[0].date, data.SORT_ORDER.get(type(p[0]), 0), p[1]))
    offset = 0
    for e, own, totals_of in pieces:
        exp.add(before.root, e, created=True, totals=totals_of, offset=offset)
        offset += own.count("\n") + 1
    return exp


class Unbuildable(Exception):
    """The intended ledger cannot be placed: a removed entry is not where the model says (a model defect)."""


def _placed(entry: Any, path: str, line: int) -> Any:
    """*entry* at *line* of *path*; its postings keep their lines relative to its first line."""
    first = (entry.meta or {}).get("lineno") or line

    def meta(m: dict[str, Any] | None) -> dict[str, Any]:
        m = dict(m or {})
        return {**m, "filename": path, "lineno": line + (m.get("lineno") or first) - first}
    entry = entry._replace(meta=meta(entry.meta))
    if isinstance(entry, data.Transaction):
        entry = entry._replace(postings=[p._replace(meta=meta(p.meta)) for p in entry.postings])
    return entry


def append_line(parsed: Parsed, path: str) -> int:
    """The first line of text appended to *path* (v0.4's insert at a file's end, `semantic_diff._planned` in the
    engine: a blank line, then the entry; in an empty file, no blank line). Further entries follow in the order
    `import_append` gives them, each after its own blank line."""
    lines, _ = parsed.files[path]
    return 1 if lines == 0 else lines + 2


def intended_entries(parsed: Parsed, before: LedgerSet, exp: Expected) -> list[Any]:
    """The intended ledger as the loader would parse it, in its order, before booking: every entry of *parsed* at
    its own file and line, except that each removed entry gives its place (file, first line, and its rank in the
    loader's order) to the entry that replaces it, and each created entry follows the last entry of its file, at
    the line v0.4 appends it to (`append_line`). Untargeted entries keep their lines: an intended change moves no
    other entry in Beancount's order (entry_sortkey: date, kind, line, across files)."""
    entries: list[Any | None] = list(parsed.entries)
    where: dict[tuple[str, int], int] = {}
    for i, e in enumerate(entries):
        where.setdefault(location(e), i)
    for rel, fd in exp.files.items():
        placed = sum(1 for (r, _) in exp.at if r == rel)
        if placed != sum(fd.removed.values()):
            raise Unbuildable(f"{rel}: {sum(fd.removed.values())} entries removed, {placed} placed")
    for (rel, line), key in exp.at.items():
        path = str((before.base / rel).resolve())
        i = where.get((path, line))
        if i is None or entries[i] is None or exp.files[rel].removed[canonical_key(entries[i])] <= 0:
            raise Unbuildable(f"{rel}:{line} does not hold an entry the change removes")
        entries[i] = None if key is None else _placed(exp.entries[key], path, line)
    for rel, key, offset in exp.appended:
        path = str((before.base / rel).resolve())
        last = max((i for i, e in enumerate(entries) if e is not None and location(e)[0] == path), default=-1)
        entries.insert(last + 1, _placed(exp.entries[key], path, append_line(parsed, path) + offset))
    return [e for e in entries if e is not None]


def counts(exp: Expected) -> tuple[int, int]:
    return (sum(sum(f.removed.values()) for f in exp.files.values()),
            sum(sum(f.added.values()) for f in exp.files.values()))
