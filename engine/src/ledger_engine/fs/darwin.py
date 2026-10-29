"""The file-system port on macOS (`ledger_engine.fs.port`; `design/specs/engine-jobs.md` §6.2, §6.3; S0060).

- `durable_sync`: `fcntl(F_FULLFSYNC)` (plain `fsync` does not reach the platter on macOS); where the volume refuses
  it (`ENOTSUP`, `EINVAL`), `fsync`, reported once per volume through `on_fallback` (content-free: no path).
- Swap and exclusive rename: `renameatx_np(fd, a, fd, b, RENAME_SWAP | RENAME_EXCL)` on the directory's own
  descriptor, so the folder checked by `path()` (`F_GETPATH` on that descriptor) is the folder written.
- `resolve`: the deepest ancestor that exists (`stat`, which opens nothing, so a FIFO never blocks and a cloud
  placeholder is not downloaded; a dangling link counts as absent, its own name as the first missing component).
  A folder's canonical path is `F_GETPATH` on a descriptor of the folder (`O_EVTONLY`); a file's is its folder's
  canonical path joined with the entry's stored name, read from the folder's listing, never `F_GETPATH` on the file:
  for a file with several hard links APFS answers whichever name was looked up last (S0060 red team finding 1).
  A link to a file is followed by `readlink` from its folder. The object found is checked by `(st_dev, st_ino)`
  against the one the path named, and returned as `Resolved.identity`. Each component's rule comes from
  `pathconf(_PC_CASE_SENSITIVE)` of the directory that holds it (an answer other than 1 counts as
  case-insensitive: folding may merge names, never split them, §15).
- `read` and `stat` never follow a link; `read` refuses anything but a regular file before opening it, and opens
  with `O_NONBLOCK`. `create(..., like=...)` copies the extended attributes and the ACL of `like` (`fcopyfile`
  with `COPYFILE_XATTR | COPYFILE_ACL`; not `COPYFILE_STAT`, which would copy the old modification time).

Other operating systems refuse (`for_this_os`): their primitives are BL-0087 (spec §14).
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import stat
import sys
from collections.abc import Callable
from typing import Final, Self

from ledger_engine.fs.port import (
    EntryStat,
    FileSystem,
    FoldRule,
    Kind,
    MalformedPathError,
    Resolved,
    check_name,
    fold,
    split_absolute,
)

COPYFILE_ACL: Final = 1 << 0
COPYFILE_XATTR: Final = 1 << 2
LOCK_FLAGS: Final = stat.UF_IMMUTABLE | stat.UF_APPEND | stat.SF_IMMUTABLE | stat.SF_APPEND
MAX_LINKS: Final = 40
RENAME_SWAP: Final = 0x2
RENAME_EXCL: Final = 0x4
PC_CASE_SENSITIVE: Final = 11  # <sys/unistd.h> _PC_CASE_SENSITIVE (absent from os.pathconf_names)
MAXPATHLEN: Final = 1024


class UnsupportedPlatform(OSError):
    """An `OSError`: a write refuses on this OS like on any other I/O failure."""

    def __init__(self) -> None:
        super().__init__(errno.ENOTSUP, "the write path's file-system port is macOS-only until BL-0087")


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    fn = lib.renameatx_np
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    cp = lib.fcopyfile
    cp.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    cp.restype = ctypes.c_int
    return lib


def _errno_raise() -> None:
    e = ctypes.get_errno()
    raise OSError(e, os.strerror(e))


def _entry(st: os.stat_result) -> EntryStat:
    m = st.st_mode
    kind = (
        Kind.FILE if stat.S_ISREG(m) else Kind.DIR if stat.S_ISDIR(m) else Kind.LINK if stat.S_ISLNK(m) else Kind.OTHER
    )
    return EntryStat(
        kind=kind,
        dev=st.st_dev,
        ino=st.st_ino,
        nlink=st.st_nlink,
        mode=stat.S_IMODE(m),
        locked=bool(getattr(st, "st_flags", 0) & LOCK_FLAGS),
    )


def _getpath(fd: int) -> str:
    raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(MAXPATHLEN))
    return os.fsdecode(raw.split(b"\0", 1)[0])


class DarwinDir:
    """A handle on one directory (`ledger_engine.fs.port.DirHandle`)."""

    def __init__(self, fs: DarwinFS, path: str) -> None:
        self._fs = fs
        self._fd: int | None = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

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

    def path(self) -> str:
        return _getpath(self.fd)

    def stat(self, name: str) -> EntryStat | None:
        try:
            return _entry(os.stat(check_name(name), dir_fd=self.fd, follow_symlinks=False))
        except FileNotFoundError:
            return None

    def _open_regular(self, name: str) -> int:
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

    def read(self, name: str) -> bytes | None:
        try:
            fd = self._open_regular(check_name(name))
        except FileNotFoundError:
            return None
        try:
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1 << 20):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(check_name(name), flags, mode & 0o7777, dir_fd=self.fd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            if like is not None:
                src = self._open_regular(check_name(like))
                try:
                    if self._fs.libc.fcopyfile(src, fd, None, COPYFILE_XATTR | COPYFILE_ACL) != 0:
                        _errno_raise()
                finally:
                    os.close(src)
            os.fchmod(fd, mode & 0o7777)  # the mode as asked, not cut by the umask (a preserved mode)
            self._fs.durable_sync(fd)
        finally:
            os.close(fd)

    def _renamex(self, a: str, b: str, flags: int) -> None:
        rc = self._fs.libc.renameatx_np(self.fd, os.fsencode(check_name(a)), self.fd, os.fsencode(check_name(b)), flags)
        if rc != 0:
            _errno_raise()

    def swap(self, a: str, b: str) -> None:
        self._renamex(a, b, RENAME_SWAP)

    def rename_excl(self, src: str, dst: str) -> None:
        self._renamex(src, dst, RENAME_EXCL)

    def remove(self, name: str) -> None:
        os.unlink(check_name(name), dir_fd=self.fd)

    def mkdir(self, name: str) -> None:
        os.mkdir(check_name(name), 0o755, dir_fd=self.fd)

    def names(self) -> list[str]:
        return sorted(os.listdir(self.fd))

    def sync(self) -> None:
        self._fs.durable_sync(self.fd)


class DarwinFS:
    """The real file system on macOS (`ledger_engine.fs.port.FileSystem`)."""

    def __init__(self, on_fallback: Callable[[], None] | None = None) -> None:
        if sys.platform != "darwin":
            raise UnsupportedPlatform
        self.libc = _libc()
        self._fallback_volumes: set[int] = set()  # bounded by the number of mounted volumes (INV-10)
        self._on_fallback = on_fallback

    def durable_sync(self, fd: int) -> None:
        """§6.2: `F_FULLFSYNC`, or `fsync` where the volume refuses it (reported once per volume)."""
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError as e:
            if e.errno not in (errno.ENOTSUP, errno.EINVAL, errno.ENOTTY):
                raise
            os.fsync(fd)
            dev = os.fstat(fd).st_dev
            if dev not in self._fallback_volumes:
                self._fallback_volumes.add(dev)
                if self._on_fallback is not None:
                    self._on_fallback()

    def open_dir(self, path: str) -> DarwinDir:
        split_absolute(path)
        return DarwinDir(self, path)

    def _case_insensitive(self, directory: str) -> bool:
        try:
            return os.pathconf(directory, PC_CASE_SENSITIVE) != 1
        except OSError:
            return True

    def _folder(self, path: str) -> tuple[int, str]:
        """A descriptor on the folder at `path` (links followed) and its canonical path."""
        fd = os.open(path, os.O_EVTONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            return fd, _getpath(fd)
        except BaseException:
            os.close(fd)
            raise

    def _file(self, path: str, rule_of: Callable[[str], FoldRule]) -> tuple[str, tuple[int, int]]:
        """The canonical path of the file at `path`: its folder's canonical path and the entry's stored name."""
        for _ in range(MAX_LINKS):
            head, _, name = path.rpartition("/")
            fd, folder = self._folder(head or "/")
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(name, dir_fd=fd)
                    path = target if target.startswith("/") else _join(folder, target)
                    continue
                rule = rule_of(folder)
                want = fold(name, rule)
                names = [n for n in os.listdir(fd) if fold(n, rule) == want]
                same = [n for n in names if _identity(os.stat(n, dir_fd=fd, follow_symlinks=False)) == _identity(st)]
                if len(same) != 1:
                    raise OSError(errno.ENOENT, "the entry's stored name is not one name")
                return _join(folder, same[0]), _identity(st)
            finally:
                os.close(fd)
        raise OSError(errno.ELOOP, "too many links")

    def resolve(self, path: str) -> Resolved:
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
                rule_cache[folder] = FoldRule(nfd=True, casefold=self._case_insensitive(folder))
            return rule_cache[folder]

        if is_dir:
            fd, canonical = self._folder(probe)
            try:
                found = os.fstat(fd)
            finally:
                os.close(fd)
            identity = (found.st_dev, found.st_ino)
        else:
            canonical, identity = self._file(probe, rule_of)
        if identity != (st.st_dev, st.st_ino):
            raise OSError(errno.ESTALE, "the path changed while it was resolved")
        existing = split_absolute(canonical)
        rest = parts[cut:]
        rules = [rule_of("/" + "/".join(existing[:i])) for i in range(len(existing))]
        if rest:
            holder = canonical if is_dir else "/" + "/".join(existing[:-1])
            rules.extend(rule_of(holder) for _ in rest)
        return Resolved(existing=existing, rest=rest, rules=tuple(rules), identity=identity)


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _join(folder: str, name: str) -> str:
    return "/" + name if folder == "/" else f"{folder}/{name}"


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real port for this operating system; elsewhere than macOS it raises `UnsupportedPlatform` (BL-0087)."""
    return DarwinFS(on_fallback)
