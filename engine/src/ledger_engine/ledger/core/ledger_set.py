"""The extended fingerprint of a ledger set, as a value (ADR-0008 step 4, ADR-0015, `design/specs/engine-jobs.md` §1
"Fingerprint"; S0051). Pure.

A **ledger set** is everything one load looked at: its root, every file it named (read, or looked for and not
found), every `include` it globbed (plain paths too: the loader globs each), every documents root it scanned, and the
file of every `document` entry whose existence Beancount checks. A **fingerprint** is one observation of it: per file
the SHA-256 of its bytes, `absent` (no file: `os.path.exists` is false) or `unreadable {errno}`; per include the
glob's match list in the loader's order (it decides the order files are parsed in, so it can change entry order; for
a plain path it is the path or nothing, the glob's `lexists`, which tells "does not exist" from "does not match");
per documents root its file names, each with whether it leads to a file, or `absent`; per document file whether it
exists. Equal fingerprints mean the next load reads the same inputs; that is the whole contract, and what the write gate
(ADR-0008 step 4, ADR-0013) and the mirror's freshness (ADR-0015) rely on. The one input of a load left out on purpose
is each file's mtime and size, which Beancount reads only into `options["input_hash"]` (its cache key; no entry, error
or other option depends on it): a fingerprint that held them would change on every `touch`. A digest that compares two
loads' results must therefore leave `input_hash` out (R4, TODO(BL-0118)).

Content hashes, not `(mtime, size)` as ADR-0015 first wrote: an edit that keeps the size within the clock's
resolution, or a tool that restores the mtime, would pass a stat (ADR-0064's spec, S0051 journal).

`digest` is `sha256:` + the SHA-256 of `encode`, a canonical JSON encoding with a version (`v`). Changing what the
encoding means bumps `v`; the price of any change is one mirror rebuild per workspace (a cache, ADR-0015).

Paths are keyed as the load named them. The spec keys them by `path_key` (§6.3), which exists since S0060
(`ledger_engine.ledger.paths`); for equality this is safe: the same ledger set is always observed under the same names,
and two names for one file only make two entries that change together. The save's gate and re-check (S0063) compare
observations of one ledger set under its own names, and lock by `path_key`; keying the stored fingerprint itself by
`path_key` comes with the published generation that carries it (TODO(BL-0125)).

`decode` is `encode`'s strict inverse (the plan's fingerprint crosses the engine protocol as its encoding, S0063);
`as_before` is what the disk must hold for a load of a save's candidate to have read it (the re-check, §6.1 step 6);
`as_after` what it holds once that save landed (the closed record's post fingerprint, R4).
"""

from __future__ import annotations

import glob
import hashlib
import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final, cast

ENCODING_VERSION: Final = 1
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")


class NotCanonicalError(ValueError):
    """A caller's error: a fingerprint built out of canonical order, or a content state that is not a SHA-256."""

    def __init__(self, what: str) -> None:
        super().__init__(f"not canonical: {what} (keys sorted and unique, listings sorted, sha256 lower-case hex)")


@dataclass(frozen=True, slots=True)
class Content:
    """The file was read: the SHA-256 of its bytes (lower-case hex)."""

    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.sha256):
            raise NotCanonicalError("sha256")


@dataclass(frozen=True, slots=True)
class Absent:
    """No file at the path: `os.path.exists`, the loader's question, is false (nothing there, or a link to nothing:
    the loader cannot tell them apart; whether its glob found the name is the include's match list)."""


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Something exists at the path but could not be read: the error's errno name (the next load would fail)."""

    error: str


type FileState = Content | Absent | Unreadable
# A documents root's file names (relative, sorted), each with whether it leads to a file (`os.path.exists`: Beancount
# makes a `document` entry for a dated name and then checks it); None when the root does not exist.
type Listing = tuple[tuple[str, bool], ...] | None


