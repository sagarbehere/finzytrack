"""Parser-level semantic diff of a ledger set (review:D1 §3 step 2), for the oracle harness.

Each file of the set is parsed on its own with Beancount's parser (no plugins, no booking). An entry's canonical
key is the printer's output for it with location-only meta removed and the remaining meta keys sorted. Files are
compared as multisets of keys, plus their header (options, plugins, includes) and their count of comment lines.
This is the oracle's own copy of the idea; the app's contract implements step 2 in `ledger/contract` (M1b).
"""

from __future__ import annotations

import copy
import glob
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.core import data
from beancount.ops import validation
from beancount.parser import booking
from beancount.parser import options as bc_options
from beancount.parser import parser, printer

LOCATION_KEYS = frozenset({"filename", "lineno"})
# Options the parser derives or records rather than reads from an `option` line.
DERIVED_OPTIONS = frozenset({"filename", "include", "plugin", "dcontext", "input_hash"})
GLOB_CHARS = frozenset("*?[")


def _strip(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not meta:
        return meta
    kept = {k: meta[k] for k in sorted(meta) if k not in LOCATION_KEYS and not k.startswith("__")}
    return kept or None


def strip_location(entry: Any) -> Any:
    """The entry with location-only meta removed and meta keys sorted, on the entry and on every posting."""
    entry = entry._replace(meta=_strip(entry.meta) or {})
    if hasattr(entry, "postings"):
        entry = entry._replace(postings=[p._replace(meta=_strip(p.meta)) for p in entry.postings])
    return entry


def canonical_key(entry: Any) -> str:
    return printer.format_entry(strip_location(entry))


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    return value


@dataclass(frozen=True)
class ParsedFile:
    keys: Counter[str]
    by_key: dict[str, Any]
    header: frozenset[tuple[str, str, Any]]
    comment_lines: int
    errors: tuple[str, ...]
    totals: dict[str, dict[int, Decimal]]  # key → {posting index: total} for postings written with `@@`
    lines: dict[str, tuple[int, ...]] = field(default_factory=dict)  # key → the line of each of its instances


@dataclass(frozen=True)
class LedgerSet:
    base: Path
    root: str
    files: dict[str, ParsedFile]
    glob_files: frozenset[str]
    outside: tuple[str, ...]  # include targets that resolve outside *base* (a copy must not reach them)

    def entry(self, key: str) -> Any:
        for f in self.files.values():
            if key in f.by_key:
                return f.by_key[key]
        raise KeyError(key)


def _header(opts: dict[str, Any]) -> frozenset[tuple[str, str, Any]]:
    items: set[tuple[str, str, Any]] = {("include", inc, None) for inc in opts.get("include") or ()}
    items |= {("plugin", name, conf) for name, conf in opts.get("plugin") or ()}
    for name, default in bc_options.OPTIONS_DEFAULTS.items():
        if name not in DERIVED_OPTIONS and opts.get(name) != default:
            items.add(("option", name, _hashable(opts.get(name))))
    return frozenset(items)


def _comment_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.lstrip().startswith(";"))


TOTAL_PRICE = re.compile(r"@@\s+(-?\d[\d,]*(?:\.\d+)?|-?\.\d+)\s+[A-Z]")


def total_price_postings(entry: Any, lines: list[str]) -> dict[int, Decimal]:
    """The total (as written) of each of the entry's postings whose source line uses `@@`; the parser keeps only the
    per-unit quotient, so this is read from the text. A total written as an expression is left out."""
    out: dict[int, Decimal] = {}
    for i, p in enumerate(getattr(entry, "postings", ())):
        n = (p.meta or {}).get("lineno")
        if n and 0 < n <= len(lines):
            m = TOTAL_PRICE.search(lines[n - 1].split(";", 1)[0])
            if m:
                out[i] = Decimal(m.group(1).replace(",", ""))
    return out


def parse_entries_with_totals(text: str, filename: str) -> tuple[list[Any], list[Any], dict[str, Any], dict[str, dict[int, Decimal]]]:
    entries, errors, opts = parser.parse_string(text, filename)
    lines = text.splitlines()
    return entries, errors, opts, {canonical_key(e): total_price_postings(e, lines) for e in entries}


