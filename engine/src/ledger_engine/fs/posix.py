"""What the macOS and Linux file-system adapters share (`ledger_engine.fs.port`; S0125, BL-0266): the POSIX calls that
are the same on both, each on the directory's own descriptor (`openat`, `O_NOFOLLOW`, `unlinkat`, `mkdirat`,
`symlinkat`, `readlinkat`, `utimensat`), the regular-file reads and the exclusive create, the resolve walk, and the
legacy app's lock (`flock`, the call portalocker 3.2.0 makes on POSIX). What differs is each adapter's: the path of a
descriptor, the durable sync, the swap and the exclusive rename, what a volume offers, drive and folder identity, how a
folder compares names, the rights and the ACL, a file's lock flags and its extended attributes
(`ledger_engine.fs.darwin`, `ledger_engine.fs.linux`).

Not an adapter: only the adapters import it (`tests/architecture/test_platform_selection.py`), and the engine takes
them only from `ledger_engine.fs.this_os`.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import stat
import sys
from collections.abc import Callable, Iterable
from typing import Final, Self

from ledger_engine.fs.port import (
    CHUNK,
    EntryStat,
    FoldRule,
    Kind,
    LegacyAbsent,
    LegacyHeld,
    MalformedPathError,
    Resolved,
    check_name,
    fold,
    split_absolute,
)

if sys.platform == "win32":
    raise ImportError("the POSIX adapters' shared code; select an adapter with ledger_engine.fs.this_os")  # noqa: TRY003

MAX_LINKS: Final = 40


def kind_of(mode: int) -> Kind:
    if stat.S_ISREG(mode):
        return Kind.FILE
    if stat.S_ISDIR(mode):
        return Kind.DIR
    if stat.S_ISLNK(mode):
        return Kind.LINK
    return Kind.OTHER


class PosixFS:
    """The parts of a `FileSystem` both POSIX adapters share; each adapter gives the rest."""

    def durable_sync(self, fd: int) -> None:
        raise NotImplementedError

    def same_path(self, now: str, recorded: str) -> bool:
        raise NotImplementedError

    def open_folder(self, path: str) -> tuple[int, str]:
        """A descriptor on the folder at `path` (links followed) and its canonical path: no data is read through it."""
        raise NotImplementedError

    def rule_at(self, folder: str) -> FoldRule:
        """How the folder at the canonical path `folder` compares the names in it (an answer that cannot be read counts
        as the widest rule)."""
        raise NotImplementedError

    def _file(self, path: str, rule_of: Callable[[str], FoldRule]) -> tuple[str, tuple[int, int]]:
        """The canonical path of the file at `path`: its folder's canonical path and the entry's stored name, read from
        the folder's listing (never the file's own descriptor path: with several hard links it is whichever name was
        looked up last; S0060 red team finding 1)."""
        for _ in range(MAX_LINKS):
            head, _, name = path.rpartition("/")
            fd, folder = self.open_folder(head or "/")
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(name, dir_fd=fd)
                    path = target if target.startswith("/") else join(folder, target)
                    continue
                rule = rule_of(folder)
                want = fold(name, rule)
                names = [n for n in os.listdir(fd) if fold(n, rule) == want]
                same = [n for n in names if identity(os.stat(n, dir_fd=fd, follow_symlinks=False)) == identity(st)]
                if len(same) != 1:
                    raise OSError(errno.ENOENT, "the entry's stored name is not one name")
                return join(folder, same[0]), identity(st)
            finally:
                os.close(fd)
        raise OSError(errno.ELOOP, "too many links")

    def resolve(self, path: str) -> Resolved:
        """`FileSystem.resolve`: the deepest ancestor that exists (`stat`, which opens nothing, so a FIFO never blocks;
        a dangling link counts as absent, its own name as the first missing component), its canonical path and the
        object found, checked by `(st_dev, st_ino)` against the one the path named."""
        parts = split_absolute(path)
        cut = len(parts)
        while True:
            probe = "/" + "/".join(parts[:cut])
            try:
                st = os.stat(probe)  # opens nothing; a dangling link is absent
            except (FileNotFoundError, NotADirectoryError):
                if cut == 0:
                    raise
                cut -= 1
                continue
            break
        is_dir = stat.S_ISDIR(st.st_mode)
        rule_cache: dict[str, FoldRule] = {}

        def rule_of(folder: str) -> FoldRule:
            if folder not in rule_cache:
                rule_cache[folder] = self.rule_at(folder)
            return rule_cache[folder]

        if is_dir:
            fd, canonical = self.open_folder(probe)
            try:
                found = os.fstat(fd)
            finally:
                os.close(fd)
            ident = (found.st_dev, found.st_ino)
        else:
            canonical, ident = self._file(probe, rule_of)
        if ident != (st.st_dev, st.st_ino):
            raise OSError(errno.ESTALE, "the path changed while it was resolved")
        existing = split_absolute(canonical)
        rest = parts[cut:]
        rules = [rule_of("/" + "/".join(existing[:i])) for i in range(len(existing))]
        if rest:
            holder = canonical if is_dir else "/" + "/".join(existing[:-1])
            rules.extend(rule_of(holder) for _ in rest)
        return Resolved(existing=existing, rest=rest, rules=tuple(rules), identity=ident)


class PosixDir:
    """The parts of a `DirHandle` both POSIX adapters share."""

    def __init__(self, fs: PosixFS, path: str, *, fd: int | None = None) -> None:
        self._fs = fs
        self._fd: int | None = fd if fd is not None else os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise MalformedPathError("closed_handle")
        return self._fd

    def device(self) -> int:
        return os.fstat(self.fd).st_dev

    def identity(self) -> tuple[int, int]:
        st = os.fstat(self.fd)
        return (st.st_dev, st.st_ino)

    def locked(self, name: str, st: os.stat_result) -> bool:
        """Whether the entry `name` (its `lstat` is `st`) carries an immutable or append-only flag."""
        raise NotImplementedError

    def copy_xattrs(self, src: int, dst: int) -> None:
        """Copy the extended attributes a replaced file keeps from the descriptor `src` to `dst`."""
        raise NotImplementedError

    def stat(self, name: str) -> EntryStat | None:
        try:
            st = os.stat(check_name(name), dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return EntryStat(
            kind=kind_of(st.st_mode),
            dev=st.st_dev,
            ino=st.st_ino,
            nlink=st.st_nlink,
            mode=stat.S_IMODE(st.st_mode),
            locked=self.locked(name, st),
            mtime_ns=st.st_mtime_ns,
        )

    def open_regular(self, name: str) -> int:
        """A descriptor on the regular file `name`: checked by `lstat` before the open (a FIFO is never opened),
        opened without following a link and without blocking, and checked again to be the same file."""
        before = self.stat(name)
        if before is None:
            raise FileNotFoundError(errno.ENOENT, "no entry")
        if before.kind is not Kind.FILE:
            raise OSError(errno.EINVAL, "not a regular file")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != before.identity:
            os.close(fd)
            raise OSError(errno.EINVAL, "changed while opened")
        return fd

    def read(self, name: str, limit: int | None = None) -> bytes | None:
        try:
            fd = self.open_regular(check_name(name))
        except FileNotFoundError:
            return None
        try:
            return read_fd(fd, limit)
        finally:
            os.close(fd)

    def read_identified(self, name: str) -> tuple[bytes, tuple[int, int]] | None:
        try:
            fd = self.open_regular(check_name(name))
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(fd)
            return read_fd(fd, None), (st.st_dev, st.st_ino)
        finally:
            os.close(fd)

    def sync_file(self, name: str) -> tuple[int, int]:
        fd = self.open_regular(check_name(name))  # read-only: both syncs work on it (probed: APFS S0061 r4; Linux)
        try:
            st = os.fstat(fd)
            self._fs.durable_sync(fd)
            return st.st_dev, st.st_ino
        finally:
            os.close(fd)

    def sha256(self, name: str, limit: int) -> tuple[str, int] | None:
        try:
            fd = self.open_regular(check_name(name))
        except FileNotFoundError:
            return None
        try:
            h = hashlib.sha256()
            n = 0
            while n < limit:
                chunk = os.read(fd, min(CHUNK, limit - n))
                if not chunk:
                    break
                h.update(chunk)
                n += len(chunk)
            return h.hexdigest(), n
        finally:
            os.close(fd)

    def create(self, name: str, data: bytes | Iterable[bytes], mode: int, like: str | None = None) -> tuple[int, int]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(check_name(name), flags, mode & 0o7777, dir_fd=self.fd)
        try:
            for piece in (data,) if isinstance(data, bytes) else data:
                view = memoryview(piece)
                while view:
                    view = view[os.write(fd, view) :]
            if like is not None:
                src = self.open_regular(check_name(like))
                try:
                    self.copy_xattrs(src, fd)
                finally:
                    os.close(src)
            os.fchmod(fd, mode & 0o7777)  # the mode as asked, not cut by the umask (a preserved mode)
            self._fs.durable_sync(fd)
            st = os.fstat(fd)
            return (st.st_dev, st.st_ino)
        finally:
            os.close(fd)

    def rename(self, src: str, dst: str) -> None:
        os.rename(check_name(src), check_name(dst), src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def rename_shared(self, src: str, dst: str) -> None:
        self.rename(src, dst)  # an open file never blocks a rename on POSIX

    def remove(self, name: str) -> None:
        os.unlink(check_name(name), dir_fd=self.fd)

    def mkdir(self, name: str, mode: int = 0o755) -> None:
        os.mkdir(check_name(name), mode, dir_fd=self.fd)

    def symlink(self, target: str, name: str) -> None:
        os.symlink(check_name(target), check_name(name), dir_fd=self.fd)

    def readlink(self, name: str) -> str | None:
        try:
            return os.readlink(check_name(name), dir_fd=self.fd)
        except FileNotFoundError:
            return None

    def touch(self, name: str) -> None:
        os.utime(check_name(name), dir_fd=self.fd, follow_symlinks=False)

    def child_fd(self, name: str) -> int:
        """A descriptor on the directory `name` of this directory, never through a link (`open_child`)."""
        return os.open(check_name(name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)

    def rmdir(self, name: str) -> None:
        os.rmdir(check_name(name), dir_fd=self.fd)

    def names(self) -> list[str]:
        return sorted(os.listdir(self.fd))

    def sync(self) -> None:
        self._fs.durable_sync(self.fd)


def read_fd(fd: int, limit: int | None) -> bytes:
    """The bytes from `fd`'s position on, at most `limit` of them (all with None), in chunks of 1 MiB."""
    chunks: list[bytes] = []
    left = limit
    while left is None or left > 0:
        chunk = os.read(fd, 1 << 20 if left is None else min(1 << 20, left))
        if not chunk:
            break
        chunks.append(chunk)
        if left is not None:
            left -= len(chunk)
    return b"".join(chunks)


