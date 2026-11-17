"""The file-system port on macOS (`ledger_engine.fs.port`; `design/specs/engine-jobs.md` §6.2, §6.3; S0060).

- `durable_sync`: `fcntl(F_FULLFSYNC)` (plain `fsync` does not reach the platter on macOS); where the volume refuses
  it (`ENOTSUP`, `EINVAL`), `fsync`, reported once per volume through `on_fallback` (content-free: no path).
- Swap and exclusive rename: `renameatx_np(fd, a, fd, b, RENAME_SWAP | RENAME_EXCL)` on the directory's own
  descriptor, so the folder checked by `path()` (`F_GETPATH` on that descriptor) is the folder written; each only
  where the volume's capabilities offer it, else `ENOTSUP` before the call (S0065 probe: FAT answers `RENAME_SWAP`
  with 0 and replaces the second file). A plain `rename` is `renameat` on that descriptor.
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
  with `COPYFILE_XATTR` only: the ACL is not kept, owner, 2026-10-29; not `COPYFILE_STAT`, which would copy the old
  modification time). `allowed` is `access(2)` with the extended bits (`_DELETE_OK`, `_REXT_OK`), which apply ACLs;
  `caps` reads the volume's `VOL_CAP_INT_RENAME_SWAP` and `_EXCL` (`fgetattrlist`; probed S0065: exFAT and FAT
  neither, HFS+ only the exclusive rename). `volume_id` reads the volume's UUID (`ATTR_VOL_UUID`, with
  `ATTR_CMN_RETURNED_ATTRS`: None when the volume returns none; S0089).
  `acl` reads the folder's extended ACL (`acl_get_fd_np`), each entry's kind and every permission and flag bit
  (S0061 r6, probed on APFS); never whom it names (owner, 2026-10-30: "Any subject (Recommended)", S0061 r7: the
  membership lookup answered "not a member" for macOS's `owner` and `group` subjects, which the kernel applies).

- `rmdir`: `unlinkat(AT_REMOVEDIR)` on the folder's descriptor (`ENOTEMPTY` for a folder holding a `.DS_Store`,
  `ENOTDIR` for a link, probed S0061). `DarwinLegacyLocks`: the legacy app's write lock, tried only for Copy
  (INV-26, ADR-0068 D4; Copy is BL-0026's: the save no longer tries it).

The calls macOS and Linux share (the reads, the exclusive create, the entry operations on the descriptor, the
resolve walk, the legacy lock) are `ledger_engine.fs.posix` (S0125). This module is macOS-only: importing it elsewhere
raises `ImportError`. The engine selects the adapter in `ledger_engine.fs.this_os` (`for_this_os`,
`legacy_locks_for_this_os`), which gives Linux `ledger_engine.fs.linux` and refuses other operating systems (Windows is
BL-0087; spec §14).
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import stat
import struct
import sys
import uuid
from collections.abc import Callable
from typing import Final, override

from ledger_engine.fs.port import (
    AclEntry,
    Caps,
    FolderMark,
    FoldRule,
    NotOffered,
    Right,
    check_name,
    same_path,
    split_absolute,
)

# The one statement of this module's platform (not an `assert`, which `-O` strips): elsewhere the import fails, and
# pyright checks the rest of the module for macOS only (`--pythonplatform`; S0113).
if sys.platform != "darwin":
    raise ImportError("the macOS adapter; select one with ledger_engine.fs.this_os")  # noqa: TRY003 (one raise site)

from ledger_engine.fs.posix import PosixDir, PosixFS, PosixLegacyLocks

COPYFILE_XATTR: Final = 1 << 2
ACCESS_DELETE: Final = 1 << 12  # <sys/unistd.h> _DELETE_OK: access(2) answers it from the file's and folder's ACLs
ACCESS_READ_XATTRS: Final = 1 << 17  # _REXT_OK
ATTR_VOL_INFO: Final = 0x80000000
ATTR_VOL_CAPABILITIES: Final = 0x00020000
ATTR_VOL_UUID: Final = 0x00040000  # <sys/attr.h>, checked against the macOS 26 SDK and probed (S0089)
ATTR_CMN_RETURNED_ATTRS: Final = 0x80000000  # the attributes the volume really returned come first
ATTR_CMN_CRTIME: Final = 0x00000200  # <sys/attr.h>: the creation time, a `struct timespec` (S0089 round 1)
MFSTYPENAMELEN: Final = 16
STATFS_SIZE: Final = 2168  # `struct statfs` (64-bit inodes, the only layout on arm64 and macOS 11+), checked: SDK 26
STATFS_FSTYPENAME: Final = 72  # offset of `f_fstypename` in it
VOL_CAP_INT_RENAME_SWAP: Final = 0x00040000
VOL_CAP_INT_RENAME_EXCL: Final = 0x00080000
LOCK_FLAGS: Final = stat.UF_IMMUTABLE | stat.UF_APPEND | stat.SF_IMMUTABLE | stat.SF_APPEND
RENAME_SWAP: Final = 0x2
RENAME_EXCL: Final = 0x4
PC_CASE_SENSITIVE: Final = 11  # <sys/unistd.h> _PC_CASE_SENSITIVE (absent from os.pathconf_names)
MAXPATHLEN: Final = 1024
ACL_TYPE_EXTENDED: Final = 0x100  # <sys/acl.h>, checked against the macOS 26 SDK and probed on APFS (S0061 r5)
ACL_FIRST_ENTRY: Final = 0
ACL_NEXT_ENTRY: Final = -1
ACL_EXTENDED_ALLOW: Final = 1
ACCESS_RMFILE: Final = 1 << 14  # <sys/unistd.h> _RMFILE_OK: entries of the folder may be removed (delete_child)
# <sys/acl.h> acl_perm_t, in chmod's words for a folder (checked against the macOS 26 SDK, S0061 r6)
ACL_PERMS: Final = (
    ("list", 1 << 1),
    ("add_file", 1 << 2),
    ("search", 1 << 3),
    ("delete", 1 << 4),
    ("add_subdirectory", 1 << 5),
    ("delete_child", 1 << 6),
    ("readattr", 1 << 7),
    ("writeattr", 1 << 8),
    ("readextattr", 1 << 9),
    ("writeextattr", 1 << 10),
    ("readsecurity", 1 << 11),
    ("writesecurity", 1 << 12),
    ("chown", 1 << 13),
    ("synchronize", 1 << 20),
)
ACL_FLAGS: Final = (  # <sys/acl.h> acl_flag_t, the entry flags
    ("inherited", 1 << 4),
    ("file_inherit", 1 << 5),
    ("directory_inherit", 1 << 6),
    ("limit_inherit", 1 << 7),
    ("only_inherit", 1 << 8),
)


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    fn = lib.renameatx_np
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    cp = lib.fcopyfile
    cp.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    cp.restype = ctypes.c_int
    ga = lib.fgetattrlist
    ga.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    ga.restype = ctypes.c_int
    lib.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    lib.fstatfs.restype = ctypes.c_int
    lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.acl_get_fd_np.restype = ctypes.c_void_p
    lib.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    lib.acl_get_entry.restype = ctypes.c_int
    lib.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.acl_get_tag_type.restype = ctypes.c_int
    lib.acl_get_flagset_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.acl_get_flagset_np.restype = ctypes.c_int
    lib.acl_get_flag_np.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.acl_get_flag_np.restype = ctypes.c_int
    lib.acl_get_permset.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.acl_get_permset.restype = ctypes.c_int
    lib.acl_get_perm_np.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.acl_get_perm_np.restype = ctypes.c_int
    lib.acl_free.argtypes = [ctypes.c_void_p]
    lib.acl_free.restype = ctypes.c_int
    return lib


class _AttrList(ctypes.Structure):
    _fields_ = (
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    )


def _errno_raise() -> None:
    e = ctypes.get_errno()
    raise OSError(e, os.strerror(e))


def _named(
    word: ctypes.c_void_p, bits: tuple[tuple[str, int], ...], get: Callable[[ctypes.c_void_p, int], int]
) -> frozenset[str]:
    """The names of the bits set in a permset or flagset, by libc's own getter, plus `unknown` when the word it points
    at holds a bit libc does not name or disagrees with the getter (fails closed)."""
    named = {n for n, b in bits if get(word, b) == 1}
    raw = ctypes.c_uint32.from_address(word.value).value if word.value else None
    known = 0
    for _, b in bits:
        known |= b
    if raw is None or raw & ~known or {n for n, b in bits if raw & b} != named:
        named.add("unknown")
    return frozenset(named)


def _getpath(fd: int) -> str:
    raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(MAXPATHLEN))
    return os.fsdecode(raw.split(b"\0", 1)[0])


class DarwinDir(PosixDir):
    """A handle on one directory (`ledger_engine.fs.port.DirHandle`)."""

    def __init__(self, fs: DarwinFS, path: str, *, fd: int | None = None) -> None:
        super().__init__(fs, path, fd=fd)
        self._mac = fs

    def path(self) -> str:
        return _getpath(self.fd)

    @override
    def locked(self, name: str, st: os.stat_result) -> bool:
        return bool(getattr(st, "st_flags", 0) & LOCK_FLAGS)

    @override
    def copy_xattrs(self, src: int, dst: int) -> None:
        if self._mac.libc.fcopyfile(src, dst, None, COPYFILE_XATTR) != 0:
            _errno_raise()

    def allowed(self, name: str, right: Right) -> bool:
        mode = {
            Right.CHANGE_ENTRIES: os.W_OK | os.X_OK,
            Right.REMOVE_ENTRIES: ACCESS_RMFILE,
            Right.SEARCH: os.X_OK,
            Right.DELETE: ACCESS_DELETE,
            Right.READ_XATTRS: ACCESS_READ_XATTRS,
        }[right]
        target = "." if name == "." else check_name(name)
        return os.access(target, mode, dir_fd=self.fd, follow_symlinks=False)

    def caps(self) -> Caps:
        attrs = _AttrList(5, 0, 0, ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES, 0, 0, 0)
        buf = ctypes.create_string_buffer(64)
        if self._mac.libc.fgetattrlist(self.fd, ctypes.byref(attrs), buf, len(buf), 0) != 0:
            _errno_raise()
        # u_int32 length, then vol_capabilities_attr_t: capabilities[4], valid[4]; index 1 = the interfaces
        _, _, interfaces, _, _, _, valid, _, _ = struct.unpack("<9I", buf.raw[:36])
        return caps_from(interfaces, valid)

    def volume_id(self) -> str | None:
        """`fgetattrlist(ATTR_VOL_UUID)` on the folder's descriptor, with `ATTR_CMN_RETURNED_ATTRS` so a volume that
        has no UUID is told from one that has (S0089 probe: APFS, HFS+, exFAT and FAT return one; devfs and autofs
        do not)."""
        attrs = _AttrList(5, 0, ATTR_CMN_RETURNED_ATTRS, ATTR_VOL_INFO | ATTR_VOL_UUID, 0, 0, 0)
        buf = ctypes.create_string_buffer(64)
        if self._mac.libc.fgetattrlist(self.fd, ctypes.byref(attrs), buf, len(buf), 0) != 0:
            _errno_raise()
        # u_int32 length, then attribute_set_t (common, vol, dir, file, fork), then the uuid_t when returned
        returned_vol = struct.unpack_from("<I", buf.raw, 8)[0]
        return uuid_from(returned_vol, buf.raw[24:40])

    def born(self) -> int | None:
        """The folder's creation time from `fgetattrlist` (`ATTR_CMN_CRTIME`, a `timespec`: whole precision;
        `fstat`'s `st_birthtime` is a float), None when the file system returns none (S0089 round 2, verifier F2)."""
        attrs = _AttrList(5, 0, ATTR_CMN_RETURNED_ATTRS | ATTR_CMN_CRTIME, 0, 0, 0, 0)
        out = ctypes.create_string_buffer(64)
        if self._mac.libc.fgetattrlist(self.fd, ctypes.byref(attrs), out, len(out), 0) != 0:
            _errno_raise()
        if not struct.unpack_from("<I", out.raw, 4)[0] & ATTR_CMN_CRTIME:
            return None
        sec, nsec = struct.unpack_from("<qq", out.raw, 24)
        return sec * 1_000_000_000 + nsec

    def mark(self) -> FolderMark:
        """`volume_id`; the type name from `fstatfs` (`f_fstypename`); `born` (never an error when the file system
        keeps no creation time, so a drive with no UUID is still told `no_drive_id`)."""
        volume = self.volume_id()
        buf = ctypes.create_string_buffer(STATFS_SIZE)
        if self._mac.libc.fstatfs(self.fd, buf) != 0:
            _errno_raise()
        fs_type = fs_type_from(buf.raw[STATFS_FSTYPENAME : STATFS_FSTYPENAME + MFSTYPENAMELEN])
        return FolderMark(volume, fs_type, self.born())

    def acl(self) -> tuple[AclEntry, ...]:
        """`acl_get_fd_np(ACL_TYPE_EXTENDED)` on the folder's descriptor, each entry read in full: its kind and its
        permission and flag bits by name (not its subject: S0061 r7). A bit libc does not name is
        `unknown`: the permission word and the flag word are also read whole where the permset and flagset point
        (probed on macOS 26: they point at the entry's own words) and must agree with the named bits, else `unknown`
        (fails closed). No ACL at all (`ENOENT`) is empty; any other error is raised."""
        lib = self._mac.libc
        acl = lib.acl_get_fd_np(self.fd, ACL_TYPE_EXTENDED)
        if not acl:
            e = ctypes.get_errno()
            if e == errno.ENOENT:
                return ()
            raise OSError(e, os.strerror(e))
        try:
            out: list[AclEntry] = []
            entry = ctypes.c_void_p()
            which = ACL_FIRST_ENTRY
            while lib.acl_get_entry(acl, which, ctypes.byref(entry)) == 0:
                which = ACL_NEXT_ENTRY
                tag, flags, perms = ctypes.c_int(), ctypes.c_void_p(), ctypes.c_void_p()
                if (
                    lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0
                    or lib.acl_get_flagset_np(entry, ctypes.byref(flags)) != 0
                    or lib.acl_get_permset(entry, ctypes.byref(perms)) != 0
                ):
                    _errno_raise()
                out.append(
                    AclEntry(
                        deny=tag.value != ACL_EXTENDED_ALLOW,
                        perms=_named(perms, ACL_PERMS, lib.acl_get_perm_np),
                        flags=_named(flags, ACL_FLAGS, lib.acl_get_flag_np),
                    )
                )
            return tuple(out)
        finally:
            lib.acl_free(acl)

    def _renamex(self, a: str, b: str, flags: int) -> None:
        rc = self._mac.libc.renameatx_np(
            self.fd, os.fsencode(check_name(a)), self.fd, os.fsencode(check_name(b)), flags
        )
        if rc != 0:
            _errno_raise()

    def swap(self, a: str, b: str) -> None:
        if not self.caps().swap:
            raise NotOffered("RENAME_SWAP")
        self._renamex(a, b, RENAME_SWAP)

    def rename_excl(self, src: str, dst: str) -> None:
        if not self.caps().excl:
            raise NotOffered("RENAME_EXCL")
        self._renamex(src, dst, RENAME_EXCL)

    def open_child(self, name: str) -> DarwinDir:
        return DarwinDir(self._mac, "", fd=self.child_fd(name))

    def rule(self) -> FoldRule:
        try:
            return FoldRule(nfd=True, casefold=os.fpathconf(self.fd, PC_CASE_SENSITIVE) != 1)
        except OSError:
            return FoldRule(nfd=True, casefold=True)