def parse_file(path: Path) -> tuple[ParsedFile, list[str]]:
    """One file, parsed alone; returns it and its raw include strings."""
    entries, errors, opts, totals = parse_entries_with_totals(path.read_text(encoding="utf-8"), str(path))
    keys: Counter[str] = Counter()
    by_key: dict[str, Any] = {}
    lines: dict[str, list[int]] = {}
    for e in entries:
        k = canonical_key(e)
        keys[k] += 1
        by_key.setdefault(k, e)
        lines.setdefault(k, []).append(e.meta["lineno"])
    pf = ParsedFile(keys, by_key, _header(opts), _comment_lines(path), tuple(e.message for e in errors), totals,
                    {k: tuple(v) for k, v in lines.items()})
    return pf, list(opts.get("include") or ())


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def parse_set(base: Path, root_file: str) -> LedgerSet:
    """The root file and every file it includes (globs expanded as Beancount's loader does), keyed by path
    relative to *base*. Include targets that resolve outside *base* are listed in `outside` and not followed."""
    base = base.resolve()
    files: dict[str, ParsedFile] = {}
    via_glob: set[str] = set()
    outside: set[str] = set()
    todo = [(base / root_file).resolve()]
    while todo:
        path = todo.pop()
        rel = Path(os.path.relpath(path, base)).as_posix()
        if rel in files or not path.is_file():
            continue
        pf, includes = parse_file(path)
        files[rel] = pf
        for inc in includes:
            pattern = os.path.join(path.parent, inc)
            if not _inside(Path(os.path.realpath(pattern)), base):
                outside.add(os.path.realpath(pattern))
            matches = [Path(m).resolve() for m in sorted(glob.glob(pattern, recursive=True))]
            outside.update(str(m) for m in matches if not _inside(m, base))
            matches = [m for m in matches if _inside(m, base)]
            if GLOB_CHARS & set(inc):
                via_glob.update(Path(os.path.relpath(m, base)).as_posix() for m in matches)
            todo.extend(matches)
    return LedgerSet(base, Path(root_file).as_posix(), files, frozenset(via_glob), tuple(sorted(outside)))


def location(entry: Any) -> tuple[str, int]:
    """An entry's (resolved file, line), as the parser recorded it."""
    meta = entry.meta or {}
    return str(Path(meta.get("filename", "")).resolve()), meta.get("lineno")


def at_kind(entry: Any) -> tuple[str, str, int]:
    """An entry's kind and place: a generated entry may carry the place of the entry it came from (a pad's
    padding transaction carries the pad's), never its kind."""
    return (type(entry).__name__, *location(entry))


@dataclass(frozen=True)
class Parsed:
    """A ledger set as Beancount's loader parses it, before booking: the entries in the loader's order (root first,
    then each include as it is reached), the parse errors, the root's options, and per file its line count and
    whether it ends with a line end (the place an appended entry would take). Never handed to `book` itself."""
    entries: tuple[Any, ...]
    errors: tuple[Any, ...]
    options: dict[str, Any]
    files: dict[str, tuple[int, bool]]  # resolved path → (lines, ends with a line end)


def parse_ledger(base: Path, root_file: str) -> Parsed:
    """The loader's own parse (`loader._parse_recursive`, the first step of `loader.load_file`)."""
    entries, errors, options = loader._parse_recursive([(str(base.resolve() / root_file), True)], None)
    files: dict[str, tuple[int, bool]] = {}
    for path in {location(e)[0] for e in entries} | {str((base / root_file).resolve())}:
        p = Path(path)
        if p.is_file():
            text = p.read_bytes()
            files[path] = (text.count(b"\n") + (0 if not text or text.endswith(b"\n") else 1), text.endswith(b"\n"))
    return Parsed(tuple(entries), tuple(errors), options, files)


def _fresh(entries: list[Any]) -> list[Any]:
    """Each entry with its own metadata dicts: booking and plugins may change them in place, and a `Parsed` is
    booked more than once."""
    def one(e: Any) -> Any:
        e = e._replace(meta=dict(e.meta or {}))
        if isinstance(e, data.Transaction):
            e = e._replace(postings=[p._replace(meta=dict(p.meta) if p.meta is not None else None) for p in e.postings])
        return e
    return [one(e) for e in entries]


def book(entries: list[Any], parsed: Parsed) -> tuple[list[Any], list[Any]]:
    """What `loader.load_file` does after its parse, step for step (Beancount 3.2.0 `loader._load`): sort, book,
    run the plugins (with the ledger's `pythonpath`), validate. `test_booked.FidelityTest` holds it to the loader's
    own result on the repo's corpora."""
    options = copy.deepcopy(parsed.options)
    entries = sorted(_fresh(entries), key=data.entry_sortkey)
    errors = list(parsed.errors)
    entries, balance_errors = booking.book(entries, options)
    errors.extend(balance_errors)
    saved = list(sys.path)
    try:
        if "pythonpath" in options:
            sys.path[0:0] = options["pythonpath"]
        entries, errors = loader.run_transformations(entries, errors, options, None)
    finally:
        sys.path[:] = saved
    errors.extend(validation.validate(entries, options, None, None))
    return entries, errors


