"""Probe what Windows' cloud filter (Cloud Files, `cldflt` and `cldapi.dll`, on which OneDrive's Files On-Demand is
built) shows the engine's Windows adapter (S0144, BL-0283, BL-0087; `design/journal/S0144-cloud-files-probe.md`).
Prints only, one JSON line per step; it changes no product code and decides nothing.

It plays both sides, in two processes, as the product would meet them (the engine is never the sync provider):

- `serve <folder>` (the step in `ci.yml`): the **provider**. It prints the cloud filter's state (`fltmc filters`,
  `sc.exe`; it loads or starts `CldFlt` if the filter is missing), registers a sync root in a scratch folder
  `ft-cloud-<pid>` under `<folder>` (`CfRegisterSyncRoot`; no Microsoft account) and connects to it
  (`CfConnectSyncRoot`) with a FETCH_DATA callback that serves each file's bytes (`CfExecute`, TRANSFER_DATA; one line
  per fetch, naming the process that read). It makes the placeholders (`CfCreatePlaceholders`): per compatibility
  mode, cloud-only files, files then hydrated by a reader's plain `open()`, one also pinned (`CfSetPinState`: "always
  keep on this device"), and a placeholder folder with a cloud-only file inside. Then it runs the readers below, each
  in a child process, disconnects (`CfDisconnectSyncRoot`), runs the offline readers, and at the end, also after a
  failure, unregisters the sync root (`CfUnregisterSyncRoot`) and removes the folder.
- `hydrate <root> <name>...`: reads each file with Python's `open()`, as Beancount reads a ledger.
- `reader <root> <mode>`: in the placeholder compatibility mode `default` (the process's own, unchanged) or `expose`
  (`RtlSetProcessPlaceholderCompatibilityMode(PHCM_EXPOSE_PLACEHOLDERS)`), through the adapter (`WindowsFS().open_dir`
  on the sync root, then the port's calls): (2) each entry's attributes, reparse tag, size on disk and `_kind`, as the
  adapter sees it, as `GetFileAttributesW` sees it and as Python's `os.stat` sees it; (3) the reads of a cloud-only
  file through our own handle (opened with `FILE_OPEN_REPARSE_POINT`, as the adapter opens every entry, without the
  adapter's kind check), through the adapter's `read` and `sha256`, and through `open()`, each followed by the
  entry's state (hydrated or not); the placeholder folder by `stat` and `open_child`; (5) the save's own sequence on a
  hydrated and on a cloud-only placeholder, in the writer's order (`writer._check_target`, `_backups`,
  `_best_effort`): `stat`, `allowed` (delete, and the attribute read the staging copy needs), `sync_file`, `read`
  (the backup's bytes), `create(staging, like=the file)`, `rename` over the file (ADR-0069), the folder's `sync`, the
  read-back, and what is at the name afterwards. It goes on past a refused step, to show the next one.
- `offline <root> <mode>`: (4) the same reads of a cloud-only and of a hydrated file while the provider is
  disconnected, each error as the adapter raises it (`errno`, the Win32 code, the NTSTATUS in its message).

A Windows call's refusal is a finding: printed, never a failure. A bug of the probe itself (any other exception, a
child that fails or runs over 15 minutes) is printed as `probe_error` and ends `serve` with exit code 1 after the
cleanup, so the CI step fails visibly. It writes only inside its scratch folder.

Usage (from `engine/`, on Windows only): python scripts/probe_windows_cloud.py serve <folder>
"""

from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    raise SystemExit("a Windows probe")  # noqa: TRY003 (one raise site)

# pyright: reportPrivateUsage=false
# (a probe reads the adapter's own handles and calls, as `probe_windows_share_save.py` does)

from ledger_engine.fs import windows
from ledger_engine.fs.port import Right, check_name
from ledger_engine.fs.windows import WindowsDir, WindowsFS

