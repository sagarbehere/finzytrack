"""A Cloud Files sync provider for the runner's tests (S0149, BL-0283): Windows' own cloud filter (`cldflt`,
`cldapi.dll`), on which OneDrive's Files On-Demand is built, with a small provider in place of OneDrive's sync engine.
The structures and calls are S0144's probe's (`scripts/probe_windows_cloud.py`; its journal's Facts).

The provider runs in **its own process** (`python -m tests.fs.cloud_provider <sync root>`, from `engine/`), as OneDrive
does: the engine is never the provider, and the cloud filter treats a provider's own reads otherwise. A test drives it
with one JSON line per command on its stdin and reads one JSON line per answer on its stdout (`Cloud`, below):

- `start` (`files`: relative path to base64 bytes, `folders`: relative paths, made first, in order): registers the sync
  root (`CfRegisterSyncRoot`; no Microsoft account), connects (`CfConnectSyncRoot`, a FETCH_DATA callback serving each
  file's bytes with `CfExecute` TRANSFER_DATA) and makes each folder and file as a cloud-only placeholder, in sync;
- `pin` (`rel`): "always keep on this device" (`CfSetPinState`);
- `dehydrate` (`rel`): back to cloud-only, as OneDrive's "free up space" does (`CfDehydratePlaceholder`); a file that
  is no longer a placeholder (our save renamed a new file over it) is first made one, as OneDrive does after its
  upload, with its current bytes (`CfConvertToPlaceholder` with the dehydrate flag);
- `disconnect`, `connect`: the provider stops and starts serving (offline: `ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING`);
- `fetches`: each fetch so far (its file and the reader's process ID);
- `mode`, `child_mode`: the placeholder compatibility mode of this process and of a child it starts;
- `quit`: disconnects, unregisters the sync root and ends.

**What it cannot show** (S0144's list, for the owner's check by hand in S0150): OneDrive's own sync engine (its upload
after our rename, a hold during it, its conflict copies, its own errors offline or paused).
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import json
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

if sys.platform != "win32":
    raise ImportError("the Cloud Files provider is Windows only")  # noqa: TRY003 (one raise site)

from ctypes import wintypes

ENGINE = Path(__file__).resolve().parents[2]
ANSWER_LIMIT_S = 120
"""A command not answered within this is a failure of the provider (the cloud filter's own fetch timeout is 60 s)."""

PHCM_DISGUISE = 1
PHCM_EXPOSE = 2
MODE_QUERY = (
    "import ctypes; q = ctypes.WinDLL('ntdll').RtlQueryProcessPlaceholderCompatibilityMode; q.restype = ctypes.c_byte; "
    "print(int(q()))"
)
"""A child's own report of its mode (a Python one-liner, run with the engine's flags)."""
ENGINE_FLAGS = ("-s", "-S", "-B", "-P")

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80
STATUS_UNSUCCESSFUL = 0xC0000001
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
CF_CONVERT_FLAG_MARK_IN_SYNC = 0x1
CF_CONVERT_FLAG_DEHYDRATE = 0x2
TRANSFER_PARAM_SIZE = 40
FETCH_REQUIRED_OFFSET = 16
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
WRITE_DAC = 0x40000
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


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
    return out


def own_mode() -> int:
    query = ctypes.WinDLL("ntdll").RtlQueryProcessPlaceholderCompatibilityMode
    query.argtypes = []
    query.restype = ctypes.c_byte
    return int(query())


def set_mode(value: int) -> int:
    """This process's placeholder compatibility mode set to `value` (`RtlSetProcessPlaceholderCompatibilityMode`);
    returns the previous one."""
    setter = ctypes.WinDLL("ntdll").RtlSetProcessPlaceholderCompatibilityMode
    setter.argtypes = [ctypes.c_byte]
    setter.restype = ctypes.c_byte
    return int(setter(value))


def child_mode(*flags: str) -> int | str:
    """The mode a child of this process reports (`sys.executable` with `flags`), or its error text."""
    done = subprocess.run(  # noqa: S603 (this interpreter, a fixed one-liner)
        [sys.executable, *flags, "-c", MODE_QUERY], capture_output=True, text=True, timeout=60, check=False
    )
    out = done.stdout.strip()
    return int(out) if done.returncode == 0 and out.isdigit() else f"rc {done.returncode}: {out} {done.stderr.strip()}"


def _kernel32() -> Any:
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
    return k32


@contextlib.contextmanager
def _opened(path: Path, access: int) -> Generator[int]:
    k32 = _kernel32()
    h = k32.CreateFileW(str(path), access, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield int(h)
    finally:
        k32.CloseHandle(h)


class Provider:
    """The sync root, its connection and its fetch callback (this process is the provider)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cf: Any = ctypes.WinDLL("cldapi.dll")
        self.registered = False
        self.key: int | None = None
        self.files: dict[str, bytes] = {}
        self.fetched: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._callback = CF_CALLBACK(self._on_fetch)  # kept alive while connected
        self._keep: list[Any] = []
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
        cf.CfDehydratePlaceholder.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.c_longlong,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        cf.CfDehydratePlaceholder.restype = ctypes.c_long
        cf.CfConvertToPlaceholder.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cf.CfConvertToPlaceholder.restype = ctypes.c_long

    def register(self) -> dict[str, Any]:
        identity = ctypes.create_string_buffer(b"ft-test-root", 12)
        file_identity = ctypes.create_string_buffer(b"root", 4)
        self._keep += [identity, file_identity]
        reg = CF_SYNC_REGISTRATION()
        reg.StructSize = ctypes.sizeof(CF_SYNC_REGISTRATION)
        reg.ProviderName = "FinzytrackTest"
        reg.ProviderVersion = "1.0"
        reg.SyncRootIdentity = ctypes.cast(identity, ctypes.c_void_p)
        reg.SyncRootIdentityLength = 12
        reg.FileIdentity = ctypes.cast(file_identity, ctypes.c_void_p)
        reg.FileIdentityLength = 4
        reg.ProviderId = GUID.from_buffer_copy(uuid.uuid4().bytes_le)
        pol = CF_SYNC_POLICIES()
        pol.HydrationPrimary = CF_HYDRATION_POLICY_FULL
        pol.PopulationPrimary = CF_POPULATION_POLICY_ALWAYS_FULL
        pol.InSync = CF_INSYNC_POLICY_TRACK_ALL
        pol.HardLink = CF_HARDLINK_POLICY_NONE
        pol.StructSize = ctypes.sizeof(CF_SYNC_POLICIES)
        flags = CF_REGISTER_FLAG_DISABLE_ON_DEMAND_POPULATION_ON_ROOT | CF_REGISTER_FLAG_MARK_IN_SYNC_ON_ROOT
        out = hresult(int(self.cf.CfRegisterSyncRoot(str(self.root), ctypes.byref(reg), ctypes.byref(pol), flags)))
        self.registered = out["ok"]
        return out

    def connect(self) -> dict[str, Any]:
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
        if hr >= 0:
            self.key = int(key.value)
        return hresult(hr)

    def disconnect(self) -> dict[str, Any]:
        if self.key is None:
            return {"ok": True, "already": True}
        out = hresult(int(self.cf.CfDisconnectSyncRoot(self.key)))
        if out["ok"]:
            self.key = None
        return out

    def unregister(self) -> dict[str, Any]:
        if not self.registered:
            return {"ok": True, "already": True}
        out = hresult(int(self.cf.CfUnregisterSyncRoot(str(self.root))))
        self.registered = not out["ok"]
        return out

    def fetches(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.fetched)

    def _on_fetch(self, info_p: Any, params: int | None) -> None:
        """FETCH_DATA: the whole file in one TRANSFER_DATA, or a failure status for an unknown file, so no reader
        waits. A callback must not raise: an error is recorded as the fetch's."""
        record: dict[str, Any] = {}
        try:
            info = info_p.contents
            name = ""
            if info.FileIdentity and 0 < info.FileIdentityLength <= 4096:
                name = ctypes.string_at(info.FileIdentity, info.FileIdentityLength).decode("utf-8", "replace")
            pid = ctypes.c_ulong.from_address(info.ProcessInfo + 4).value if info.ProcessInfo else None
            record = {"rel": name, "reader_pid": pid}
            data = self.files.get(name)
            request = info.RequestKey if info.StructSize >= ctypes.sizeof(CF_CALLBACK_INFO) else 0
            record.update(served=None if data is None else len(data), **self._transfer(info, request, data))
        except Exception as e:  # noqa: BLE001 (recorded, never raised into the cloud filter)
            record["error"] = f"{type(e).__name__}: {e}"
        with self._lock:
            self.fetched.append(record)

    def _transfer(self, info: Any, request: int, data: bytes | None) -> dict[str, Any]:
        op = CF_OPERATION_INFO()
        op.StructSize = ctypes.sizeof(CF_OPERATION_INFO)
        op.Type = CF_OPERATION_TYPE_TRANSFER_DATA
        op.ConnectionKey = info.ConnectionKey
        op.TransferKey = info.TransferKey
        op.RequestKey = request
        p = CF_TRANSFER_DATA_PARAMS()
        p.ParamSize = TRANSFER_PARAM_SIZE
        buf = ctypes.create_string_buffer(data or b"\0", max(len(data or b""), 1))
        if data is None:
            p.CompletionStatus = ctypes.c_long(STATUS_UNSUCCESSFUL).value
        else:
            p.Buffer = ctypes.cast(buf, ctypes.c_void_p)
            p.Offset, p.Length = 0, len(data)
        return hresult(int(self.cf.CfExecute(ctypes.byref(op), ctypes.byref(p))))

    def placeholder(self, rel: str, data: bytes | None) -> dict[str, Any]:
        """One cloud-only placeholder at `rel` (`data` None: a folder), in sync; its identity is `rel`."""
        parent, _, name = rel.rpartition("/")
        ident = ctypes.create_string_buffer(rel.encode(), len(rel.encode()))
        self._keep.append(ident)
        info = CF_PLACEHOLDER_CREATE_INFO()
        info.RelativeFileName = name
        t = _now()
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
            self.files[rel] = data
        info.FileIdentity = ctypes.cast(ident, ctypes.c_void_p)
        info.FileIdentityLength = len(rel.encode())
        base = self.root.joinpath(*parent.split("/")) if parent else self.root
        done = ctypes.c_ulong()
        hr = int(self.cf.CfCreatePlaceholders(str(base), ctypes.byref(info), 1, 0, ctypes.byref(done)))
        out = hresult(hr)
        out["entry"] = hresult(int(info.Result))
        out["ok"] = out["ok"] and out["entry"]["ok"]
        return out

    def path(self, rel: str) -> Path:
        return self.root.joinpath(*rel.split("/"))

    def pin(self, rel: str) -> dict[str, Any]:
        with _opened(self.path(rel), FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES) as h:
            return hresult(int(self.cf.CfSetPinState(h, CF_PIN_STATE_PINNED, 0, None)))

    def dehydrate(self, rel: str) -> dict[str, Any]:
        """Back to cloud-only: a placeholder dehydrated; a plain file (our save's) made a dehydrated placeholder with
        its current bytes, which this provider then serves. Each try's answer is in the result."""
        tries: list[dict[str, Any]] = []
        for access in (FILE_READ_ATTRIBUTES | WRITE_DAC, GENERIC_READ | GENERIC_WRITE):
            try:
                with _opened(self.path(rel), access) as h:
                    out = hresult(int(self.cf.CfDehydratePlaceholder(h, 0, -1, 0, None)))
            except OSError as e:
                out = {"ok": False, "open_error": getattr(e, "winerror", None)}
            tries.append({"call": "CfDehydratePlaceholder", "access": f"{access:#x}", **out})
            if out["ok"]:
                return {"ok": True, "tries": tries}
        data = self.path(rel).read_bytes()
        ident = ctypes.create_string_buffer(rel.encode(), len(rel.encode()))
        self._keep.append(ident)
        flags = CF_CONVERT_FLAG_MARK_IN_SYNC | CF_CONVERT_FLAG_DEHYDRATE
        for access in (FILE_READ_ATTRIBUTES | WRITE_DAC, GENERIC_READ | GENERIC_WRITE):
            self.files[rel] = data
            try:
                with _opened(self.path(rel), access) as h:
                    out = hresult(int(self.cf.CfConvertToPlaceholder(h, ident, len(rel.encode()), flags, None, None)))
            except OSError as e:
                out = {"ok": False, "open_error": getattr(e, "winerror", None)}
            tries.append({"call": "CfConvertToPlaceholder", "access": f"{access:#x}", **out})
            if out["ok"]:
                return {"ok": True, "tries": tries}
        return {"ok": False, "tries": tries}


def _now() -> int:
    t = ctypes.c_ulonglong()
    ctypes.WinDLL("kernel32").GetSystemTimeAsFileTime(ctypes.byref(t))
    return int(t.value)


def serve(root: Path, lines: IO[str], out: IO[str]) -> int:
    """The command loop: one JSON line in, one JSON line out, until `quit` or the end of input (then the same
    cleanup)."""
    provider = Provider(root)

    def answer(**fields: Any) -> None:
        out.write(json.dumps(fields, default=str) + "\n")
        out.flush()

    try:
        for line in lines:
            cmd = json.loads(line)
            op = cmd["op"]
            try:
                if op == "start":
                    steps: dict[str, Any] = {"register": provider.register()}
                    if steps["register"]["ok"]:
                        steps["connect"] = provider.connect()
                    made = [provider.placeholder(rel, None) for rel in cmd.get("folders", [])]
                    made += [provider.placeholder(rel, base64.b64decode(b)) for rel, b in cmd["files"].items()]
                    ok = (
                        steps["register"]["ok"]
                        and steps.get("connect", {}).get("ok", False)
                        and all(m["ok"] for m in made)
                    )
                    answer(ok=ok, steps=steps, placeholders=made, pid=os.getpid())
                elif op == "pin":
                    answer(**provider.pin(cmd["rel"]))
                elif op == "dehydrate":
                    answer(**provider.dehydrate(cmd["rel"]))
                elif op == "disconnect":
                    answer(**provider.disconnect())
                elif op == "connect":
                    answer(**provider.connect())
                elif op == "fetches":
                    answer(ok=True, fetches=provider.fetches())
                elif op == "mode":
                    answer(ok=True, mode=own_mode())
                elif op == "child_mode":
                    answer(ok=True, plain=child_mode(), engine_flags=child_mode(*ENGINE_FLAGS))
                elif op == "quit":
                    break
                else:
                    answer(ok=False, error=f"unknown op {op}")
            except OSError as e:
                answer(ok=False, error=f"{type(e).__name__}: {e}", winerror=getattr(e, "winerror", None))
    finally:
        done = {"disconnect": provider.disconnect(), "unregister": provider.unregister()}
        answer(ok=done["unregister"]["ok"], quit=done)
    return 0


@dataclass
class Cloud:
    """A test's side of a provider process (`start` it, then call its commands)."""

    root: Path
    proc: subprocess.Popen[str] | None = None
    pid: int | None = None
    log: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def start(self, files: dict[str, bytes], folders: tuple[str, ...] = ()) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(  # noqa: S603 (this interpreter, this module)
            [sys.executable, "-m", "tests.fs.cloud_provider", str(self.root)],
            cwd=ENGINE,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        out = self.call(
            "start", files={r: base64.b64encode(b).decode("ascii") for r, b in files.items()}, folders=list(folders)
        )
        self.pid = out.get("pid")
        return out

    def call(self, op: str, **fields: Any) -> dict[str, Any]:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("the provider is not running")  # noqa: TRY003 (one raise site)
        proc.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        proc.stdin.flush()
        got: list[str] = []
        reader = threading.Thread(target=lambda: got.append(proc.stdout.readline() if proc.stdout else ""))
        reader.start()
        reader.join(ANSWER_LIMIT_S)
        if not got or not got[0]:
            raise RuntimeError(f"the provider did not answer {op}")  # noqa: TRY003 (one raise site)
        answer: dict[str, Any] = json.loads(got[0])
        self.log.append({"op": op, **answer})
        return answer

    def fetches(self) -> list[dict[str, Any]]:
        return list(self.call("fetches")["fetches"])

    def stop(self) -> dict[str, Any] | None:
        proc = self.proc
        if proc is None:
            return None
        self.proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                proc.stdin.close()
            out, _ = proc.communicate(timeout=ANSWER_LIMIT_S)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
            return {"ok": False, "killed": True}
        last = [line for line in out.splitlines() if line.strip()]
        return json.loads(last[-1]) if last else None


if __name__ == "__main__":
    sys.exit(serve(Path(sys.argv[1]), sys.stdin, sys.stdout))