@dataclass(frozen=True, slots=True)
class LedgerSet:
    """What to observe: the keys of a fingerprint."""

    root: str
    files: tuple[str, ...]
    globs: tuple[str, ...]
    documents: tuple[str, ...]
    document_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One observation of a ledger set. Built in canonical order only (keys sorted and unique, listings sorted), so
    two equal observations are equal values; `Fingerprint.of` sorts for the caller."""

    root: str
    files: tuple[tuple[str, FileState], ...]
    globs: tuple[tuple[str, tuple[str, ...]], ...]
    documents: tuple[tuple[str, Listing], ...]
    document_files: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        for name, keys in (
            ("files", [k for k, _ in self.files]),
            ("globs", [k for k, _ in self.globs]),
            ("documents", [k for k, _ in self.documents]),
            ("document_files", [k for k, _ in self.document_files]),
        ):
            if not _strictly_sorted(keys):
                raise NotCanonicalError(name)
        for _, listing in self.documents:
            if listing is not None and not _strictly_sorted([name for name, _ in listing]):
                raise NotCanonicalError("listing")

    @classmethod
    def of(
        cls,
        root: str,
        files: Mapping[str, FileState],
        globs: Mapping[str, Sequence[str]],
        documents: Mapping[str, Mapping[str, bool] | None],
        document_files: Mapping[str, bool],
    ) -> Fingerprint:
        return cls(
            root,
            tuple(sorted(files.items())),
            tuple((k, tuple(v)) for k, v in sorted(globs.items())),
            tuple((k, None if v is None else tuple(sorted(v.items()))) for k, v in sorted(documents.items())),
            tuple(sorted(document_files.items())),
        )

    @property
    def ledger_set(self) -> LedgerSet:
        return LedgerSet(
            self.root,
            tuple(k for k, _ in self.files),
            tuple(k for k, _ in self.globs),
            tuple(k for k, _ in self.documents),
            tuple(k for k, _ in self.document_files),
        )


def _strictly_sorted(items: Sequence[str]) -> bool:
    return all(a < b for a, b in pairwise(items))


def _state(state: FileState) -> list[str]:
    match state:
        case Content(sha256=h):
            return ["sha256", h]
        case Absent():
            return ["absent"]
        case Unreadable(error=e):
            return ["unreadable", e]


def encode(fp: Fingerprint) -> bytes:
    """Canonical JSON: fixed key order, no spaces, ASCII only (undecodable file names stay as escapes)."""
    value = {
        "v": ENCODING_VERSION,
        "root": fp.root,
        "files": [[p, _state(s)] for p, s in fp.files],
        "globs": [[p, list(m)] for p, m in fp.globs],
        "documents": [[p, None if m is None else [[n, e] for n, e in m]] for p, m in fp.documents],
        "document_files": [[p, e] for p, e in fp.document_files],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(fp: Fingerprint) -> str:
    """The fingerprint as one string: what the mirror's stamp and `ledger_version` will carry (TODO(BL-0125))."""
    return "sha256:" + hashlib.sha256(encode(fp)).hexdigest()


