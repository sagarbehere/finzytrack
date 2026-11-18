"""Probe what a Windows folder's volume offers the engine's write path (S0126; `design/specs/engine-jobs.md` §6.2, §6.3,
§6.8, §15; ADR-0013): for each folder given, one JSON line with the volume (file system, serial, GUID, flags), the
folder's identity (`FileIdInfo`, creation time, Python's `os.stat`), the paths `GetFinalPathNameByHandleW` gives, case
sensitivity, a folder's and a read-only file's `FlushFileBuffers`, the exclusive create and renames, `ReplaceFileW` and
its error names, whether a concurrent reader ever finds the target missing during each replace primitive (a state a
power loss could keep), renames and removals of open files and of folders holding open entries, renames and opens
relative to a folder handle (`NtCreateFile`, `FileRenameInformation` with a root directory), hard links, the
mirror's pointer file (BL-0206: a generation held by SQLite, the pointer's swap under readers), NTFS tunnelling
of creation times, file-ID reuse, reserved names and trailing dots, and `LockFileEx` against `msvcrt.locking`; and
(BL-0294, S0132) whether the volume is remote, the read-only attribute against the publish's removals and renames and
its clearing under each handle the product holds, and the POSIX rename on a network share (a UNC folder gets only the
volume and these two parts).

Prints only; it writes only scratch entries inside a folder `.ft-probe-<pid>` it makes in each folder given and
removes. The manual CI run runs it on the runner's folders and on scratch volumes (VHDs) it formats (`ci.yml`, the
`engine-windows` job), so the journal can record which volume kinds give what.

Usage: python probe_windows_fs.py <folder> [<folder> ...]
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import json
import msvcrt
import os
import struct
import sys
import threading
from ctypes import wintypes
from typing import Any

if sys.platform != "win32":
    raise SystemExit("a Windows probe")  # noqa: TRY003 (one raise site)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
SYNCHRONIZE = 0x00100000
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
FILE_LIST_DIRECTORY = 0x1
FILE_SHARE_ALL = 0x7
FILE_SHARE_RW = 0x3
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID = ctypes.c_void_p(-1).value
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8
REPLACEFILE_WRITE_THROUGH = 0x1
FILE_BASIC_INFO_CLASS = 0
FILE_RENAME_INFO_CLASS = 3
FILE_RENAME_INFO_EX_CLASS = 22
FILE_ID_INFO_CLASS = 18
FILE_CASE_SENSITIVE_INFO_CLASS = 23
FILE_RENAME_FLAG_REPLACE_IF_EXISTS = 0x1
FILE_RENAME_FLAG_POSIX_SEMANTICS = 0x2
NT_FILE_RENAME_INFORMATION = 10
NT_FILE_RENAME_INFORMATION_EX = 65
FILE_OPEN = 1
FILE_CREATE = 2
FILE_DIRECTORY_FILE = 0x1
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_NON_DIRECTORY_FILE = 0x40
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_OPEN_FOR_BACKUP_INTENT = 0x4000
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK = 0x2
VOLUME_FLAGS = {
    "case_sensitive_search": 0x1,
    "case_preserved_names": 0x2,
    "persistent_acls": 0x8,
    "posix_unlink_rename": 0x400,
    "transactions": 0x200000,
    "hard_links": 0x400000,
    "open_by_file_id": 0x01000000,
    "usn_journal": 0x02000000,
    "read_only": 0x80000,
}
ERRORS = {
    2: "FILE_NOT_FOUND",
    3: "PATH_NOT_FOUND",
    5: "ACCESS_DENIED",
    6: "INVALID_HANDLE",
    19: "WRITE_PROTECT",
    32: "SHARING_VIOLATION",
    33: "LOCK_VIOLATION",
    50: "NOT_SUPPORTED",
    80: "FILE_EXISTS",
    87: "INVALID_PARAMETER",
    123: "INVALID_NAME",
    145: "DIR_NOT_EMPTY",
    183: "ALREADY_EXISTS",
    1175: "UNABLE_TO_REMOVE_REPLACED",
    1176: "UNABLE_TO_MOVE_REPLACEMENT",
    1177: "UNABLE_TO_MOVE_REPLACEMENT_2",
}


def _sig(fn: Any, args: list[Any], res: Any) -> None:
    fn.argtypes = args
    fn.restype = res


_sig(
    k32.CreateFileW,
    [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ],
    wintypes.HANDLE,
)
_sig(k32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)
_sig(k32.FlushFileBuffers, [wintypes.HANDLE], wintypes.BOOL)
_sig(k32.WriteFile, [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL)
_sig(k32.MoveFileExW, [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL)
_sig(
    k32.ReplaceFileW,
    [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p],
    wintypes.BOOL,
)
_sig(k32.GetFileAttributesW, [wintypes.LPCWSTR], wintypes.DWORD)
_sig(
    k32.GetFileInformationByHandleEx,
    [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
    wintypes.BOOL,
)
_sig(
    k32.SetFileInformationByHandle,
    [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
    wintypes.BOOL,
)
_sig(
    k32.GetFinalPathNameByHandleW,
    [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD],
    wintypes.DWORD,
)
_sig(
    k32.GetVolumeInformationByHandleW,
    [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ],
    wintypes.BOOL,
)
_sig(k32.GetVolumePathNameW, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL)
_sig(k32.GetVolumeNameForVolumeMountPointW, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL)
_sig(k32.CreateHardLinkW, [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p], wintypes.BOOL)
_sig(k32.CreateDirectoryW, [wintypes.LPCWSTR, ctypes.c_void_p], wintypes.BOOL)
_sig(k32.RemoveDirectoryW, [wintypes.LPCWSTR], wintypes.BOOL)
_sig(k32.DeleteFileW, [wintypes.LPCWSTR], wintypes.BOOL)
_sig(k32.GetFileType, [wintypes.HANDLE], wintypes.DWORD)
_sig(
    k32.LockFileEx,
    [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p],
    wintypes.BOOL,
)
_sig(ntdll.RtlNtStatusToDosError, [ctypes.c_long], wintypes.ULONG)
_sig(k32.SetFileAttributesW, [wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL)
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_VALID_SET_FLAGS = 0x31A7
NT_FILE_DISPOSITION_INFORMATION_EX = 64
FILE_DISPOSITION_DELETE_POSIX = 0x3  # DELETE | POSIX_SEMANTICS
FILE_FS_DEVICE_INFORMATION = 4
FILE_REMOTE_DEVICE = 0x10


class UNICODE_STRING(ctypes.Structure):
    _fields_ = (("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p))


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    )


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p))


_sig(
    ntdll.NtCreateFile,
    [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ],
    ctypes.c_long,
)
_sig(
    ntdll.NtSetInformationFile,
    [wintypes.HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG, ctypes.c_int],
    ctypes.c_long,
)
_sig(
    ntdll.NtQueryVolumeInformationFile,
    [wintypes.HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG, ctypes.c_int],
    ctypes.c_long,
)


def err(code: int | None = None) -> str:
    c = ctypes.get_last_error() if code is None else code
    return f"{c}:{ERRORS.get(c, '?')}"


def nt_err(status: int) -> str:
    return f"nt {status & 0xFFFFFFFF:#010x} -> {err(ntdll.RtlNtStatusToDosError(status))}"


def open_path(path: str, access: int, share: int = FILE_SHARE_ALL, disp: int = OPEN_EXISTING, flags: int = 0) -> int:
    h = k32.CreateFileW(path, access, share, None, disp, flags | FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == INVALID:
        raise OSError(ctypes.get_last_error(), err())
    return int(h)


def open_dir(path: str, access: int = GENERIC_READ, share: int = FILE_SHARE_ALL) -> int:
    return open_path(path, access, share, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)


def close(h: int) -> None:
    k32.CloseHandle(h)


def write_file(path: str, data: bytes) -> None:
    h = open_path(path, GENERIC_WRITE, FILE_SHARE_ALL, CREATE_NEW)
    try:
        n = wintypes.DWORD()
        if not k32.WriteFile(h, data, len(data), ctypes.byref(n), None):
            raise OSError(ctypes.get_last_error(), err())
    finally:
        close(h)


def exists(path: str) -> bool:
    return k32.GetFileAttributesW(path) != 0xFFFFFFFF


def attempt(fn: Any) -> Any:
    """`fn()`'s value, or the error it raised as text."""
    try:
        return fn()
    except OSError as e:
        return f"error {e.args[1] if len(e.args) > 1 else e}"
    except Exception as e:  # noqa: BLE001 (a probe reports every failure and goes on)
        return f"error {type(e).__name__}: {e}"


