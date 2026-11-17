"""The file-system port on Linux (`ledger_engine.fs.port`; `design/specs/engine-jobs.md` §6.2, §6.3, §6.8, §15; S0125,
BL-0266). It writes exactly what the macOS adapter writes, under the same contract; only these primitives differ (owner,
2026-11-16: "Rule unchanged (Recommended)"). The calls both share are `ledger_engine.fs.posix`.

- `durable_sync`: `fsync` (on Linux it reaches stable storage: no `F_FULLFSYNC` and no fallback to report; a mount
  with the disk's write cache trusted and barriers off is the system's own setting, §15).
- Swap and exclusive rename: glibc's `renameat2(fd, a, fd, b, RENAME_EXCHANGE | RENAME_NOREPLACE)` on the
  directory's own descriptor. The swap only where the volume's file system is one known to give it as one operation
  that a power loss keeps or drops whole (`SWAP_TYPES`: ext3, ext4, xfs, btrfs and f2fs, by their journal or
  copy-on-write, and tmpfs, which keeps nothing over a power loss anyway), else `NotOffered` before the call: §6.8's
  best-effort mode, as on macOS's exFAT, FAT, HFS+ and network shares (ext2, vfat, exFAT, NTFS, network and FUSE
  file systems; Linux has no call that says whether a volume offers it without making it). A volume of a swap type
  that refuses the exchange after all raises it with nothing done, and the save rolls back ("not saved"). The
  exclusive rename is tried on every volume (owner, 2026-11-17: "A2"; probed: every tested type accepts it); a
  refusal of the call (`REFUSED_FLAGS`) is `NotOffered`, nothing done, remembered per volume for this `LinuxFS` (one
  job), and only then do the callers check that the name is free and rename plainly (`ledger.handles.move`).
- A volume's file-system type is its mount's (`/proc/self/mountinfo`, found by `statx`'s mount ID, so ext2 and ext4,
  which share one `statfs` magic, are told apart); a mount not found there has the empty type, which the record
  refuses (`no_drive_id`) and which offers no swap.
- `path`: the descriptor's link in `/proc/self/fd` (the kernel's path of the folder now); for a folder removed since,
  its last path, as macOS's `F_GETPATH` answers (probed, S0125): the kernel's " (deleted)" is dropped only when the
  folder has no link left (`st_nlink` 0). `/proc` must be mounted (else `OSError`: the save refuses).
- `volume_id` (BL-0218): the drive's UUID as the file system reports it (`fs_uuid`: btrfs's `BTRFS_IOC_FS_INFO`;
  else `FS_IOC_GETFSUUID`, Linux 6.5 and later: ext2-4, xfs and a fresh one per tmpfs mount give a UUID, vfat its
  serial; probed on the CI runner, S0125), else the name in `/dev/disk/by-uuid` whose device is the folder's
  (`st_rdev` = `st_dev`; udev's names, as `blkid` reads them; exFAT): a UUID as it is, a FAT or exFAT format serial
  (`XXXX-XXXX`) or an NTFS serial (16 hex digits) as the version 5 UUID of that serial (`SERIAL_NAMESPACE`), so twin
  sticks share it as on macOS (`handles.SERIAL_IDENTIFIED`); else None (ZFS, network and FUSE file systems, a system
  without udev), and the save refuses `no_drive_id`. `mark`'s creation time is `statx`'s birth time
  (`STATX_BTIME`), None where the file system keeps none (probed: nanoseconds on ext4, xfs and btrfs; whole seconds on
  ext2 with small inodes; 10 ms on exFAT; 0 for a FAT drive's top level).
- `rule`: names are compared as bytes (Linux normalizes nothing) on the file systems known to be case-sensitive
  (`CASE_SENSITIVE_TYPES`), except a folder with the case-folding flag (`FS_IOC_GETFLAGS` & `FS_CASEFOLD_FL`: ext4,
  f2fs, tmpfs, bcachefs), which folds like a case-insensitive volume (NFD and `casefold`); every other file system
  (vfat, exFAT, NTFS, CIFS, FUSE, unknown) and an answer that cannot be read count as folding (§15: folding may merge
  names, never split them). `same_path` compares bytes: a folder renamed to another spelling of its name has moved.
- `allowed`: `access(2)` (which applies POSIX ACLs) for write, search and read, plus what it does not check: the
  folder's and the entry's immutable and append-only flags (`statx` attributes) and the sticky folder's owner rule
  for removing an entry. `acl` is empty: a POSIX ACL has no deny entry and `access(2)` already applies it (an NFSv4
  ACL on an NFS mount is judged by the server through `access(2)` too, §15). `create(..., like=...)` copies the
  `user.` extended attributes (not `security.`, which the system sets, nor `system.`, the ACL: owner, 2026-10-29).
- `LinuxLegacyLocks`: `flock`, as portalocker 3.2.0 takes it on Linux (`ledger_engine.fs.posix`).
- `folder_identity`: the app folder's identity (BL-0262): `(st_dev, st_ino)` and the birth time, since ext4 gives a
  folder removed and made again at once the freed inode number (the Linux CI run of 2026-11-13).

glibc 2.28 or later (`renameat2`, `statx`) and Linux 4.11 or later are needed; with an older glibc every file
operation refuses (`OSError` `ENOTSUP`). This module is Linux-only: importing it elsewhere raises `ImportError`.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import re
import stat
import struct
import sys
import uuid
from collections.abc import Callable
from typing import Final, override

from ledger_engine.fs.port import AclEntry, Caps, FolderMark, FoldRule, NotOffered, Right, check_name, split_absolute
from ledger_engine.fs.posix import PosixDir, PosixFS, PosixLegacyLocks

# The one statement of this module's platform (not an `assert`, which `-O` strips): elsewhere the import fails, and
# pyright checks the rest of the module for Linux only (`--pythonplatform`; S0113).
if sys.platform != "linux":
    raise ImportError("the Linux adapter; select one with ledger_engine.fs.this_os")  # noqa: TRY003 (one raise site)

RENAME_NOREPLACE: Final = 1
RENAME_EXCHANGE: Final = 2
AT_EMPTY_PATH: Final = 0x1000
AT_SYMLINK_NOFOLLOW: Final = 0x100
STATX_BTIME: Final = 0x800
STATX_MNT_ID: Final = 0x1000
STATX_BASIC: Final = 0x7FF
STATX_ATTR_IMMUTABLE: Final = 0x10
STATX_ATTR_APPEND: Final = 0x20
STATX_SIZE: Final = 256  # `struct statx` (<linux/stat.h>): 256 bytes, the offsets below
FS_IOC_GETFSUUID: Final = 0x80111500  # _IOR(0x15, 0, struct fsuuid2 { __u8 len; __u8 uuid[16]; })
BTRFS_IOC_FS_INFO: Final = 0x8400941F  # _IOR(0x94, 31, struct btrfs_ioctl_fs_info_args), 1024 bytes
BTRFS_FS_INFO_SIZE: Final = 1024
BTRFS_FSID: Final = 16  # offset of `fsid[16]`, after `max_id` and `num_devices`
FS_IOC_GETFLAGS: Final = 0x80086601  # _IOR('f', 1, long), on 64-bit Linux; the kernel writes an int
FS_CASEFOLD_FL: Final = 0x40000000
REFUSED_FLAGS: Final = frozenset({errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP})
"""What `renameat2` answers on a file system without the flag asked (or a kernel without the call): nothing done."""
NOT_OFFERED: Final = frozenset({errno.ENOTTY, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOSYS})
"""What an `ioctl` a file system does not have answers: the question has no answer there (not an I/O error)."""
SWAP_TYPES: Final = frozenset({"ext3", "ext4", "xfs", "btrfs", "f2fs", "tmpfs"})
"""File systems that give `RENAME_EXCHANGE` and `RENAME_NOREPLACE` (Linux 3.15 to 4.7) as one operation a power loss
keeps or drops whole: a journal (ext3, ext4 as mounted with one, xfs), copy-on-write (btrfs), f2fs's checkpoints, or
nothing kept at all (tmpfs). Every other file system takes §6.8's best-effort mode."""
CASE_SENSITIVE_TYPES: Final = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "tmpfs", "zfs", "bcachefs"})
"""File systems that compare names as bytes, except in a folder with the case-folding flag."""
CASEFOLD_FLAG_TYPES: Final = frozenset({"ext4", "f2fs", "tmpfs", "bcachefs"})
"""File systems whose folders may carry the case-folding flag (`chattr +F`)."""
BY_UUID: Final = "/dev/disk/by-uuid"
SERIAL: Final = re.compile(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}|[0-9A-Fa-f]{16}")
SERIAL_NAMESPACE: Final = uuid.UUID("3b1c6a52-8f0e-5d6a-9b1e-6c4f0f25a125")
"""The namespace of the version 5 UUID a Linux drive named only by a format serial gets (FAT, exFAT, NTFS), fixed
here so that one serial always names one drive (S0125)."""
DELETED: Final = " (deleted)"


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    try:
        r2 = lib.renameat2
        sx = lib.statx
    except AttributeError:
        raise OSError(errno.ENOTSUP, "glibc 2.28 or later is needed (renameat2, statx)") from None
    r2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    r2.restype = ctypes.c_int
    sx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    sx.restype = ctypes.c_int
    return lib


