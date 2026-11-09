#!/usr/bin/env python3
"""Probe whether Finzytrack can identify the drive a folder is on (S0089; owner, 2026-11-09: "You run a 1-minute
script on a network share you use").

usage (from anywhere, macOS): python3 scripts/probe_drive_id.py <folder> [<folder> ...]

For each folder you name (for example the folder of your ledger on a network share), it prints the drive's kind
(`f_fstypename`: `smbfs`, `nfs`, `apfs`, `msdos`, ...), its volume UUID as macOS reports it (`ATTR_VOL_UUID`), the
folder's creation time (`ATTR_CMN_CRTIME`), and whether Finzytrack would save there (the same rules, in the same order,
as `ledger_engine.ledger.handles.drive_of` and `known_birth`; S0089 round 2, verifier F2) (`design/specs/engine-jobs.md`
§6.3 "Drive identity"). Read-only: it opens each folder itself and asks for these attributes; it lists no file, reads
no file and writes nothing. Paste its output to the agent; it holds no ledger content (the path you give is printed
only as you typed it, so leave it out when you paste if you prefer).
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
import uuid

ATTR_CMN_RETURNED_ATTRS = 0x80000000
ATTR_CMN_CRTIME = 0x00000200
ATTR_VOL_INFO = 0x80000000
ATTR_VOL_UUID = 0x00040000
STATFS_SIZE = 2168  # `struct statfs` on macOS 11+ (64-bit inodes)
STATFS_FSTYPENAME = 72
SERIAL = ("msdos", "exfat")  # their UUID comes from a 32-bit format serial; no creation time at their top level
UNSTABLE = ("msdos",)  # a version 4 UUID there is made up at each mount (a serial clash)
FS_TYPE = re.compile(r"[A-Za-z0-9_.-]{1,15}")  # a type name the save's record holds (`journal.FS_TYPE`; S0090)


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


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    lib.fgetattrlist.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    lib.fgetattrlist.restype = ctypes.c_int
    lib.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    lib.fstatfs.restype = ctypes.c_int
    return lib


def _attrs(lib: ctypes.CDLL, fd: int, common: int, vol: int) -> bytes:
    attrs = _AttrList(5, 0, ATTR_CMN_RETURNED_ATTRS | common, vol, 0, 0, 0)
    buf = ctypes.create_string_buffer(64)
    if lib.fgetattrlist(fd, ctypes.byref(attrs), buf, len(buf), 0) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return buf.raw


def probe(path: str) -> list[str]:
    """The lines printed for one folder."""
    lib = _libc()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sf = ctypes.create_string_buffer(STATFS_SIZE)
        kind = "unknown"
        if lib.fstatfs(fd, sf) == 0:
            kind = sf.raw[STATFS_FSTYPENAME : STATFS_FSTYPENAME + 16].split(b"\0", 1)[0].decode("ascii", "replace")
        raw = _attrs(lib, fd, 0, ATTR_VOL_INFO | ATTR_VOL_UUID)
        returned_vol = struct.unpack_from("<I", raw, 8)[0]
        u = raw[24:40]
        volume = str(uuid.UUID(bytes=u)) if returned_vol & ATTR_VOL_UUID and u != bytes(16) else None
        raw = _attrs(lib, fd, ATTR_CMN_CRTIME, 0)
        born = struct.unpack_from("<qq", raw, 24) if struct.unpack_from("<I", raw, 4)[0] & ATTR_CMN_CRTIME else None
    finally:
        os.close(fd)
    version = uuid.UUID(volume).version if volume else None
    return [
        f"folder:        {path}",
        f"drive kind:    {kind}",
        f"drive ID:      {volume or 'none'}" + (f" (version {version})" if version else ""),
        f"folder born:   {'none' if born is None else f'{born[0]}.{born[1]:09d}'}",
        f"saves here:    {verdict(kind, volume, born)}",
    ]


def verdict(kind: str, volume: str | None, born: tuple[int, int] | None) -> str:
    """Whether the save would go ahead: the engine's rules in its order (`handles.drive_of`, then
    `handles.known_birth`): no drive ID or a made-up one is `no_drive_id`, before any look at the creation time."""
    if volume is None:
        return "NO: the drive reports no ID (Finzytrack refuses to save here: no_drive_id)"
    if not FS_TYPE.fullmatch(kind):
        return "NO: Finzytrack cannot record this drive's kind (refused: no_drive_id)"
    if kind in UNSTABLE and uuid.UUID(volume).version == 4:
        return "NO: this ID changes at every mount (refused: no_drive_id)"
    if born is None:
        return "NO: this drive keeps no folder creation time (refused: no_folder_id)"
    if kind in SERIAL and born == (0, 0):
        return "NO: this folder has no creation time (refused: no_folder_id); use a folder inside the drive"
    return "YES: Finzytrack can identify this drive and folder"


def main(argv: list[str]) -> int:
    if sys.platform != "darwin" or not argv:
        sys.stderr.write("usage (macOS): python3 scripts/probe_drive_id.py <folder> [<folder> ...]\n")
        return 2
    for path in argv:
        try:
            sys.stdout.write("\n".join(probe(path)) + "\n\n")
        except OSError as e:
            sys.stdout.write(f"folder:        {path}\nerror:         {e.strerror} (errno {e.errno})\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
