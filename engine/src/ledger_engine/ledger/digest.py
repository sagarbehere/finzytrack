"""The digest of a load result: what R4 compares (`design/specs/engine-jobs.md` §6.7 "Digests"; ADR-0010 guardrail 3;
BL-0118; S0066). Pure: file names are turned into `path_key`s by a function the caller gives.

`of_result(entries, errors, options, key_of)` is `sha256:` + the SHA-256 of a canonical encoding of one load's (or one
parse's) result:
- **entries** in Beancount's order (date, `sort_order` of the type, line number), ties broken by each entry's canonical
  text (its encoding), so a plugin that emits equal-ranked entries in another order changes nothing;
- **errors**: each error's type, message, source and entry, sorted by their encoding (the order a plugin reports them
  in is not part of the result);
- **options**: every option a plugin can read, except `input_hash`, which Beancount computes from each file's mtime
  and size (only its pickle cache reads it): a `touch` between the save and R4, or the save itself, would otherwise
  make an unchanged ledger a mismatch (BL-0118).

Values encode by type: strings, numbers (`Decimal` by its text, so `1.0` and `1.00` differ, as they do to Beancount),
dates, booleans, `None`, enums, types (by module and name); tuples with fields (Beancount's entries, postings, amounts,
costs) by their type name and fields; lists in order; sets and dicts by their members' encodings, sorted, so the hash
order of a process never shows; any other object by its class and attributes (`DisplayContext` in the options), with
cycles cut; and as a last resort its `repr`, memory addresses masked. File names (the `filename` of an entry's or a
posting's meta, an error's source, a `Document`'s file, the options `filename` and `include`) encode as `path_key`s
(§6.3), so two spellings of one file give one digest (spec: "no false mismatch from spelling or hash order").

The engine starts with `PYTHONHASHSEED=0` (§5.1), but the encoding does not rely on it.
"""

from __future__ import annotations

import datetime
import enum
import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Final, cast

from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.core import paths as core_paths

type KeyOf = Callable[[str], str]

PREFIX: Final = "sha256:"
_FILE_FIELDS: Final = frozenset({"filename"})
_META_FIELDS: Final = frozenset({"meta", "source"})
_PATH_OPTIONS: Final = frozenset({"filename", "include"})
_LEFT_OUT: Final = frozenset({"input_hash"})
_ADDRESS: Final = re.compile(r"0x[0-9a-fA-F]+")
_MAX_DEPTH: Final = 64


def path_keys(fs: FileSystem) -> KeyOf:
    """`path_key` of each file name, resolved once per name. A name that cannot be resolved (an error, a malformed
    path) encodes as itself, marked, the same way each time."""
    seen: dict[str, str] = {}

    def key_of(name: str) -> str:
        key = seen.get(name)
        if key is None:
            try:
                key = "k:" + core_paths.path_key(fs.resolve(name))
            except (OSError, ValueError):
                key = "n:" + name
            seen[name] = key
        return key

    return key_of


def _date(value: object) -> str:
    return "D" + cast("datetime.date", value).isoformat()


_SCALARS: Final[dict[type, Callable[[object], str]]] = {
    str: lambda v: "s" + repr(v),
    Decimal: lambda v: "d" + str(v),
    int: lambda v: "i" + str(v),
    bool: lambda v: "T" if v else "F",
    type(None): lambda _: "N",
    datetime.date: _date,
}
"""The plain values, by exact type (a subclass, such as `datetime.datetime`, goes the long way): one dictionary
lookup instead of a chain of tests, since a 54k ledger has about two million of them."""


