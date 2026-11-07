"""Where a copy puts each source file, and the lines it rewrites (`design/specs/engine-jobs.md` §8.2, the copy;
ADR-0017; S0081, the owner's decision 3 of 2026-11-07: "Copy them in"). Pure: paths are strings, nothing is read.

**The layout.** The folder of the source's root is the base (**B**). A file under B keeps its path relative to B.
Every other place is moved into the copy's folder under a name of its own:
- each documents root outside B becomes `documents` (a second one `documents-2`, and so on; ADR-0017: copied
  workspaces write `option "documents" "documents"`), with everything under it;
- the ledger files outside B (and the paths the load looked for there, the folders of the `include` globs that reach
  there, and the files of `document` directives there that no documents root holds) go under `outside`, kept
  relative to their deepest common folder (**A**), so their own relative `include` lines still resolve among
  themselves.
A name any copied path under B already uses at the top of the copy, a file of a documents root at or above B
included (S0081 r1 F2), is never taken (compared without case, as a case-insensitive volume does): the next number is
(`outside-2`, `documents-3`). Under B is decided first, so a moved folder that holds B (a documents root `..`) never
moves B's own files.

**The rewrites** (`rewrite`), decided from what Beancount parsed from the file (`Parsed`), never from the shape alone:
an `include` whose path, placed, would read another place (an absolute path, or one that reaches outside B, or one in
a moved file that reaches back into B), the root's `option "documents"` whose root moves, and a `document` directive
whose file moves or is named by an absolute path (S0081 r1 F3: owner's decision 3, the copy is self-contained). The new
text is the placed path relative to the file's new folder. The lines are then found by their shape (leading blanks
allowed: Beancount reads an indented `include` too): the `include` and `option "documents"` lines found must be
exactly the strings Beancount parsed, and a `document` directive's line (its line number) must hold a string that
resolves to the file Beancount resolved; otherwise (a quote, a backslash or a line break in a path, a line hidden in a
multi-line string, a line the shape misses) the copy is refused (`Refusal`). A rewrite changes no line count, so every
entry keeps its line number. What the copy then loads is compared with the source's
(`ledger_engine.ledger.loads_same`): a rewrite that changed meaning cannot pass unseen. Beancount quotes an `include`
as written in its error for a glob that matches nothing, so a rewrite reports each changed include text
(`Rewritten.quoted`) for that comparison to map back (S0081 r1 F4).

`back` maps a path in the copy back to the source's place, for that comparison.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

OUTSIDE: Final = "outside"
DOCUMENTS: Final = "documents"
_MAGIC: Final = re.compile(r"[*?[]")
_INCLUDE: Final = re.compile(rb'^[ \t]*include[ \t]+"([^"\\\r\n]*)"')
_DOCUMENTS: Final = re.compile(rb'^[ \t]*option[ \t]+"documents"[ \t]+"([^"\\\r\n]*)"')
_DOCUMENT: Final = re.compile(rb'^\d{4}[-/]\d{2}[-/]\d{2}[ \t]+document[ \t]+[^ \t"]+[ \t]+"([^"\\\r\n]*)"')
_UNSAFE: Final = re.compile(r'["\\\r\n]')


@dataclass(frozen=True, slots=True)
class SourceSet:
    """What the source's load named (all paths absolute and normalized as the loader normalizes them): its root, the
    ledger files it parsed, the paths it looked for and did not find, each `include`'s search string (as globbed),
    each documents root, the file of each `document` entry (there or not), and every file listed under a documents
    root."""

    root: str
    files: tuple[str, ...]
    missing: tuple[str, ...]
    globs: tuple[str, ...]
    documents: tuple[str, ...]
    document_files: tuple[str, ...]
    listed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Layout:
    """`base`: B. `moves`: each moved source folder and its folder in the copy (relative), longest source first."""

    base: str
    moves: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Refusal:
    """A line that cannot be rewritten (`path`: the source file; `line`: its 1-based number, 0 when no line is
    known)."""

    path: str
    line: int


def _under(folder: str, p: str) -> str | None:
    """`p` relative to `folder` ("" for the folder itself), or None when it is not under it."""
    if folder == "/":
        return p[1:]
    if p == folder:
        return ""
    if p.startswith(folder + "/"):
        return p[len(folder) + 1 :]
    return None


def _fold(name: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def literal_folder(pattern: str) -> str:
    """The folder of a glob pattern before its first component with a wildcard (the pattern's own folder when it has
    none)."""
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        if _MAGIC.search(part):
            return "/".join(parts[:i]) or "/"
    return posixpath.dirname(pattern) or "/"


def _free(name: str, used: set[str]) -> str:
    n = 1
    while True:
        candidate = name if n == 1 else f"{name}-{n}"
        if _fold(candidate) not in used:
            used.add(_fold(candidate))
            return candidate
        n += 1


def _common(folders: Iterable[str]) -> str | None:
    folders = list(folders)
    return posixpath.commonpath(folders) if folders else None


def layout(src: SourceSet) -> Layout:
    """The module's docstring: B, the moved documents roots, then the outside files' folder."""
    base = posixpath.dirname(src.root) or "/"
    used: set[str] = set()
    inside_paths = [
        *src.files,
        *src.missing,
        *src.documents,
        *src.document_files,
        *src.listed,
        *(literal_folder(posixpath.normpath(g)) for g in src.globs),
    ]
    for p in inside_paths:
        rel = _under(base, p)
        if rel:
            used.add(_fold(rel.split("/")[0]))
    moves: list[tuple[str, str]] = []
    for root in src.documents:
        if _under(base, root) is None:
            moves.append((root, _free(DOCUMENTS, used)))
    moved = [m[0] for m in moves]

    def placed_by_move(p: str) -> bool:
        return any(_under(m, p) is not None for m in moved)

    outside = [
        posixpath.dirname(p)
        for p in (*src.files, *src.missing, *src.document_files)
        if _under(base, p) is None and not placed_by_move(p)
    ]
    outside += [
        f
        for g in src.globs
        if _under(base, f := literal_folder(posixpath.normpath(g))) is None and not placed_by_move(f)
    ]
    a = _common(outside)
    if a is not None:
        moves.append((a, _free(OUTSIDE, used)))
    moves.sort(key=lambda m: -len(m[0]))
    return Layout(base, tuple(moves))


def place(lay: Layout, p: str) -> str | None:
    """Where the source path `p` goes in the copy, relative to its folder ("" for the folder itself), or None when the
    layout has no place for it."""
    rel = _under(lay.base, p)
    if rel is not None:
        return rel
    for src, dst in lay.moves:
        rel = _under(src, p)
        if rel is not None:
            return posixpath.join(dst, rel) if rel else dst
    return None


def back(lay: Layout, target: str) -> tuple[re.Pattern[str], dict[str, str]]:
    """The mapping from paths in the copy (in its folder `target`) back to the source's places, as one pattern and
    its replacements: every occurrence is replaced in one pass, longest first, so nothing is replaced twice
    (`loads_same`)."""
    t = target.rstrip("/")
    table: dict[str, str] = {}
    for src, dst in lay.moves:
        table[f"{t}/{dst}/"] = src.rstrip("/") + "/"
        table[f"{t}/{dst}"] = src  # the moved folder itself (a documents root, `pythonpath`)
    table[f"{t}/"] = lay.base.rstrip("/") + "/"
    table[t] = lay.base
    # A key without its slash names a whole folder: it must not be followed by more of a name (`documents` is not
    # the start of `documentsX`).
    parts = [re.escape(k) + ("" if k.endswith("/") else _END) for k in sorted(table, key=len, reverse=True)]
    return re.compile("|".join(parts)), table


_END: Final = r"(?![^\"'\s:,)\]])"


def _relative(to: str, start_folder: str) -> str:
    """`to` relative to `start_folder`, both relative to the copy's folder ("" is the folder itself)."""
    return posixpath.relpath("/" + to if to else "/", "/" + start_folder if start_folder else "/")