def bool_call(ok: int) -> str:
    return "ok" if ok else err()


def final_paths(h: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, flags in (("normalized_dos", 0), ("guid", 1), ("nt", 2), ("opened_dos", 8)):
        buf = ctypes.create_unicode_buffer(1024)
        n = k32.GetFinalPathNameByHandleW(h, buf, 1024, flags)
        out[label] = buf.value if n else err()
    return out


def file_id(h: int) -> dict[str, Any]:
    buf = ctypes.create_string_buffer(24)
    if not k32.GetFileInformationByHandleEx(h, FILE_ID_INFO_CLASS, buf, 24):
        return {"error": err()}
    serial = struct.unpack_from("<Q", buf.raw, 0)[0]
    return {"volume_serial": f"{serial:016x}", "file_id": buf.raw[8:24][::-1].hex()}


def basic(h: int) -> dict[str, Any]:
    buf = ctypes.create_string_buffer(40)
    if not k32.GetFileInformationByHandleEx(h, FILE_BASIC_INFO_CLASS, buf, 40):
        return {"error": err()}
    created, _, written, changed = struct.unpack_from("<qqqq", buf.raw, 0)
    attrs = struct.unpack_from("<I", buf.raw, 32)[0]
    return {"created_100ns": created, "written_100ns": written, "changed_100ns": changed, "attributes": hex(attrs)}


def volume(path: str, h: int) -> dict[str, Any]:
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    out: dict[str, Any] = {}
    if k32.GetVolumeInformationByHandleW(
        h, name, 261, ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags), fs, 261
    ):
        out.update(
            fs=fs.value,
            serial=f"{serial.value:08x}",
            max_component=maxlen.value,
            flags={k: bool(flags.value & v) for k, v in VOLUME_FLAGS.items()},
        )
    else:
        out["error"] = err()
    # BL-0294 (S0132): a network share (`FILE_REMOTE_DEVICE`), as the adapter's `remote()` asks it
    dev = ctypes.create_string_buffer(8)
    io = IO_STATUS_BLOCK()
    status = ntdll.NtQueryVolumeInformationFile(h, ctypes.byref(io), dev, 8, FILE_FS_DEVICE_INFORMATION)
    out["remote"] = (
        bool(struct.unpack_from("<I", dev.raw, 4)[0] & FILE_REMOTE_DEVICE) if status == 0 else nt_err(status)
    )
    root = ctypes.create_unicode_buffer(1024)
    if k32.GetVolumePathNameW(path, root, 1024):
        out["mount_root"] = root.value
        guid = ctypes.create_unicode_buffer(1024)
        out["volume_guid"] = guid.value if k32.GetVolumeNameForVolumeMountPointW(root.value, guid, 1024) else err()
    return out


def rename_info(flags: int, root: int, name: str) -> ctypes.Array[ctypes.c_char]:
    """A `FILE_RENAME_INFO`: the flags (or `ReplaceIfExists`), the root folder handle, the name's length, the name."""
    raw = name.encode("utf-16-le")
    data = struct.pack("<I4xQI", flags, root, len(raw)) + raw + b"\0\0"
    return ctypes.create_string_buffer(data, len(data))


def rename_by_handle(path: str, new: str, flags: int, *, ex: bool, root: int = 0) -> str:
    """`SetFileInformationByHandle(FileRenameInfo[Ex])` on `path`, opened for delete."""
    try:
        h = open_path(path, DELETE | SYNCHRONIZE, FILE_SHARE_ALL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS)
    except OSError as e:
        return f"open {e.args[1]}"
    try:
        info = rename_info(flags, root, new)
        cls = FILE_RENAME_INFO_EX_CLASS if ex else FILE_RENAME_INFO_CLASS
        return bool_call(k32.SetFileInformationByHandle(h, cls, info, len(info)))
    finally:
        close(h)


