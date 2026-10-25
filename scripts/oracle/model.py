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

from semantic_diff import FileDiff, LedgerSet, canonical_key, parse_entries_with_totals


# Harness mutator → its row in the intent table (scripts/tests/test_intent_table.py checks both sides).
INTENT_ROWS = {"update_transaction": "txn.edit", "rename_account": "account.rename", "import_append": "import.commit"}


class NonComparable(Exception):
    """The intent cannot be expressed against this ledger, or its intended change is not decided yet."""


@dataclass
class Expected:
    files: dict[str, FileDiff] = field(default_factory=dict)
    entries: dict[str, Any] = field(default_factory=dict)  # key → entry, for the intended new entries
    created: set[str] = field(default_factory=set)  # keys of entries the mutator creates (not replacements)
    totals: dict[str, dict[int, Decimal]] = field(default_factory=dict)  # key → {posting: `@@` total}

    def add(self, rel: str, entry: Any, *, created: bool = False, totals: dict[int, Decimal] | None = None) -> None:
        k = canonical_key(entry)
        self.files.setdefault(rel, FileDiff()).added[k] += 1
        self.entries.setdefault(k, entry)
        self.totals.setdefault(k, totals or {})
        if created:
            self.created.add(k)

    def remove(self, rel: str, key: str, n: int = 1) -> None:
        self.files.setdefault(rel, FileDiff()).removed[key] += n


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
    rel, key, _ = find_by_legacy_id(before, txn_id)
    (new,), totals = parse_intent(new_text)
    exp = Expected()
    exp.remove(rel, key)
    exp.add(rel, new, totals=totals[canonical_key(new)])
    return exp


def _accounts(before: LedgerSet) -> set[str]:
    return {e.account for f in before.files.values() for e in f.by_key.values() if isinstance(e, data.Open)}


def _names(value: Any, acct: str) -> bool:
    return isinstance(value, str) and (value == acct or value.startswith(acct + ":"))


def renamed(entry: Any, old: str, new: str) -> Any:
    """The entry with every account reference *old* re-pointed to *new* (exact name only)."""
    r = lambda name: new if name == old else name  # noqa: E731
    if isinstance(entry, (data.Open, data.Close, data.Balance, data.Note, data.Document)):
        return entry._replace(account=r(entry.account))
    if isinstance(entry, data.Pad):
        return entry._replace(account=r(entry.account), source_account=r(entry.source_account))
    if isinstance(entry, data.Transaction):
        return entry._replace(postings=[p._replace(account=r(p.account)) for p in entry.postings])
    if isinstance(entry, data.Custom):  # account-typed values; a string only as a budget's quoted root (intent table)
        return entry._replace(values=[v._replace(value=r(v.value))
                                      if v.dtype is bc_account.TYPE or (v.dtype is str and entry.type == "budget") else v
                                      for v in entry.values])
    return entry


def rename_account(before: LedgerSet, old: str, new: str) -> Expected:
    """Rename a leaf account: every posting and directive that names it (ADR-0049 `move_postings` with a new B,
    plus its own open/close/balance/pad/budget)."""
    accounts = _accounts(before)
    if old not in accounts:
        raise NonComparable(f"{old} is not opened in this ledger")
    if new in accounts:
        raise NonComparable(f"{new} exists: that is a merge, its own mutator (ADR-0049)")
    if any(a.startswith(old + ":") for a in accounts):
        raise NonComparable(f"{old} has subaccounts: the intended change is not decided yet (intent table)")
    exp = Expected()
    for rel, f in before.files.items():
        for k, e in f.by_key.items():
            metas = [e.meta or {}] + [p.meta or {} for p in getattr(e, "postings", ())]
            if any(_names(v, old) for m in metas for key, v in m.items() if key != "filename"):
                raise NonComparable(f"metadata names {old}: the intended change is not decided yet (intent table)")
            if isinstance(e, data.Custom) and e.type != "budget" and any(
                    v.dtype is str and _names(v.value, old) for v in e.values):
                raise NonComparable(f"a custom string value names {old}: not decided yet (intent table)")
            r = renamed(e, old, new)
            if canonical_key(r) != k:
                exp.remove(rel, k, f.keys[k])
                for _ in range(f.keys[k]):
                    exp.add(rel, r, totals=f.totals.get(k))
    return exp


def import_append(before: LedgerSet, texts: list[str]) -> Expected:
    """Append the imported entries to the root file."""
    exp = Expected()
    for f in before.files.values():
        if any(isinstance(e, data.Custom) and e.type == "fava-option" and e.values and e.values[0].value == "insert-entry"
               for e in f.by_key.values()):
            raise NonComparable("the ledger routes new entries with insert-entry; the intended file is not modelled yet")
    for text in texts:
        entries, totals = parse_intent(text)
        for e in entries:
            exp.add(before.root, e, created=True, totals=totals[canonical_key(e)])
    return exp


def counts(exp: Expected) -> tuple[int, int]:
    return (sum(sum(f.removed.values()) for f in exp.files.values()),
            sum(sum(f.added.values()) for f in exp.files.values()))