class _Encoder:
    def __init__(self, key_of: KeyOf) -> None:
        self._key_of = key_of
        self._open: set[int] = set()
        self._fields: dict[type, tuple[str, ...]] = {}
        self._paths: dict[str, str] = {}

    def text(self, value: object) -> str:
        out: list[str] = []
        self._value(value, out, 0)
        return "".join(out)

    def _path(self, value: object, out: list[str], depth: int) -> None:
        if isinstance(value, str):
            text = self._paths.get(value)
            if text is None:
                text = self._paths[value] = "p" + repr(self._key_of(value))
            out.append(text)
        else:
            self._value(value, out, depth)

    def _meta(self, value: object, out: list[str], depth: int) -> None:
        if not isinstance(value, dict):
            self._value(value, out, depth)
            return
        items: list[str] = []
        for k, v in cast("dict[object, object]", value).items():
            pk, pv = _SCALARS.get(type(k)), _SCALARS.get(type(v))
            if pk is not None and pv is not None and k not in _FILE_FIELDS:
                items.append(f"{pk(k)}:{pv(v)}")
                continue
            o: list[str] = []
            self._value(k, o, depth + 1)
            o.append(":")
            if k in _FILE_FIELDS:
                self._path(v, o, depth + 1)
            else:
                self._value(v, o, depth + 1)
            items.append("".join(o))
        items.sort()
        out.append("<")
        out.append(",".join(items))
        out.append(">")

    def _value(self, value: object, out: list[str], depth: int) -> None:
        t = type(value)
        plain = _SCALARS.get(t)
        if plain is not None:
            out.append(plain(value))
            return
        if depth > _MAX_DEPTH:
            out.append("X")
            return
        fields = self._fields.get(t)
        if fields is None and isinstance(value, tuple) and hasattr(t, "_fields"):
            fields = self._fields[t] = cast("tuple[str, ...]", getattr(t, "_fields"))  # noqa: B009 (a namedtuple's)
        if fields is not None:
            out.append("(")
            out.append(t.__qualname__)
            for name, v in zip(fields, cast("tuple[object, ...]", value), strict=False):
                out.append(" ")
                plain = _SCALARS.get(type(v))
                if name in _META_FIELDS:
                    self._meta(v, out, depth + 1)
                elif name in _FILE_FIELDS:
                    self._path(v, out, depth + 1)
                elif plain is not None:
                    out.append(plain(v))
                else:
                    self._value(v, out, depth + 1)
            out.append(")")
            return
        if isinstance(value, list | tuple):
            out.append("[")
            for v in cast("list[object]", value):
                self._value(v, out, depth + 1)
                out.append(" ")
            out.append("]")
            return
        if isinstance(value, set | frozenset):
            out.append("{")
            out.append(" ".join(sorted(self._sub(v, depth) for v in cast("set[object]", value))))
            out.append("}")
            return
        if isinstance(value, Mapping):
            pairs = cast("Mapping[object, object]", value).items()
            out.append("<")
            out.append(",".join(sorted(f"{self._sub(k, depth)}:{self._sub(v, depth)}" for k, v in pairs)))
            out.append(">")
            return
        if isinstance(value, enum.Enum):
            out.append("e")
            out.append(f"{t.__qualname__}.{value.name}")
            return
        if isinstance(value, type):
            out.append("t")
            out.append(f"{value.__module__}.{value.__qualname__}")
            return
        if isinstance(value, float | complex | bytes):
            out.append("r")
            out.append(repr(value))
            return
        self._object(value, out, depth)

    def _sub(self, value: object, depth: int) -> str:
        o: list[str] = []
        self._value(value, o, depth + 1)
        return "".join(o)

    def _object(self, value: object, out: list[str], depth: int) -> None:
        """Any other object: its class and its attributes (`__dict__`, then `__slots__`), cycles cut; else its `repr`
        with memory addresses masked."""
        t = type(value)
        attrs: dict[str, object] = {}
        if hasattr(value, "__dict__"):
            attrs.update(cast("dict[str, object]", vars(value)))
        for cls in t.__mro__:
            slots = cast("object", getattr(cls, "__slots__", ()))
            names = (slots,) if isinstance(slots, str) else cast("tuple[object, ...]", slots)
            for slot in names:
                if isinstance(slot, str) and slot not in attrs and hasattr(value, slot):
                    attrs[slot] = getattr(value, slot)
        if not attrs:
            out.append("r")
            out.append(_ADDRESS.sub("0x?", repr(value)))
            return
        if id(value) in self._open:
            out.append("C")
            return
        self._open.add(id(value))
        try:
            out.append("o")
            out.append(f"{t.__module__}.{t.__qualname__}")
            self._value(attrs, out, depth + 1)
        finally:
            self._open.discard(id(value))


def _rank(entry: object) -> tuple[str, int, int]:
    """Beancount's order of entries (`data.entry_sortkey`), robust to an entry a plugin made without a date or a
    line number."""
    date = cast("object", getattr(entry, "date", None))
    meta = cast("object", getattr(entry, "meta", None))
    lineno = cast("dict[object, object]", meta).get("lineno") if isinstance(meta, dict) else None
    return (
        date.isoformat() if isinstance(date, datetime.date) else "",
        port.sort_order(entry),
        lineno if isinstance(lineno, int) and not isinstance(lineno, bool) else -1,
    )


def of_result(entries: Iterable[object], errors: Iterable[object], options: Mapping[str, object], key_of: KeyOf) -> str:
    """The digest of one load's (or one parse's) result (the module's docstring)."""
    enc = _Encoder(key_of)
    ranked = sorted((_rank(e), enc.text(e)) for e in entries)
    h = hashlib.sha256()
    h.update(b"finzytrack-load-digest v1\nentries\n")
    for _, text in ranked:
        h.update(text.encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    h.update(b"errors\n")
    for text in sorted(enc.text(e) for e in errors):
        h.update(text.encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    h.update(b"options\n")
    for name in sorted(options):
        if name in _LEFT_OUT:
            continue
        value = options[name]
        if name in _PATH_OPTIONS and isinstance(value, list):
            # Beancount sorts `include` by the names as spelled; keyed, they are sorted again
            keyed = [key_of(v) if isinstance(v, str) else v for v in cast("list[object]", value)]
            value = sorted(keyed, key=enc.text)
        elif name in _PATH_OPTIONS and isinstance(value, str):
            value = key_of(value)
        h.update(f"{name!r}={enc.text(value)}\n".encode("utf-8", "surrogatepass"))
    return PREFIX + h.hexdigest()