def nt_open(root: int, name: str, access: int, disposition: int, options: int, share: int = FILE_SHARE_ALL) -> int:
    """`NtCreateFile` of `name` relative to the folder handle `root`; raises OSError with the NT status."""
    buf = ctypes.create_unicode_buffer(name)
    us = UNICODE_STRING(len(name) * 2, len(name) * 2 + 2, ctypes.cast(buf, ctypes.c_void_p))
    oa = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), root, ctypes.pointer(us), 0, None, None)
    io = IO_STATUS_BLOCK()
    h = wintypes.HANDLE()
    status = ntdll.NtCreateFile(
        ctypes.byref(h),
        access | SYNCHRONIZE,
        ctypes.byref(oa),
        ctypes.byref(io),
        None,
        FILE_ATTRIBUTE_NORMAL,
        share,
        disposition,
        options | FILE_SYNCHRONOUS_IO_NONALERT,
        None,
        0,
    )
    if status != 0:
        raise OSError(status, nt_err(status))
    return int(h.value or 0)


def nt_rename(h: int, root: int, name: str, flags: int, *, ex: bool) -> str:
    info = rename_info(flags, root, name)
    io = IO_STATUS_BLOCK()
    status = ntdll.NtSetInformationFile(
        h, ctypes.byref(io), info, len(info), NT_FILE_RENAME_INFORMATION_EX if ex else NT_FILE_RENAME_INFORMATION
    )
    return "ok" if status == 0 else nt_err(status)


def observe_missing(target: str, op: Any, rounds: int = 300) -> dict[str, Any]:
    """Run `op(i)` `rounds` times while another thread asks for `target` without opening it: how often it was missing,
    and the op's failures (a name missing between two steps of one op is a state a power loss can keep)."""
    stop = threading.Event()
    seen = {"looks": 0, "missing": 0}

    def look() -> None:
        while not stop.is_set():
            seen["looks"] += 1
            if not exists(target):
                seen["missing"] += 1

    t = threading.Thread(target=look, daemon=True)
    t.start()
    failures: dict[str, int] = {}
    try:
        for i in range(rounds):
            r = op(i)
            if r != "ok":
                failures[r] = failures.get(r, 0) + 1
    finally:
        stop.set()
        t.join()
    return {"rounds": rounds, **seen, "op_failures": failures}


def probe_replace(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    x, t, b = os.path.join(d, "x.txt"), os.path.join(d, "x.tmp"), os.path.join(d, "x.bak")
    write_file(x, b"old")
    write_file(t, b"new")
    out["replacefile_with_backup"] = bool_call(k32.ReplaceFileW(x, t, b, 0, None, None))
    out["after"] = {n: exists(os.path.join(d, n)) for n in ("x.txt", "x.tmp", "x.bak")}
    with contextlib.suppress(OSError):
        out["x_bytes"] = open(x, "rb").read().decode()  # noqa: SIM115
        out["bak_bytes"] = open(b, "rb").read().decode()  # noqa: SIM115
    # the replaced file held open by a reader that does not share delete (an editor's usual open)
    write_file(t, b"new2")
    h = open_path(x, GENERIC_READ, FILE_SHARE_RW)
    try:
        os.remove(b)
        out["replacefile_target_open_no_share_delete"] = bool_call(k32.ReplaceFileW(x, t, b, 0, None, None))
        out["after_open"] = {n: exists(os.path.join(d, n)) for n in ("x.txt", "x.tmp", "x.bak")}
        out["movefileex_replace_target_open_no_share_delete"] = bool_call(
            k32.MoveFileExW(t, x, MOVEFILE_REPLACE_EXISTING)
        )
        out["posix_rename_replace_target_open_no_share_delete"] = rename_by_handle(
            t, x, FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS, ex=True
        )
    finally:
        close(h)
    # the replaced file held open sharing delete
    h = open_path(x, GENERIC_READ, FILE_SHARE_ALL)
    try:
        with contextlib.suppress(OSError):
            os.remove(b)
        if not exists(t):
            write_file(t, b"new3")
        out["replacefile_target_open_share_delete"] = bool_call(k32.ReplaceFileW(x, t, b, 0, None, None))
        if not exists(t):
            write_file(t, b"new4")
        out["movefileex_replace_target_open_share_delete"] = bool_call(k32.MoveFileExW(t, x, MOVEFILE_REPLACE_EXISTING))
        if not exists(t):
            write_file(t, b"new5")
        out["posix_rename_replace_target_open_share_delete"] = rename_by_handle(
            t, x, FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS, ex=True
        )
    finally:
        close(h)
    out["names_left"] = sorted(os.listdir(d))
    for n in os.listdir(d):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(d, n))

    # Does a reader ever find the target missing in the middle of each primitive?
    write_file(x, b"0")

    def via_replacefile(i: int) -> str:
        write_file(t, str(i).encode())
        with contextlib.suppress(OSError):
            os.remove(b)
        return bool_call(k32.ReplaceFileW(x, t, b, 0, None, None))

    def via_movefileex(i: int) -> str:
        write_file(t, str(i).encode())
        return bool_call(k32.MoveFileExW(t, x, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))

    def via_posix_rename(i: int) -> str:
        write_file(t, str(i).encode())
        return rename_by_handle(t, x, FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS, ex=True)

    def via_two_exclusive_renames(i: int) -> str:
        write_file(t, str(i).encode())
        with contextlib.suppress(OSError):
            os.remove(b)
        first = bool_call(k32.MoveFileExW(x, b, 0))
        return first if first != "ok" else bool_call(k32.MoveFileExW(t, x, 0))

    for label, op in (
        ("replacefile", via_replacefile),
        ("movefileex_replace", via_movefileex),
        ("posix_rename_replace", via_posix_rename),
        ("two_exclusive_renames", via_two_exclusive_renames),
    ):
        out[f"missing_seen_{label}"] = attempt(functools.partial(observe_missing, x, op))
        if not exists(x):
            write_file(x, b"0")
    for n in os.listdir(d):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(d, n))
    return out