def _new_include(lay: Layout, source_file: str, s: str) -> str | None:
    """The new text of `include "s"` in `source_file`, or None when the line stays as it is. Raises `LookupError`
    when the include has no place in the copy."""
    search = s if s.startswith("/") else posixpath.join(posixpath.dirname(source_file), s)
    to = place(lay, posixpath.normpath(search))
    here = place(lay, source_file)
    if to is None or here is None:
        raise LookupError(s)
    new = _relative(to, posixpath.dirname(here))
    if not s.startswith("/") and posixpath.normpath(s) == new:
        return None
    return new


def _new_documents(lay: Layout, root_file: str, v: str) -> str | None:
    """The new value of the root's `option "documents" "v"`, or None when the line stays. `LookupError` as above."""
    resolved = posixpath.normpath(v if v.startswith("/") else posixpath.join(posixpath.dirname(root_file), v))
    to = place(lay, resolved)
    if to is None:
        raise LookupError(v)
    new = to or "."
    if not v.startswith("/") and posixpath.normpath(v) == new:
        return None
    return new


@dataclass(frozen=True, slots=True)
class Parsed:
    """What Beancount parsed from one file: its `include` strings in order, its `option "documents"` strings (the
    root's only), and each `document` directive's line number and the file Beancount resolved it to."""

    includes: Sequence[str]
    documents: Sequence[str]
    document_lines: Sequence[tuple[int, str]]


