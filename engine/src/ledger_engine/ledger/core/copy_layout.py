"""Where a copy puts each source file, and the lines it rewrites (`design/specs/engine-jobs.md` §8.2, the copy;
ADR-0017; S0081, the owner's decision 3 of 2026-11-07: "Copy them in"). Pure: paths are strings, nothing is read.

**The layout.** The folder of the source's root is the base (**B**). A file under B keeps its path relative to B.
Every other place is moved into the copy's folder under a name of its own:
- each documents root outside B becomes `documents` (a second one `documents-2`, and so on; ADR-0017: copied
  workspaces write `option "documents" "documents"`), with everything under it;
- the ledger files outside B (and the paths the load looked for there, and the folders of the `include` globs that
  reach there) go under `outside`, kept relative to their deepest common folder (**A**), so their own relative
  `include` lines still resolve among themselves.
A name a path under B already uses at the top of the copy (compared without case, as a case-insensitive volume does)
is never taken: the next number is (`outside-2`, `documents-3`). Under B is decided first, so a moved folder that
holds B (a documents root `..`) never moves B's own files.

**The rewrites** (`rewrite`): only an `include` line whose path, placed, would read another place (an absolute path,
or one that reaches outside B, or one in a moved file that reaches back into B), and only the root's
`option "documents"` lines whose root moves. Its new text is the placed path relative to the file's new folder. A
line is rewritten only when the lines found by their shape are exactly the strings Beancount parsed from that file
(`includes`, `documents`): a string with a quote, a backslash or a line break, an `include` hidden in a multi-line
string, or a line Beancount reads that the shape does not find, refuses the copy (`Refusal`). A rewrite changes no
line count, so every entry keeps its line number. What the copy then loads is compared with the source's
(`ledger_engine.ledger.loads_same`): a rewrite that changed meaning cannot pass unseen.

`back` maps a path in the copy back to the source's place, for that comparison.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

OUTSIDE: Final = "outside"
DOCUMENTS: Final = "documents"
_MAGIC: Final = re.compile(r"[*?[]")
_INCLUDE: Final = re.compile(rb'^include[ \t]+"([^"\\\r\n]*)"')
_DOCUMENTS: Final = re.compile(rb'^option[ \t]+"documents"[ \t]+"([^"\\\r\n]*)"')
_UNSAFE: Final = re.compile(r'["\\\r\n]')
_LOOSE: Final = re.compile(rb"^(?:include\b|option[ \t]+\"documents\")")


@dataclass(frozen=True, slots=True)
class SourceSet:
    """What the source's load named (all paths absolute and normalized as the loader normalizes them): its root, the
    ledger files it parsed, the paths it looked for and did not find, each `include`'s search string (as globbed),
    each documents root, and the file of each `document` entry."""

    root: str
    files: tuple[str, ...]
    missing: tuple[str, ...]
    globs: tuple[str, ...]
    documents: tuple[str, ...]
    document_files: tuple[str, ...]


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
        posixpath.dirname(p) for p in (*src.files, *src.missing) if _under(base, p) is None and not placed_by_move(p)
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


def rewrite(
    lay: Layout,
    source_file: str,
    data: bytes,
    *,
    is_root: bool,
    parse: Callable[[], tuple[Sequence[str], Sequence[str]]],
) -> bytes | Refusal | None:
    """`data` (the bytes of `source_file`, as the load parsed them) with its moved `include` lines and, for the root,
    its moved `option "documents"` lines rewritten; None when no line changes. The lines are found by their shape;
    when one must change (or has no place), `parse` gives the strings Beancount parsed from this file (its includes,
    and its documents roots: Beancount reads that option from the root only), which must be exactly the strings
    found, else `Refusal`."""
    lines = data.splitlines(keepends=True)
    found: list[tuple[int, re.Match[bytes], bool]] = []  # (line index, match, is a documents option)
    for i, line in enumerate(lines):
        if m := _INCLUDE.match(line):
            found.append((i, m, False))
        elif is_root and (m := _DOCUMENTS.match(line)):
            found.append((i, m, True))
    texts = [_text(m) for _, m, _ in found]
    try:
        new = [
            None if t is None else (_new_documents if docs else _new_include)(lay, source_file, t)
            for t, (_, _, docs) in zip(texts, found, strict=True)
        ]
    except LookupError:
        new = [""]  # a line with no place: Beancount decides below whether it reads it
    loose = sum(1 for line in lines if _LOOSE.match(line) and (is_root or line.startswith(b"include")))
    if all(n is None for n in new) and loose == len(found):
        return None  # every line of the shape found, none moves (a line the shape misses is checked below)
    includes, documents = parse()
    if [t for t, (_, _, d) in zip(texts, found, strict=True) if not d] != list(includes) or [
        t for t, (_, _, d) in zip(texts, found, strict=True) if d
    ] != (list(documents) if is_root else []):
        return Refusal(source_file, 0)
    try:
        new = [
            (_new_documents if docs else _new_include)(lay, source_file, t or "")
            for t, (_, _, docs) in zip(texts, found, strict=True)
        ]
    except LookupError:
        return Refusal(source_file, 0)
    for (i, m, _), n in zip(found, new, strict=True):
        if n is None:
            continue
        if _UNSAFE.search(n):
            return Refusal(source_file, i + 1)
        lines[i] = lines[i][: m.start(1)] + n.encode("utf-8") + lines[i][m.end(1) :]
    return b"".join(lines)


def inside(lay: Layout, p: str) -> bool:
    """Whether the source path `p` is under B."""
    return _under(lay.base, p) is not None


def _text(m: re.Match[bytes]) -> str | None:
    try:
        return m.group(1).decode("utf-8")
    except UnicodeDecodeError:
        return None
