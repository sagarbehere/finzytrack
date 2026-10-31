"""The engine's file-system port: every write of a ledger file, a backup or a journal goes through it
(`design/specs/engine-jobs.md` §6.1 step 8, §6.2, §6.3; ADR-0012, ADR-0013; S0060).

Writes act on a **handle on one directory** (`DirHandle`), never on a path: a check of where the directory is
(`DirHandle.path`) and the write that follows use the same descriptor, so the directory cannot be swapped for a link
between them (§6.3 "Path guard"; `renameatx_np` on macOS). Names given to a handle are single components (no `/`,
never `.` or `..`); a name is never followed through a link.

Durability (§6.2, §13 "File-system assumptions"): a file's data is durable after `DirHandle.create` returns (it
syncs the data before it returns), and an existing file's after `DirHandle.sync_file`; an entry made by `create`,
`swap`, `rename_excl`, `rename`, `remove` or `mkdir` is durable only after the directory's `sync`, which makes
everything done before it in that directory durable. A swap and an exclusive rename are one operation: a power loss
keeps or drops each whole; so is a plain rename, except over an existing file on a volume without a journal (exFAT,
FAT: `rename`). Nothing is assumed about the order in
which unsynced operations reach the disk. Every sync is `durable_sync`: `F_FULLFSYNC` on macOS.

Every operation may raise `OSError`. The real adapter is `ledger_engine.fs.darwin` (macOS); the test adapter is the
in-memory fake `tests/fakes/memfs.py`, with a separate durable layer, crash, I/O-error and power-loss injection.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self


@dataclass(frozen=True, slots=True)
class FoldRule:
    """How a directory's volume compares the names in it (§6.3): `nfd` (macOS: always), `casefold` (a
    case-insensitive volume or directory)."""

    nfd: bool
    casefold: bool


@dataclass(frozen=True, slots=True)
class Resolved:
    """A path resolved as far as it exists (§6.3).

    `existing`: the components of the deepest existing ancestor's canonical path (every link resolved, each
    component in the volume's stored spelling; `()` for the root). `rest`: the remaining components, as written.
    `rules`: one per component of `existing + rest`, the rule of the directory that holds it (for `rest`, the
    deepest existing ancestor's)."""

    existing: tuple[str, ...]
    rest: tuple[str, ...]
    rules: tuple[FoldRule, ...]
    identity: tuple[int, int] | None = None
    """`(st_dev, st_ino)` of the last existing component, the object the canonical path was checked to name."""


class Right(StrEnum):
    """What the save needs to be allowed, as the system would decide it (permissions and ACLs, `access(2)`)."""

    CHANGE_ENTRIES = "change_entries"  # in a folder: add, rename and remove entries (write and search)
    REMOVE_ENTRIES = "remove_entries"  # in a folder: remove and rename away entries (`delete_child`; `_RMFILE_OK`)
    SEARCH = "search"  # in a folder: look names up in it (`X_OK`; only to name the folder when a lookup fails)
    DELETE = "delete"  # an entry may be renamed away or removed (its own ACL, or the folder's delete_child)
    READ_XATTRS = "read_xattrs"  # a file's extended attributes may be read (to copy them to the temp)


@dataclass(frozen=True, slots=True)
class AclEntry:
    """One entry of a folder's extended ACL, as the system stores it (S0061 r6). `deny`: a deny entry (an allow entry,
    False; a kind the adapter does not know counts as a deny). `perms` and `flags` in `chmod`'s words (`delete_child`,
    `file_inherit`, ...); a bit the adapter cannot name is `unknown`. Whom the entry names is not read: every deny
    entry is judged whatever its subject (owner, 2026-10-30: "Any subject (Recommended)"; S0061 r7)."""

    deny: bool
    perms: frozenset[str]
    flags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Caps:
    """What a folder's volume offers (S0065): `swap`, the atomic exchange of two entries (`RENAME_SWAP`); `excl`, a
    rename that fails when its target exists (`RENAME_EXCL`). APFS offers both; HFS+ only `excl`; exFAT, FAT and
    network shares neither (probed on disk images, S0065)."""

    swap: bool
    excl: bool


class Kind(StrEnum):
    FILE = "file"
    DIR = "dir"
    LINK = "link"
    OTHER = "other"  # a FIFO, a socket, a device


@dataclass(frozen=True, slots=True)
class EntryStat:
    """One entry, not followed through a link (`lstat`). `locked`: an immutable or append-only flag is set
    (`uchg`, `schg`, `uappnd`, `sappnd`): the entry cannot be swapped, renamed or removed."""

    kind: Kind
    dev: int
    ino: int
    nlink: int
    mode: int
    locked: bool

    @property
    def identity(self) -> tuple[int, int]:
        return (self.dev, self.ino)


class DirHandle(Protocol):
    """An open directory. Close it (or use it as a context manager) when done."""

    def path(self) -> str:
        """The directory's canonical path now (`F_GETPATH`): after the directory moved, its new place."""
        ...

    def read(self, name: str) -> bytes | None:
        """The bytes of the regular file `name`, or None when there is no entry of that name. A link, a directory or
        any other kind of entry (a FIFO included: it never blocks) raises `OSError`."""
        ...

    def stat(self, name: str) -> EntryStat | None:
        """The entry `name`, not followed through a link, or None when there is none."""
        ...

    def sync_file(self, name: str) -> None:
        """`durable_sync` of the data of the regular file `name`, through a read-only descriptor (it writes nothing):
        what another program wrote into it without a sync is durable after it returns. Refused as `read` refuses (a
        link, a folder, a FIFO: `OSError`; no entry: `FileNotFoundError`)."""
        ...

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        """Create the file `name` exclusively (it fails if any entry of that name exists), write `data`, give it
        `mode` and, with `like`, the extended attributes of the regular file `like` in this directory (a replaced file
        keeps its Finder tags; not its ACL: owner, 2026-10-29), and sync its data. The entry is durable only after
        `sync`."""
        ...

    def allowed(self, name: str, right: Right) -> bool:
        """Whether the system allows `right` on the entry `name` (`"."`: this folder). Asking writes nothing."""
        ...

    def caps(self) -> Caps:
        """What this folder's volume offers: the swap (`RENAME_SWAP`) and the exclusive rename (`RENAME_EXCL`). Asking
        writes nothing. The write path it chooses is `ledger.handles.write_mode`."""
        ...

    def acl(self) -> tuple[AclEntry, ...]:
        """This folder's extended ACL entries, in order (none: empty). Asking writes nothing. The rule that judges them
        is `ledger.handles.acl_denial`."""
        ...

    def swap(self, a: str, b: str) -> None:
        """Exchange the entries `a` and `b` in one operation (`RENAME_SWAP`); both must exist. On a volume that does
        not offer it (`caps`), `ENOTSUP` with nothing done."""
        ...

    def rename_excl(self, src: str, dst: str) -> None:
        """Rename `src` to `dst` in one operation that fails if `dst` exists (`RENAME_EXCL`). On a volume that does not
        offer it (`caps`), `ENOTSUP` with nothing done."""
        ...

    def rename(self, src: str, dst: str) -> None:
        """A plain rename of `src` to `dst`, which replaces a file at `dst` (S0065: the write path on a volume without
        the swap, `ledger.handles`). On HFS+ a replace is one operation; on exFAT and FAT (no journal) a power loss
        may keep the removal of the old `dst` and lose the rename: `dst` is then missing, never torn."""
        ...

    def remove(self, name: str) -> None:
        """Remove the file `name` (not a directory)."""
        ...

    def mkdir(self, name: str) -> None:
        """Make the directory `name`; it fails if any entry of that name exists."""
        ...

    def rmdir(self, name: str) -> None:
        """Remove the directory `name` if it is empty: it fails with `ENOTEMPTY` when it holds any entry, `ENOTDIR`
        when it is not a directory (a link to one included: never followed) and `ENOENT` when there is none. Durable
        only after `sync`."""
        ...

    def names(self) -> list[str]:
        """The names in this directory, sorted."""
        ...

    def sync(self) -> None:
        """`durable_sync` of the directory: every entry operation made in it before this call is durable."""
        ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc: object) -> None: ...


