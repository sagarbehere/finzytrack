"""The test-only edit producer (S0116; ADR-0009: every mutator is an edit producer): `(snapshot, request) ->
EditPlan + IntendedChange` for insert, replace, delete and token edits. No real mutator exists yet; the contract's
tests drive `ledger.contract.prepare` and the `preview` op with it (and later the Hypothesis model).

The intent is worked out apart from the edits, as a mutator would from its request: the entry each target names is
read from the parser's output of the current text (its canonical key is the `old` the contract checks at that line),
the new entry's key from the request's text parsed as the file it lands in, and a token edit's new entry from the
parsed entry with the account replaced (never from the spliced text). The edits come from the primitives
(`source_edit`).

`from_json` reads the `preview` op's request (`{"edits": [...]}`, each edit an object with a `kind`), so the op's tests
register `produce_json` under `TEST_MUTATOR`.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from beancount.core import data

from ledger_engine.ledger import grammar
from ledger_engine.ledger.core import entry_keys
from ledger_engine.ledger.core import semantic_diff as sd
from ledger_engine.ledger.core import source_edit as se

TEST_MUTATOR = "test.edits"


@dataclass(frozen=True, slots=True)
class InsertReq:
    text: str
    default_file: str


@dataclass(frozen=True, slots=True)
class ReplaceReq:
    target: se.Target
    text: str


@dataclass(frozen=True, slots=True)
class DeleteReq:
    target: se.Target


@dataclass(frozen=True, slots=True)
class TokenReq:
    target: se.Target
    lineno: int
    old: str
    new: str


type Request = InsertReq | ReplaceReq | DeleteReq | TokenReq


def parsed_entry(snapshot: se.Snapshot, target: se.Target) -> data.Directive | None:
    parsed = snapshot.parsed(target.path)
    if isinstance(parsed, se.Refusal):
        return None
    return parsed.entries.get(target.lineno)


def _renamed(entry: data.Directive, lineno: int, old: str, new: str) -> data.Directive:
    """The parsed entry with the account `old` replaced by `new` on line `lineno` (its own line, or a posting's)."""
    if isinstance(entry, data.Transaction) and lineno != entry.meta["lineno"]:
        postings = [
            p._replace(account=new) if p.meta and p.meta.get("lineno") == lineno and p.account == old else p
            for p in entry.postings
        ]
        return entry._replace(postings=postings)
    if isinstance(entry, data.Pad):
        if entry.account == old:
            return entry._replace(account=new)
        return entry._replace(source_account=new)
    return entry._replace(account=new)  # every other entry with an account


def produce(
    snapshot: se.Snapshot, requests: Sequence[Request], files: Iterable[str] | None = None
) -> sd.Produced | se.Refusal:
    edits: list[se.Edit] = []
    inserts: list[sd.Insertion] = []
    removes: list[sd.Removal] = []
    renamed: dict[se.Target, data.Directive] = {}
    replaced: dict[se.Target, str] = {}
    old_keys: dict[se.Target, str] = {}
    for r in requests:
        if isinstance(r, InsertReq):
            edit = se.route_insert(snapshot, r.text, r.default_file)
            if isinstance(edit, se.Refusal):
                return edit
            key = entry_keys.key_of_text(r.text, edit.path)
            assert key is not None, r.text
            edits.append(edit)
            inserts.append(sd.Insertion(edit.path, key))
            continue
        span = se.locate(snapshot, r.target)
        if isinstance(span, se.Refusal):
            return span
        entry = parsed_entry(snapshot, r.target)
        assert entry is not None
        old_keys[r.target] = entry_keys.entry_key(entry)
        if isinstance(r, DeleteReq):
            edits.append(se.Delete(span))
            removes.append(sd.Removal(r.target, old_keys[r.target]))
        elif isinstance(r, ReplaceReq):
            edits.append(se.Replace(span, r.text))
            key = entry_keys.key_of_text(r.text, r.target.path)
            assert key is not None, r.text
            replaced[r.target] = key
        else:
            edit = se.replace_token(snapshot, span, r.lineno, grammar.AccountName(r.old), grammar.AccountName(r.new))
            if isinstance(edit, se.Refusal):
                return edit
            edits.append(edit)
            renamed[r.target] = _renamed(renamed.get(r.target, entry), r.lineno, r.old, r.new)
    replaces = [sd.Replacement(t, old_keys[t], k) for t, k in replaced.items()]
    replaces += [sd.Replacement(t, old_keys[t], entry_keys.entry_key(e)) for t, e in renamed.items()]
    paths = {e.path if isinstance(e, se.Insert) else e.span.target.path for e in edits}
    plan = se.EditPlan.of(snapshot, paths if files is None else files, edits)
    return sd.Produced(plan, sd.IntendedChange(tuple(inserts), tuple(replaces), tuple(removes)))


def _target(o: Mapping[str, object]) -> se.Target:
    keyword = o["keyword"]
    assert isinstance(keyword, str)
    return se.Target(
        str(o["path"]),
        int(str(o["line"])),
        datetime.date.fromisoformat(str(o["date"])),
        keyword,  # pyright: ignore[reportArgumentType] (a test's request names a known keyword)
    )


def from_json(request: Mapping[str, object]) -> list[Request]:
    out: list[Request] = []
    items = cast("list[Mapping[str, object]]", request["edits"])
    for o in items:
        kind = o["kind"]
        if kind == "insert":
            out.append(InsertReq(str(o["text"]), str(o["default_file"])))
        elif kind == "replace":
            out.append(ReplaceReq(_target(o), str(o["text"])))
        elif kind == "delete":
            out.append(DeleteReq(_target(o)))
        else:
            out.append(TokenReq(_target(o), int(str(o["lineno"])), str(o["old"]), str(o["new"])))
    return out


def produce_json(snapshot: se.Snapshot, request: Mapping[str, object]) -> sd.Produced | se.Refusal:
    return produce(snapshot, from_json(request))