class PosixLock:
    """A legacy write lock held by this process (`PosixLegacyLocks`)."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases its `flock`
            self._fd = None


class PosixLegacyLocks:
    """The legacy write lock (`ledger_engine.fs.port.LegacyLocks`; ADR-0062 decisions 2 and 5): the file opened
    read-only, never through a link and without blocking on a FIFO, only if it exists (v0.4 never creates it), then
    `flock(LOCK_EX | LOCK_NB)`: the call portalocker 3.2.0 makes on POSIX for `lock(fh, LOCK_EX | LOCK_NB)` (its
    `PosixLocker` calls `fcntl.flock` on macOS and Linux alike; `EAGAIN` or `EACCES` is its `AlreadyLocked`, any
    other error its `LockException`), so the detection sees the legacy app's lock and is tested against a holder that
    takes it with portalocker 3.2.0 itself (`tests/fs/test_legacy_lock.py`); the engine does not import portalocker
    (it brings pywin32 on Windows; ADR-0062's 2026-10-31 amendment). Windows (`LockFileEx` over the first 64 KiB, the
    range `msvcrt.locking` takes) is `ledger_engine.fs.windows.WindowsLegacyLocks` (S0128)."""

    def try_lock(self, path: str) -> PosixLock | LegacyAbsent | LegacyHeld:
        split_absolute(path)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return LegacyAbsent()
        try:
            regular = stat.S_ISREG(os.fstat(fd).st_mode)
            if regular:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EAGAIN, errno.EACCES):
                return LegacyHeld()
            raise
        except BaseException:
            os.close(fd)
            raise
        if not regular:
            os.close(fd)
            raise OSError(errno.EINVAL, "the legacy lock is not a regular file")
        return PosixLock(fd)  # closing the descriptor releases the lock; nothing is written or removed


def identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def join(folder: str, name: str) -> str:
    return "/" + name if folder == "/" else f"{folder}/{name}"