@dataclass(frozen=True, slots=True)
class Rewritten:
    """A file's new bytes, and each changed `include` text with the text it replaced (new, old)."""

    data: bytes
    quoted: tuple[tuple[str, str], ...]


def _new_document(lay: Layout, source_file: str, raw: str, resolved: str) -> str | None:
    """The new text of a `document` directive's `"raw"` (Beancount resolved it to `resolved`), or None when it stays.
    Raises `LookupError` when the text does not name that file or the file has no place in the copy."""
    named = posixpath.normpath(raw if raw.startswith("/") else posixpath.join(posixpath.dirname(source_file), raw))
    to = place(lay, resolved)
    here = place(lay, source_file)
    if named != resolved or to is None or here is None:
        raise LookupError(raw)
    new = _relative(to, posixpath.dirname(here))
    if not raw.startswith("/") and posixpath.normpath(raw) == new:
        return None
    return new


def rewrite(lay: Layout, source_file: str, data: bytes, parsed: Parsed) -> Rewritten | Refusal | None:
    """`data` (the bytes of `source_file`, as the load parsed them) with its moved lines rewritten (the module's
    docstring); None when no line changes."""
    try:
        new_inc = [_new_include(lay, source_file, s) for s in parsed.includes]
        new_opt = [_new_documents(lay, source_file, v) for v in parsed.documents]
    except LookupError:
        return Refusal(source_file, 0)
    lines = data.splitlines(keepends=True)
    changes: list[tuple[int, re.Match[bytes], str]] = []
    for lineno, resolved in parsed.document_lines:
        m = _DOCUMENT.match(lines[lineno - 1]) if 0 < lineno <= len(lines) else None
        raw = None if m is None else _text(m)
        if m is None or raw is None:
            return Refusal(source_file, lineno)
        try:
            new = _new_document(lay, source_file, raw, resolved)
        except LookupError:
            return Refusal(source_file, lineno)
        if new is not None:
            changes.append((lineno - 1, m, new))
    if any(n is not None for n in (*new_inc, *new_opt)):
        found_inc = [(i, m) for i, line in enumerate(lines) if (m := _INCLUDE.match(line))]
        found_opt = [(i, m) for i, line in enumerate(lines) if parsed.documents and (m := _DOCUMENTS.match(line))]
        if [_text(m) for _, m in found_inc] != list(parsed.includes) or [_text(m) for _, m in found_opt] != list(
            parsed.documents
        ):
            return Refusal(source_file, 0)
        for (i, m), new in (*zip(found_inc, new_inc, strict=True), *zip(found_opt, new_opt, strict=True)):
            if new is not None:
                changes.append((i, m, new))
    if not changes:
        return None
    for i, m, new in changes:
        if _UNSAFE.search(new):
            return Refusal(source_file, i + 1)
        lines[i] = lines[i][: m.start(1)] + new.encode("utf-8") + lines[i][m.end(1) :]
    quoted = tuple((n, o) for o, n in zip(parsed.includes, new_inc, strict=True) if n is not None)
    return Rewritten(b"".join(lines), quoted)


def inside(lay: Layout, p: str) -> bool:
    """Whether the source path `p` is under B."""
    return _under(lay.base, p) is not None


def _text(m: re.Match[bytes]) -> str | None:
    try:
        return m.group(1).decode("utf-8")
    except UnicodeDecodeError:
        return None