MODES = ("default", "expose")
PHCM_EXPOSE_PLACEHOLDERS = 2
PHCM_NAMES = {0: "application_default", 1: "disguise_placeholder", 2: "expose_placeholders", 3: "disguise_full"}
CHILD_LIMIT_S = 900
"""A child that runs longer is stopped (a fetch that is never answered; the cloud filter's own timeout is 60 s)."""
READ_LIMIT = 1 << 20
ATTRS = (
    (0x1, "READONLY"),
    (0x2, "HIDDEN"),
    (0x4, "SYSTEM"),
    (0x10, "DIRECTORY"),
    (0x20, "ARCHIVE"),
    (0x80, "NORMAL"),
    (0x100, "TEMPORARY"),
    (0x200, "SPARSE_FILE"),
    (0x400, "REPARSE_POINT"),
    (0x800, "COMPRESSED"),
    (0x1000, "OFFLINE"),
    (0x2000, "NOT_CONTENT_INDEXED"),
    (0x4000, "ENCRYPTED"),
    (0x8000, "INTEGRITY_STREAM"),
    (0x20000, "NO_SCRUB_DATA"),
    (0x40000, "RECALL_ON_OPEN"),
    (0x80000, "PINNED"),
    (0x100000, "UNPINNED"),
    (0x400000, "RECALL_ON_DATA_ACCESS"),
)
IO_REPARSE_TAG_CLOUD_MASK = 0x0000F000
IO_REPARSE_TAG_CLOUD = 0x9000001A
TAGS = {0xA0000003: "MOUNT_POINT", 0xA000000C: "SYMLINK", 0x80000013: "DEDUP", 0x8000001B: "APPEXECLINK"}
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
STATUS_UNSUCCESSFUL = 0xC0000001
# cfapi.h
CF_HYDRATION_POLICY_FULL = 2
CF_POPULATION_POLICY_ALWAYS_FULL = 3
CF_INSYNC_POLICY_TRACK_ALL = 0x00FFFFFF
CF_HARDLINK_POLICY_NONE = 0
CF_REGISTER_FLAG_DISABLE_ON_DEMAND_POPULATION_ON_ROOT = 0x2
CF_REGISTER_FLAG_MARK_IN_SYNC_ON_ROOT = 0x4
CF_CALLBACK_TYPE_FETCH_DATA = 0
CF_CALLBACK_TYPE_NONE = 0xFFFFFFFF
CF_CONNECT_FLAG_REQUIRE_PROCESS_INFO = 0x2
CF_OPERATION_TYPE_TRANSFER_DATA = 0
CF_PLACEHOLDER_CREATE_FLAG_DISABLE_ON_DEMAND_POPULATION = 0x1
CF_PLACEHOLDER_CREATE_FLAG_MARK_IN_SYNC = 0x2
CF_PIN_STATE_PINNED = 1
TRANSFER_PARAM_SIZE = 40
"""`CF_SIZE_OF_OP_PARAM(TransferData)`: the union's offset (8) and the TransferData member (32)."""
FETCH_REQUIRED_OFFSET = 16
"""`CF_CALLBACK_PARAMETERS.FetchData.RequiredFileOffset` (ParamSize, padding, Flags, padding); its length follows."""

probe_errors: list[str] = []


def say(part: str, **fields: Any) -> None:
    sys.stdout.write(json.dumps({"part": part, "pid": os.getpid(), **fields}, default=str) + "\n")
    sys.stdout.flush()


def probe_error(where: str, e: BaseException) -> None:
    probe_errors.append(where)
    say("probe_error", where=where, error=f"{type(e).__name__}: {e}")


def os_error(e: OSError) -> dict[str, Any]:
    return {
        "type": type(e).__name__,
        "errno": e.errno,
        "errno_name": errno.errorcode.get(e.errno or 0),
        "winerror": getattr(e, "winerror", None),
        "strerror": e.strerror,
    }


def attempt(part: str, fn: Callable[[], Any], **fields: Any) -> tuple[bool, Any]:
    """`fn()` printed as `part`: its result, or the `OSError` it raised (a finding); any other exception is a probe
    error. Returns whether it answered, and its answer."""
    try:
        result = fn()
    except OSError as e:
        say(part, ok=False, error=os_error(e), **fields)
        return False, None
    except Exception as e:  # noqa: BLE001 (a probe bug is printed and counted, never hidden)
        probe_error(part, e)
        return False, None
    say(part, ok=True, result=result, **fields)
    return True, result


def attr_names(attrs: int) -> list[str]:
    names = [name for bit, name in ATTRS if attrs & bit]
    rest = attrs & ~sum(bit for bit, _ in ATTRS)
    return [*names, f"other:{rest:#x}"] if rest else names


def tag_name(tag: int) -> str:
    if tag == 0:
        return "none"
    if tag & ~IO_REPARSE_TAG_CLOUD_MASK == IO_REPARSE_TAG_CLOUD:
        return f"CLOUD_{(tag & IO_REPARSE_TAG_CLOUD_MASK) >> 12:X}"
    return TAGS.get(tag, "other")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the files ------------------------------------------------------------------------------------------------------

CLOUD, HYDRATED, PINNED = "cloud", "hydrated", "pinned"
FOLDER_FILE = "inner.bean"
STAGE = ".{}.ft-stage"


def plan(mode: str) -> dict[str, str]:
    """Each placeholder file of a mode's set, by name, and the state it is left in before the readers run."""
    return {
        f"{mode}-cloud-raw.bean": CLOUD,
        f"{mode}-cloud-adapter.bean": CLOUD,
        f"{mode}-cloud-py.bean": CLOUD,
        f"{mode}-hyd.bean": HYDRATED,
        f"{mode}-pinned.bean": PINNED,
        f"{mode}-save-hyd.bean": HYDRATED,
        f"{mode}-save-cloud.bean": CLOUD,
        f"{mode}-offline-cloud.bean": CLOUD,
        f"{mode}-offline-hyd.bean": HYDRATED,
    }