def _lib() -> ctypes.CDLL:
    """The C library with `renameat2` and `statx` typed (a handle on the library already loaded: no cache is held
    across jobs, `tests/architecture/test_caches.py`)."""
    return _libc()


def _errno_raise() -> None:
    e = ctypes.get_errno()
    raise OSError(e, os.strerror(e))


def _renameat2(dfd: int, a: str, b: str, flags: int) -> None:
    """`renameat2` within the folder `dfd`; an error raises its `OSError`, with nothing done."""
    if _lib().renameat2(dfd, os.fsencode(a), dfd, os.fsencode(b), flags) != 0:
        _errno_raise()


class _Statx:
    """What the adapter reads of a `struct statx`."""

    __slots__ = ("attributes", "attributes_mask", "born", "mnt_id")

    def __init__(self, raw: bytes) -> None:
        mask = struct.unpack_from("<I", raw, 0)[0]
        self.attributes: int = struct.unpack_from("<Q", raw, 8)[0]
        self.attributes_mask: int = struct.unpack_from("<Q", raw, 56)[0]
        sec, nsec = struct.unpack_from("<qI", raw, 80)
        self.born: int | None = sec * 1_000_000_000 + nsec if mask & STATX_BTIME else None
        self.mnt_id: int | None = struct.unpack_from("<Q", raw, 144)[0] if mask & STATX_MNT_ID else None

    @property
    def locked(self) -> bool:
        return bool(self.attributes & self.attributes_mask & (STATX_ATTR_IMMUTABLE | STATX_ATTR_APPEND))


