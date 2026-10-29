"""The file-system port on macOS (`ledger_engine.fs.port`; `design/specs/engine-jobs.md` §6.2, §6.3; S0060).

- `durable_sync`: `fcntl(F_FULLFSYNC)` (plain `fsync` does not reach the platter on macOS); where the volume refuses
  it (`ENOTSUP`, `EINVAL`), `fsync`, reported once per volume through `on_fallback` (content-free: no path).
- Swap and exclusive rename: `renameatx_np(fd, a, fd, b, RENAME_SWAP | RENAME_EXCL)` on the directory's own
  descriptor, so the folder checked by `path()` (`F_GETPATH` on that descriptor) is the folder written.
- `resolve`: `F_GETPATH` on a descriptor opened with `O_EVTONLY` (it does not download a cloud placeholder), on the
  deepest ancestor that exists; each component's rule from `pathconf(_PC_CASE_SENSITIVE)` of the directory that
  holds it (an answer other than 1 counts as case-insensitive: folding may merge names, never split them, §15).

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

from ledger_engine.fs.port import FileSystem, FoldRule, MalformedPathError, Resolved, check_name, split_absolute

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
    return lib


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

    def read(self, name: str) -> bytes | None:
        try:
            fd = os.open(check_name(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1 << 20):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def mode(self, name: str) -> int:
        return stat.S_IMODE(os.stat(check_name(name), dir_fd=self.fd, follow_symlinks=False).st_mode)

    def create(self, name: str, data: bytes, mode: int) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(check_name(name), flags, mode & 0o7777, dir_fd=self.fd)
        try:
            os.fchmod(fd, mode & 0o7777)  # the mode as asked, not cut by the umask (a preserved mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            self._fs.durable_sync(fd)
        finally:
            os.close(fd)

    def _renamex(self, a: str, b: str, flags: int) -> None:
        rc = self._fs.libc.renameatx_np(self.fd, os.fsencode(check_name(a)), self.fd, os.fsencode(check_name(b)), flags)
        if rc != 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e))

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

    def resolve(self, path: str) -> Resolved:
        parts = split_absolute(path)
        cut = len(parts)
        while True:
            probe = "/" + "/".join(parts[:cut])
            try:
                fd = os.open(probe, os.O_EVTONLY | os.O_CLOEXEC)
            except (FileNotFoundError, NotADirectoryError):
                if cut == 0:
                    raise
                cut -= 1
                continue
            try:
                canonical = _getpath(fd)
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
            finally:
                os.close(fd)
            break
        existing = split_absolute(canonical)
        rest = parts[cut:]
        rules: list[FoldRule] = []
        for i in range(len(existing)):
            holder = "/" + "/".join(existing[:i])
            rules.append(FoldRule(nfd=True, casefold=self._case_insensitive(holder)))
        if rest:
            holder = canonical if is_dir else "/" + "/".join(existing[:-1])
            ci = self._case_insensitive(holder)
            rules.extend(FoldRule(nfd=True, casefold=ci) for _ in rest)
        return Resolved(existing=existing, rest=rest, rules=tuple(rules))


def for_this_os(on_fallback: Callable[[], None] | None = None) -> FileSystem:
    """The real port for this operating system; elsewhere than macOS it raises `UnsupportedPlatform` (BL-0087)."""
    return DarwinFS(on_fallback)