class DarwinFS(PosixFS):
    """The real file system on macOS (`ledger_engine.fs.port.FileSystem`)."""

    def __init__(self, on_fallback: Callable[[], None] | None = None) -> None:
        self.libc = _libc()
        self._fallback_volumes: set[int] = set()  # bounded by the number of mounted volumes (INV-10)
        self._on_fallback = on_fallback

    @override
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

    @override
    def same_path(self, now: str, recorded: str) -> bool:
        return same_path(now, recorded)

    def open_dir(self, path: str) -> DarwinDir:
        split_absolute(path)
        return DarwinDir(self, path)

    @override
    def rule_at(self, folder: str) -> FoldRule:
        try:
            return FoldRule(nfd=True, casefold=os.pathconf(folder, PC_CASE_SENSITIVE) != 1)
        except OSError:
            return FoldRule(nfd=True, casefold=True)

    @override
    def open_folder(self, path: str) -> tuple[int, str]:
        fd = os.open(path, os.O_EVTONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            return fd, _getpath(fd)
        except BaseException:
            os.close(fd)
            raise


class DarwinLegacyLocks(PosixLegacyLocks):
    """The legacy write lock on macOS: `flock`, as portalocker 3.2.0 takes it there (`PosixLegacyLocks`)."""


def caps_from(interfaces: int, valid: int) -> Caps:
    """The volume's rename capabilities from its `vol_capabilities_attr_t` interface words: a call is offered only
    when its bit is both valid and set (probed S0065: APFS both; HFS+ the swap bit not valid, the exclusive one set;
    exFAT and FAT both valid and unset)."""

    def offers(bit: int) -> bool:
        return bool(interfaces & bit and valid & bit)

    return Caps(swap=offers(VOL_CAP_INT_RENAME_SWAP), excl=offers(VOL_CAP_INT_RENAME_EXCL))


def fs_type_from(raw: bytes) -> str:
    """`f_fstypename` up to its NUL; a byte that is not ASCII becomes U+FFFD, never an exception out of `mark` (S0090,
    BL-0221's overflow): the record refuses such a name, so the drive is one Finzytrack cannot identify
    (`ledger.handles.drive_of`: `no_drive_id`, `unknown_drive`)."""
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def uuid_from(returned_vol: int, raw: bytes) -> str | None:
    """The volume UUID in lower case (`8-4-4-4-12` hex digits), or None when the volume did not return one
    (`returned_vol` lacks `ATTR_VOL_UUID`) or returned the nil UUID, which names no drive (S0089)."""
    if not returned_vol & ATTR_VOL_UUID or raw == bytes(16):
        return None
    return str(uuid.UUID(bytes=raw))
