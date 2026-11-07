"""What a copy's "loads the same" check compares (`design/specs/engine-jobs.md` §8.2, the copy; S0081; the owner's
decision 3 of 2026-11-07: "Decide and justify the exact comparison").

The copy is the same ledger when Beancount's full load of it gives what Beancount's full load of the source gave, read
as the same books. A load is reduced to a `Summary`, so the source's entries need not stay in memory while the copy
loads:
- **entries**, in load order (after sorting, booking, plugins and validation), every field, metadata included, each
  in a canonical form, hashed. Line numbers are compared (a rewrite keeps every line's number);
- **errors**, in order, each with its kind, source, message and entry. The same errors, not none: the source may
  have Beancount errors (ADR-0067: accounting advises), and the copy must keep exactly them;
- **options**, every one, except `input_hash` (Beancount's cache key, from file times and sizes: S0051) and the raw
  strings of `option "documents"` (rewritten on purpose: compared instead as the documents roots they resolve to);
  `include` and `pythonpath`, which Beancount sorts by path, compare as sorted after mapping.
Every text in the copy's load is first mapped back to the source's places (`back`: `copy_layout.back`), so a file
name in metadata, a `document` entry's file, a path in an error message, `filename`, `include` and `pythonpath`
compare equal when they name the copied file. A value of a kind this module does not know (an object a plugin put
there) is not compared by guess: `Uncomparable`, and the copy is refused.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final, Literal, cast

from ledger_engine.ledger.overlay import Loaded, documents_roots

IGNORED_OPTIONS: Final = frozenset({"input_hash", "documents"})
"""Options not compared as they are: Beancount's cache key, and the raw documents strings (their roots are)."""
NAME_SORTED: Final = frozenset({"include", "pythonpath"})
"""Options Beancount sorts by path (`loader._parse_recursive`'s `sorted(seen)`, `aggregate_options_map`'s
`sorted(pythonpath)`): compared as sets of mapped paths, since the copy's paths sort in another order."""


class UncomparableError(Exception):
    """A value of a kind the comparison does not know."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class Summary:
    """One load, reduced: the hash of its options, each entry's hash with its file and line, each error's hash."""

    options: str
    entries: tuple[tuple[str, str, int], ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Difference:
    """Where two loads first differ: an entry (its file and line in the source), an error, or the options."""

    what: Literal["entry", "error", "options"]
    path: str | None = None
    line: int | None = None


type Back = Callable[[str], str]


def _key(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def canonical(v: object, back: Back) -> object:
    """`v` as plain JSON-able data, texts mapped by `back`; `UncomparableError` for an unknown kind."""
    match v:
        case None | bool() | int():
            return v
        case str():
            return ["s", back(v)]
        case float():
            return ["f", repr(v)]
        case Decimal():
            return ["d", str(v)]
        case datetime.date():  # a datetime too
            return ["t", type(v).__name__, v.isoformat()]
        case Enum():
            return ["e", type(v).__qualname__, v.name]
        case bytes():
            return ["b", v.hex()]
        case tuple() if _named(cast("object", v)):  # Beancount's entries, postings, amounts, costs: named tuples
            named = cast("object", v)
            fields = cast("tuple[str, ...]", getattr(named, "_fields"))  # noqa: B009
            return ["n", type(named).__qualname__, [canonical(getattr(named, f), back) for f in fields]]
        case list() | tuple():
            return ["l", [canonical(x, back) for x in cast("list[object]", v)]]
        case dict():
            items = [(canonical(k, back), canonical(x, back)) for k, x in cast("dict[object, object]", v).items()]
            return ["m", sorted(([k, x] for k, x in items), key=lambda kx: _key(kx[0]))]
        case set() | frozenset():
            return ["S", sorted((canonical(x, back) for x in cast("set[object]", v)), key=_key)]
        case _ if type(v).__module__.startswith("beancount.") and type(v).__str__ is not object.__str__:
            # Beancount's own objects with a text form (the display context, an inventory)
            return ["o", type(v).__qualname__, back(str(v))]
        case _:
            raise UncomparableError(type(v).__qualname__)


def _named(v: object) -> bool:
    return hasattr(v, "_fields")


def _sha(value: object) -> str:
    return hashlib.sha256(_key(value).encode("utf-8")).hexdigest()


def summary(loaded: Loaded, back: Back) -> Summary:
    """The load reduced to what is compared (the module's docstring). `back` maps the copy's texts to the source's
    places (the identity for the source)."""
    options = {k: v for k, v in loaded.options.items() if k not in IGNORED_OPTIONS | NAME_SORTED}
    for k in NAME_SORTED:  # sorted by name by Beancount: their order follows where the files are, so mapped first
        if k in loaded.options:
            options[k] = sorted(back(str(p)) for p in loaded.options[k])
    roots = sorted(back(r) for r in documents_roots(loaded.options)) if loaded.options.get("documents") else []
    entries: list[tuple[str, str, int]] = []
    for e in loaded.entries:
        meta = cast("dict[str, object]", e.meta or {})
        filename, lineno = meta.get("filename"), meta.get("lineno")
        entries.append(
            (
                _sha(canonical(e, back)),
                back(filename) if isinstance(filename, str) else "",
                lineno if isinstance(lineno, int) else 0,
            )
        )
    return Summary(
        _sha([canonical(options, back), roots]),
        tuple(entries),
        tuple(_sha([type(err).__qualname__, canonical(err, back)]) for err in loaded.errors),
    )


def first_difference(source: Summary, copy: Summary) -> Difference | None:
    """Where the copy's load first differs from the source's, or None when they are the same."""
    for a, b in zip(source.entries, copy.entries, strict=False):
        if a[0] != b[0]:
            return Difference("entry", a[1] or None, a[2] or None)
    if len(source.entries) != len(copy.entries):
        longer = source.entries if len(source.entries) > len(copy.entries) else copy.entries
        extra = longer[min(len(source.entries), len(copy.entries))]
        return Difference("entry", extra[1] or None, extra[2] or None)
    if source.errors != copy.errors:
        return Difference("error")
    if source.options != copy.options:
        return Difference("options")
    return None