def folder_of(mode: str) -> str:
    return f"{mode}-folder"


def content(name: str) -> bytes:
    """The bytes the provider serves for a file (a small ledger, different per name)."""
    return f'option "title" "{name}"\n2026-01-01 open Assets:Cash\n'.encode()


def saved(name: str) -> bytes:
    return b"; saved by the probe\n" + content(name)


# --- cfapi structures -----------------------------------------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


class CF_SYNC_REGISTRATION(ctypes.Structure):
    _fields_ = (
        ("StructSize", ctypes.c_ulong),
        ("ProviderName", ctypes.c_wchar_p),
        ("ProviderVersion", ctypes.c_wchar_p),
        ("SyncRootIdentity", ctypes.c_void_p),
        ("SyncRootIdentityLength", ctypes.c_ulong),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", ctypes.c_ulong),
        ("ProviderId", GUID),
    )


class CF_SYNC_POLICIES(ctypes.Structure):
    _fields_ = (
        ("StructSize", ctypes.c_ulong),
        ("HydrationPrimary", ctypes.c_ushort),
        ("HydrationModifier", ctypes.c_ushort),
        ("PopulationPrimary", ctypes.c_ushort),
        ("PopulationModifier", ctypes.c_ushort),
        ("InSync", ctypes.c_ulong),
        ("HardLink", ctypes.c_ulong),
        ("PlaceholderManagement", ctypes.c_ulong),
    )


class CF_CALLBACK_INFO(ctypes.Structure):
    _fields_ = (
        ("StructSize", ctypes.c_ulong),
        ("ConnectionKey", ctypes.c_longlong),
        ("CallbackContext", ctypes.c_void_p),
        ("VolumeGuidName", ctypes.c_wchar_p),
        ("VolumeDosName", ctypes.c_wchar_p),
        ("VolumeSerialNumber", ctypes.c_ulong),
        ("SyncRootFileId", ctypes.c_longlong),
        ("SyncRootIdentity", ctypes.c_void_p),
        ("SyncRootIdentityLength", ctypes.c_ulong),
        ("FileId", ctypes.c_longlong),
        ("FileSize", ctypes.c_longlong),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", ctypes.c_ulong),
        ("NormalizedPath", ctypes.c_wchar_p),
        ("TransferKey", ctypes.c_longlong),
        ("PriorityHint", ctypes.c_ubyte),
        ("CorrelationVector", ctypes.c_void_p),
        ("ProcessInfo", ctypes.c_void_p),
        ("RequestKey", ctypes.c_longlong),
    )


CF_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(CF_CALLBACK_INFO), ctypes.c_void_p)


class CF_CALLBACK_REGISTRATION(ctypes.Structure):
    _fields_ = (("Type", ctypes.c_uint), ("Callback", CF_CALLBACK))


class CF_OPERATION_INFO(ctypes.Structure):
    _fields_ = (
        ("StructSize", ctypes.c_ulong),
        ("Type", ctypes.c_uint),
        ("ConnectionKey", ctypes.c_longlong),
        ("TransferKey", ctypes.c_longlong),
        ("CorrelationVector", ctypes.c_void_p),
        ("SyncStatus", ctypes.c_void_p),
        ("RequestKey", ctypes.c_longlong),
    )


