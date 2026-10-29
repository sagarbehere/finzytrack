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
(`ledger_engine.ledger.paths`) and keys the fingerprint with the save's gate (TODO(BL-0151)); for equality this is
safe: the same ledger set is always observed under the same names, and two names for one file only make two entries
that change together.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

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