def statx(dfd: int, name: str, flags: int) -> _Statx:
    """`statx` of `name` in `dfd` (`""` with `AT_EMPTY_PATH`: `dfd` itself); an error raises its `OSError`."""
    buf = ctypes.create_string_buffer(STATX_SIZE)
    if _lib().statx(dfd, os.fsencode(name), flags, STATX_BASIC | STATX_BTIME | STATX_MNT_ID, buf) != 0:
        _errno_raise()
    return _Statx(buf.raw)


def folder_identity(fd: int) -> tuple[int, int] | tuple[int, int, int]:
    """The app folder's identity through the descriptor `fd` (S0102, BL-0262): `(st_dev, st_ino, birth time in ns)`,
    or `(st_dev, st_ino)` where the file system keeps no birth time (§6.1 step 8, limit (4))."""
    st = os.fstat(fd)
    born = statx(fd, "", AT_EMPTY_PATH).born
    return (st.st_dev, st.st_ino) if born is None else (st.st_dev, st.st_ino, born)


def _unescape(field: str) -> str:
    """A `mountinfo` field with its octal escapes (`\\040` for a space) undone."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def mount_type(mnt_id: int | None, dev: int) -> str:
    """The file-system type of the mount `mnt_id` (`statx`'s mount ID; without one, the mount of the device `dev`
    when exactly one mount has it), from `/proc/self/mountinfo`; empty when it is not found."""
    found: list[str] = []
    majmin = f"{os.major(dev)}:{os.minor(dev)}"
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
        for line in f:
            left, sep, right = line.partition(" - ")
            fields, tail = left.split(), right.split()
            if not sep or len(fields) < 5 or not tail:
                continue
            if (mnt_id is not None and fields[0] == str(mnt_id)) or (mnt_id is None and fields[2] == majmin):
                found.append(_unescape(tail[0]))
    return found[0] if len(set(found)) == 1 else ""


def uuid_of_name(name: str) -> str | None:
    """A `/dev/disk/by-uuid` name as a drive UUID in lower case: a UUID as it is (never the nil UUID), a format serial
    as the version 5 UUID of its upper-case spelling; any other name names no drive Finzytrack can rely on."""
    try:
        u = uuid.UUID(name)
    except ValueError:
        u = None
    if u is not None and len(name) == 36:
        return None if u.int == 0 else str(u)
    if SERIAL.fullmatch(name):
        return str(uuid.uuid5(SERIAL_NAMESPACE, name.upper()))
    return None


def _by_uuid(dev: int) -> str | None:
    """The drive UUID from udev's `/dev/disk/by-uuid` for the block device `dev`, or None (no such folder, no name
    for that device, or names that disagree)."""
    try:
        names = os.listdir(BY_UUID)
    except FileNotFoundError:
        return None
    found: set[str | None] = set()
    for n in names:
        try:
            st = os.stat(os.path.join(BY_UUID, n))
        except OSError:
            continue
        if stat.S_ISBLK(st.st_mode) and st.st_rdev == dev:
            found.add(uuid_of_name(n))
    return found.pop() if len(found) == 1 else None


def fs_uuid(fd: int, fs_type: str) -> str | None:
    """The drive UUID the file system of `fd` reports itself, or None: btrfs's `BTRFS_IOC_FS_INFO` fsid (probed on Linux
    7.0: btrfs answers no `FS_IOC_GETFSUUID`, and its folders' `st_dev` is no block device's, so udev's names never
    match it); else `FS_IOC_GETFSUUID`: a 16-byte UUID as it is, a 4-byte FAT serial (probed: vfat) as udev spells
    it, `XXXX-XXXX`, then `uuid_of_name`. A file system without the call gives None; any other error raises."""
    if fs_type == "btrfs":
        info = bytearray(BTRFS_FS_INFO_SIZE)
        try:
            fcntl.ioctl(fd, BTRFS_IOC_FS_INFO, info)
        except OSError as e:
            if e.errno not in NOT_OFFERED:
                raise
        else:
            raw = bytes(info[BTRFS_FSID : BTRFS_FSID + 16])
            return str(uuid.UUID(bytes=raw)) if any(raw) else None
    buf = bytearray(17)
    try:
        fcntl.ioctl(fd, FS_IOC_GETFSUUID, buf)
    except OSError as e:
        if e.errno not in NOT_OFFERED:
            raise
        return None
    if buf[0] == 16:
        raw = bytes(buf[1:17])
        return str(uuid.UUID(bytes=raw)) if any(raw) else None
    if buf[0] == 4:
        serial = int.from_bytes(buf[1:5], "little")
        return uuid_of_name(f"{serial >> 16:04X}-{serial & 0xFFFF:04X}")
    return None


def _getpath(fd: int) -> str:
    """The folder's path now (`/proc/self/fd`); a removed folder's last path, as macOS answers."""
    text = os.readlink(f"/proc/self/fd/{fd}")
    if text.endswith(DELETED) and os.fstat(fd).st_nlink == 0:
        return text[: -len(DELETED)]
    return text


def rule_of_fd(fd: int, fs_type: str) -> FoldRule:
    """How the folder `fd` (on a file system of type `fs_type`) compares the names in it (the module's docstring)."""
    wide = FoldRule(nfd=True, casefold=True)
    if fs_type not in CASE_SENSITIVE_TYPES:
        return wide
    if fs_type in CASEFOLD_FLAG_TYPES:
        buf = bytearray(8)
        try:
            fcntl.ioctl(fd, FS_IOC_GETFLAGS, buf)
        except OSError as e:
            if e.errno not in NOT_OFFERED:
                return wide
            return FoldRule(nfd=False, casefold=False)  # the file system has no flags to ask: no folding flag either
        if struct.unpack_from("<I", buf, 0)[0] & FS_CASEFOLD_FL:
            return wide
    return FoldRule(nfd=False, casefold=False)


class LinuxDir(PosixDir):
    """A handle on one directory (`ledger_engine.fs.port.DirHandle`)."""

    def __init__(self, fs: LinuxFS, path: str, *, fd: int | None = None) -> None:
        super().__init__(fs, path, fd=fd)
        self._linux = fs

    def path(self) -> str:
        return _getpath(self.fd)

    def _statx(self) -> _Statx:
        return statx(self.fd, "", AT_EMPTY_PATH)

    def fs_type(self) -> str:
        return mount_type(self._statx().mnt_id, os.fstat(self.fd).st_dev)

    @override
    def locked(self, name: str, st: os.stat_result) -> bool:
        return statx(self.fd, name, AT_SYMLINK_NOFOLLOW).locked

    @override
    def copy_xattrs(self, src: int, dst: int) -> None:
        try:
            names = os.listxattr(src)
        except OSError as e:
            if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                return  # the file system keeps none
            raise
        for n in names:
            if n.startswith("user."):
                os.setxattr(dst, n, os.getxattr(src, n))

    def allowed(self, name: str, right: Right) -> bool:
        target = "." if name == "." else check_name(name)
        if right is Right.SEARCH:
            return os.access(target, os.X_OK, dir_fd=self.fd, follow_symlinks=False)
        if right is Right.READ_XATTRS:
            return os.access(target, os.R_OK, dir_fd=self.fd, follow_symlinks=False)
        if right is Right.CHANGE_ENTRIES:
            return os.access(target, os.W_OK | os.X_OK, dir_fd=self.fd, follow_symlinks=False)
        if right is Right.REMOVE_ENTRIES:
            return os.access(target, os.W_OK | os.X_OK, dir_fd=self.fd, follow_symlinks=False) and not (
                statx(self.fd, target, AT_SYMLINK_NOFOLLOW).locked
            )
        # Right.DELETE: the entry `name` of this folder may be renamed away or removed
        if target == ".":
            return False  # a folder's own removal is its parent's to judge; never asked of `.` (fails closed)
        try:
            entry = os.stat(target, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        folder = os.fstat(self.fd)
        me = os.geteuid()
        sticky_denies = bool(folder.st_mode & stat.S_ISVTX) and me not in (0, entry.st_uid, folder.st_uid)
        return (
            self.allowed(".", Right.REMOVE_ENTRIES)
            and not statx(self.fd, target, AT_SYMLINK_NOFOLLOW).locked
            and not sticky_denies
        )

    def caps(self) -> Caps:
        """The swap by the mount's type (`SWAP_TYPES`); the exclusive rename wherever the volume has not refused it in
        this job (owner, 2026-11-17: "A2": it is tried everywhere, and a refusal at its first use, `NotOffered`, is
        remembered per volume for this `LinuxFS`, which lives for one job, never across jobs)."""
        excl = os.fstat(self.fd).st_dev not in self._linux.no_excl
        return Caps(swap=excl and self.fs_type() in SWAP_TYPES, excl=excl)

    def acl(self) -> tuple[AclEntry, ...]:
        """A POSIX ACL has no deny entry, and `access(2)` applies it (`allowed`): none to judge here."""
        return ()

    def volume_id(self) -> str | None:
        found = fs_uuid(self.fd, self.fs_type())
        return found if found is not None else _by_uuid(os.fstat(self.fd).st_dev)

    def born(self) -> int | None:
        return self._statx().born

    def mark(self) -> FolderMark:
        return FolderMark(self.volume_id(), self.fs_type(), self.born())

    def swap(self, a: str, b: str) -> None:
        if not self.caps().swap:
            raise NotOffered("RENAME_EXCHANGE")
        _renameat2(self.fd, check_name(a), check_name(b), RENAME_EXCHANGE)

    def rename_excl(self, src: str, dst: str) -> None:
        """`renameat2(RENAME_NOREPLACE)`, tried on every volume (owner: "A2"): a refusal of the call itself
        (`REFUSED_FLAGS`; the kernel checks a file system's flags only once both names are looked up, so there is no
        asking without renaming) is `NotOffered`, nothing done, and the volume says so from then on in this job."""
        dev = os.fstat(self.fd).st_dev
        if dev in self._linux.no_excl:
            raise NotOffered("RENAME_NOREPLACE")
        try:
            _renameat2(self.fd, check_name(src), check_name(dst), RENAME_NOREPLACE)
        except OSError as e:
            if e.errno not in REFUSED_FLAGS:
                raise
            self._linux.no_excl.add(dev)
            raise NotOffered("RENAME_NOREPLACE") from None

    def open_child(self, name: str) -> LinuxDir:
        return LinuxDir(self._linux, "", fd=self.child_fd(name))

    def rule(self) -> FoldRule:
        try:
            return rule_of_fd(self.fd, self.fs_type())
        except OSError:
            return FoldRule(nfd=True, casefold=True)


class LinuxFS(PosixFS):
    """The real file system on Linux (`ledger_engine.fs.port.FileSystem`). `on_fallback` is never called: Linux's
    sync has no weaker fallback to report (§6.2)."""

    def __init__(self, on_fallback: Callable[[], None] | None = None) -> None:
        _lib()  # glibc's renameat2 and statx, or `OSError` (ENOTSUP) before any file operation
        self._on_fallback = on_fallback
        self.no_excl: set[int] = set()  # the volumes (`st_dev`) that refused RENAME_NOREPLACE in this job (INV-10)

    @override
    def durable_sync(self, fd: int) -> None:
        os.fsync(fd)

    @override
    def same_path(self, now: str, recorded: str) -> bool:
        return now == recorded

    def open_dir(self, path: str) -> LinuxDir:
        split_absolute(path)
        return LinuxDir(self, path)

    @override
    def open_folder(self, path: str) -> tuple[int, str]:
        """Read-only, so the resolve walk may list the folder; a folder that may be searched but not read is opened
        with `O_PATH` (its path is still read; a listing then fails, as it would on macOS)."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except PermissionError:
            fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            return fd, _getpath(fd)
        except BaseException:
            os.close(fd)
            raise

    @override
    def rule_at(self, folder: str) -> FoldRule:
        try:
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            return FoldRule(nfd=True, casefold=True)
        try:
            return rule_of_fd(fd, mount_type(statx(fd, "", AT_EMPTY_PATH).mnt_id, os.fstat(fd).st_dev))
        except OSError:
            return FoldRule(nfd=True, casefold=True)
        finally:
            os.close(fd)


class LinuxLegacyLocks(PosixLegacyLocks):
    """The legacy write lock on Linux: `flock`, as portalocker 3.2.0 takes it there (`PosixLegacyLocks`)."""