class CF_TRANSFER_DATA_PARAMS(ctypes.Structure):
    """`CF_OPERATION_PARAMETERS` with its TransferData member (the union starts at offset 8)."""

    _fields_ = (
        ("ParamSize", ctypes.c_ulong),
        ("_pad", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("CompletionStatus", ctypes.c_long),
        ("Buffer", ctypes.c_void_p),
        ("Offset", ctypes.c_longlong),
        ("Length", ctypes.c_longlong),
    )


class FILE_BASIC_INFO_S(ctypes.Structure):
    _fields_ = (
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", ctypes.c_ulong),
    )


class CF_FS_METADATA(ctypes.Structure):
    _fields_ = (("BasicInfo", FILE_BASIC_INFO_S), ("FileSize", ctypes.c_longlong))


class CF_PLACEHOLDER_CREATE_INFO(ctypes.Structure):
    _fields_ = (
        ("RelativeFileName", ctypes.c_wchar_p),
        ("FsMetadata", CF_FS_METADATA),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("Result", ctypes.c_long),
        ("CreateUsn", ctypes.c_longlong),
    )


def hresult(hr: int) -> dict[str, Any]:
    u = hr & 0xFFFFFFFF
    out: dict[str, Any] = {"hresult": f"{u:#010x}", "ok": hr >= 0}
    if u >> 16 == 0x8007:
        out["win32"] = u & 0xFFFF
        out["message"] = ctypes.FormatError(u & 0xFFFF).strip()
    elif hr < 0:
        out["message"] = ctypes.FormatError(u).strip()
    return out


def placeholder_mode() -> dict[str, Any]:
    nt = ctypes.WinDLL("ntdll")
    query = nt.RtlQueryProcessPlaceholderCompatibilityMode
    query.argtypes = []
    query.restype = ctypes.c_byte
    value = int(query())
    return {"value": value, "name": PHCM_NAMES.get(value, "unknown")}


def set_placeholder_mode(value: int) -> int:
    nt = ctypes.WinDLL("ntdll")
    setter = nt.RtlSetProcessPlaceholderCompatibilityMode
    setter.argtypes = [ctypes.c_byte]
    setter.restype = ctypes.c_byte
    return int(setter(value))


def now_filetime() -> int:
    k32 = ctypes.WinDLL("kernel32")
    t = ctypes.c_ulonglong()
    k32.GetSystemTimeAsFileTime(ctypes.byref(t))
    return int(t.value)


# --- the provider ---------------------------------------------------------------------------------------------------


class Provider:
    """The sync root, its connection and its fetch callback (the provider's side, in `serve`'s process)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cf: Any = None
        self.registered = False
        self.key: int | None = None
        self.files: dict[str, bytes] = {}
        self.fetches = 0
        self._lock = threading.Lock()
        self._callback = CF_CALLBACK(self._on_fetch)  # kept alive while connected
        self._keep: list[Any] = []

    def load(self) -> bool:
        try:
            self.cf = ctypes.WinDLL("cldapi.dll")
        except OSError as e:
            say("item1.cldapi", ok=False, error=os_error(e))
            return False
        cf = self.cf
        cf.CfRegisterSyncRoot.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(CF_SYNC_REGISTRATION),
            ctypes.POINTER(CF_SYNC_POLICIES),
            ctypes.c_ulong,
        ]
        cf.CfRegisterSyncRoot.restype = ctypes.c_long
        cf.CfUnregisterSyncRoot.argtypes = [ctypes.c_wchar_p]
        cf.CfUnregisterSyncRoot.restype = ctypes.c_long
        cf.CfConnectSyncRoot.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(CF_CALLBACK_REGISTRATION),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_longlong),
        ]
        cf.CfConnectSyncRoot.restype = ctypes.c_long
        cf.CfDisconnectSyncRoot.argtypes = [ctypes.c_longlong]
        cf.CfDisconnectSyncRoot.restype = ctypes.c_long
        cf.CfExecute.argtypes = [ctypes.POINTER(CF_OPERATION_INFO), ctypes.POINTER(CF_TRANSFER_DATA_PARAMS)]
        cf.CfExecute.restype = ctypes.c_long
        cf.CfCreatePlaceholders.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(CF_PLACEHOLDER_CREATE_INFO),
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        cf.CfCreatePlaceholders.restype = ctypes.c_long
        cf.CfSetPinState.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
        cf.CfSetPinState.restype = ctypes.c_long
        say("item1.cldapi", ok=True)
        return True

    def register(self) -> bool:
        identity = ctypes.create_string_buffer(b"ft-probe-root", 13)
        file_identity = ctypes.create_string_buffer(b"root", 4)
        self._keep += [identity, file_identity]
        reg = CF_SYNC_REGISTRATION()
        reg.StructSize = ctypes.sizeof(CF_SYNC_REGISTRATION)
        reg.ProviderName = "FinzytrackProbe"
        reg.ProviderVersion = "1.0"
        reg.SyncRootIdentity = ctypes.cast(identity, ctypes.c_void_p)
        reg.SyncRootIdentityLength = 13
        reg.FileIdentity = ctypes.cast(file_identity, ctypes.c_void_p)
        reg.FileIdentityLength = 4
        reg.ProviderId = GUID.from_buffer_copy(uuid.uuid4().bytes_le)
        pol = CF_SYNC_POLICIES()
        pol.HydrationPrimary = CF_HYDRATION_POLICY_FULL
        pol.PopulationPrimary = CF_POPULATION_POLICY_ALWAYS_FULL
        pol.InSync = CF_INSYNC_POLICY_TRACK_ALL
        pol.HardLink = CF_HARDLINK_POLICY_NONE
        flags = CF_REGISTER_FLAG_DISABLE_ON_DEMAND_POPULATION_ON_ROOT | CF_REGISTER_FLAG_MARK_IN_SYNC_ON_ROOT
        # the policies' size with PlaceholderManagement (Windows 10 2004 on), then without it
        for size in (ctypes.sizeof(CF_SYNC_POLICIES), ctypes.sizeof(CF_SYNC_POLICIES) - 4):
            pol.StructSize = size
            hr = int(self.cf.CfRegisterSyncRoot(str(self.root), ctypes.byref(reg), ctypes.byref(pol), flags))
            say("item1.register", policies_size=size, **hresult(hr))
            if hr >= 0:
                self.registered = True
                return True
        return False

    def connect(self) -> bool:
        table = (CF_CALLBACK_REGISTRATION * 2)()
        table[0].Type = CF_CALLBACK_TYPE_FETCH_DATA
        table[0].Callback = self._callback
        table[1].Type = CF_CALLBACK_TYPE_NONE
        self._keep.append(table)
        key = ctypes.c_longlong()
        hr = int(
            self.cf.CfConnectSyncRoot(
                str(self.root), table, None, CF_CONNECT_FLAG_REQUIRE_PROCESS_INFO, ctypes.byref(key)
            )
        )
        say("item1.connect", **hresult(hr))
        if hr < 0:
            return False
        self.key = int(key.value)
        return True

    def disconnect(self) -> None:
        if self.key is None:
            return
        hr = int(self.cf.CfDisconnectSyncRoot(self.key))
        say("disconnect", **hresult(hr))
        if hr >= 0:
            self.key = None

    def unregister(self) -> None:
        if not self.registered:
            return
        hr = int(self.cf.CfUnregisterSyncRoot(str(self.root)))
        say("unregister", **hresult(hr))
        self.registered = hr < 0

    def _on_fetch(self, info_p: Any, params: int | None) -> None:
        """FETCH_DATA: the whole file served in one TRANSFER_DATA (a small file: its one chunk ends at its end, which
        the 4 KiB alignment rule allows), or a failure status when the file is unknown, so no reader waits."""
        try:
            info = info_p.contents
            name = ""
            if info.FileIdentity and 0 < info.FileIdentityLength <= 4096:
                name = ctypes.string_at(info.FileIdentity, info.FileIdentityLength).decode("utf-8", "replace")
            pid = ctypes.c_ulong.from_address(info.ProcessInfo + 4).value if info.ProcessInfo else None
            offset = ctypes.c_longlong.from_address((params or 0) + FETCH_REQUIRED_OFFSET).value if params else None
            length = ctypes.c_longlong.from_address((params or 0) + FETCH_REQUIRED_OFFSET + 8).value if params else None
            data = self.files.get(name)
            request = info.RequestKey if info.StructSize >= ctypes.sizeof(CF_CALLBACK_INFO) else 0
            hr = self._transfer(info.ConnectionKey, info.TransferKey, request, data)
            with self._lock:
                self.fetches += 1
            say(
                "fetch",
                who="provider",
                identity=name,
                reader_pid=pid,
                path=info.NormalizedPath,
                required=[offset, length],
                file_size=info.FileSize,
                info_size=info.StructSize,
                served=None if data is None else len(data),
                **hresult(hr),
            )
        except Exception as e:  # noqa: BLE001 (a callback must not raise: printed and counted)
            probe_error("fetch", e)

    def _transfer(self, connection: int, transfer: int, request: int, data: bytes | None) -> int:
        op = CF_OPERATION_INFO()
        op.StructSize = ctypes.sizeof(CF_OPERATION_INFO)
        op.Type = CF_OPERATION_TYPE_TRANSFER_DATA
        op.ConnectionKey = connection
        op.TransferKey = transfer
        op.RequestKey = request
        p = CF_TRANSFER_DATA_PARAMS()
        p.ParamSize = TRANSFER_PARAM_SIZE
        buf = ctypes.create_string_buffer(data or b"\0", max(len(data or b""), 1))
        if data is None:
            p.CompletionStatus = ctypes.c_long(STATUS_UNSUCCESSFUL).value
            p.Offset, p.Length = 0, 0
        else:
            p.Buffer = ctypes.cast(buf, ctypes.c_void_p)
            p.Offset, p.Length = 0, len(data)
        return int(self.cf.CfExecute(ctypes.byref(op), ctypes.byref(p)))

    def placeholder(self, base: Path, name: str, data: bytes | None) -> bool:
        """One placeholder (`data` None: a folder), cloud-only and marked in sync."""
        ident = ctypes.create_string_buffer(name.encode(), len(name.encode()))
        self._keep.append(ident)
        info = CF_PLACEHOLDER_CREATE_INFO()
        info.RelativeFileName = name
        t = now_filetime()
        basic = info.FsMetadata.BasicInfo
        basic.CreationTime = basic.LastAccessTime = basic.LastWriteTime = basic.ChangeTime = t
        if data is None:
            basic.FileAttributes = FILE_ATTRIBUTE_DIRECTORY
            info.Flags = (
                CF_PLACEHOLDER_CREATE_FLAG_MARK_IN_SYNC | CF_PLACEHOLDER_CREATE_FLAG_DISABLE_ON_DEMAND_POPULATION
            )
        else:
            basic.FileAttributes = FILE_ATTRIBUTE_NORMAL
            info.FsMetadata.FileSize = len(data)
            info.Flags = CF_PLACEHOLDER_CREATE_FLAG_MARK_IN_SYNC
            self.files[name] = data
        info.FileIdentity = ctypes.cast(ident, ctypes.c_void_p)
        info.FileIdentityLength = len(name.encode())
        done = ctypes.c_ulong()
        hr = int(self.cf.CfCreatePlaceholders(str(base), ctypes.byref(info), 1, 0, ctypes.byref(done)))
        say(
            "placeholder",
            name=name,
            folder=data is None,
            sha256=None if data is None else sha(data),
            entry_result=hresult(int(info.Result)),
            processed=int(done.value),
            **hresult(hr),
        )
        return hr >= 0 and int(info.Result) >= 0

    def pin(self, path: Path) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        access = windows.FILE_READ_ATTRIBUTES | windows.FILE_WRITE_ATTRIBUTES
        h = k32.CreateFileW(str(path), access, windows.FILE_SHARE_ALL, None, windows.OPEN_EXISTING, 0, None)
        if h is None or h == ctypes.c_void_p(-1).value:
            say("pin", name=path.name, ok=False, open_error=ctypes.get_last_error())
            return
        try:
            hr = int(self.cf.CfSetPinState(h, CF_PIN_STATE_PINNED, 0, None))
            say("pin", name=path.name, **hresult(hr))
        finally:
            k32.CloseHandle(h)


def command(*cmd: str) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as e:
        say("command", cmd=list(cmd), error=str(e))
        return ""
    out = (done.stdout + done.stderr).strip()
    say("command", cmd=list(cmd), rc=done.returncode, output=out.splitlines())
    return out


def filter_state() -> None:
    """Item 1's first part: is the cloud filter loaded, and can it be (the runner's user is an administrator)?"""
    loaded = "cldflt" in command("fltmc", "filters").lower()
    command("sc.exe", "qc", "CldFlt")
    command("sc.exe", "query", "CldFlt")
    if not loaded:
        command("fltmc", "load", "CldFlt")
        command("sc.exe", "start", "CldFlt")
        loaded = "cldflt" in command("fltmc", "filters").lower()
    say("item1.filter", loaded=loaded)


def child(*args: str) -> None:
    cmd = [sys.executable, os.path.abspath(__file__), *args]
    say("child", args=list(args))
    try:
        rc = subprocess.run(cmd, timeout=CHILD_LIMIT_S, check=False).returncode  # noqa: S603
    except subprocess.TimeoutExpired as e:
        probe_error("child " + " ".join(args), e)
        return
    say("child_done", args=list(args), rc=rc)
    if rc != 0:
        probe_errors.append("child " + " ".join(args))


def steps(provider: Provider, root: Path) -> None:
    """Items 1 to 6, in order; the caller cleans up whatever happens here."""
    if not provider.load() or not provider.register():
        say("item1", answer="no sync root on this runner")
        return
    if not provider.connect():
        say("item1", answer="a sync root registers but does not connect")
        return
    say("item1", answer="a sync root registers and connects")
    for mode in MODES:
        for name in plan(mode):
            provider.placeholder(root, name, content(name))
        folder = folder_of(mode)
        if provider.placeholder(root, folder, None):
            provider.placeholder(root / folder, FOLDER_FILE, content(FOLDER_FILE))
    to_hydrate = [n for m in MODES for n, state in plan(m).items() if state in (HYDRATED, PINNED)]
    child("hydrate", str(root), *to_hydrate)
    for mode in MODES:
        for name, state in plan(mode).items():
            if state == PINNED:
                provider.pin(root / name)
    for mode in MODES:
        child("reader", str(root), mode)
    provider.disconnect()
    for mode in MODES:
        child("offline", str(root), mode)
    say("fetches", count=provider.fetches)


def serve(base: str) -> int:
    say("system", version=platform.version(), release=platform.release(), python=sys.version.split()[0])
    say("provider_mode", **placeholder_mode())
    filter_state()
    root = Path(base) / f"ft-cloud-{os.getpid()}"
    root.mkdir()
    provider = Provider(root)
    try:
        steps(provider, root)
    except Exception as e:  # noqa: BLE001 (printed and counted; the cleanup still runs)
        probe_error("serve", e)
    finally:
        try:
            provider.disconnect()
            provider.unregister()
        except Exception as e:  # noqa: BLE001 (printed and counted)
            probe_error("cleanup", e)
        try:
            shutil.rmtree(root)
            say("removed", ok=True)
        except OSError as e:
            say("removed", ok=False, error=os_error(e))
    say("done", probe_errors=probe_errors)
    return 1 if probe_errors else 0


# --- the reader (a child process) -----------------------------------------------------------------------------------


def k32_attributes(path: Path) -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    k32.GetFileAttributesW.restype = wintypes.DWORD
    a = int(k32.GetFileAttributesW(str(path)))
    if a == INVALID_FILE_ATTRIBUTES:
        raise ctypes.WinError(ctypes.get_last_error())
    return a


def describe(d: WindowsDir, folder: Path, name: str, when: str) -> None:
    """Item 2: the entry as the adapter's handle sees it (attributes, tag, `_kind`, size on disk), as `stat` gives it,
    as `GetFileAttributesW` and Python's `os.stat` see it."""
    out: dict[str, Any] = {"name": name, "when": when}
    try:
        with d._open(check_name(name), windows.FILE_READ_ATTRIBUTES) as h:
            attrs, tag = h.attrs_tag()
            alloc, eof = (int.from_bytes(h.info(windows.FILE_STANDARD_INFO, 24)[i : i + 8], "little") for i in (0, 8))
        out["handle"] = {
            "attrs": attr_names(attrs),
            "attrs_hex": f"{attrs:#x}",
            "tag": f"{tag:#010x}",
            "tag_name": tag_name(tag),
            "kind": windows._kind(attrs, tag).value,
            "allocated": alloc,
            "size": eof,
        }
    except OSError as e:
        out["handle"] = {"error": os_error(e)}
    try:
        st = d.stat(name)
        out["stat"] = None if st is None else {"kind": st.kind.value, "nlink": st.nlink, "locked": st.locked}
    except OSError as e:
        out["stat"] = {"error": os_error(e)}
    try:
        out["win32_attrs"] = attr_names(k32_attributes(folder / name))
    except OSError as e:
        out["win32_attrs"] = {"error": os_error(e)}
    for label, fn in (("os_lstat", os.lstat), ("os_stat", os.stat)):
        try:
            s = fn(folder / name)
            out[label] = {
                "attrs": attr_names(s.st_file_attributes),
                "tag": tag_name(s.st_reparse_tag),
                "size": s.st_size,
            }
        except OSError as e:
            out[label] = {"error": os_error(e)}
    say("entry", **out)


