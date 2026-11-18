"""The engine's file-system port: every write of a ledger file, a backup or a journal goes through it
(`design/specs/engine-jobs.md` §6.1 step 8, §6.2, §6.3; ADR-0012, ADR-0013; S0060).

Writes act on a **handle on one directory** (`DirHandle`), never on a path: a check of where the directory is
(`DirHandle.path`) and the write that follows use the same descriptor, so the directory cannot be swapped for a link
between them (§6.3 "Path guard"; `renameatx_np` on macOS). Names given to a handle are single components (no `/`,
never `.` or `..`); a name is never followed through a link.

Durability (§6.2, §13 "File-system assumptions"): a file's data is durable after `DirHandle.create` returns (it syncs
the data before it returns), and an existing file's after `DirHandle.sync_file`; an entry made by `create`, `swap`,
`rename_excl`, `rename`, `remove` or `mkdir` is durable only after the directory's `sync`, which makes every entry
operation done before it in that directory durable, never the data of the files those entries name (another program's
unsynced in-place write needs `sync_file`; S0101, BL-0243). A swap and an exclusive rename are one operation: a power
loss keeps or drops each whole; so is a plain rename, except over an existing file on a volume without a journal (exFAT,
FAT: `rename`). Nothing is assumed about the order in which unsynced operations reach the disk. Every sync is
`durable_sync`: `F_FULLFSYNC` on macOS, `fsync` on Linux, `FlushFileBuffers` on Windows.

Every operation may raise `OSError`. The real adapters are `ledger_engine.fs.darwin` (macOS) and
`ledger_engine.fs.linux` (Linux, S0125), sharing `ledger_engine.fs.posix`, and `ledger_engine.fs.windows` (Windows,
S0128: `FlushFileBuffers`, no swap; selected only under test until BL-0087), taken only through
`ledger_engine.fs.this_os.for_this_os` (S0113); the test adapter is the in-memory fake `tests/fakes/memfs.py`, with a
separate durable layer, crash, I/O-error and power-loss injection.
"""

from __future__ import annotations

import errno
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, Self

from ledger_engine.fs.pathmodel import FoldRule, MalformedPathError, fold, host

SERIAL_NAMESPACE: Final = uuid.UUID("3b1c6a52-8f0e-5d6a-9b1e-6c4f0f25a125")
"""The namespace of the version 5 UUID a drive named only by a format serial gets (FAT, exFAT, NTFS, ReFS; Linux
since S0125, Windows since S0128): the serial's upper-case hex, `XXXX-XXXX` for a 32-bit FAT or exFAT serial, 16
digits for a 64-bit NTFS or ReFS serial, as udev spells them, so one serial names one drive on both systems."""

