"""Parser-level semantic diff of a ledger set (review:D1 §3 step 2), for the oracle harness.

Each file of the set is parsed on its own with Beancount's parser (no plugins, no booking). An entry's canonical
key is the printer's output for it with location-only meta removed and the remaining meta keys sorted. Files are
compared as multisets of keys, plus their header (options, plugins, includes) and their count of comment lines.
This is the oracle's own copy of the idea; the app's contract implements step 2 in `ledger/contract` (M1b).
"""

from __future__ import annotations

import glob
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beancount import loader
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


@dataclass(frozen=True)
class LedgerSet:
    base: Path
    root: str
    files: dict[str, ParsedFile]
    glob_files: frozenset[str]

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


def parse_file(path: Path) -> tuple[ParsedFile, list[str]]:
    """One file, parsed alone; returns it and its raw include strings."""
    entries, errors, opts = parser.parse_file(str(path))
    keys: Counter[str] = Counter()
    by_key: dict[str, Any] = {}
    for e in entries:
        k = canonical_key(e)
        keys[k] += 1
        by_key.setdefault(k, e)
    pf = ParsedFile(keys, by_key, _header(opts), _comment_lines(path), tuple(e.message for e in errors))
    return pf, list(opts.get("include") or ())


def parse_set(base: Path, root_file: str) -> LedgerSet:
    """The root file and every file it includes (globs expanded as Beancount's loader does), keyed by path
    relative to *base*."""
    base = base.resolve()
    files: dict[str, ParsedFile] = {}
    via_glob: set[str] = set()
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
            matches = sorted(glob.glob(pattern, recursive=True))
            if GLOB_CHARS & set(inc):
                via_glob.update(Path(os.path.relpath(m, base)).as_posix() for m in matches)
            todo.extend(Path(m).resolve() for m in matches)
    return LedgerSet(base, Path(root_file).as_posix(), files, frozenset(via_glob))


def generated_keys(base: Path, root_file: str) -> frozenset[str]:
    """Keys of entries a full load adds that no file contains: plugin output (e.g. implicit prices) and pad
    transactions. A loaded entry is generated when no parsed entry of its type sits at its file and line."""
    base = base.resolve()
    parsed_at: set[tuple[type, str, int]] = set()
    for rel in parse_set(base, root_file).files:
        entries, _, _ = parser.parse_file(str(base / rel))
        parsed_at |= {(type(e), str(Path(e.meta["filename"]).resolve()), e.meta["lineno"]) for e in entries}
    loader.initialize(use_cache=False)
    loaded, _, _ = loader.load_file(str(base / root_file))
    return frozenset(canonical_key(e) for e in loaded
                     if (type(e), str(Path(e.meta.get("filename", "")).resolve()), e.meta.get("lineno")) not in parsed_at)


def booked_by_legacy_id(base: Path, root_file: str) -> dict[str, Any]:
    """Loaded (booked) transactions by legacy `id`, as the v0.3 frontend saw them; ids carried twice are left out."""
    loader.initialize(use_cache=False)
    loaded, _, _ = loader.load_file(str(base.resolve() / root_file))
    ids = Counter(e.meta.get("id") for e in loaded if hasattr(e, "postings") and "id" in e.meta)
    return {e.meta["id"]: e for e in loaded if hasattr(e, "postings") and ids.get(e.meta.get("id")) == 1}


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


_EMPTY = ParsedFile(Counter(), {}, frozenset(), 0, ())


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