def raw_read(d: WindowsDir, name: str) -> dict[str, Any]:
    """Our own handle (`FILE_OPEN_REPARSE_POINT`, as the adapter opens every entry), without the kind check."""
    with d._open(check_name(name), windows.GENERIC_READ, options=windows.FILE_NON_DIRECTORY_FILE) as h:
        data = h.read_all(READ_LIMIT)
    return {"len": len(data), "sha256": sha(data), "is_content": data == content(name)}


def adapter_read(d: WindowsDir, name: str) -> dict[str, Any] | None:
    data = d.read(name, READ_LIMIT)
    return None if data is None else {"len": len(data), "sha256": sha(data), "is_content": data == content(name)}


def adapter_sha(d: WindowsDir, name: str) -> dict[str, Any] | None:
    got = d.sha256(name, READ_LIMIT)
    return None if got is None else {"len": got[1], "is_content": got[0] == sha(content(name))}


def py_read(path: Path, name: str) -> dict[str, Any]:
    with path.open("rb") as f:
        data = f.read()
    return {"len": len(data), "sha256": sha(data), "is_content": data == content(name)}


def reads(d: WindowsDir, folder: Path, name: str, order: tuple[str, ...], item: str) -> None:
    """Each read in `order`, each followed by the entry's state."""
    ways: dict[str, Callable[[], Any]] = {
        "raw": lambda: raw_read(d, name),
        "adapter_read": lambda: adapter_read(d, name),
        "adapter_sha256": lambda: adapter_sha(d, name),
        "python_open": lambda: py_read(folder / name, name),
    }
    for way in order:
        attempt(f"{item}.read", ways[way], name=name, way=way)
        describe(d, folder, name, f"after {way}")