def probe_exclusive(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
    write_file(a, b"a")
    write_file(b, b"b")
    out["create_new_on_existing"] = attempt(lambda: write_file(a, b"again") or "ok")
    out["movefileex_no_replace_onto_existing"] = bool_call(k32.MoveFileExW(a, b, 0))
    out["rename_info_no_replace_onto_existing"] = rename_by_handle(a, b, 0, ex=False)
    out["rename_info_ex_posix_no_replace_onto_existing"] = rename_by_handle(
        a, b, FILE_RENAME_FLAG_POSIX_SEMANTICS, ex=True
    )
    root = open_dir(d)
    try:
        out["rename_info_relative_to_root"] = rename_by_handle(a, "c.txt", 0, ex=False, root=root)
        out["names_after_relative"] = sorted(os.listdir(d))
        # open relative to the folder handle, never through a reparse point (the port's `openat`)
        try:
            h = nt_open(root, "made.txt", GENERIC_WRITE | DELETE, FILE_CREATE, FILE_NON_DIRECTORY_FILE)
        except OSError as e:
            out["ntcreatefile_relative_create"] = e.args[1]
        else:
            out["ntcreatefile_relative_create"] = "ok"
            out["nt_rename_relative_no_replace"] = nt_rename(h, root, "b.txt", 0, ex=False)
            out["nt_rename_ex_relative_posix"] = nt_rename(
                h, root, "made2.txt", FILE_RENAME_FLAG_POSIX_SEMANTICS, ex=True
            )
            out["flush_new_file"] = bool_call(k32.FlushFileBuffers(h))
            close(h)
        try:
            h = nt_open(root, "made2.txt", GENERIC_WRITE, FILE_CREATE, FILE_NON_DIRECTORY_FILE)
        except OSError as e:
            out["ntcreatefile_relative_create_existing"] = e.args[1]
        else:
            out["ntcreatefile_relative_create_existing"] = "ok (unexpected)"
            close(h)
    finally:
        close(root)
    out["names_left"] = sorted(os.listdir(d))
    for n in os.listdir(d):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(d, n))
    return out


