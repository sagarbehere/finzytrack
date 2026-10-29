"""`canonical_path(fs, p)` and `path_key(fs, p)` over the file-system port (`design/specs/engine-jobs.md` §6.3; S0060).
The values and the folding are `ledger.core.paths`; here they are resolved on a file system."""

from __future__ import annotations

from dataclasses import dataclass

from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger.core import paths as core


@dataclass(frozen=True, slots=True)
class PathId:
    """A path's two identities (§6.3): `canonical` to open the file and in messages, `key` to compare it."""

    canonical: str
    key: str


def identify(fs: FileSystem, path: str) -> PathId:
    resolved = fs.resolve(path)
    return PathId(canonical=core.canonical_path(resolved), key=core.path_key(resolved))


def canonical_path(fs: FileSystem, path: str) -> str:
    return core.canonical_path(fs.resolve(path))


def path_key(fs: FileSystem, path: str) -> str:
    return core.path_key(fs.resolve(path))