def enter_mode(mode: str) -> None:
    """Item 6: this process's placeholder compatibility mode, set once before any look (it is per process)."""
    before = placeholder_mode()
    if mode == "expose":
        previous = set_placeholder_mode(PHCM_EXPOSE_PLACEHOLDERS)
        say("mode_set", mode=mode, previous=previous, before=before, now=placeholder_mode())
    else:
        say("mode_set", mode=mode, before=before, now=before)


def kind_of(d: WindowsDir, name: str) -> str | None:
    st = d.stat(name)
    return None if st is None else st.kind.value


def save_sequence(d: WindowsDir, folder: Path, name: str) -> None:
    """Item 5: the save's own effects on one replaced file, in the writer's order, through the adapter."""
    describe(d, folder, name, "before the save")
    attempt("item5.stat", lambda: kind_of(d, name), name=name)
    attempt("item5.allowed_delete", lambda: d.allowed(name, Right.DELETE), name=name)
    attempt("item5.allowed_read_xattrs", lambda: d.allowed(name, Right.READ_XATTRS), name=name)
    attempt("item5.sync_file", lambda: list(d.sync_file(name)), name=name)
    attempt("item5.backup_read", lambda: adapter_read(d, name), name=name)
    stage = STAGE.format(name)
    made, _ = attempt("item5.create_like", lambda: list(d.create(stage, saved(name), 0o666, like=name)), name=name)
    if not made:
        made, _ = attempt("item5.create_plain", lambda: list(d.create(stage, saved(name), 0o666)), name=name)
    if not made:
        return
    describe(d, folder, stage, "the staging file")
    renamed, _ = attempt("item5.rename", lambda: d.rename(stage, name), name=name)
    attempt("item5.sync", d.sync, name=name)
    describe(d, folder, name, "after the save")
    attempt("item5.read_back", lambda: d.read(name) == saved(name), name=name)
    attempt("item5.python_open_after", lambda: (folder / name).read_bytes() == saved(name), name=name)
    attempt("item5.names_left", lambda: [n for n in d.names() if name in n], name=name)
    if not renamed:
        attempt("item5.remove_stage", lambda: d.remove(stage), name=name)