def probe_sync(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, access in (
        ("read", GENERIC_READ),
        ("write", GENERIC_WRITE),
        ("read_write", GENERIC_READ | GENERIC_WRITE),
    ):
        try:
            h = open_dir(d, access)
        except OSError as e:
            out[f"dir_open_{label}"] = e.args[1]
            continue
        out[f"dir_flush_{label}"] = bool_call(k32.FlushFileBuffers(h))
        close(h)
    f = os.path.join(d, "s.txt")
    write_file(f, b"s")
    for label, access in (("read", GENERIC_READ), ("write", GENERIC_WRITE)):
        h = open_path(f, access, FILE_SHARE_ALL)
        out[f"file_flush_{label}"] = bool_call(k32.FlushFileBuffers(h))
        close(h)
    os.remove(f)
    return out


def probe_open_entries(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sub = os.path.join(d, "a")
    os.makedirs(os.path.join(sub, "b"))
    f = os.path.join(sub, "b", "f.txt")
    write_file(f, b"f")
    h = open_path(f, GENERIC_READ, FILE_SHARE_ALL)
    try:
        out["rename_grandparent_of_open_file"] = bool_call(k32.MoveFileExW(sub, sub + "2", 0))
        if exists(sub + "2"):
            k32.MoveFileExW(sub + "2", sub, 0)
    finally:
        close(h)
    h = open_dir(os.path.join(sub, "b"), GENERIC_READ, FILE_SHARE_RW)
    try:
        out["rename_folder_held_without_share_delete"] = bool_call(
            k32.MoveFileExW(os.path.join(sub, "b"), os.path.join(sub, "b2"), 0)
        )
        out["rename_parent_of_held_folder"] = bool_call(k32.MoveFileExW(sub, sub + "3", 0))
        if exists(sub + "3"):
            k32.MoveFileExW(sub + "3", sub, 0)
    finally:
        close(h)
    h = open_path(f, GENERIC_READ, FILE_SHARE_RW)
    try:
        out["rename_open_file_no_share_delete"] = bool_call(k32.MoveFileExW(f, f + ".r", 0))
        out["delete_open_file_no_share_delete"] = bool_call(k32.DeleteFileW(f))
    finally:
        close(h)
    h = open_path(f, GENERIC_READ, FILE_SHARE_ALL)
    try:
        out["rename_open_file_share_delete"] = bool_call(k32.MoveFileExW(f, f + ".r", 0))
        g = f + ".r" if exists(f + ".r") else f
        out["delete_open_file_share_delete"] = bool_call(k32.DeleteFileW(g))
        out["name_after_delete_while_open"] = exists(g)
    finally:
        close(h)
    out["name_after_last_close"] = exists(f) or exists(f + ".r")
    for n in (f, f + ".r"):
        with contextlib.suppress(OSError):
            os.remove(n)
    os.rmdir(os.path.join(sub, "b"))
    os.rmdir(sub)
    return out


def probe_identity(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    p = os.path.join(d, "again")
    os.mkdir(p)
    h = open_dir(p)
    first = (file_id(h), basic(h).get("created_100ns"))
    close(h)
    os.rmdir(p)
    os.mkdir(p)
    h = open_dir(p)
    second = (file_id(h), basic(h).get("created_100ns"))
    close(h)
    os.rmdir(p)
    out["folder_made_again"] = {
        "same_file_id": first[0] == second[0],
        "same_creation_time": first[1] == second[1],
        "first": first,
        "second": second,
    }
    f = os.path.join(d, "again.txt")
    write_file(f, b"1")
    h = open_path(f, FILE_READ_ATTRIBUTES)
    first = (file_id(h), basic(h).get("created_100ns"))
    close(h)
    os.remove(f)
    write_file(f, b"2")
    h = open_path(f, FILE_READ_ATTRIBUTES)
    second = (file_id(h), basic(h).get("created_100ns"))
    close(h)
    os.remove(f)
    out["file_made_again_tunnelled"] = {
        "same_file_id": first[0] == second[0],
        "same_creation_time": first[1] == second[1],
    }
    st = os.stat(d)
    out["python_stat"] = {
        "st_dev": f"{st.st_dev:x}",
        "st_ino": f"{st.st_ino:x}",
        "st_birthtime_ns": getattr(st, "st_birthtime_ns", None),
        "st_nlink": st.st_nlink,
    }
    return out


def probe_names(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    long = "\\\\?\\" + d if not d.startswith("\\\\?\\") else d
    for name in ("aux.txt", "trail.", "space "):
        try:
            h = open_path(os.path.join(d, name), GENERIC_WRITE, FILE_SHARE_ALL, CREATE_NEW)
            out[f"plain_{name!r}"] = f"type {k32.GetFileType(h)}"
            close(h)
        except OSError as e:
            out[f"plain_{name!r}"] = e.args[1]
        try:
            h = open_path(long + "\\" + name, GENERIC_WRITE, FILE_SHARE_ALL, CREATE_NEW)
            out[f"prefixed_{name!r}"] = f"type {k32.GetFileType(h)}"
            close(h)
        except OSError as e:
            out[f"prefixed_{name!r}"] = e.args[1]
    out["names"] = sorted(os.listdir(long))
    for n in os.listdir(long):
        k32.DeleteFileW(long + "\\" + n)
    out["names_left"] = sorted(os.listdir(long))
    a = os.path.join(d, "Case.txt")
    write_file(a, b"c")
    out["other_case_exists"] = exists(os.path.join(d, "CASE.TXT"))
    out["create_other_case"] = attempt(lambda: write_file(os.path.join(d, "case.txt"), b"x") or "ok")
    os.remove(a)
    with contextlib.suppress(OSError):
        os.remove(os.path.join(d, "case.txt"))
    h = open_dir(d)
    buf = ctypes.create_string_buffer(4)
    out["case_sensitive_dir"] = (
        struct.unpack_from("<I", buf.raw, 0)[0]
        if k32.GetFileInformationByHandleEx(h, FILE_CASE_SENSITIVE_INFO_CLASS, buf, 4)
        else err()
    )
    close(h)
    return out


def probe_links(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    a = os.path.join(d, "l.txt")
    write_file(a, b"l")
    out["hard_link"] = bool_call(k32.CreateHardLinkW(os.path.join(d, "l2.txt"), a, None))
    out["symlink_file"] = attempt(lambda: os.symlink(a, os.path.join(d, "s.txt")) or "ok")
    out["symlink_dir"] = attempt(lambda: os.symlink(d, os.path.join(d, "sd"), target_is_directory=True) or "ok")
    for n in os.listdir(d):
        p = os.path.join(d, n)
        with contextlib.suppress(OSError):
            os.rmdir(p) if os.path.isdir(p) and os.path.islink(p) else os.remove(p)
    out["names_left"] = sorted(os.listdir(d))
    return out


_sig(k32.SetFileTime, [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL)
_sig(k32.GetSystemTimeAsFileTime, [ctypes.c_void_p], None)
_sig(k32.ReadFile, [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL)


def write_synced(path: str, data: bytes) -> None:
    """A new file holding `data`, its data flushed (the port's `create`)."""
    h = open_path(path, GENERIC_WRITE, FILE_SHARE_ALL, CREATE_NEW)
    try:
        n = wintypes.DWORD()
        if not k32.WriteFile(h, data, len(data), ctypes.byref(n), None) or not k32.FlushFileBuffers(h):
            raise OSError(ctypes.get_last_error(), err())
    finally:
        close(h)


def set_mtime(path: str) -> str:
    """The modification time set to now through a handle that shares everything (the adapter's `touch`)."""
    try:
        h = open_path(path, FILE_WRITE_ATTRIBUTES, FILE_SHARE_ALL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)
    except OSError as e:
        return f"open {e.args[1]}"
    try:
        now = ctypes.c_ulonglong()
        k32.GetSystemTimeAsFileTime(ctypes.byref(now))
        return bool_call(k32.SetFileTime(h, None, None, ctypes.byref(now)))
    finally:
        close(h)


def probe_pointer(d: str) -> dict[str, Any]:
    """BL-0206 (S0130): the primitives of the mirror's pointer file. P1: a generation held by a read-only `sqlite3`
    connection (removal, rename, modification time; removal after it closes). P2-P4: the pointer swap (a synced
    temporary pointer, then a rename with replace relative to the folder handle) at three spacings, while a reader
    reads the pointer, sharing delete (P3: missing, empty or other content seen) or not (P4: the swaps it refuses)."""
    import sqlite3

    out: dict[str, Any] = {}
    gen = os.path.join(d, "gen-1-0000000000000001.db")
    con = sqlite3.connect(gen)
    con.execute("create table t (x)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    reader = sqlite3.connect(f"file:{gen.replace(os.sep, '/')}?mode=ro", uri=True)
    try:
        out["p1_read_while_open"] = reader.execute("select x from t").fetchone()
        out["p1_set_mtime_while_open"] = set_mtime(gen)
        out["p1_delete_while_open"] = bool_call(k32.DeleteFileW(gen))
        out["p1_name_after_delete_while_open"] = exists(gen)
        out["p1_rename_while_open"] = bool_call(k32.MoveFileExW(gen, gen + ".aside", 0))
        try:
            root = open_dir(d, GENERIC_READ | FILE_LIST_DIRECTORY)
            try:
                h = nt_open(root, os.path.basename(gen), DELETE, FILE_OPEN, FILE_NON_DIRECTORY_FILE)
                close(h)
                out["p1_nt_open_for_delete_while_open"] = "ok"
            finally:
                close(root)
        except OSError as e:
            out["p1_nt_open_for_delete_while_open"] = e.args[1]
    finally:
        reader.close()
    out["p1_delete_after_close"] = bool_call(k32.DeleteFileW(gen)) if exists(gen) else "already gone"

    names = [f"gen-{i}-{i:016x}.db" for i in range(1, 4)]
    ptr = os.path.join(d, "current.txt")
    root = open_dir(d, GENERIC_READ | FILE_LIST_DIRECTORY)
    try:

        def swap(i: int, gap: float, how: str = "handle") -> str:
            """One pointer swap: `handle` (FileRenameInformation, ReplaceIfExists, relative to the folder handle: the
            adapter's rename), `posix` (FileRenameInformationEx with ReplaceIfExists and POSIX semantics, relative to
            the folder handle), `movefileex` (MoveFileExW with REPLACE_EXISTING and WRITE_THROUGH, by path)."""
            tmp = f"current.txt.{i:016x}.tmp"
            write_synced(os.path.join(d, tmp), names[i % 3].encode("ascii"))
            if gap:
                threading.Event().wait(gap)
            if how == "movefileex":
                r = bool_call(
                    k32.MoveFileExW(os.path.join(d, tmp), ptr, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
                )
            else:
                try:
                    h = nt_open(root, tmp, DELETE, FILE_OPEN, FILE_NON_DIRECTORY_FILE)
                except OSError as e:
                    return f"open {e.args[1]}"
                try:
                    if how == "posix":
                        flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS
                        r = nt_rename(h, root, "current.txt", flags, ex=True)
                    else:
                        r = nt_rename(h, root, "current.txt", 1, ex=False)
                finally:
                    close(h)
            if r != "ok":
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(d, tmp))
            return r

        def read_shared() -> bytes | str:
            try:
                h = open_path(ptr, GENERIC_READ, FILE_SHARE_ALL)
            except OSError as e:
                return f"open {e.args[1]}"
            try:
                buf = ctypes.create_string_buffer(64)
                n = wintypes.DWORD()
                if not k32.ReadFile(h, buf, 64, ctypes.byref(n), None):
                    return f"read {err()}"
                return buf.raw[: n.value]
            finally:
                close(h)

        def read_plain() -> bytes | str:
            try:
                with open(ptr, "rb") as f:  # Python's own open: no delete sharing
                    return f.read(64)
            except OSError as e:
                return f"open {e}"

        loops = [
            ("p3", "handle", "shared", read_shared, 0.0, 300),
            ("p3", "handle", "shared", read_shared, 0.05, 100),
            ("p3", "handle", "shared", read_shared, 0.5, 20),
            ("p4", "handle", "plain_open", read_plain, 0.0, 300),
            ("p4", "handle", "plain_open", read_plain, 0.05, 100),
            ("p4", "handle", "plain_open", read_plain, 0.5, 20),
            # P5 (S0130 run 2): the other two replace primitives under the same readers
            ("p5", "posix", "shared", read_shared, 0.0, 300),
            ("p5", "posix", "shared", read_shared, 0.05, 100),
            ("p5", "posix", "plain_open", read_plain, 0.0, 300),
            ("p5", "movefileex", "shared", read_shared, 0.0, 300),
            ("p5", "movefileex", "shared", read_shared, 0.05, 100),
            ("p5", "movefileex", "plain_open", read_plain, 0.0, 300),
        ]
        for part, how, reader_label, read, gap, rounds in loops:
            write_synced(ptr + ".init", names[0].encode("ascii"))
            with contextlib.suppress(OSError):
                os.remove(ptr)
            k32.MoveFileExW(ptr + ".init", ptr, 0)
            stop = threading.Event()
            seen: dict[str, int] = {"reads": 0}

            def look(read: Any = read, stop: threading.Event = stop, seen: dict[str, int] = seen) -> None:
                while not stop.is_set():
                    r = read()
                    seen["reads"] += 1
                    key = (
                        "ok"
                        if r in {n.encode("ascii") for n in names}
                        else ("empty" if r == b"" else (r if isinstance(r, str) else "other_content"))
                    )
                    if key != "ok":
                        seen[key] = seen.get(key, 0) + 1

            t = threading.Thread(target=look, daemon=True)
            t.start()
            failures: dict[str, int] = {}
            try:
                for i in range(rounds):
                    r = swap(i, gap, how)
                    if r != "ok":
                        failures[r] = failures.get(r, 0) + 1
            finally:
                stop.set()
                t.join()
            out[f"{part}_{how}_swaps_{reader_label}_gap_{int(gap * 1000)}ms"] = {
                "rounds": rounds,
                "swap_failures": failures,
                **seen,
            }
        # P2 alone: no reader, so a refusal is the system's (an antivirus scan), not ours
        for gap, rounds in ((0.0, 300), (0.05, 100), (0.5, 20)):
            failures = {}
            for i in range(rounds):
                r = swap(i, gap)
                if r != "ok":
                    failures[r] = failures.get(r, 0) + 1
            out[f"p2_swaps_alone_gap_{int(gap * 1000)}ms"] = {"rounds": rounds, "swap_failures": failures}
    finally:
        close(root)
    out["names_left"] = sorted(os.listdir(d))
    return out


def set_readonly(path: str, on: bool) -> str:
    attrs = k32.GetFileAttributesW(path)
    if attrs == 0xFFFFFFFF:
        return err()
    new = (attrs | FILE_ATTRIBUTE_READONLY) if on else (attrs & ~FILE_ATTRIBUTE_READONLY)
    return bool_call(k32.SetFileAttributesW(path, (new & FILE_ATTRIBUTE_VALID_SET_FLAGS) or FILE_ATTRIBUTE_NORMAL))


def attrs_of(path: str) -> str:
    a = k32.GetFileAttributesW(path)
    return err() if a == 0xFFFFFFFF else hex(a)


def clear_readonly(root: int, name: str, options: int = 0) -> str:
    """The planned port operation (BL-0294): the entry opened relative to the folder handle, never through a link,
    for its attributes only (sharing everything), and its read-only attribute cleared with `FileBasicInfo` (times 0:
    left as they are; the attributes masked as `SetFileAttributesW` masks them)."""
    try:
        h = nt_open(
            root, name, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES, FILE_OPEN, options | FILE_OPEN_REPARSE_POINT
        )
    except OSError as e:
        return f"open {e.args[1]}"
    try:
        buf = ctypes.create_string_buffer(40)
        if not k32.GetFileInformationByHandleEx(h, FILE_BASIC_INFO_CLASS, buf, 40):
            return f"get {err()}"
        attrs = struct.unpack_from("<I", buf.raw, 32)[0]
        new = (attrs & FILE_ATTRIBUTE_VALID_SET_FLAGS & ~FILE_ATTRIBUTE_READONLY) or FILE_ATTRIBUTE_NORMAL
        info = struct.pack("<qqqqI4x", 0, 0, 0, 0, new)
        ib = ctypes.create_string_buffer(info, len(info))
        return bool_call(k32.SetFileInformationByHandle(h, FILE_BASIC_INFO_CLASS, ib, len(info)))
    finally:
        close(h)


def nt_delete_posix(root: int, name: str, options: int = FILE_NON_DIRECTORY_FILE) -> str:
    """The adapter's `remove` (`rmdir` with `FILE_DIRECTORY_FILE`): opened for delete relative to the folder handle,
    then `FileDispositionInformationEx` with POSIX semantics."""
    try:
        h = nt_open(root, name, DELETE | FILE_READ_ATTRIBUTES, FILE_OPEN, options | FILE_OPEN_REPARSE_POINT)
    except OSError as e:
        return f"open {e.args[1]}"
    try:
        info = ctypes.create_string_buffer(struct.pack("<I", FILE_DISPOSITION_DELETE_POSIX), 4)
        io = IO_STATUS_BLOCK()
        status = ntdll.NtSetInformationFile(h, ctypes.byref(io), info, 4, NT_FILE_DISPOSITION_INFORMATION_EX)
        return "ok" if status == 0 else nt_err(status)
    finally:
        close(h)


def nt_rename_over(root: int, src: str, dst: str, *, posix: bool) -> str:
    """The adapter's `rename_shared` (`posix`) or `rename`: `src` opened for delete relative to the folder handle,
    renamed over `dst` with replace."""
    try:
        h = nt_open(root, src, DELETE, FILE_OPEN, FILE_NON_DIRECTORY_FILE)
    except OSError as e:
        return f"open {e.args[1]}"
    try:
        flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS if posix else 1
        return nt_rename(h, root, dst, flags, ex=posix)
    finally:
        close(h)


def probe_readonly(d: str) -> dict[str, Any]:
    """BL-0294 (S0132): what a read-only entry in the mirror folder does to the publish and its cleanup, and whether
    clearing the attribute (the planned `clear_readonly`) works under every handle the product holds there: SQLite's
    read-only connection on a generation (no delete sharing), the pin's share-all read of `current.txt`, and Python's
    own `open()` (another program's usual open)."""
    import sqlite3

    out: dict[str, Any] = {}
    root = open_dir(d, GENERIC_READ | FILE_LIST_DIRECTORY)
    try:
        # R1: a read-only generation, held by SQLite, then not
        gen = "gen-7-0000000000000007.db"
        path = os.path.join(d, gen)
        con = sqlite3.connect(path)
        con.execute("create table t (x)")
        con.commit()
        con.close()
        out["r1_set_readonly"] = set_readonly(path, True)
        out["r1_attrs"] = attrs_of(path)
        out["r1_set_mtime_readonly"] = set_mtime(path)  # the swap's touch of the replaced generation
        out["r1_delete_posix_readonly"] = nt_delete_posix(root, gen)
        out["r1_deletefile_readonly"] = bool_call(k32.DeleteFileW(path))
        reader = sqlite3.connect(f"file:{path.replace(os.sep, '/')}?mode=ro", uri=True)
        try:
            out["r1_read_readonly_while_held"] = reader.execute("select count(*) from t").fetchone()
            out["r1_clear_while_sqlite_holds"] = clear_readonly(root, gen)
            out["r1_attrs_after_clear"] = attrs_of(path)
            out["r1_delete_posix_while_sqlite_holds"] = nt_delete_posix(root, gen)
        finally:
            reader.close()
        out["r1_delete_posix_after_close"] = nt_delete_posix(root, gen) if exists(path) else "already gone"
        out["r1_name_after"] = exists(path)
        # R2: a read-only pointer, held by the pin's share-all read, replaced by each rename
        ptr = os.path.join(d, "current.txt")
        write_synced(ptr, b"gen-1-0000000000000001.db")
        out["r2_set_readonly"] = set_readonly(ptr, True)
        h = open_path(ptr, GENERIC_READ, FILE_SHARE_ALL)
        try:
            for posix in (True, False):
                write_synced(os.path.join(d, "t.tmp"), b"gen-2-0000000000000002.db")
                key = "posix" if posix else "plain"
                out[f"r2_{key}_rename_over_readonly_held_shared"] = nt_rename_over(
                    root, "t.tmp", "current.txt", posix=posix
                )
                if exists(os.path.join(d, "t.tmp")):
                    os.remove(os.path.join(d, "t.tmp"))
            out["r2_clear_while_held_shared"] = clear_readonly(root, "current.txt")
            out["r2_attrs_after_clear"] = attrs_of(ptr)
            write_synced(os.path.join(d, "t.tmp"), b"gen-2-0000000000000002.db")
            out["r2_posix_rename_over_cleared_held_shared"] = nt_rename_over(root, "t.tmp", "current.txt", posix=True)
        finally:
            close(h)
        out["r2_attrs_of_new_pointer"] = attrs_of(ptr)
        for n in ("t.tmp", "current.txt"):
            if exists(os.path.join(d, n)):
                set_readonly(os.path.join(d, n), False)
                os.remove(os.path.join(d, n))
        # R2b: a read-only pointer with no holder: the plain rename (ReFS, FAT32, exFAT) after the clear
        write_synced(ptr, b"gen-1-0000000000000001.db")
        set_readonly(ptr, True)
        write_synced(os.path.join(d, "t.tmp"), b"gen-2-0000000000000002.db")
        out["r2b_plain_rename_over_readonly"] = nt_rename_over(root, "t.tmp", "current.txt", posix=False)
        out["r2b_clear"] = clear_readonly(root, "current.txt")
        out["r2b_plain_rename_over_cleared"] = nt_rename_over(root, "t.tmp", "current.txt", posix=False)
        for n in ("t.tmp", "current.txt"):
            if exists(os.path.join(d, n)):
                set_readonly(os.path.join(d, n), False)
                os.remove(os.path.join(d, n))
        # R3: a read-only file held by Python's open() (no delete sharing)
        f = os.path.join(d, "held.bin")
        write_synced(f, b"x")
        set_readonly(f, True)
        with open(f, "rb"):
            out["r3_clear_while_held_plain_open"] = clear_readonly(root, "held.bin")
            out["r3_attrs_after_clear"] = attrs_of(f)
        out["r3_delete_posix_after_close"] = nt_delete_posix(root, "held.bin")
        # R4: a read-only folder (with a read-only file in it), as the cleanup's `_remove_tree` meets it
        sub = os.path.join(d, "rodir")
        os.mkdir(sub)
        write_synced(os.path.join(sub, "in.txt"), b"x")
        set_readonly(os.path.join(sub, "in.txt"), True)
        out["r4_set_readonly_folder"] = set_readonly(sub, True)
        out["r4_folder_attrs"] = attrs_of(sub)
        out["r4_rename_readonly_folder_aside"] = bool_call(k32.MoveFileExW(sub, sub + ".aside", 0))
        if exists(sub + ".aside"):
            k32.MoveFileExW(sub + ".aside", sub, 0)
        sroot = open_dir(sub, GENERIC_READ | FILE_LIST_DIRECTORY)
        try:
            out["r4_delete_readonly_file_in_readonly_folder"] = nt_delete_posix(sroot, "in.txt")
            out["r4_clear_file"] = clear_readonly(sroot, "in.txt")
            out["r4_delete_cleared_file_in_readonly_folder"] = nt_delete_posix(sroot, "in.txt")
        finally:
            close(sroot)
        out["r4_rmdir_readonly_empty_folder"] = nt_delete_posix(root, "rodir", FILE_DIRECTORY_FILE)
        if exists(sub):
            out["r4_clear_folder"] = clear_readonly(root, "rodir", FILE_DIRECTORY_FILE)
            out["r4_rmdir_cleared_folder"] = nt_delete_posix(root, "rodir", FILE_DIRECTORY_FILE)
    finally:
        close(root)
    for n in os.listdir(d):  # leave nothing read-only behind for the probe's own cleanup
        set_readonly(os.path.join(d, n), False)
    out["names_left"] = sorted(os.listdir(d))
    return out


def probe_share_rename(d: str) -> dict[str, Any]:
    """BL-0294 (S0132): on this folder's volume, the rename with POSIX semantics and the plain rename over a file, with
    no reader and with a reader that shares deletion (the question for a network share: does it offer the POSIX
    rename, and does it answer `NotOffered`-like statuses or others?)."""
    out: dict[str, Any] = {}
    root = open_dir(d, GENERIC_READ | FILE_LIST_DIRECTORY)
    try:
        for held in (False, True):
            for posix in (True, False):
                write_synced(os.path.join(d, "dst.txt"), b"old")
                write_synced(os.path.join(d, "src.tmp"), b"new")
                h = open_path(os.path.join(d, "dst.txt"), GENERIC_READ, FILE_SHARE_ALL) if held else None
                try:
                    key = f"{'posix' if posix else 'plain'}_{'held_shared' if held else 'alone'}"
                    out[key] = nt_rename_over(root, "src.tmp", "dst.txt", posix=posix)
                finally:
                    if h is not None:
                        close(h)
                for n in ("src.tmp", "dst.txt"):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(d, n))
    finally:
        close(root)
    # the store's build opens its file by `Path.as_uri()` with `mode=rw` (mirror/build.py): does SQLite open that here?
    import pathlib
    import sqlite3

    db = os.path.join(d, "uri.db")
    write_file(db, b"")
    try:
        con = sqlite3.connect(f"{pathlib.Path(db).as_uri()}?mode=rw", uri=True)
        con.execute("CREATE TABLE t (x)")
        con.close()
        out["sqlite_as_uri_rw"] = "ok"
    except sqlite3.Error as e:
        out["sqlite_as_uri_rw"] = f"error {e} ({pathlib.Path(db).as_uri()})"
    with contextlib.suppress(OSError):
        os.remove(db)
    return out


def probe_locks(d: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    p = os.path.join(d, "lock")
    write_file(p, b"")
    h1 = open_path(p, GENERIC_READ, FILE_SHARE_ALL)
    h2 = open_path(p, GENERIC_READ, FILE_SHARE_ALL)
    try:
        ov1, ov2 = ctypes.create_string_buffer(32), ctypes.create_string_buffer(32)
        flags = LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY
        out["lockfileex_first"] = bool_call(k32.LockFileEx(h1, flags, 0, 0xFFFFFFFF, 0xFFFFFFFF, ov1))
        out["lockfileex_second_handle"] = bool_call(k32.LockFileEx(h2, flags, 0, 0xFFFFFFFF, 0xFFFFFFFF, ov2))
        fd = os.open(p, os.O_RDONLY)
        try:
            out["msvcrt_locking_against_lockfileex"] = attempt(lambda: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1) or "ok")
        finally:
            os.close(fd)
    finally:
        close(h1)
        close(h2)
    os.remove(p)
    return out


def probe(folder: str) -> dict[str, Any]:
    out: dict[str, Any] = {"folder": folder}
    try:
        h = open_dir(folder)
    except OSError as e:
        return {**out, "error": e.args[1]}
    try:
        out["volume"] = volume(folder, h)
        out["final_path"] = final_paths(h)
        out["file_id"] = file_id(h)
        out["basic"] = basic(h)
    finally:
        close(h)
    d = os.path.join(folder, f".ft-probe-{os.getpid()}")
    os.mkdir(d)
    share = folder.startswith("\\\\")  # a UNC path: only the volume and the BL-0294 questions (S0132)
    try:
        for name, fn in (
            ("readonly", probe_readonly),
            ("share_rename", probe_share_rename),
        ) + (
            ()
            if share
            else (
                ("sync", probe_sync),
                ("exclusive", probe_exclusive),
                ("replace", probe_replace),
                ("open_entries", probe_open_entries),
                ("identity", probe_identity),
                ("names", probe_names),
                ("links", probe_links),
                ("locks", probe_locks),
                ("pointer", probe_pointer),
            )
        ):
            out[name] = attempt(functools.partial(fn, d))
            for n in os.listdir(d):
                p = os.path.join(d, n)
                with contextlib.suppress(OSError):
                    os.rmdir(p) if os.path.isdir(p) else os.remove(p)
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(d)
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(str(__doc__).strip().splitlines()[-1], file=sys.stderr)  # noqa: T201 (prints only)
        return 2
    for folder in argv:
        print(json.dumps(probe(os.path.abspath(folder)), default=str))  # noqa: T201 (prints only)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