CHUNK: Final = 1 << 20
"""How many bytes a streamed create or a hash reads or writes at a time (S0081: a copied document is never held whole
in memory)."""


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
    network shares neither (probed on disk images, S0065). `links`: the mirror's current generation is named by a
    symbolic link renamed over `current.db` (macOS, Linux), or by a pointer file (Windows, which makes no link without a
    privilege and cannot rename over an open file; ADR-0015, `design/specs/mirror/publish.md` §4 step 8; S0130). One
    answer per OS, never learnt from a failure."""

    swap: bool
    excl: bool
    links: bool = True


@dataclass(frozen=True, slots=True)
class FolderMark:
    """What tells a folder on one drive from a folder at the same path on another (S0089; spec §6.3 "Drive identity"):
    `volume`, its drive's volume UUID (None: the drive reports none); `fs_type`, the file system's type name
    (`f_fstypename`); `born`, the folder's creation time in nanoseconds since the epoch, at the resolution the file
    system keeps (probed: APFS nanoseconds, HFS+ 1 s, exFAT 10 ms but whole seconds until a remount for a time set with
    `setattrlist`, FAT 2 s; 0 for the root of a FAT or exFAT drive, which keeps none)."""

    volume: str | None
    fs_type: str
    born: int | None  # None: the file system keeps no creation time (S0089 round 2, verifier F2)


class NotOffered(OSError):
    """The volume does not offer the call (`ENOTSUP`): nothing was done. Raised by `DirHandle.swap` and
    `DirHandle.rename_excl` when `caps` says the volume lacks it, or when the volume refuses it at its first use (Linux
    learns whether a volume has `RENAME_NOREPLACE` only by using it; S0125 round 1, owner: "A2"); `caps` then says so
    for the rest of the job."""

    def __init__(self, what: str) -> None:
        super().__init__(errno.ENOTSUP, f"the volume does not offer {what}")


class Kind(StrEnum):
    FILE = "file"
    DIR = "dir"
    LINK = "link"
    OTHER = "other"  # a FIFO, a socket, a device


@dataclass(frozen=True, slots=True)
class EntryStat:
    """One entry, not followed through a link (`lstat`). `locked`: an immutable or append-only flag is set
    (`uchg`, `schg`, `uappnd`, `sappnd`): the entry cannot be swapped, renamed or removed. `mtime_ns`: its modification
    time (`st_mtime_ns`; a rename keeps it, a folder's moves when its entries change); only the history trim reads it,
    to tell older history from newer (S0074; spec §6.5), never to decide what may be deleted."""

    kind: Kind
    dev: int
    ino: int
    nlink: int
    mode: int
    locked: bool
    mtime_ns: int = 0
    encrypted: bool = False
    """The file is encrypted by the file system itself (Windows' EFS, S0128 round 1): the save never writes it, so its
    bytes never reach a backup or a staging file in the clear (owner, 2026-11-17: "1a"). Never set on macOS or
    Linux."""

    @property
    def identity(self) -> tuple[int, int]:
        return (self.dev, self.ino)


class DirHandle(Protocol):
    """An open directory. Close it (or use it as a context manager) when done."""

    def path(self) -> str:
        """The directory's canonical path now (`F_GETPATH`): after the directory moved, its new place."""
        ...

    def device(self) -> int:
        """The volume this directory is on (`fstat`'s `st_dev`; S0087: the mirror's cleanup never crosses a mount
        point)."""
        ...

    def identity(self) -> tuple[int, int]:
        """`(st_dev, st_ino)` of this directory, asked through its descriptor (`fstat`): the object it opened, whatever
        its path names now (S0102, BL-0244: the save makes `local/` only in the app folder of the start)."""
        ...

    def volume_id(self) -> str | None:
        """The UUID of the drive this directory is on, in lower case (`8-4-4-4-12` hex digits), or None when the drive
        reports none (S0089, BL-0188: the journal names each folder's drive by it, and the undo writes only on that
        drive). Asking writes nothing; an error raises `OSError`. macOS: `ATTR_VOL_UUID` (APFS, HFS+, exFAT and FAT
        report one; probed). Linux: the file system's UUID (`FS_IOC_GETFSUUID`, else udev's `/dev/disk/by-uuid`; a
        format serial as a name-based UUID; S0125). Windows: the volume serial as a name-based UUID (S0128)."""
        ...

    def born(self) -> int | None:
        """This folder's creation time in nanoseconds since the epoch, asked through this descriptor, or None when its
        file system keeps none (`FolderMark.born`; on Linux the app folder's identity too, BL-0262). Asking writes
        nothing; an error raises `OSError`."""
        ...

    def mark(self) -> FolderMark:
        """This folder's `FolderMark`: `volume_id`, its file system's type and its creation time, all asked through this
        descriptor (S0089 round 1). Asking writes nothing; an error raises `OSError`."""
        ...

    def read(self, name: str, limit: int | None = None) -> bytes | None:
        """The bytes of the regular file `name`, or None when there is no entry of that name. A link, a directory or
        any other kind of entry (a FIFO included: it never blocks) raises `OSError`. With `limit`, at most its first
        `limit` bytes, however long the file is (or grows) by then (S0080 r2: a file is never read whole only to
        learn that it is not ours)."""
        ...

    def read_identified(self, name: str) -> tuple[bytes, tuple[int, int]] | None:
        """`read` of the whole file, with the identity `(st_dev, st_ino)` of the object the bytes were read from, taken
        from the descriptor that read them (`fstat`), so a later act on the file can check that it meets that same
        object (S0101 round 1: the restore's flush). None when there is no entry; refused as `read` refuses."""
        ...

    def stat(self, name: str) -> EntryStat | None:
        """The entry `name`, not followed through a link, or None when there is none."""
        ...

    def sync_file(self, name: str) -> tuple[int, int]:
        """`durable_sync` of the data of the regular file `name`, through a read-only descriptor (it writes nothing):
        what another program wrote into it without a sync is durable after it returns. Returns the identity
        `(st_dev, st_ino)` of the object it synced, from that descriptor (S0101 round 1). Refused as `read` refuses (a
        link, a folder, a FIFO: `OSError`; no entry: `FileNotFoundError`)."""
        ...

    def sha256(self, name: str, limit: int) -> tuple[str, int] | None:
        """The SHA-256 (lower-case hex) and the length of at most the first `limit` bytes of the regular file `name`,
        read in chunks of `CHUNK` (S0081: a copy's read-back and cleanup check never hold a file whole), or None when
        there is no entry of that name. Refused as `read` refuses."""
        ...

    def create(self, name: str, data: bytes | Iterable[bytes], mode: int, like: str | None = None) -> tuple[int, int]:
        """Create the file `name` exclusively (it fails if any entry of that name exists), write `data` (the bytes,
        or each chunk an iterable gives, in order: S0081's streamed copy; the iterable is read only after the entry
        is made), give it
        `mode` and, with `like`, the extended attributes of the regular file `like` in this directory (a replaced file
        keeps its Finder tags; not its ACL: owner, 2026-10-29), and sync its data. The entry is durable only after
        `sync`. Returns the identity `(st_dev, st_ino)` of the file it made, read from its own descriptor (`fstat`),
        so no other program's entry can be taken for it (S0080 r3). Every adapter reads it: the create's cleanup never
        takes a later `stat` for a file's identity (BL-0200)."""
        ...

    def allowed(self, name: str, right: Right) -> bool:
        """Whether the system allows `right` on the entry `name` (`"."`: this folder). Asking writes nothing."""
        ...

    def caps(self) -> Caps:
        """What this folder's volume offers: the swap (`RENAME_SWAP`) and the exclusive rename (`RENAME_EXCL`). Asking
        writes nothing. The write path it chooses is `ledger.handles.write_mode`. Linux learns the exclusive rename's
        answer at its first use (`NotOffered`): until then `excl` says it is offered (S0125 round 1)."""
        ...

    def acl(self) -> tuple[AclEntry, ...]:
        """This folder's extended ACL entries, in order (none: empty). Asking writes nothing. The rule that judges them
        is `ledger.handles.acl_denial`."""
        ...

    def swap(self, a: str, b: str) -> None:
        """Exchange the entries `a` and `b` in one operation (`RENAME_SWAP`); both must exist. On a volume that does
        not offer it (`caps`), `NotOffered` (`ENOTSUP`) with nothing done."""
        ...

    def rename_excl(self, src: str, dst: str) -> None:
        """Rename `src` to `dst` in one operation that fails if `dst` exists (`RENAME_EXCL`). On a volume that does not
        offer it (`caps`, or refused at its first use on Linux), `NotOffered` (`ENOTSUP`) with nothing done."""
        ...

    def rename(self, src: str, dst: str) -> None:
        """A plain rename of `src` to `dst`, which replaces a file at `dst` (S0065: the write path on a volume without
        the swap, `ledger.handles`). On HFS+ a replace is one operation; on exFAT and FAT (no journal) a power loss
        may keep the removal of the old `dst` and lose the rename: `dst` is then missing, never torn."""
        ...

    def remove(self, name: str) -> None:
        """Remove the file `name` (not a directory)."""
        ...

    def mkdir(self, name: str, mode: int = 0o755) -> None:
        """Make the directory `name` with `mode` (the umask may clear bits); it fails if any entry of that name
        exists."""
        ...

    def symlink(self, target: str, name: str) -> None:
        """Make the symbolic link `name` whose target is `target`, itself one name of this directory (S0087: the
        mirror's `current.db`, publish.md §4 step 8; never a path, so the link can only lead to an entry of this
        folder). It fails if any entry of that name exists. Durable only after `sync`."""
        ...

    def readlink(self, name: str) -> str | None:
        """The target of the link `name`, as stored; None when there is no entry of that name. Any other kind of entry
        raises `OSError` (`EINVAL`)."""
        ...

    def touch(self, name: str) -> None:
        """Set the modification time of the entry `name` itself to now, never through a link (S0087: a replaced
        generation's replacement time, publish.md §4 step 8). Writes no data."""
        ...

    def open_child(self, name: str) -> DirHandle:
        """Open the directory `name` of this directory, relative to this handle and never through a link: a link, a
        file or no entry raises `OSError` (S0087: the mirror folder and the cleanup of folders in it, publish.md §2)."""
        ...

    def rule(self) -> FoldRule:
        """How this folder compares the names in it (§6.3), asked through this descriptor, never by path (S0117 round
        3: a test copy's save walks its folders by handle). An answer that cannot be read counts as case-insensitive,
        as `resolve` counts it."""
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
    def same_path(self, now: str, recorded: str) -> bool:
        """The path guard's comparison (§6.3): a folder's path now (`DirHandle.path`) names the recorded canonical
        path, compared as this OS's volumes give paths back: after NFD on macOS (`same_path`), byte for byte on Linux
        (S0125; a folder renamed to another spelling of its name has moved). Case stays exact everywhere."""
        ...

    def resolve(self, path: str) -> Resolved:
        """Resolve an absolute path as far as it exists (§6.3). A missing component ends the existing part; any other
        error raises `OSError`. A relative path, or a `.` or `..` component, raises `ValueError`."""
        ...

    def open_dir(self, path: str) -> DirHandle:
        """Open the directory at the absolute `path` (links in it are followed)."""
        ...


class Lock(Protocol):
    """A legacy write lock held by this process (`LegacyLocks`)."""

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LegacyAbsent:
    """No lock file: the legacy app never saved there (ADR-0062 decision 2: v0.4 never creates it)."""


@dataclass(frozen=True, slots=True)
class LegacyHeld:
    """Another process holds the legacy app's write lock: the old app is saving."""


class LegacyLocks(Protocol):
    """A legacy install's write lock, `<legacy root>/data/.write.lock` (ADR-0062 decisions 2 and 5; INV-26), for Copy
    only (ADR-0068 D4; Copy is BL-0026's, so nothing calls it yet): tried
    once, without waiting, with the legacy app's own primitive (portalocker 3.2.0, `LOCK_EX | LOCK_NB`), on the file
    opened read-only and only if it exists; nothing is ever created, written or removed there."""

    def try_lock(self, path: str) -> Lock | LegacyAbsent | LegacyHeld:
        """Free: the lock, held until released. Any other outcome than these three (the file cannot be opened, is not
        a regular file, or the lock call fails otherwise) raises `OSError`: the Copy refuses (fail closed)."""
        ...


def same_path(now: str, recorded: str) -> bool:
    """The path guard's comparison (§6.3): a folder's path now names the recorded canonical path, compared as the
    volume compares names. Every volume the save writes on (macOS) treats two normalizations of a name as one, and
    HFS+ answers NFD for a name made in NFC (S0061 probe), so both are compared after NFD; case stays exact, so a
    folder renamed in case only counts as moved (fails closed). macOS's rule (`DarwinFS.same_path` and the fake);
    Linux compares bytes (`LinuxFS.same_path`, S0125)."""
    return unicodedata.normalize("NFD", now) == unicodedata.normalize("NFD", recorded)


type FolderId = tuple[int, int] | tuple[int, int, int]
"""A folder's identity over time (S0102, BL-0262): `(st_dev, st_ino)`, and its creation time in ns where the OS's
adapter adds one (`this_os.folder_identity`: Linux, where ext4 gives a folder removed and made again at once the freed
inode number; macOS's APFS never reuses one, so its identity has none)."""


def same_identity(now: FolderId, start: FolderId) -> bool:
    """Whether a folder whose identity is `now` is the folder of `start`: the same `(st_dev, st_ino)` and, when
    `start` has a creation time, the same one (a `now` without one fails)."""
    if now[:2] != start[:2]:
        return False
    if len(start) == 2:
        return True
    return len(now) == 3 and now[2] == start[2]


def folder_id(h: DirHandle) -> FolderId:
    """The identity of the folder `h` holds, with its creation time where its file system keeps one (S0125 round 1,
    owner: "B2": the save's own folders, `local/`, `<key>/`, `journal/` and `backups/`, so that a folder made again
    with a freed inode number is another folder on Linux too)."""
    dev, ino = h.identity()
    born = h.born()
    return (dev, ino) if born is None else (dev, ino, born)


def same_folder(h: DirHandle, start: FolderId) -> bool:
    """`same_identity` of the folder `h` holds, asked through its descriptor."""
    dev, ino = h.identity()
    if len(start) == 2:
        return (dev, ino) == start
    born = h.born()
    return same_identity((dev, ino) if born is None else (dev, ino, born), start)


def check_name(name: str) -> str:
    """A single path component this OS can hold, or `MalformedPathError` (`pathmodel`: POSIX as before S0127;
    Windows also refuses reserved device names, a trailing dot or space and the characters no Windows name holds)."""
    return host().check_name(name)


def split_absolute(path: str) -> tuple[str, ...]:
    """The components of an absolute path of this OS (Windows: its root first); `ValueError` for a relative path or a
    `.`/`..` component (they would change meaning with links), or a name `check_name` refuses."""
    return host().split(path)


__all__ = [
    "CHUNK",
    "SERIAL_NAMESPACE",
    "AclEntry",
    "Caps",
    "DirHandle",
    "EntryStat",
    "FileSystem",
    "FoldRule",
    "FolderId",
    "FolderMark",
    "Kind",
    "LegacyAbsent",
    "LegacyHeld",
    "LegacyLocks",
    "Lock",
    "MalformedPathError",
    "NotOffered",
    "Resolved",
    "Right",
    "check_name",
    "fold",
    "folder_id",
    "same_folder",
    "same_identity",
    "same_path",
    "split_absolute",
]