def sync_root_seen(root: Path) -> None:
    """Item 2 for the sync root itself, from its parent folder: a ledger below a OneDrive folder is reached by
    `open_child` through it."""
    with WindowsFS().open_dir(str(root.parent)) as parent:
        describe(parent, root.parent, root.name, "the sync root, from its parent")
        opened, d = attempt("item2.sync_root_open_child", lambda: parent.open_child(root.name), name=root.name)
        if opened:
            d.close()


def reader(root: str, mode: str) -> int:
    folder = Path(root)
    files = plan(mode)
    enter_mode(mode)
    sync_root_seen(folder)
    with WindowsFS().open_dir(root) as d:
        attempt("reader.root", lambda: {"path": d.path(), "fs_type": d.fs_type(), "remote": d.remote()}, mode=mode)
        for name in [*files, folder_of(mode)]:
            describe(d, folder, name, "before any read")  # item 2
        # item 3: a cloud-only file read three ways, each way first once
        all_ways = ("raw", "adapter_read", "adapter_sha256", "python_open")
        reads(d, folder, f"{mode}-cloud-raw.bean", ("raw", "adapter_read", "python_open"), "item3")
        reads(d, folder, f"{mode}-cloud-adapter.bean", ("adapter_read", "adapter_sha256", "raw"), "item3")
        reads(d, folder, f"{mode}-cloud-py.bean", ("python_open", "adapter_read", "raw"), "item3")
        reads(d, folder, f"{mode}-hyd.bean", all_ways, "item3")
        reads(d, folder, f"{mode}-pinned.bean", all_ways, "item3")
        # item 2: the placeholder folder
        sub = folder_of(mode)
        attempt("item2.folder_stat", lambda: kind_of(d, sub), name=sub)
        opened, child_dir = attempt("item2.open_child", lambda: d.open_child(sub), name=sub)
        if opened:
            with child_dir:
                attempt("item2.folder_names", child_dir.names, name=sub)
                describe(child_dir, folder / sub, FOLDER_FILE, "inside the placeholder folder")
                attempt("item2.inner_read", lambda: adapter_read(child_dir, FOLDER_FILE), name=sub)
                attempt("item2.inner_raw", lambda: raw_read(child_dir, FOLDER_FILE), name=sub)
                describe(child_dir, folder / sub, FOLDER_FILE, "after the inner reads")
        # item 5
        save_sequence(d, folder, f"{mode}-save-hyd.bean")
        save_sequence(d, folder, f"{mode}-save-cloud.bean")
    return 1 if probe_errors else 0


