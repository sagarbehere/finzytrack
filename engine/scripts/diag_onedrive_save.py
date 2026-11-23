"""Diagnose why a save into a OneDrive folder is refused `permission` (the owner's hand check, 2026-11-23; BL-0283).

Run with the engine's environment from the hand check's download (Windows only):

    cd C:\\ft-check\\finzytrack-next\\engine
    .venv\\Scripts\\python.exe -I scripts\\diag_onedrive_save.py

It makes two new folders of its own, writes only in them, and keeps them (delete them when done):
- `Finzytrack diag <time>` in OneDrive (`%OneDrive%`), with `ledger\\main.bean` and `ledger\\sub\\x.bean`;
- `ft-diag-<time>` in the temp folder: the same files, as the control (a plain local folder).

For each folder, and in each placeholder mode (0 application default, 1 disguise, 2 expose; ADR-0070), it records:
- the files' and folders' attributes and reparse tags;
- the Windows adapter's own answers as the save asks them (`allowed`, `acl`, `mark`, `open_dir`'s fallback);
- the same accesses asked raw (`CreateFileW`, with and without `FILE_FLAG_OPEN_REPARSE_POINT`), with Windows' error;
- each access bit alone, to name the one refused;
- the folder's and the file's security descriptor (SDDL; SIDs masked);
- the save's own effects through the adapter (create a temp file, rename it over main.bean, remove a temp file),
  and Python's own `os.replace` as the control of what any program may do.

Every line goes to `diag.jsonl` in the OneDrive folder and to the screen. Paths are shown relative to the test
folders, or as `<onedrive>`, `<temp>`, `<home>`; account SIDs as `<sid>`. It never reads or changes any other file.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Windows only")  # noqa: TRY003 (one raise site)

from ledger_engine.fs import this_os, windows
from ledger_engine.fs.port import Right

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
k32.GetFileAttributesW.restype = wintypes.DWORD
INVALID = wintypes.HANDLE(-1).value
k32.FindFirstFileW.restype = wintypes.HANDLE
k32.FindFirstFileW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
k32.FindClose.argtypes = [wintypes.HANDLE]
k32.LocalFree.argtypes = [ctypes.c_void_p]
k32.LocalFree.restype = ctypes.c_void_p

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
SYNCHRONIZE = 0x00100000
FILE_READ_DATA = 0x1
FILE_ADD_FILE = 0x2
FILE_ADD_SUBDIRECTORY = 0x4
FILE_READ_EA = 0x8
FILE_TRAVERSE = 0x20
FILE_DELETE_CHILD = 0x40
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
SHARE_ALL = 0x7
OPEN_EXISTING = 3
BACKUP = 0x02000000
REPARSE = 0x00200000

ATTRS = {
    0x1: "READONLY",
    0x2: "HIDDEN",
    0x4: "SYSTEM",
    0x10: "DIRECTORY",
    0x20: "ARCHIVE",
    0x200: "SPARSE",
    0x400: "REPARSE_POINT",
    0x1000: "OFFLINE",
    0x2000: "NOT_INDEXED",
    0x40000: "RECALL_ON_OPEN",
    0x80000: "PINNED",
    0x100000: "UNPINNED",
    0x400000: "RECALL_ON_DATA_ACCESS",
}
MODE_NAMES = {0: "application_default", 1: "disguise_placeholder", 2: "expose_placeholders"}


def mode_name(m: int | None) -> str:
    return "unknown" if m is None else MODE_NAMES.get(m, str(m))


STAMP = uuid.uuid4().hex[:8]  # a name of our own; the engine's scripts read no clock
ONEDRIVE = os.environ.get("OneDrive", "")  # noqa: SIM112 (Windows spells it so)
TEMP = os.environ.get("TEMP", "")
HOME = os.environ.get("USERPROFILE", "")
LOG: list[Path] = []  # the log file, once its folder exists
SPELLINGS: list[tuple[str, str]] = []


def mask(text: str) -> str:
    for full, label in SPELLINGS:
        if full:
            text = re.sub(re.escape(full), label, text, flags=re.IGNORECASE)
            text = re.sub(re.escape(full.replace("\\", "\\\\")), label, text, flags=re.IGNORECASE)
    return re.sub(r"S-1-5-21-\d+-\d+-\d+-\d+", "<sid>", text)


def log(**fields: object) -> None:
    line = mask(json.dumps(fields, ensure_ascii=False, default=str))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if LOG:
        with LOG[0].open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def attrs_of(path: str) -> list[str]:
    a = k32.GetFileAttributesW(path)
    if a == 0xFFFFFFFF:
        return [f"error {ctypes.get_last_error()}"]
    return [n for b, n in ATTRS.items() if a & b] + ([f"other 0x{a:x}"] if a & ~sum(ATTRS) else [])


def reparse_tag(path: str) -> str:
    class WFD(ctypes.Structure):
        _fields_ = [
            ("attrs", wintypes.DWORD),
            ("c", wintypes.FILETIME),
            ("a", wintypes.FILETIME),
            ("w", wintypes.FILETIME),
            ("hi", wintypes.DWORD),
            ("lo", wintypes.DWORD),
            ("r0", wintypes.DWORD),
            ("r1", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260),
            ("alt", wintypes.WCHAR * 14),
        ]

    d = WFD()
    h = k32.FindFirstFileW(path, ctypes.byref(d))
    if h == INVALID:
        return f"error {ctypes.get_last_error()}"
    k32.FindClose(h)
    return f"0x{d.r0:08x}" if d.attrs & 0x400 else "none"


def raw_open(path: str, access: int, flags: int) -> str:
    h = k32.CreateFileW(path, access, SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if h == INVALID:
        err = ctypes.get_last_error()
        return f"refused {err} ({ctypes.FormatError(err).strip()})"
    k32.CloseHandle(h)
    return "granted"


def sddl(path: str) -> str:
    sd = ctypes.c_void_p()
    adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    rc = adv.GetNamedSecurityInfoW(path, 1, 0x4 | 0x1, None, None, None, None, ctypes.byref(sd))  # DACL | OWNER
    if rc != 0:
        return f"error {rc}"
    s = wintypes.LPWSTR()
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.c_void_p,
    ]
    ok = adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4 | 0x1, ctypes.byref(s), None)
    k32.LocalFree(sd)
    if not ok:
        return f"error {ctypes.get_last_error()}"
    text = s.value or ""
    k32.LocalFree(ctypes.cast(s, ctypes.c_void_p))
    return text


def set_mode(m: int) -> str:
    ntdll.RtlSetProcessPlaceholderCompatibilityMode.restype = ctypes.c_char
    before = ord(ntdll.RtlSetProcessPlaceholderCompatibilityMode(ctypes.c_char(m)))
    return f"{MODE_NAMES.get(before, before)} -> {mode_name(this_os.placeholder_mode())}"


def guard(step: str, place: str, mode: str, fn: Callable[[], object]) -> object:
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001 (a diagnostic: every failure is a finding)
        log(
            step=step,
            place=place,
            mode=mode,
            ok=False,
            error=type(e).__name__,
            errno=getattr(e, "errno", None),
            winerror=getattr(e, "winerror", None),
            text=str(e),
            where=traceback.format_exc(limit=3).splitlines()[-3:],
        )
        return None
    log(step=step, place=place, mode=mode, ok=True, result=out)
    return out


def make_tree(root: Path) -> Path:
    led = root / "ledger"
    (led / "sub").mkdir(parents=True)
    (led / "main.bean").write_text('include "sub/x.bean"\n2026-01-01 open Assets:Cash EUR\n', encoding="utf-8")
    (led / "sub" / "x.bean").write_text("2026-01-01 open Expenses:Food EUR\n", encoding="utf-8")
    return led


def look(place: str, led: Path) -> None:
    for p in (led.parent, led, led / "main.bean", led / "sub", led / "sub" / "x.bean"):
        log(step="look", place=place, path=str(p), attrs=attrs_of(str(p)), tag=reparse_tag(str(p)))


def probe(place: str, led: Path, mode: str) -> None:
    folder, main = str(led), str(led / "main.bean")
    # the adapter as the save uses it
    fs = windows.WindowsFS()
    d = guard("adapter.open_dir", place, mode, lambda: fs.open_dir(folder))
    if d is not None:
        if not isinstance(d, windows.WindowsDir):
            raise TypeError(d)
        with d:
            for right in (Right.CHANGE_ENTRIES, Right.REMOVE_ENTRIES, Right.SEARCH):
                guard(f"adapter.allowed('.', {right.value})", place, mode, lambda r=right: d.allowed(".", r))
                guard(
                    f"adapter._reopen({right.value}) raw",
                    place,
                    mode,
                    lambda r=right: d._reopen(windows.RIGHT_ACCESS[r]).close(),  # pyright: ignore[reportPrivateUsage] (the adapter's own call),
                )
            for right in (Right.DELETE, Right.READ_XATTRS):
                guard(
                    f"adapter.allowed(main.bean, {right.value})", place, mode, lambda r=right: d.allowed("main.bean", r)
                )
            guard("adapter.stat(main.bean)", place, mode, lambda: repr(d.stat("main.bean")))
            guard("adapter.acl()", place, mode, lambda: repr(d.acl()))
            guard("adapter.mark()", place, mode, lambda: repr(d.mark()))
            guard("adapter.caps()", place, mode, lambda: repr(d.caps()))
            # the save's own effects, in the order the writer takes them
            tmp = f".main.bean.diag-{mode.split(' ')[-1]}.new"
            if (
                guard(
                    "adapter.create(tmp, like=main.bean)",
                    place,
                    mode,
                    lambda: d.create(tmp, b"2026-01-01 open Assets:Cash EUR\n", 0o644, like="main.bean"),
                )
                is None
            ):
                guard(
                    "adapter.create(tmp) plain",
                    place,
                    mode,
                    lambda: d.create(tmp, b"2026-01-01 open Assets:Cash EUR\n", 0o644),
                )
            guard("adapter.sync_file(main.bean)", place, mode, lambda: d.sync_file("main.bean"))
            guard("adapter.rename(tmp -> main.bean)", place, mode, lambda: d.rename(tmp, "main.bean"))
            guard("adapter.sync()", place, mode, lambda: d.sync())
            guard(
                "adapter.remove(tmp) if left",
                place,
                mode,
                lambda: d.remove(tmp) if d.stat(tmp) is not None else "nothing left",
            )
    # raw accesses, to name the bit refused and whether FILE_FLAG_OPEN_REPARSE_POINT matters
    folder_bits = {
        "FILE_ADD_FILE": FILE_ADD_FILE,
        "FILE_ADD_SUBDIRECTORY": FILE_ADD_SUBDIRECTORY,
        "FILE_TRAVERSE": FILE_TRAVERSE,
        "FILE_DELETE_CHILD": FILE_DELETE_CHILD,
        "READ_CONTROL": READ_CONTROL,
        "FILE_READ_ATTRIBUTES": FILE_READ_ATTRIBUTES,
        "change_entries": FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_TRAVERSE,
        "remove_entries": FILE_DELETE_CHILD | FILE_TRAVERSE,
        "GENERIC_WRITE": GENERIC_WRITE,
    }
    for label, bits in folder_bits.items():
        for flags, fl in ((BACKUP, "backup"), (BACKUP | REPARSE, "backup+open_reparse_point")):
            log(step=f"raw folder {label} [{fl}]", place=place, mode=mode, result=raw_open(folder, bits, flags))
    file_bits = {
        "DELETE": DELETE,
        "FILE_READ_ATTRIBUTES": FILE_READ_ATTRIBUTES,
        "READ_CONTROL|GENERIC_READ": READ_CONTROL | GENERIC_READ,
        "GENERIC_WRITE": GENERIC_WRITE,
        "WRITE_DAC": WRITE_DAC,
        "FILE_WRITE_ATTRIBUTES": FILE_WRITE_ATTRIBUTES,
    }
    for label, bits in file_bits.items():
        for flags, fl in ((0, "plain"), (REPARSE, "open_reparse_point")):
            log(step=f"raw main.bean {label} [{fl}]", place=place, mode=mode, result=raw_open(main, bits, flags))

    # what any program may do: Python's own replace in the same folder
    def py_replace() -> str:
        t = led / f"py-{mode.split(' ')[-1]}.tmp"
        t.write_text("2026-01-01 open Assets:Cash EUR\n", encoding="utf-8")
        os.replace(t, led / "main.bean")
        return "replaced"

    guard("python os.replace(tmp -> main.bean)", place, mode, py_replace)


def wait_in_sync(led: Path, tries: int = 45) -> None:
    """Up to about 90 s for OneDrive to take the new files (it tells us nothing, so we look every 2 s)."""
    for n in range(tries):
        if "REPARSE_POINT" in attrs_of(str(led / "main.bean")):
            log(step="onedrive took the files", looks=n)
            return
        threading.Event().wait(2)  # a bounded look at OneDrive's progress (EP-28: no condition to wait on)
    log(step="onedrive did not mark the files within the wait", looks=tries)


def main() -> int:
    if not ONEDRIVE or not Path(ONEDRIVE).is_dir():
        sys.stdout.write("OneDrive is not set up for this account (%OneDrive% is empty).\n")
        return 2
    od_root = Path(ONEDRIVE) / f"Finzytrack diag {STAMP}"
    tmp_root = Path(TEMP) / f"ft-diag-{STAMP}"
    od_root.mkdir()
    tmp_root.mkdir()
    LOG.append(od_root / "diag.jsonl")
    SPELLINGS.extend(
        [
            (str(od_root), "<od-test>"),
            (str(tmp_root), "<temp-test>"),
            (ONEDRIVE, "<onedrive>"),
            (TEMP, "<temp>"),
            (HOME, "<home>"),
        ]
    )
    log(
        step="start",
        python=sys.version.split()[0],
        windows=sys.getwindowsversion().build,
        mode_at_start=mode_name(this_os.placeholder_mode()),
        engine_pin_refused=this_os.pin_refused(),
    )
    log(step="sddl onedrive root", sddl=sddl(ONEDRIVE), attrs=attrs_of(ONEDRIVE), tag=reparse_tag(ONEDRIVE))

    places = {"onedrive": make_tree(od_root), "local": make_tree(tmp_root)}
    wait_in_sync(places["onedrive"])
    for place, led in places.items():
        look(place, led)
        log(step="sddl", place=place, folder=sddl(str(led)), file=sddl(str(led / "main.bean")))
    for m in (1, 0, 2):
        mode = set_mode(m)
        for place, led in places.items():
            probe(place, led, mode)
        if m == 1:
            for place, led in places.items():
                look(place, led)
    set_mode(1)
    log(step="done", log_file="<od-test>\\diag.jsonl", keep="the two test folders stay; delete them when you are done")
    sys.stdout.write(f"\nThe log is also in: {od_root / 'diag.jsonl'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
