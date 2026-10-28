"""The extended fingerprint of a ledger set, as a value (ADR-0008 step 4, ADR-0015, `design/specs/engine-jobs.md` §1
"Fingerprint"; S0051). Pure.

A **ledger set** is everything one load looked at: its root, every file it named (read, or looked for and absent),
every glob `include` it expanded, every documents root it scanned. A **fingerprint** is one observation of it: per
file the SHA-256 of its bytes, `absent` or `unreadable {errno}`; per glob the match list in the loader's order (it
decides the order files are parsed in, so it can change entry order); per documents root its file names or
`absent`. Equal fingerprints mean the next load reads the same inputs; that is the whole contract, and what the
write gate (ADR-0008 step 4, ADR-0013) and the mirror's freshness (ADR-0015) rely on.

Content hashes, not `(mtime, size)` as ADR-0015 first wrote: an edit that keeps the size within the clock's
resolution, or a tool that restores the mtime, would pass a stat (ADR-0064's spec, S0051 journal).

`digest` is `sha256:` + the SHA-256 of `encode`, a canonical JSON encoding with a version (`v`). Changing what the
encoding means bumps `v`; the price of any change is one mirror rebuild per workspace (a cache, ADR-0015).

Paths are keyed as the load named them. The spec keys them by `path_key` (§6.3), which does not exist yet
(TODO(BL-0109)); for equality this is safe: the same ledger set is always observed under the same names, and two
names for one file only make two entries that change together.
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
    """Nothing exists at the path (as `os.path.exists` says, which is what the loader asks)."""


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Something exists at the path but could not be read: the error's errno name (the next load would fail)."""

    error: str


type FileState = Content | Absent | Unreadable
type Listing = tuple[str, ...] | None  # a documents root's file names (relative, sorted), or None when absent


@dataclass(frozen=True, slots=True)
class LedgerSet:
    """What to observe: the keys of a fingerprint."""

    root: str
    files: tuple[str, ...]
    globs: tuple[str, ...]
    documents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One observation of a ledger set. Built in canonical order only (keys sorted and unique, listings sorted), so
    two equal observations are equal values; `Fingerprint.of` sorts for the caller."""

    root: str
    files: tuple[tuple[str, FileState], ...]
    globs: tuple[tuple[str, tuple[str, ...]], ...]
    documents: tuple[tuple[str, Listing], ...]

    def __post_init__(self) -> None:
        for name, keys in (
            ("files", [k for k, _ in self.files]),
            ("globs", [k for k, _ in self.globs]),
            ("documents", [k for k, _ in self.documents]),
        ):
            if not _strictly_sorted(keys):
                raise NotCanonicalError(name)
        for _, listing in self.documents:
            if listing is not None and not _strictly_sorted(listing):
                raise NotCanonicalError("listing")

    @classmethod
    def of(
        cls,
        root: str,
        files: Mapping[str, FileState],
        globs: Mapping[str, Sequence[str]],
        documents: Mapping[str, Sequence[str] | None],
    ) -> Fingerprint:
        return cls(
            root,
            tuple(sorted(files.items())),
            tuple((k, tuple(v)) for k, v in sorted(globs.items())),
            tuple((k, None if v is None else tuple(sorted(set(v)))) for k, v in sorted(documents.items())),
        )

    @property
    def ledger_set(self) -> LedgerSet:
        return LedgerSet(
            self.root,
            tuple(k for k, _ in self.files),
            tuple(k for k, _ in self.globs),
            tuple(k for k, _ in self.documents),
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
        "documents": [[p, None if m is None else list(m)] for p, m in fp.documents],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(fp: Fingerprint) -> str:
    """The fingerprint as one string: what the mirror's stamp and `ledger_version` will carry (TODO(BL-0125))."""
    return "sha256:" + hashlib.sha256(encode(fp)).hexdigest()
