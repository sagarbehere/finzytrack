"""A real folder with OneDrive's security, for the Windows runner (S0151, BL-0283; owner, 2026-11-23: "1").

Probed on the owner's PC, 2026-11-23 (`engine/scripts/diag_onedrive_save.py`): the OneDrive root's DACL is
`D:AI(D;CI;DT;;;WD)(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;FA;;;<user>)`; every folder in it inherits
`(D;CIID;DT;;;WD)` (deny everyone `delete_child`, to folders only), its files carry `(A;ID;FA;;;…)` with no deny, and
the folders have the read-only attribute (a customized folder's mark). `onedrive_folder` sets the same entries on a
scratch folder (its allow entries explicit and the DACL protected, so the runner's own inherited entries do not mix in:
what each entry grants is the same) and passes them on to what is already in it, as Windows does when a folder's DACL
is set. Only the folder given is touched; `reset` gives it back the runner's inherited entries, so the test's scratch
folder can be removed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ONEDRIVE_DENY = "(D;CI;DT;;;WD)"
"""OneDrive's own entry on its root, as the owner's PC showed it."""
NO_DELETE_FOR_NEW = "0x1201bf"
"""Read, write and execute without `DELETE` (`FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE`)."""


def user_sid() -> str:
    """This process's user SID (`whoami /user`); used only in the test's own scratch folder, never written to the
    repository."""
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)  # noqa: S607
    return out.stdout.strip().split(",")[-1].strip('"')


def onedrive_sddl(*, extra: str = "", user_access: str = "FA") -> str:
    """OneDrive's DACL in SDDL with the allow entries for SYSTEM, the administrators and this user (`user_access`:
    what the user's entry grants; FA, full access, as on the owner's PC), and any `extra` entries after the deny."""
    sid = user_sid()
    allow = f"(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;{user_access};;;{sid})"
    if user_access != "FA":
        allow = f"(A;OICI;{user_access};;;{sid})"
    return f"D:PAI{ONEDRIVE_DENY}{extra}{allow}"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x4
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    SDDL_REVISION_1 = 1

    def set_dacl(path: Path, sddl: str) -> None:
        """The DACL of `sddl` set on the folder or file at `path` (`SetNamedSecurityInfoW`, protected), passed on to
        what is in it by Windows' own inheritance."""
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        convert.restype = wintypes.BOOL
        get_dacl = adv.GetSecurityDescriptorDacl
        get_dacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        get_dacl.restype = wintypes.BOOL
        set_named = adv.SetNamedSecurityInfoW
        set_named.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        set_named.restype = wintypes.DWORD
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        sd = ctypes.c_void_p()
        if not convert(sddl, SDDL_REVISION_1, ctypes.byref(sd), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
            if not get_dacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            rc = set_named(
                str(path),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if rc != 0:
                raise ctypes.WinError(rc)
        finally:
            k32.LocalFree(sd)

    def onedrive_folder(path: Path, sddl: str | None = None) -> None:
        """`path` (an existing scratch folder) given OneDrive's security (or `sddl`) and the read-only attribute."""
        set_dacl(path, sddl or onedrive_sddl())
        subprocess.run(["attrib", "+R", str(path)], check=True, capture_output=True)  # noqa: S603, S607

    def reset(path: Path) -> None:
        """The scratch folder and everything in it back to the runner's inherited entries, read-only cleared."""
        subprocess.run(["icacls", str(path), "/reset", "/T", "/C", "/Q"], check=False, capture_output=True)  # noqa: S603, S607
        subprocess.run(["attrib", "-R", str(path / "*"), "/S", "/D"], check=False, capture_output=True)  # noqa: S603, S607
        subprocess.run(["attrib", "-R", str(path)], check=False, capture_output=True)  # noqa: S603, S607