class UndecodableError(ValueError):
    """Bytes that are not an encoding `encode` makes (`what` is content-free)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"not an encoded fingerprint: {what}")


def _items(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise UndecodableError(what)
    return cast(list[object], value)


def _pair(value: object, what: str) -> tuple[str, object]:
    items = _items(value, what)
    if len(items) != 2 or not isinstance(items[0], str):
        raise UndecodableError(what)
    return items[0], items[1]


def _decode_state(value: object) -> FileState:
    items = _items(value, "state")
    match items:
        case ["sha256", str(h)]:
            return Content(h)
        case ["absent"]:
            return Absent()
        case ["unreadable", str(e)]:
            return Unreadable(e)
        case _:
            raise UndecodableError("state")


def _strings(value: object, what: str) -> tuple[str, ...]:
    items = _items(value, what)
    if not all(isinstance(i, str) for i in items):
        raise UndecodableError(what)
    return tuple(cast(list[str], items))


def _listing(value: object) -> Listing:
    if value is None:
        return None
    out: list[tuple[str, bool]] = []
    for item in _items(value, "listing"):
        name, exists = _pair(item, "listing")
        if not isinstance(exists, bool):
            raise UndecodableError("listing")
        out.append((name, exists))
    return tuple(out)


def decode(data: bytes) -> Fingerprint:
    """The strict inverse of `encode` (S0063: the plan's fingerprint crosses the engine protocol as its encoding):
    exactly the bytes `encode` would make for the value, or `UndecodableError`."""
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise UndecodableError("json") from None
    if not isinstance(value, dict):
        raise UndecodableError("object")
    obj = cast(dict[str, object], value)
    if set(obj) != {"v", "root", "files", "globs", "documents", "document_files"} or obj["v"] != ENCODING_VERSION:
        raise UndecodableError("keys_or_version")
    root = obj["root"]
    if not isinstance(root, str):
        raise UndecodableError("root")
    try:
        files = tuple((p, _decode_state(s)) for p, s in (_pair(i, "files") for i in _items(obj["files"], "files")))
        globs = tuple((p, _strings(m, "globs")) for p, m in (_pair(i, "globs") for i in _items(obj["globs"], "globs")))
        documents = tuple(
            (p, _listing(m)) for p, m in (_pair(i, "documents") for i in _items(obj["documents"], "documents"))
        )
        document_files: list[tuple[str, bool]] = []
        for item in _items(obj["document_files"], "document_files"):
            p, e = _pair(item, "document_files")
            if not isinstance(e, bool):
                raise UndecodableError("document_files")
            document_files.append((p, e))
        fp = Fingerprint(root, files, globs, documents, tuple(document_files))
    except NotCanonicalError:
        raise UndecodableError("not_canonical") from None
    if encode(fp) != data:
        raise UndecodableError("not_canonical")
    return fp


def as_before(loaded: Fingerprint, pre: Mapping[str, str | None]) -> Fingerprint:
    """What the disk must hold now for a load of a save's candidate to have read it (`design/specs/engine-jobs.md`
    §6.1 step 6, S0063): the load's own record `loaded` (`Loaded.fingerprint`), with each candidate file (`pre`: its
    path as the load named it, and the SHA-256 of its bytes before the save, None for a file the save creates) at its
    pre state instead of the candidate's bytes the load parsed, and a plain-path include of a file the save creates
    matching nothing (the load answered it with the candidate, `overlay.load`; the disk has no file there yet).
    Everything else the load read from the disk, so it must be on the disk as the load read it."""
    files = tuple(
        (p, (Absent() if pre[p] is None else Content(cast(str, pre[p]))) if p in pre else s) for p, s in loaded.files
    )
    created = {p for p, h in pre.items() if h is None}
    globs = tuple((g, () if not glob.has_magic(g) and posixpath.normpath(g) in created else m) for g, m in loaded.globs)
    return Fingerprint(loaded.root, files, globs, loaded.documents, loaded.document_files)


def name_under(root_key: str, key: str, canonical: str) -> str | None:
    """The name a documents root's listing gives a file (relative to the root, `/`-joined, as `fingerprint.listing`
    makes it), from the root's `path_key` and the file's `path_key` and canonical path (§6.3: one folded component per
    component, links resolved, the stored spelling of what exists): the canonical path's tail below the root when the
    file's key lies under the root's; None otherwise (S0067, BL-0172 b). The tail is how the walk of the root will
    list the file once it exists, unless a folder inside the root is a link to where the file is named: that file is
    not matched (its key is not under the root's), so R4 finds the listing moved and is `superseded`."""
    root_parts = [p for p in root_key.split("/") if p]
    parts = [p for p in key.split("/") if p]
    canon = [p for p in canonical.split("/") if p]
    if len(parts) != len(canon) or len(parts) <= len(root_parts) or parts[: len(root_parts)] != root_parts:
        return None
    return "/".join(canon[len(root_parts) :])


def listing_after(listing: Mapping[str, bool] | None, created: Sequence[str]) -> dict[str, bool] | None:
    """A documents root's listing once the files `created` (names `name_under` gives) exist: each added, present; a
    root that did not exist (None) holds only them; with nothing created, the listing as it was."""
    if not created:
        return None if listing is None else dict(listing)
    return {**(listing or {}), **dict.fromkeys(created, True)}


def as_after(loaded: Fingerprint, created: Mapping[str, Sequence[str]]) -> Fingerprint:
    """What the disk holds once a save of the candidate `loaded` was parsed from has landed (the closed record's post
    fingerprint, which R4 observes; S0066 red team r1 F1): the load's own record, where every file already has its
    candidate's state and a plain-path include of a created file already matches it (`overlay.load`), with the files
    the load's prediction counted under each documents root (`created`: `Loaded.created_documents`, matched by
    `path_key`, BL-0172) in that root's listing, and a `document` file named exactly so present. The load listed the
    roots from the disk before the commit, and its documents step walked these listings (`overlay`), so the record and
    the prediction agree. A created file the load did not match is not added: R4 then finds the fingerprint moved
    (`superseded`), never a false `pass`."""
    documents: list[tuple[str, Listing]] = []
    now_present: set[str] = set()
    for root, listing in loaded.documents:
        names = tuple(created.get(root, ()))
        if not names:
            documents.append((root, listing))
            continue
        after = listing_after(dict(listing or ()), names)
        documents.append((root, None if after is None else tuple(sorted(after.items()))))
        now_present.update(posixpath.join(root, n) for n in names)
    document_files = tuple((p, e or p in now_present) for p, e in loaded.document_files)
    return Fingerprint(loaded.root, loaded.files, loaded.globs, tuple(documents), document_files)
