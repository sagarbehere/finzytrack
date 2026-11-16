"""Probe what a Linux folder's file system offers the engine's write path (S0125; `design/specs/engine-jobs.md` §6.2,
§6.3, §6.8, §15): for each folder given, one JSON line with the file system's type, its drive identity by every source
the Linux adapter may use, the folder's creation time, inode reuse, case folding, the one-step swap and the exclusive
rename, a folder's `fsync`, user extended attributes and the descriptor's path. Prints only; it writes only scratch
entries named `.ft-probe-*` inside each folder given and removes them. The manual CI run runs it on the runner's own
folders and on scratch volumes it formats (`ci.yml`, the engine job), so the journal can record which volume kinds
report what (the brief's "the drive identity probe's result is recorded per volume kind the runner offers").

Usage: python probe_linux_fs.py <folder> [<folder> ...]
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import json
import os
import struct
import sys

if sys.platform != "linux":
    raise SystemExit("a Linux probe")  # noqa: TRY003 (one raise site)

STATX_MNT_ID = 0x1000
STATX_BTIME = 0x800
STATX_BASIC = 0x7FF
AT_SYMLINK_NOFOLLOW = 0x100
AT_EMPTY_PATH = 0x1000
FS_IOC_GETFSUUID = 0x80111500
FS_IOC_GETFLAGS = 0x80086601
FS_CASEFOLD_FL = 0x40000000
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2

libc = ctypes.CDLL(None, use_errno=True)


def statx(dfd: int, name: str, flags: int) -> dict[str, object]:
    buf = ctypes.create_string_buffer(256)
    if libc.statx(dfd, os.fsencode(name), flags, STATX_BASIC | STATX_BTIME | STATX_MNT_ID, buf) != 0:
        return {"errno": errno.errorcode.get(ctypes.get_errno(), "?")}
    raw = buf.raw
    mask = struct.unpack_from("<I", raw, 0)[0]
    attrs, attrs_mask = struct.unpack_from("<Q", raw, 8)[0], struct.unpack_from("<Q", raw, 56)[0]
    ino = struct.unpack_from("<Q", raw, 32)[0]
    bsec, bnsec = struct.unpack_from("<qI", raw, 80)
    mnt = struct.unpack_from("<Q", raw, 144)[0]
    return {
        "mask": hex(mask),
        "btime": bsec * 1_000_000_000 + bnsec if mask & STATX_BTIME else None,
        "ino": ino,
        "mnt_id": mnt if mask & STATX_MNT_ID else None,
        "attributes": hex(attrs),
        "attributes_mask": hex(attrs_mask),
    }


def mount_of(mnt_id: object, dev: int) -> dict[str, str]:
    with open("/proc/self/mountinfo", encoding="utf-8") as f:
        for line in f:
            left, _, right = line.partition(" - ")
            fields = left.split()
            fstype, source = right.split()[:2]
            if (mnt_id is not None and fields[0] == str(mnt_id)) or (
                mnt_id is None and fields[2] == f"{os.major(dev)}:{os.minor(dev)}"
            ):
                return {"fstype": fstype, "source": source, "majmin": fields[2], "mount": fields[4]}
    return {}


def fsuuid(fd: int) -> object:
    import uuid

    buf = bytearray(17)
    try:
        fcntl.ioctl(fd, FS_IOC_GETFSUUID, buf)
    except OSError as e:
        return {"errno": errno.errorcode.get(e.errno or 0, "?")}
    n = buf[0]
    return {"len": n, "uuid": str(uuid.UUID(bytes=bytes(buf[1:17]))) if n == 16 else bytes(buf[1 : 1 + n]).hex()}


def by_uuid(dev: int) -> list[str]:
    found: list[str] = []
    base = "/dev/disk/by-uuid"
    try:
        names = os.listdir(base)
    except OSError as e:
        return [f"errno {errno.errorcode.get(e.errno or 0, '?')}"]
    for n in names:
        try:
            if os.stat(os.path.join(base, n)).st_rdev == dev:
                found.append(n)
        except OSError:
            continue
    return found


def statfs(fd: int) -> dict[str, str]:
    buf = ctypes.create_string_buffer(256)
    if libc.fstatfs(fd, buf) != 0:
        return {"errno": errno.errorcode.get(ctypes.get_errno(), "?")}
    f_type = struct.unpack_from("<q", buf.raw, 0)[0]
    fsid = struct.unpack_from("<ii", buf.raw, 56)
    return {"f_type": hex(f_type & 0xFFFFFFFF), "f_fsid": f"{fsid[0] & 0xFFFFFFFF:08x}{fsid[1] & 0xFFFFFFFF:08x}"}


def flags(fd: int) -> object:
    buf = bytearray(8)
    try:
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, buf)
    except OSError as e:
        return {"errno": errno.errorcode.get(e.errno or 0, "?")}
    v = struct.unpack_from("<I", buf, 0)[0]
    return {"flags": hex(v), "casefold": bool(v & FS_CASEFOLD_FL)}


def rename2(dfd: int, a: str, b: str, flag: int) -> str:
    libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if libc.renameat2(dfd, os.fsencode(a), dfd, os.fsencode(b), flag) != 0:
        return errno.errorcode.get(ctypes.get_errno(), "?")
    return "ok"


def probe(folder: str) -> dict[str, object]:
    out: dict[str, object] = {"folder": folder}
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        sx = statx(fd, "", AT_EMPTY_PATH)
        out |= {"statx": sx, "mount": mount_of(sx.get("mnt_id"), st.st_dev), "statfs": statfs(fd)}
        out |= {"fsuuid": fsuuid(fd), "by_uuid": by_uuid(st.st_dev), "flags": flags(fd)}
        out["fd_path"] = os.readlink(f"/proc/self/fd/{fd}")
        a, b, d = ".ft-probe-a", ".ft-probe-b", ".ft-probe-d"
        for n in (a, b):
            with open(os.open(n, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd), "wb") as f:
                f.write(n.encode())
        out["exchange"] = rename2(fd, a, b, RENAME_EXCHANGE)
        out["noreplace_taken"] = rename2(fd, a, b, RENAME_NOREPLACE)
        out["noreplace_free"] = rename2(fd, a, ".ft-probe-c", RENAME_NOREPLACE)
        for n in (".ft-probe-c", a, b):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(n, dir_fd=fd)
        try:
            os.fsync(fd)
            out["dir_fsync"] = "ok"
        except OSError as e:
            out["dir_fsync"] = errno.errorcode.get(e.errno or 0, "?")
        os.mkdir(d, dir_fd=fd)
        first = statx(fd, d, AT_SYMLINK_NOFOLLOW)
        os.rmdir(d, dir_fd=fd)
        os.mkdir(d, dir_fd=fd)
        again = statx(fd, d, AT_SYMLINK_NOFOLLOW)
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        try:
            os.setxattr(dfd, "user.ft-probe", b"1")
            out["user_xattr"] = "ok"
        except OSError as e:
            out["user_xattr"] = errno.errorcode.get(e.errno or 0, "?")
        finally:
            os.close(dfd)
        os.rmdir(d, dir_fd=fd)
        out["reuse"] = {
            "same_ino": first.get("ino") == again.get("ino"),
            "same_btime": first.get("btime") == again.get("btime"),
            "btime": first.get("btime"),
        }
    finally:
        os.close(fd)
    return out


def main(argv: list[str]) -> int:
    for folder in argv:
        try:
            print(json.dumps(probe(folder), sort_keys=True))  # noqa: T201 (the probe's output)
        except OSError as e:
            failed = {"folder": folder, "error": f"{errno.errorcode.get(e.errno or 0, '?')} {e.strerror}"}
            print(json.dumps(failed))  # noqa: T201 (the probe's output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