def offline(root: str, mode: str) -> int:
    folder = Path(root)
    all_ways = ("raw", "adapter_read", "adapter_sha256", "python_open")
    enter_mode(mode)
    with WindowsFS().open_dir(root) as d:
        for name in (f"{mode}-offline-cloud.bean", f"{mode}-offline-hyd.bean"):
            describe(d, folder, name, "offline, before any read")
            reads(d, folder, name, all_ways, "item4")
    return 1 if probe_errors else 0


def hydrate(root: str, names: list[str]) -> int:
    for name in names:
        attempt("hydrate", functools.partial(py_read, Path(root) / name, name), name=name)
    return 1 if probe_errors else 0


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "serve":
            return serve(argv[1])
        if len(argv) >= 2 and argv[0] == "hydrate":
            return hydrate(argv[1], argv[2:])
        if len(argv) == 3 and argv[0] == "reader" and argv[2] in MODES:
            return reader(argv[1], argv[2])
        if len(argv) == 3 and argv[0] == "offline" and argv[2] in MODES:
            return offline(argv[1], argv[2])
    except Exception as e:  # noqa: BLE001 (a probe bug: printed, and the exit code says so)
        probe_error(argv[0], e)
        return 1
    print(str(__doc__).strip().splitlines()[-1], file=sys.stderr)  # noqa: T201 (prints only)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