class FileSystem(Protocol):
    def resolve(self, path: str) -> Resolved:
        """Resolve an absolute path as far as it exists (§6.3). A missing component ends the existing part; any other
        error raises `OSError`. A relative path, or a `.` or `..` component, raises `ValueError`."""
        ...

    def open_dir(self, path: str) -> DirHandle:
        """Open the directory at the absolute `path` (links in it are followed)."""
        ...


class Lock(Protocol):
    """A ledger-set lock held by this process (`design/specs/engine-jobs.md` §6.1 step 3)."""

    def release(self) -> None: ...


class Locks(Protocol):
    """The ledger-set locks: one lock file per `path_key` in `app_local_data/locks/<sha256(path_key)>`, never
    deleted (§6.1 step 3, §8.1 step 1; ADR-0012 Q29)."""

    def try_lock(self, key: str) -> Lock | None:
        """The lock of `key` taken without waiting, or None when any holder has it (this process included). An
        `OSError` when the lock file cannot be opened."""
        ...

    def lock(self, key: str) -> Lock:
        """The lock of `key`, waiting for any holder to release it (§6.1 step 3: the save waits, with Cancel; with the
        engine lock nothing of ours holds one, so the wait ends at once). An `OSError` when the lock file cannot be
        opened or locked."""
        ...