@dataclass(frozen=True)
class LoadFacts:
    """What one official full load (plugins, booking, validation) says about a ledger set."""
    generated: frozenset[str]  # keys of entries a load adds that no file contains (plugins, pads)
    booked_at: dict[tuple[str, int], Any]  # (resolved file, line) → the booked entry parsed there
    booked_by_id: dict[str, Any]  # legacy `id` → booked transaction (ids carried twice left out)
    errors: Counter[str]  # error keys (review:D1 §12.1)
    booked: tuple[Any, ...] = ()  # every entry of the load, in its order
    keys_at: dict[tuple[str, str, int], str] = field(default_factory=dict)  # (kind, file, line) → parsed entry's key
    parsed: Parsed | None = None  # the loader's parse, kept to book the intended ledger (BL-0275)


def error_key(err: Any, base: Path) -> str:
    """Error type + canonical entry + message with the base path and file:line locations stripped (D1 §12.1)."""
    entry = getattr(err, "entry", None)
    msg = re.sub(r"\S*\.(beancount|bean)(:\d+)?", "<file>", str(err.message).replace(str(base), "<base>"))
    return f"{type(err).__name__}|{canonical_key(entry) if entry is not None and hasattr(entry, 'meta') else ''}|{msg}"


def load_facts(base: Path, root_file: str, *, keep_parsed: bool = True) -> LoadFacts:
    """One official load (`loader.load_file`); with *keep_parsed*, also the loader's parse, kept for booking the
    intended ledger (the harness keeps it for the source only)."""
    base = base.resolve()
    keys_at: dict[tuple[str, str, int], str] = {}
    parsed_at: set[tuple[type, str, int]] = set()
    for rel in parse_set(base, root_file).files:
        entries, _, _ = parser.parse_file(str(base / rel))
        for e in entries:
            parsed_at.add((type(e), *location(e)))
            keys_at.setdefault(at_kind(e), canonical_key(e))
    loader.initialize(use_cache=False)
    loaded, errors, _ = loader.load_file(str(base / root_file))

    generated = frozenset(canonical_key(e) for e in loaded if (type(e), *location(e)) not in parsed_at)
    booked_at = {location(e): e for e in loaded if (type(e), *location(e)) in parsed_at}
    ids = Counter(e.meta.get("id") for e in loaded if hasattr(e, "postings") and "id" in e.meta)
    by_id = {e.meta["id"]: e for e in loaded if hasattr(e, "postings") and ids.get(e.meta.get("id")) == 1}
    return LoadFacts(generated, booked_at, by_id, Counter(error_key(e, base) for e in errors), tuple(loaded), keys_at,
                     parse_ledger(base, root_file) if keep_parsed else None)


def booked_of(facts: LoadFacts, entry: Any) -> Any | None:
    """The booked form of a parsed entry, found by its file and line."""
    return facts.booked_at.get(location(entry))


@dataclass
class FileDiff:
    removed: Counter[str] = field(default_factory=Counter)
    added: Counter[str] = field(default_factory=Counter)

    def is_empty(self) -> bool:
        return not self.removed and not self.added


@dataclass
class SetDiff:
    files: dict[str, FileDiff]
    header_changed: dict[str, tuple[frozenset, frozenset]]
    comments_lost: dict[str, int]

    def is_empty(self) -> bool:
        return not self.files and not self.header_changed and not self.comments_lost


_EMPTY = ParsedFile(Counter(), {}, frozenset(), 0, (), {})


def diff(before: LedgerSet, after: LedgerSet) -> SetDiff:
    files: dict[str, FileDiff] = {}
    header: dict[str, tuple[frozenset, frozenset]] = {}
    comments: dict[str, int] = {}
    for rel in sorted(set(before.files) | set(after.files)):
        b, a = before.files.get(rel, _EMPTY), after.files.get(rel, _EMPTY)
        fd = FileDiff(b.keys - a.keys, a.keys - b.keys)
        if not fd.is_empty():
            files[rel] = fd
        if b.header != a.header:
            header[rel] = (b.header, a.header)
        if a.comment_lines < b.comment_lines:
            comments[rel] = b.comment_lines - a.comment_lines
    return SetDiff(files, header, comments)