@dataclass(frozen=True, slots=True)
class LegacyAbsent:
    """No lock file: the legacy app never saved there (ADR-0062 decision 2: v0.4 never creates it)."""


@dataclass(frozen=True, slots=True)
class LegacyHeld:
    """Another process holds the legacy app's write lock: the old app is saving."""


class LegacyLocks(Protocol):
    """A legacy install's write lock, `<legacy root>/data/.write.lock` (ADR-0062 decisions 2 and 5; INV-26): tried
    once, without waiting, with the legacy app's own primitive (portalocker 3.2.0, `LOCK_EX | LOCK_NB`), on the file
    opened read-only and only if it exists; nothing is ever created, written or removed there."""

    def try_lock(self, path: str) -> Lock | LegacyAbsent | LegacyHeld:
        """Free: the lock, held until released. Any other outcome than these three (the file cannot be opened, is not
        a regular file, or the lock call fails otherwise) raises `OSError`: the save refuses (fail closed)."""
        ...


def same_path(now: str, recorded: str) -> bool:
    """The path guard's comparison (§6.3): a folder's path now names the recorded canonical path, compared as the
    volume compares names. Every volume the save writes on (macOS) treats two normalizations of a name as one, and
    HFS+ answers NFD for a name made in NFC (S0061 probe), so both are compared after NFD; case stays exact, so a
    folder renamed in case only counts as moved (fails closed). TODO(BL-0087): Linux compares bytes."""
    return unicodedata.normalize("NFD", now) == unicodedata.normalize("NFD", recorded)


def fold(component: str, rule: FoldRule) -> str:
    """One name folded by its directory's rule (§6.3). Caseless matching is NFD(casefold(NFD(x))) (Unicode §3.13)."""
    out = unicodedata.normalize("NFD", component) if rule.nfd or rule.casefold else component
    if rule.casefold:
        out = unicodedata.normalize("NFD", out.casefold())
    return out


class MalformedPathError(ValueError):
    """A caller's error: a path or a name the port does not take (nothing was done)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"malformed path: {what}")


def check_name(name: str) -> str:
    """A single path component, or `ValueError`."""
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise MalformedPathError("not_one_component")
    return name


def split_absolute(path: str) -> tuple[str, ...]:
    """The components of an absolute POSIX path; `ValueError` for a relative path or a `.`/`..` component (they would
    change meaning with links)."""
    if not path.startswith("/"):
        raise MalformedPathError("not_absolute")
    parts = tuple(p for p in path.split("/") if p)
    for p in parts:
        check_name(p)
    return parts
