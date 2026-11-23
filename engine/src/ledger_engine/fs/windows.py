"""The file-system port on Windows (`ledger_engine.fs.port`; `design/specs/engine-jobs.md` §6.2, §6.3, §6.8, §15;
S0128, BL-0280, BL-0218; ADR-0069). It writes exactly what the macOS and Linux adapters write, under the same contract;
only these primitives differ (owner, 2026-11-16: "Rule unchanged (Recommended)"). Facts from S0126's probe on the
Windows runner (Windows Server 2022) are marked "probed".

**Selected by the product since S0150** (BL-0087: Windows writes on). `ledger_engine.fs.this_os` gives this adapter
while the process is in the engine's placeholder mode (ADR-0070, below); in any other mode the product refuses every
file operation (`UnsupportedPlatform`) except the engine lock (`take_engine_lock`), a guard that writes only
`locks/engine.lock` in the app folder (the sidecar starts engines on Windows since S0129, each in its own Job Object).

- **Handles and names.** A `WindowsDir` holds a folder handle (shared for reading, writing and deleting, as a POSIX
  descriptor blocks nothing). Every entry is opened, made, renamed and removed **relative to that handle**
  (`NtCreateFile` with the handle as the root, `NtSetInformationFile(FileRenameInformation)` with it as the target's
  root; probed: `SetFileInformationByHandle` refuses a root handle), one name at a time, with
  `FILE_OPEN_REPARSE_POINT`: a symbolic link or a junction is never followed below an open folder, as `O_NOFOLLOW` on
  POSIX. Only `open_dir` and `resolve` take a path, with the long-path prefix (`\\\\?\\`, `PathModel.extended`), and
  follow links, as `open(2)` does. Names are checked by the Windows path model first (`check_name`: reserved device
  names, a trailing dot or space and the characters no name holds are refused).
- **Kinds.** A reparse point whose tag is a symbolic link or a junction is a `LINK`; any other reparse point the
  process sees (a deduplicated file, an AppExecLink) is `OTHER`, so the save refuses it (`not_a_file`). **Cloud
  placeholders** (OneDrive's Files On-Demand and every other Cloud Files provider alike; S0149, BL-0283, ADR-0070):
  every engine process pins `disguise_placeholder` at its start (`this_os.pin_placeholder_mode`), so the cloud filter
  hides the reparse point: a placeholder file or folder, cloud-only or downloaded, and the sync root are a `FILE` or a
  `DIR` here, read and saved as one; a read of a cloud-only file downloads it; the save's rename leaves a plain file at
  the name (probed with Windows' own cloud filter, S0144 and the 2026-11-22 run 11). Links and junctions stay `LINK`
  (run 11). Where a process exposes placeholders (not the engine's) a placeholder is `OTHER`. A read of a file's data
  that the cloud filter refuses because a cloud-only file could not be downloaded (the sync app not running, offline:
  `port.CLOUD_DOWNLOAD_ERRORS`) raises `port.CloudUnavailable` (`_read_error`, at `ReadFile` only); every other call
  keeps such a code as a plain `OSError` (S0149 round 1, V2).
  A file with the read-only attribute is `locked` (Windows refuses to replace or remove it): the save refuses it before
  writing. A file Windows' EFS encrypts is `encrypted`: the save refuses it before writing (`encrypted_file`; owner,
  2026-11-17: "1a"), so its bytes never reach a backup or a staging file in the clear. Windows has no mode bits:
  `mode` reads 0o444 or 0o666; a new file gets the folder's inherited ACL, and a replaced file's new version its own
  security (`create(..., like=...)`, below).
- **`durable_sync`:** `FlushFileBuffers`, which needs a handle opened for writing (probed: a read-only handle is
  refused): a file the adapter creates is flushed through its own handle; `sync_file` opens the file for writing (it
  writes nothing; a file another program holds without sharing writes cannot be synced, and the caller fails closed);
  a folder's `sync` opens the folder again for writing (`FILE_ADD_FILE`) and flushes it (probed: works on NTFS, ReFS,
  FAT32 and exFAT; a network share's server refuses it, `ERROR_INVALID_FUNCTION`, probed in S0132 and the 2026-11-20
  runs 2 and 3: `SyncRefused` on a remote volume only, which only a ledger folder's sync takes as done,
  `ledger.handles.ledger_sync`, S0136; the mirror refuses a cache folder on a share before any sync, S0134). The
  rename's source is opened with `FILE_WRITE_THROUGH`. **What this makes durable for a rename** is relied on, not
  documented: Microsoft documents `FlushFileBuffers` for a file's data and metadata, not for a folder's entries; NTFS
  and ReFS log a rename as one metadata operation and a flush of the folder commits that log as far as it is known, and
  FAT32 and exFAT write the folder's entries with no log (a replace there may lose the old entry and keep no new one, as
  macOS's FAT: §6.8). Not power-cut tested (the runner cannot cut power): engine-jobs §15 "Limit (Windows
  durability)".
- **The replace (ADR-0069):** Windows has no exchange of two names (probed), so `caps().swap` is False on every
  volume and the writer takes §6.8's best-effort mode: the new bytes synced at the staging name, then one plain rename
  over the file (`FileRenameInformation` with `ReplaceIfExists`, relative to the folder handle: the handle-relative
  form of `MoveFileExW(MOVEFILE_REPLACE_EXISTING)`; S0126 probed `MoveFileExW`, and `tests/fs/test_windows.py` this
  rename itself, for a moment when the name is missing during 300 replaces on each volume kind), the
  folder synced. `swap` raises `NotOffered`.
- **The exclusive rename and create:** `FileRenameInformation` without `ReplaceIfExists`, and `FILE_CREATE`; both
  refuse an existing name on every volume (probed), so `caps().excl` is True everywhere (A2's rule: a volume that
  refuses the call itself, `STATUS_NOT_SUPPORTED`, is `NotOffered`, remembered for the job).
- **A file another program holds open** without sharing deletion (the usual open) cannot be renamed, replaced or
  removed (probed). The save asks for delete access to each file it replaces before its first effect (`allowed`),
  so such a file stops the save there: `InUseError` (`EBUSY`; `fs.port`, S0148), which the save tells `in_use`
  ("in use by another program"), "not saved", unchanged, naming the file. Opened after that check, or held by a
  program that shares deletion (antivirus, an indexer), the replace fails with nothing changed (`ACCESS_DENIED`,
  raised as `ReplaceDenied`, which the save tells `in_use`: the file passed its check just before) and the save
  rolls back; recovery's put-back, after its own check granted delete access, tells it as the in-use error too
  (`restore`, owner, 2026-11-22: "Fix it").
- **Removing:** `FileDispositionInformationEx` with POSIX semantics, so the name goes at once as `unlink` makes it go
  (NTFS, ReFS); where a volume refuses those (FAT32, exFAT), the classic delete-on-close, whose name stays until every
  other handle on the file is closed (an antivirus scan holding one shows as a create at that name failing).
- **Paths:** a folder's path is `GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)` (every 8.3 short
  name expanded, each name as stored), the prefix taken off; a volume with no drive letter or mount folder gives its
  volume GUID path instead (`VOLUME_NAME_GUID`). **The canonical root (Q1)** is therefore the one the handle gives,
  whatever spelling opened it: a short name, the prefix, a `subst` drive or a volume GUID path of a lettered volume
  all give the drive letter; a root's key is folded by ordinal upper case (a server name's case). A mapped drive
  gives its share's UNC path. A network share has the drive ID of the server's volume (below; S0136), so each of its
  spellings (`\\\\localhost\\...`, `\\\\<machine>\\...`, and `\\\\localhost\\C$` for a local folder) is another
  root and key for a folder with one identity (engine-jobs §6.3, "Limit (share spellings)").
- **Identity (BL-0218):** `identity()` and `stat`'s `(dev, ino)` are `FileIdInfo`'s 64-bit volume serial and 128-bit
  file ID on NTFS and ReFS; FAT32 and exFAT have no `FileIdInfo` (probed), so there they are the 32-bit volume serial
  and the file index `GetFileInformationByHandle` gives. `born()` is the file ID on NTFS and ReFS (the creation time is
  no identity there: a folder removed and made again at once keeps its predecessor's creation time, tunnelling,
  probed, but gets a new file ID) and the creation time on FAT32 and exFAT (0 for a drive's top level, probed: the
  save refuses there, `no_folder_id`). **Limit (Windows FAT identity):** tunnelling gives a FAT or exFAT folder made
  again at once at the same path its predecessor's creation time, and its file index may be reused, so such a
  replacement is not caught there (as macOS's FAT, whose 2 s creation time catches it only by chance).
  `volume_id`: the serial as a version 5 UUID in `port.SERIAL_NAMESPACE` (16 hex digits for NTFS and ReFS, `XXXX-XXXX`
  for FAT32 and exFAT: the names Linux's udev gives the same drives), so twin sticks share it, as on macOS and Linux;
  on a network share the server volume's serial, as the share's `FileIdInfo` gives it (S0136, BL-0219; probed equal
  through every spelling and the server's own path, stable across a remap and a server restart); another file system
  has none. The file-system type is `GetVolumeInformationByHandleW`'s name in
  lower case (`ntfs`, `refs`, `fat32`, `fat`, `exfat`).
- **Names and folding:** NTFS, ReFS, FAT32 and exFAT compare names without case (probed); a folder compares by
  ordinal upper case (`FoldRule(upper=True)`), except an NTFS folder with its case-sensitive flag
  (`FileCaseSensitiveInfo`), which compares exactly. Names are never normalized.
- **Rights and the ACL:** `allowed` asks Windows itself, by opening the entry with the access the save needs (delete
  access to a file it replaces, `FILE_ADD_FILE` and `FILE_DELETE_CHILD` on a folder it writes in, traverse to search):
  granted is yes, `ACCESS_DENIED` no, a sharing violation `EBUSY` (above). Windows grants removing an entry when the
  folder grants `FILE_DELETE_CHILD` or the entry grants `DELETE`, so where the folder refuses `FILE_DELETE_CHILD` (every
  OneDrive folder: probed on the owner's PC, 2026-11-23), `allowed(".", REMOVE_ENTRIES)` asks whether the entries the
  save makes would grant `DELETE` (`_may_remove_entries`; S0151, owner, 2026-11-23: "1"), and `caps().delete_by_entry`
  is True. `acl` reads the folder's DACL (`GetSecurityInfo`), each entry's kind, access bits and inheritance flags in
  `chmod`'s words, so `handles.acl_denial` judges it as on macOS (any deny entry, whoever it names, unless it is not
  inherited and denies only what protects the folder itself), except that a denied `delete_child` counts for nothing
  there (`handles.blocking_entry`). `create(..., like=...)` (a replaced file's new version) takes the old file's
  alternate data streams, its hidden attribute and its DACL with its protected or inherited state (owner, 2026-11-17:
  "1a", narrowed 2026-11-20: "Drop archive from rule 1a (Recommended)", so the archive attribute is Windows' own, set on
  the saved file as on any edit; replacing for Windows the 2026-10-29 rule that keeps no ACL: a Windows file's own ACL
  is its only privacy setting, as the mode bits are on macOS); not its owner or SACL, which need a privilege.
  `allowed(name, READ_XATTRS)` asks for what that copy reads. **The volume's own flags decide** what is asked (S0135,
  BL-0285; owner, 2026-11-20: "The drive's own flags (Recommended)"): a DACL (the file's, and the folder's in `acl`)
  only where `FILE_PERSISTENT_ACLS` is set, the streams only where `FILE_NAMED_STREAMS` is; where a flag is set, any
  refusal raises (the save is "not saved", nothing changed), never read as "this volume keeps none". **Limit (Windows
  privacy during a save)** (owner, 2026-11-20: "State it as a limit"): the new bytes are written into the staging file,
  which has the folder's ACL, before its own DACL is set, and a put-back likewise: on Windows a ledger is as private as
  its folder (engine-jobs §6.3).
- **Links:** hard links on NTFS and ReFS only (probed); a file with several is refused as on macOS (`nlink`).
  `symlink` refuses (`ENOTSUP`): the mirror's `current.db` link is ADR-0015's pointer file `current.txt` on Windows
  (`caps().links` False; S0130, BL-0206), and `readlink` reads no link target (a link: `ENOTSUP`).
- **The rename over a file readers hold** (`rename_shared`, S0130): the rename with POSIX semantics, which NTFS offers
  and the mirror's pointer swap takes; a reader that shares deletion does not refuse it, where it refuses the plain
  `rename` (probed, S0130's P3 and P5). Offered by what the volume offers (S0132, BL-0294; owner, 2026-11-18): the flag
  `FILE_SUPPORTS_POSIX_UNLINK_RENAME`, and not a network share (probed, S0132's P6: an SMB share reports NTFS and the
  flag and refuses the rename, `INVALID_PARAMETER`); ReFS, FAT32 and exFAT clear the flag. Elsewhere `NotOffered`.
- **The read-only attribute** (`clear_readonly`, S0132, BL-0294): only the mirror's publish clears it, on what it
  removes or replaces in its own folder (ADR-0015 amendment of 2026-11-18). Probed (S0132's P7) on NTFS, ReFS, FAT32
  and exFAT: a read-only file refuses its removal and a rename over it (POSIX or plain), and a read-only folder its
  removal; clearing the attribute works while SQLite, a share-all reader or a plain `open()` holds the file, and then
  the removal (once no reader holds it) and the renames land. Setting a read-only file's modification time works.
- **Locks:** `WindowsLegacyLocks` takes the legacy app's lock as portalocker 3.2.0 does on Windows (its default
  `MsvcrtLocker`: `msvcrt.locking` of the first 64 KiB, `LockFile` underneath, which `LockFileEx` sees; INV-26);
  `take_engine_lock` takes the engine lock with `LockFileEx` over the whole file, waiting (engine-jobs §5.1; the
  sidecar's wait takes the same, S0129).

This module is Windows-only: importing it elsewhere raises `ImportError`.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import struct
import sys
from collections.abc import Callable, Generator, Iterable
from ctypes import wintypes
from typing import Any, Final, Self

from ledger_engine.fs.pathmodel import host
from ledger_engine.fs.port import (
    CHUNK,
    CLOUD_DOWNLOAD_ERRORS,
    AclEntry,
    Caps,
    CloudUnavailable,
    EntryStat,
    FolderMark,
    FoldRule,
    InUseError,
    Kind,
    LegacyAbsent,
    LegacyHeld,
    MalformedPathError,
    NotOffered,
    ReplaceDenied,
    Resolved,
    Right,
    SyncRefused,
    check_name,
    fold,
)

# The one statement of this module's platform (not an `assert`, which `-O` strips): elsewhere the import fails, and
# pyright checks the rest of the module for Windows only (`--pythonplatform`; S0113).
if sys.platform != "win32":
    raise ImportError("the Windows adapter; select one with ledger_engine.fs.this_os")  # noqa: TRY003 (one raise site)

import uuid

from ledger_engine.fs.port import SERIAL_NAMESPACE

# Access rights (winnt.h)
DELETE: Final = 0x00010000
READ_CONTROL: Final = 0x00020000
WRITE_DAC: Final = 0x00040000
WRITE_OWNER: Final = 0x00080000
SYNCHRONIZE: Final = 0x00100000
GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
FILE_LIST_DIRECTORY: Final = 0x0001
FILE_ADD_FILE: Final = 0x0002
FILE_ADD_SUBDIRECTORY: Final = 0x0004
FILE_READ_EA: Final = 0x0008
FILE_WRITE_EA: Final = 0x0010
FILE_TRAVERSE: Final = 0x0020
FILE_DELETE_CHILD: Final = 0x0040
FILE_READ_ATTRIBUTES: Final = 0x0080
FILE_WRITE_ATTRIBUTES: Final = 0x0100
FILE_SHARE_READ_WRITE: Final = 0x3
FILE_SHARE_ALL: Final = 0x7  # read, write and delete: a handle of ours blocks no other program, as a descriptor
# CreateFileW
OPEN_EXISTING: Final = 3
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_TYPE_DISK: Final = 1
# NtCreateFile
FILE_OPEN: Final = 1
FILE_CREATE: Final = 2
FILE_OPEN_IF: Final = 3
FILE_DIRECTORY_FILE: Final = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT: Final = 0x00000020
FILE_NON_DIRECTORY_FILE: Final = 0x00000040
FILE_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_ATTRIBUTE_READONLY: Final = 0x1
FILE_ATTRIBUTE_DIRECTORY: Final = 0x10
FILE_ATTRIBUTE_NORMAL: Final = 0x80
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400
FILE_ATTRIBUTE_HIDDEN: Final = 0x2
FILE_ATTRIBUTE_ARCHIVE: Final = 0x20
FILE_ATTRIBUTE_ENCRYPTED: Final = 0x4000
KEPT_ATTRIBUTES: Final = FILE_ATTRIBUTE_HIDDEN
"""The attributes a replaced file passes to its new version (owner, 2026-11-17: "1a"): hidden only. Not archive
(owner, 2026-11-20: "Drop archive from rule 1a (Recommended)"; S0135): it means "changed since the last backup", so
the saved file keeps the archive attribute Windows gives a new or changed file, and a backup tool sees the save. The
read-only attribute never (the save refuses such a file: `locked`); encryption never (refused: `encrypted_file`)."""
FILE_WRITE_THROUGH: Final = 0x00000002
FILE_STREAM_INFO: Final = 7
ERROR_MORE_DATA: Final = 234
ERROR_HANDLE_EOF: Final = 38
SE_DACL_PROTECTED: Final = 0x1000
PROTECTED_DACL_SECURITY_INFORMATION: Final = 0x80000000
UNPROTECTED_DACL_SECURITY_INFORMATION: Final = 0x20000000
MAIN_STREAM: Final = "::$DATA"
IO_REPARSE_TAG_MOUNT_POINT: Final = 0xA0000003
IO_REPARSE_TAG_SYMLINK: Final = 0xA000000C
LINK_TAGS: Final = frozenset({IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK})
# Information classes
FILE_BASIC_INFO: Final = 0  # GetFileInformationByHandleEx / SetFileInformationByHandle
FILE_STANDARD_INFO: Final = 1
FILE_ATTRIBUTE_TAG_INFO: Final = 9
FILE_FULL_DIRECTORY_INFO: Final = 14
FILE_FULL_DIRECTORY_RESTART_INFO: Final = 15
FILE_ID_INFO: Final = 18
FILE_CASE_SENSITIVE_INFO: Final = 23
NT_FILE_RENAME_INFORMATION: Final = 10  # NtSetInformationFile
NT_FILE_RENAME_INFORMATION_EX: Final = 65
FILE_RENAME_REPLACE_IF_EXISTS: Final = 0x1
FILE_RENAME_POSIX_SEMANTICS: Final = 0x2
FILE_SUPPORTS_POSIX_UNLINK_RENAME: Final = 0x400
"""The volume flag (`GetVolumeInformationByHandleW`) that says the rename with POSIX semantics is offered (S0132,
BL-0294; probed: set on NTFS, clear on FAT32, exFAT and ReFS, and set on an SMB share, which refuses the rename with
`INVALID_PARAMETER`: so a remote volume is never offered it, whatever its flags say)."""
POSIX_RENAME: Final = "a rename over a file that readers hold while sharing deletion"
FILE_PERSISTENT_ACLS: Final = 0x8
"""The volume flag that says the volume keeps ACLs (S0135, BL-0285; owner, 2026-11-20: "The drive's own flags
(Recommended)"): where it is set, a refused read of a file's or a folder's DACL raises (the save is "not saved",
nothing changed); where it is clear, no DACL is asked or carried. Never decided by an error code or the name."""
FILE_NAMED_STREAMS: Final = 0x40000
"""The volume flag that says the volume keeps alternate data streams (S0135): where it is set, a refused listing of
a file's streams raises; where it is clear, no listing is asked and none is carried."""
FILE_ATTRIBUTE_VALID_SET_FLAGS: Final = 0x31A7
"""The attributes a caller may set (`SetFileAttributesW` masks with the same value): read-only, hidden, system,
archive, normal, temporary, offline, not content indexed."""
NT_FILE_DISPOSITION_INFORMATION: Final = 13
NT_FILE_DISPOSITION_INFORMATION_EX: Final = 64
FILE_DISPOSITION_DELETE: Final = 0x1
FILE_DISPOSITION_POSIX_SEMANTICS: Final = 0x2
FILE_FS_DEVICE_INFORMATION: Final = 4  # NtQueryVolumeInformationFile
FILE_REMOTE_DEVICE: Final = 0x10
FILE_NAME_NORMALIZED: Final = 0x0
VOLUME_NAME_DOS: Final = 0x0
VOLUME_NAME_GUID: Final = 0x1
LOCKFILE_FAIL_IMMEDIATELY: Final = 0x1
LOCKFILE_EXCLUSIVE_LOCK: Final = 0x2
LEGACY_LOCK_BYTES: Final = 0x10000
"""portalocker 3.2.0's `MsvcrtLocker._msvcrt_lock_length`: its exclusive lock is `msvcrt.locking` of the file's first
64 KiB (it seeks to 0 first)."""
SE_FILE_OBJECT: Final = 1
OWNER_SECURITY_INFORMATION: Final = 0x1
GROUP_SECURITY_INFORMATION: Final = 0x2
DACL_SECURITY_INFORMATION: Final = 0x4
TOKEN_DUPLICATE: Final = 0x0002
TOKEN_QUERY: Final = 0x0008
SECURITY_IMPERSONATION: Final = 2
SEF_NEW_ENTRY: Final = 0x01 | 0x08 | 0x10
"""`CreatePrivateObjectSecurityEx`'s flags for the security a new file or folder gets, as NTFS gives it: the DACL
inherited automatically (`SEF_DACL_AUTO_INHERIT`), with no privilege or owner check (`SEF_AVOID_PRIVILEGE_CHECK`,
`SEF_AVOID_OWNER_CHECK`: we only ask, nothing is assigned)."""
FILE_GENERIC_MAPPING: Final = (0x120089, 0x120116, 0x1200A0, 0x1F01FF)
"""A file's generic rights (read, write, execute, all), as `GENERIC_MAPPING`."""
NEW_ENTRY_DEPTH: Final = 2
"""How deep the folders the save makes are followed when asking whether its new entries may be removed
(`WindowsDir._new_entries_deletable`): what a folder passes on is the same from its second level down (an entry
`limit_inherit` stops after one level; every other inheritable entry passes on unchanged), so a file and a folder at
depths 0 to 2 stand for every depth."""
EPOCH_AS_FILETIME: Final = 116_444_736_000_000_000
"""1970-01-01 in 100 ns units since 1601-01-01 (a FILETIME)."""
MAX_PATH_CHARS: Final = 32_768
# Win32 errors
ERROR_INVALID_FUNCTION: Final = 1
ERROR_FILE_NOT_FOUND: Final = 2
ERROR_PATH_NOT_FOUND: Final = 3
ERROR_ACCESS_DENIED: Final = 5
ERROR_INVALID_PARAMETER: Final = 87
ERROR_NO_MORE_FILES: Final = 18
ERROR_NOT_SUPPORTED: Final = 50
ERROR_SHARING_VIOLATION: Final = 32
ERROR_LOCK_VIOLATION: Final = 33
ERROR_INVALID_NAME: Final = 123
ERROR_DIRECTORY: Final = 267
ERROR_CANT_ACCESS_FILE: Final = 1920
NOT_FOUND_ERRORS: Final = frozenset({ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_DIRECTORY})
STATUS_ERRNO: Final = {
    0xC0000034: errno.ENOENT,  # OBJECT_NAME_NOT_FOUND
    0xC000003A: errno.ENOENT,  # OBJECT_PATH_NOT_FOUND
    0xC0000035: errno.EEXIST,  # OBJECT_NAME_COLLISION
    0xC0000103: errno.ENOTDIR,  # NOT_A_DIRECTORY
    0xC00000BA: errno.EISDIR,  # FILE_IS_A_DIRECTORY
    0xC0000101: errno.ENOTEMPTY,  # DIRECTORY_NOT_EMPTY
    0xC0000022: errno.EACCES,  # ACCESS_DENIED
    0xC0000043: errno.EBUSY,  # SHARING_VIOLATION: another program holds it without sharing what is asked
    0xC0000121: errno.EACCES,  # CANNOT_DELETE (a read-only file)
    0xC0000056: errno.EACCES,  # DELETE_PENDING: a name another handle keeps until it is closed
    0xC00000BB: errno.ENOTSUP,  # NOT_SUPPORTED
    0xC0000010: errno.ENOTSUP,  # INVALID_DEVICE_REQUEST
    0xC000000D: errno.EINVAL,  # INVALID_PARAMETER
    0xC0000033: errno.EINVAL,  # OBJECT_NAME_INVALID
}
"""What an NTSTATUS means as `errno`, so callers read Windows' answers as they read POSIX ones; any other status is
`EIO` with its number in the message."""
STATUS_SHARING_VIOLATION: Final = 0xC0000043
STATUS_ACCESS_DENIED: Final = 0xC0000022
REFUSED_STATUS: Final = frozenset({0xC00000BB, 0xC0000010})
"""What a volume answers to an information class it does not have: nothing done."""
FS_SERIAL64: Final = frozenset({"ntfs", "refs"})
FS_SERIAL32: Final = frozenset({"fat", "fat32", "exfat"})
UPPER: Final = FoldRule(nfd=False, casefold=False, upper=True)
EXACT: Final = FoldRule(nfd=False, casefold=False)
DIR_ACCESS: Final = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE
PERMS: Final = (
    (FILE_LIST_DIRECTORY, "list"),
    (FILE_ADD_FILE, "add_file"),
    (FILE_ADD_SUBDIRECTORY, "add_subdirectory"),
    (FILE_READ_EA, "readextattr"),
    (FILE_WRITE_EA, "writeextattr"),
    (FILE_TRAVERSE, "search"),
    (FILE_DELETE_CHILD, "delete_child"),
    (FILE_READ_ATTRIBUTES, "readattr"),
    (FILE_WRITE_ATTRIBUTES, "writeattr"),
    (DELETE, "delete"),
    (READ_CONTROL, "readsecurity"),
    (WRITE_DAC, "writesecurity"),
    (WRITE_OWNER, "chown"),
    (SYNCHRONIZE, "synchronize"),
)
"""An ACE's access bits in `chmod`'s words (`handles.FOLDER_SELF` names the ones that protect a folder itself); any
other bit (a generic right, `ACCESS_SYSTEM_SECURITY`) is `unknown`."""
ACE_FLAGS: Final = ((0x1, "file_inherit"), (0x2, "directory_inherit"), (0x4, "limit_inherit"), (0x8, "only_inherit"))
INHERITED_ACE: Final = 0x10
RIGHT_ACCESS: Final = {
    Right.CHANGE_ENTRIES: FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_TRAVERSE,
    Right.REMOVE_ENTRIES: FILE_DELETE_CHILD | FILE_TRAVERSE,
    Right.SEARCH: FILE_TRAVERSE,
    Right.DELETE: DELETE,
    Right.READ_XATTRS: FILE_READ_ATTRIBUTES,
}
"""The access each right is asked as (`WindowsDir.allowed`): adding and removing entries in a folder, searching it,
removing or renaming away an entry (delete access, which Windows grants by the entry's own ACL or the folder's
`FILE_DELETE_CHILD`)."""
EXCHANGE: Final = "an exchange of two names (Windows has none: ADR-0069)"
EXCLUSIVE_RENAME: Final = "a rename that refuses an existing name"
ALLOW_ACE_TYPES: Final = frozenset({0x0, 0x5, 0x9, 0xB})
"""Allowed, allowed object, allowed callback, allowed callback object; any other kind counts as a deny."""


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


class OVERLAPPED(ctypes.Structure):
    _fields_ = (
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


def _sig(fn: Any, args: list[Any], res: Any) -> Any:
    fn.argtypes = args
    fn.restype = res
    return fn


class _Api:
    """The system calls, typed (a handle on libraries already loaded, made per `WindowsFS`, which lives for one job:
    no state is held across jobs)."""

    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        h, d, b, p = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p
        self.CreateFileW = _sig(k32.CreateFileW, [wintypes.LPCWSTR, d, d, p, d, d, h], h)
        self.CloseHandle = _sig(k32.CloseHandle, [h], b)
        self.FlushFileBuffers = _sig(k32.FlushFileBuffers, [h], b)
        self.ReadFile = _sig(k32.ReadFile, [h, p, d, ctypes.POINTER(d), p], b)
        self.WriteFile = _sig(k32.WriteFile, [h, p, d, ctypes.POINTER(d), p], b)
        self.GetFileType = _sig(k32.GetFileType, [h], d)
        self.GetInfo = _sig(k32.GetFileInformationByHandleEx, [h, ctypes.c_int, p, d], b)
        self.SetInfo = _sig(k32.SetFileInformationByHandle, [h, ctypes.c_int, p, d], b)
        self.GetFileInformationByHandle = _sig(k32.GetFileInformationByHandle, [h, p], b)
        self.GetFinalPathNameByHandleW = _sig(k32.GetFinalPathNameByHandleW, [h, wintypes.LPWSTR, d, d], d)
        self.GetVolumeInformationByHandleW = _sig(
            k32.GetVolumeInformationByHandleW,
            [h, wintypes.LPWSTR, d, ctypes.POINTER(d), ctypes.POINTER(d), ctypes.POINTER(d), wintypes.LPWSTR, d],
            b,
        )
        self.LockFileEx = _sig(k32.LockFileEx, [h, d, d, d, d, ctypes.POINTER(OVERLAPPED)], b)
        self.GetSystemTimeAsFileTime = _sig(k32.GetSystemTimeAsFileTime, [p], None)
        self.LocalFree = _sig(k32.LocalFree, [p], p)
        self.GetSecurityInfo = _sig(
            adv.GetSecurityInfo, [h, ctypes.c_int, d, p, p, ctypes.POINTER(p), p, ctypes.POINTER(p)], d
        )
        self.GetAce = _sig(adv.GetAce, [p, d, ctypes.POINTER(p)], b)
        self.SetSecurityInfo = _sig(adv.SetSecurityInfo, [h, ctypes.c_int, d, p, p, p, p], d)
        self.GetCurrentProcess = _sig(k32.GetCurrentProcess, [], h)
        self.OpenProcessToken = _sig(adv.OpenProcessToken, [h, d, ctypes.POINTER(h)], b)
        self.DuplicateToken = _sig(adv.DuplicateToken, [h, ctypes.c_int, ctypes.POINTER(h)], b)
        self.CreatePrivateObjectSecurityEx = _sig(
            adv.CreatePrivateObjectSecurityEx, [p, p, ctypes.POINTER(p), p, b, wintypes.ULONG, h, p], b
        )
        self.DestroyPrivateObjectSecurity = _sig(adv.DestroyPrivateObjectSecurity, [ctypes.POINTER(p)], b)
        self.AccessCheck = _sig(
            adv.AccessCheck, [p, h, d, p, p, ctypes.POINTER(d), ctypes.POINTER(d), ctypes.POINTER(b)], b
        )
        self.GetSecurityDescriptorControl = _sig(
            adv.GetSecurityDescriptorControl, [p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(d)], b
        )
        self.NtCreateFile = _sig(
            nt.NtCreateFile,
            [
                ctypes.POINTER(h),
                wintypes.ULONG,
                ctypes.POINTER(OBJECT_ATTRIBUTES),
                ctypes.POINTER(IO_STATUS_BLOCK),
                p,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.ULONG,
                p,
                wintypes.ULONG,
            ],
            ctypes.c_long,
        )
        self.NtSetInformationFile = _sig(
            nt.NtSetInformationFile,
            [h, ctypes.POINTER(IO_STATUS_BLOCK), p, wintypes.ULONG, ctypes.c_int],
            ctypes.c_long,
        )
        self.NtQueryVolumeInformationFile = _sig(
            nt.NtQueryVolumeInformationFile,
            [h, ctypes.POINTER(IO_STATUS_BLOCK), p, wintypes.ULONG, ctypes.c_int],
            ctypes.c_long,
        )


def _win_error(*what: str, code: int | None = None) -> OSError:
    """The last Win32 error (or `code`) as Python's `OSError`, its `errno` from Windows' own mapping; `what` names
    the call and the entry."""
    c = ctypes.get_last_error() if code is None else code
    return OSError(None, " ".join(what) + ": " + ctypes.FormatError(c).strip(), None, c)


def _read_error(*what: str, code: int | None = None) -> OSError:
    """`_win_error` of a read of a file's data (`ReadFile`), the one place a cloud-only file's download is met: a code
    of `CLOUD_DOWNLOAD_ERRORS` is `CloudUnavailable` (S0149, BL-0283). Every other call (an open, a create, a write, a
    rename, a flush, a folder's sync) keeps a cloud code as a plain `OSError`, told as any I/O error (round 1, V2)."""
    c = ctypes.get_last_error() if code is None else code
    if c in CLOUD_DOWNLOAD_ERRORS:
        return CloudUnavailable(None, *what, f"(Win32 {c})")
    return _win_error(*what, code=c)


def _nt_error(status: int, *what: str) -> OSError:
    """An NTSTATUS as `OSError`, its `errno` from `STATUS_ERRNO`; a sharing violation is `InUseError` (`EBUSY`:
    another program holds the entry; S0148, BL-0284). No NTSTATUS is a data read here (reads are `ReadFile`'s,
    `_read_error`), so a cloud filter status stays a plain `OSError` (S0149 round 1, V2)."""
    s = status & 0xFFFFFFFF
    if s == STATUS_SHARING_VIOLATION:
        return InUseError(*what, f"(NTSTATUS {s:#010x})")
    return OSError(STATUS_ERRNO.get(s, errno.EIO), " ".join(what) + f" (NTSTATUS {s:#010x})")


def _refuse(code: int, why: str, name: str | None = None) -> OSError:
    """An `OSError` with `errno` `code` (the adapter's own refusals, read as POSIX ones are)."""
    return OSError(code, why) if name is None else OSError(code, why, name)


def _close(api: _Api, handle: int) -> None:
    api.CloseHandle(handle)


def _vetted(h: _Handle, problem: Callable[[_Handle], OSError | None]) -> _Handle:
    """`h` when `problem(h)` finds nothing wrong with it; else `h` closed and the problem raised (an error asking
    closes it too)."""
    try:
        found = problem(h)
    except BaseException:
        h.close()
        raise
    if found is not None:
        h.close()
        raise found
    return h


def _not_regular(api: _Api, h: _Handle) -> OSError | None:
    attrs, tag = h.attrs_tag()
    if _kind(attrs, tag) is not Kind.FILE or api.GetFileType(h.handle) != FILE_TYPE_DISK:
        return _refuse(errno.EINVAL, "not a regular file")
    return None


def _streams(api: _Api, h: _Handle, flags: int) -> list[str]:
    """The names of the file's alternate data streams (`:name:$DATA`), its main stream left out; none, unasked, where
    the volume's `flags` lack `FILE_NAMED_STREAMS` (FAT32, exFAT). Elsewhere a refused listing raises (S0135)."""
    if not flags & FILE_NAMED_STREAMS:
        return []
    size = 64 * 1024
    while True:
        buf = ctypes.create_string_buffer(size)
        if api.GetInfo(h.handle, FILE_STREAM_INFO, buf, size):
            break
        code = ctypes.get_last_error()
        if code == ERROR_MORE_DATA and size < 1 << 24:
            size *= 4
            continue
        if code == ERROR_HANDLE_EOF:
            return []  # no stream at all
        raise _win_error("FileStreamInfo", code=code)
    raw = buf.raw
    out: list[str] = []
    at = 0
    while True:
        nxt, length = struct.unpack_from("<II", raw, at)
        stream = raw[at + 24 : at + 24 + length].decode("utf-16-le", "surrogatepass")
        if stream and stream != MAIN_STREAM:
            out.append(stream)
        if nxt == 0:
            return out
        at += nxt


def _carry_over(api: _Api, src: _Handle, new: _Handle) -> None:
    """The replaced file's own security and marks onto its new version (owner, 2026-11-17: "1a"; the counterpart of
    macOS keeping the mode bits), in this order: its alternate data streams, byte for byte; its hidden attribute
    (`KEPT_ATTRIBUTES`; not archive, which Windows sets on the saved file: owner, 2026-11-20); last its DACL, with its
    protected or inherited state (an explicit ACL that denies us writing applies only to later opens). Not its owner nor
    its SACL: setting either needs a privilege the user's process does not hold (the new file is owned by the user who
    saves, as on macOS). What is asked is decided by the volume's own flags (S0135, BL-0285; owner, 2026-11-20: "The
    drive's own flags (Recommended)"): streams only where it sets `FILE_NAMED_STREAMS`, the DACL only where it sets
    `FILE_PERSISTENT_ACLS` (FAT32 and exFAT set neither: the attributes only); where it sets one, any refusal raises, so
    the save is "not saved", never a file saved without its own ACL or streams."""
    flags = src.flags()
    for stream in _streams(api, src, flags):
        with (
            _nt_open(api, src.handle, stream, GENERIC_READ, FILE_OPEN, FILE_NON_DIRECTORY_FILE) as a,
            _nt_open(api, new.handle, stream, GENERIC_WRITE, FILE_CREATE, FILE_NON_DIRECTORY_FILE) as b,
        ):
            for chunk in a.chunks(1 << 62):
                b.write_all(chunk)
            b.flush()
    attrs = new.basic()[2]
    kept = (attrs & ~KEPT_ATTRIBUTES) | (src.basic()[2] & KEPT_ATTRIBUTES)
    info = struct.pack("<qqqqI4x", 0, 0, 0, 0, kept or FILE_ATTRIBUTE_NORMAL)  # 0 leaves a time as it is
    buf = ctypes.create_string_buffer(info, len(info))
    if not api.SetInfo(new.handle, FILE_BASIC_INFO, buf, len(info)):
        raise _win_error("SetFileInformationByHandle")
    if not flags & FILE_PERSISTENT_ACLS:
        return  # the volume keeps no ACLs
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = api.GetSecurityInfo(
        src.handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None, ctypes.byref(sd)
    )
    if rc != 0:
        raise _win_error("GetSecurityInfo", "the replaced file", code=rc)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not api.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise _win_error("GetSecurityDescriptorControl")
        state = (
            PROTECTED_DACL_SECURITY_INFORMATION
            if control.value & SE_DACL_PROTECTED
            else UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        rc = api.SetSecurityInfo(new.handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | state, None, None, dacl, None)
        if rc != 0:
            raise _win_error("SetSecurityInfo", code=rc)
    finally:
        api.LocalFree(sd)


def _a_link(h: _Handle, name: str) -> OSError | None:
    return _refuse(errno.ELOOP, "a link", name) if h.attrs_tag()[0] & FILE_ATTRIBUTE_REPARSE_POINT else None


def _not_a_folder(h: _Handle, path: str) -> OSError | None:
    return None if h.attrs_tag()[0] & FILE_ATTRIBUTE_DIRECTORY else _refuse(errno.ENOTDIR, "not a folder", path)


def _filetime_ns(raw: int) -> int:
    """A FILETIME (100 ns since 1601) as nanoseconds since the epoch."""
    return (raw - EPOCH_AS_FILETIME) * 100


class _Handle:
    """One open handle (a file or a folder), closed once."""

    __slots__ = ("api", "h")

    def __init__(self, api: _Api, h: int) -> None:
        self.api = api
        self.h: int | None = h

    @property
    def handle(self) -> int:
        if self.h is None:
            raise MalformedPathError("closed_handle")
        return self.h

    def close(self) -> None:
        if self.h is not None:
            _close(self.api, self.h)
            self.h = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def info(self, cls: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        if not self.api.GetInfo(self.handle, cls, buf, size):
            raise _win_error("GetFileInformationByHandleEx")
        return buf.raw

    def attrs_tag(self) -> tuple[int, int]:
        """The attributes and the reparse tag (0: none). The attributes from `FileBasicInfo` (FAT32 and exFAT refuse
        `FileAttributeTagInfo` for a drive's top level: the 2026-11-17 run 8), the tag only for a reparse point."""
        attrs = self.basic()[2]
        if not attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            return attrs, 0
        return attrs, struct.unpack_from("<I", self.info(FILE_ATTRIBUTE_TAG_INFO, 8), 4)[0]

    def basic(self) -> tuple[int, int, int]:
        """Creation time, last write time (FILETIME) and attributes."""
        raw = self.info(FILE_BASIC_INFO, 40)
        created, _, written, _ = struct.unpack_from("<qqqq", raw, 0)
        return created, written, struct.unpack_from("<I", raw, 32)[0]

    def links(self) -> int:
        return struct.unpack_from("<I", self.info(FILE_STANDARD_INFO, 24), 16)[0]

    def file_id_info(self) -> tuple[int, int] | None:
        """`FileIdInfo`'s `(64-bit volume serial, 128-bit file ID)`, or None where the volume has none (FAT32, exFAT:
        `INVALID_PARAMETER`, probed; a server that does not offer it: `INVALID_PARAMETER` or `NOT_SUPPORTED`)."""
        buf = ctypes.create_string_buffer(24)
        if self.api.GetInfo(self.handle, FILE_ID_INFO, buf, 24):
            return struct.unpack_from("<Q", buf.raw, 0)[0], int.from_bytes(buf.raw[8:24], "little")
        if ctypes.get_last_error() not in (ERROR_INVALID_PARAMETER, ERROR_NOT_SUPPORTED):
            raise _win_error("FileIdInfo")
        return None

    def identity(self) -> tuple[int, int]:
        """`(volume serial, file ID)`: `FileIdInfo` on NTFS and ReFS (64-bit serial, 128-bit ID); where a volume has
        none (FAT32, exFAT: `INVALID_PARAMETER`, probed), the 32-bit serial and the file index."""
        ids = self.file_id_info()
        if ids is not None:
            return ids
        info = ctypes.create_string_buffer(52)
        if not self.api.GetFileInformationByHandle(self.handle, info):
            raise _win_error("GetFileInformationByHandle")
        serial, _, _, _, high, low = struct.unpack_from("<IIIIII", info.raw, 28)
        return serial, (high << 32) | low

    def _volume_info(self) -> tuple[str, int, int]:
        fs = ctypes.create_unicode_buffer(261)
        serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        if not self.api.GetVolumeInformationByHandleW(
            self.handle, None, 0, ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags), fs, 261
        ):
            raise _win_error("GetVolumeInformationByHandleW")
        return fs.value.lower(), serial.value, flags.value

    def volume(self) -> tuple[str, int]:
        """The file system's name in lower case and the volume's 32-bit serial."""
        fs, serial, _ = self._volume_info()
        return fs, serial

    def flags(self) -> int:
        """The volume's file-system flags (`FILE_SUPPORTS_*`)."""
        return self._volume_info()[2]

    def remote(self) -> bool:
        buf = ctypes.create_string_buffer(8)
        io = IO_STATUS_BLOCK()
        status = self.api.NtQueryVolumeInformationFile(
            self.handle, ctypes.byref(io), buf, 8, FILE_FS_DEVICE_INFORMATION
        )
        if status != 0:
            raise _nt_error(status, "NtQueryVolumeInformationFile")
        return bool(struct.unpack_from("<I", buf.raw, 4)[0] & FILE_REMOTE_DEVICE)

    def final_path(self) -> str:
        """The handle's path now, in the path model's form (the module's docstring, "Paths")."""
        buf = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
        n = self.api.GetFinalPathNameByHandleW(self.handle, buf, MAX_PATH_CHARS, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)
        if n == 0 or n >= MAX_PATH_CHARS:
            if ctypes.get_last_error() != ERROR_PATH_NOT_FOUND:
                raise _win_error("GetFinalPathNameByHandleW")
            n = self.api.GetFinalPathNameByHandleW(
                self.handle, buf, MAX_PATH_CHARS, FILE_NAME_NORMALIZED | VOLUME_NAME_GUID
            )
            if n == 0 or n >= MAX_PATH_CHARS:
                raise _win_error("GetFinalPathNameByHandleW")
        model = host()
        return model.join(model.split(buf.value))

    def flush(self) -> None:
        if not self.api.FlushFileBuffers(self.handle):
            raise _win_error("FlushFileBuffers")

    def read_all(self, limit: int | None) -> bytes:
        chunks: list[bytes] = []
        left = limit
        buf = ctypes.create_string_buffer(CHUNK)
        got = wintypes.DWORD()
        while left is None or left > 0:
            want = CHUNK if left is None else min(CHUNK, left)
            if not self.api.ReadFile(self.handle, buf, want, ctypes.byref(got), None):
                raise _read_error("ReadFile")
            if got.value == 0:
                break
            chunks.append(buf.raw[: got.value])
            if left is not None:
                left -= got.value
        return b"".join(chunks)

    def chunks(self, limit: int) -> Iterable[bytes]:
        buf = ctypes.create_string_buffer(CHUNK)
        got = wintypes.DWORD()
        n = 0
        while n < limit:
            if not self.api.ReadFile(self.handle, buf, min(CHUNK, limit - n), ctypes.byref(got), None):
                raise _read_error("ReadFile")
            if got.value == 0:
                return
            n += got.value
            yield buf.raw[: got.value]

    def write_all(self, data: bytes) -> None:
        view = memoryview(data)
        done = wintypes.DWORD()
        while view:
            piece = view[: 1 << 30]
            src = (ctypes.c_char * len(piece)).from_buffer_copy(piece)
            if not self.api.WriteFile(self.handle, src, len(piece), ctypes.byref(done), None):
                raise _win_error("WriteFile")
            view = view[done.value :]

    def set_info(self, cls: int, data: bytes) -> int:
        """`NtSetInformationFile`; its status (0: done)."""
        buf = ctypes.create_string_buffer(data, len(data))
        io = IO_STATUS_BLOCK()
        return int(self.api.NtSetInformationFile(self.handle, ctypes.byref(io), buf, len(data), cls))


def _open_path(api: _Api, path: str, access: int, flags: int) -> _Handle:
    """`CreateFileW` of an absolute path (with the long-path prefix), following links unless `flags` says otherwise."""
    h = api.CreateFileW(host().extended(path), access, FILE_SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise _win_error("open", path)
    return _Handle(api, int(h))


def _nt_open(
    api: _Api, root: int, name: str, access: int, disposition: int, options: int, share: int = FILE_SHARE_ALL
) -> _Handle:
    """`NtCreateFile` of `name` (one name, checked; "" is the folder `root` itself) relative to the folder handle
    `root`. Raises `OSError` with the status's `errno`."""
    text = ctypes.create_unicode_buffer(name)
    us = UNICODE_STRING(len(name) * 2, len(name) * 2 + 2, ctypes.cast(text, ctypes.c_void_p))
    oa = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), root, ctypes.pointer(us), 0, None, None)
    io = IO_STATUS_BLOCK()
    h = wintypes.HANDLE()
    status = api.NtCreateFile(
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
        raise _nt_error(status, "open", name or ".")
    return _Handle(api, int(h.value or 0))


def _kind(attrs: int, tag: int) -> Kind:
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return Kind.LINK if tag in LINK_TAGS else Kind.OTHER
    return Kind.DIR if attrs & FILE_ATTRIBUTE_DIRECTORY else Kind.FILE


def _rename_info(replace: bool | int, root: int, name: str) -> bytes:
    """A `FILE_RENAME_INFORMATION`: `ReplaceIfExists` (or, for `FileRenameInformationEx`, its flags in the same
    place), the root folder handle, the name's length in bytes, the name."""
    raw = name.encode("utf-16-le")
    return struct.pack("<I4xQI", int(replace), root, len(raw)) + raw + b"\0\0"


class WindowsDir:
    """A handle on one folder (`ledger_engine.fs.port.DirHandle`)."""

    def __init__(self, fs: WindowsFS, handle: _Handle) -> None:
        self._fs = fs
        self._h = handle

    @property
    def api(self) -> _Api:
        return self._fs.api

    @property
    def handle(self) -> int:
        return self._h.handle

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._h.close()

    def _open(self, name: str, access: int, disposition: int = FILE_OPEN, options: int = 0) -> _Handle:
        return _nt_open(self.api, self.handle, name, access, disposition, options | FILE_OPEN_REPARSE_POINT)

    def _reopen(self, access: int) -> _Handle:
        """This folder opened again with `access` (relative to its own handle: the same object)."""
        return _nt_open(self.api, self.handle, "", access, FILE_OPEN, FILE_DIRECTORY_FILE)

    def path(self) -> str:
        return self._h.final_path()

    def device(self) -> int:
        return self._h.identity()[0]

    def remote(self) -> bool:
        """`FILE_REMOTE_DEVICE` (S0134; probed in S0132: True on the CI share by its mapped drive and its UNC paths,
        False on NTFS, ReFS, FAT32 and exFAT)."""
        return self._h.remote()

    def identity(self) -> tuple[int, int]:
        return self._h.identity()

    def fs_type(self) -> str:
        return self._h.volume()[0]

    def volume_id(self) -> str | None:
        """The drive ID (the module's docstring, "Identity"), on a network share too: the server volume's serial, as
        `FileIdInfo` gives it there (S0136, BL-0219; owner, 2026-11-20: "Best effort + open notice (Recommended)"):
        probed equal through a mapped drive, every UNC spelling and the server's own local path, and stable across a
        remap and a restart of the file server (the 2026-11-20 runs 2 and 3). On a share only `FileIdInfo`'s serial
        counts: a server that does not offer it has no drive ID here (`no_drive_id`; S0136 round 1, verifier F1: the
        32-bit fallback would name it by a serial a local NTFS volume's 64-bit one could equal)."""
        fs_type, serial32 = self._h.volume()
        if self._h.remote():
            ids = self._h.file_id_info()
            if ids is None or ids[0] == 0 or fs_type not in FS_SERIAL64 | FS_SERIAL32:
                return None
            return str(uuid.uuid5(SERIAL_NAMESPACE, f"{ids[0]:016X}"))
        if fs_type in FS_SERIAL64:
            serial = self._h.identity()[0]
            name = f"{serial:016X}"
        elif fs_type in FS_SERIAL32:
            serial = serial32
            name = f"{serial >> 16:04X}-{serial & 0xFFFF:04X}"
        else:
            return None
        return None if serial == 0 else str(uuid.uuid5(SERIAL_NAMESPACE, name))

    def born(self) -> int | None:
        """The folder's identity within its drive (the module's docstring, "Identity"): the file ID on NTFS and ReFS,
        the creation time on FAT32 and exFAT. On a network share it is what the server gives (S0136 round 1, L3):
        Windows' own server gives NTFS's file ID; Samba or a NAS may give one it reuses, so a folder removed and made
        again at once may not be told apart there (engine-jobs §6.3, "Limit (a share's file ID)").
        It stays in the folder mark: leaving it out would refuse every save on a share (`no_folder_id`)."""
        fs_type = self.fs_type()
        if fs_type in FS_SERIAL64:
            return self._h.identity()[1]  # the file ID: the creation time is tunnelled (the module's docstring)
        if fs_type in FS_SERIAL32:
            created = self._h.basic()[0]
            return 0 if created == 0 else _filetime_ns(created)  # 0: a FAT or exFAT drive's top level
        return None

    def mark(self) -> FolderMark:
        return FolderMark(self.volume_id(), self.fs_type(), self.born())

    def _regular(self, name: str, access: int) -> _Handle:
        """A handle on the regular file `name`, never through a link; anything else raises `OSError`."""
        h = self._open(check_name(name), access, options=FILE_NON_DIRECTORY_FILE)
        return _vetted(h, lambda x: _not_regular(self.api, x))

    def read(self, name: str, limit: int | None = None) -> bytes | None:
        try:
            h = self._regular(name, GENERIC_READ)
        except FileNotFoundError:
            return None
        with h:
            return h.read_all(limit)

    def read_identified(self, name: str) -> tuple[bytes, tuple[int, int]] | None:
        try:
            h = self._regular(name, GENERIC_READ)
        except FileNotFoundError:
            return None
        with h:
            return h.read_all(None), h.identity()

    def stat(self, name: str) -> EntryStat | None:
        try:
            h = self._open(check_name(name), FILE_READ_ATTRIBUTES)
        except FileNotFoundError:
            return None
        with h:
            attrs, tag = h.attrs_tag()
            _, written, _ = h.basic()
            dev, ino = h.identity()
            kind = _kind(attrs, tag)
            return EntryStat(
                kind=kind,
                dev=dev,
                ino=ino,
                nlink=h.links(),
                mode=0o444 if attrs & FILE_ATTRIBUTE_READONLY else 0o666,
                locked=bool(attrs & FILE_ATTRIBUTE_READONLY) and kind is Kind.FILE,
                mtime_ns=_filetime_ns(written),
                encrypted=bool(attrs & FILE_ATTRIBUTE_ENCRYPTED),
            )

    def sync_file(self, name: str) -> tuple[int, int]:
        with self._regular(name, GENERIC_WRITE | FILE_READ_ATTRIBUTES) as h:  # FlushFileBuffers needs write access
            ident = h.identity()
            h.flush()
            return ident

    def sha256(self, name: str, limit: int) -> tuple[str, int] | None:
        try:
            h = self._regular(name, GENERIC_READ)
        except FileNotFoundError:
            return None
        with h:
            digest = hashlib.sha256()
            n = 0
            for chunk in h.chunks(limit):
                digest.update(chunk)
                n += len(chunk)
            return digest.hexdigest(), n

    def create(self, name: str, data: bytes | Iterable[bytes], mode: int, like: str | None = None) -> tuple[int, int]:
        """`FILE_CREATE` (fails on any entry, a link included), the data written and flushed through the same handle.
        `mode` gives nothing on Windows. With `like` (a replaced file, S0128 round 1; owner, 2026-11-17: "1a"), the
        new file takes the old one's own security and marks before the rename puts it at the name (`_carry_over`)."""
        del mode
        access = GENERIC_WRITE | FILE_READ_ATTRIBUTES | (READ_CONTROL | WRITE_DAC if like is not None else 0)
        h = self._open(check_name(name), access, FILE_CREATE, options=FILE_NON_DIRECTORY_FILE)
        with h:
            for piece in (data,) if isinstance(data, bytes) else data:
                h.write_all(piece)
            if like is not None:
                with self._regular(like, READ_CONTROL | GENERIC_READ) as src:
                    _carry_over(self.api, src, h)
            h.flush()
            return h.identity()

    def allowed(self, name: str, right: Right) -> bool:
        """Windows' own answer: the entry opened with the access `right` needs (the module's docstring). A sharing
        violation is no answer: `OSError` `EBUSY` (another program holds the file). `REMOVE_ENTRIES` of this folder:
        `_may_remove_entries` (S0151)."""
        if name == "." and right is Right.DELETE:
            return False  # a folder's own removal is its parent's to judge; never asked of `.` (fails closed)
        if name == "." and right is Right.REMOVE_ENTRIES:
            return self._may_remove_entries()
        access = RIGHT_ACCESS[right]
        if name != "." and right is Right.READ_XATTRS:
            access = READ_CONTROL | GENERIC_READ  # what `create(..., like=...)` reads: the DACL and the streams
        try:
            if name == ".":
                self._reopen(access).close()
            else:
                self._open(check_name(name), access).close()
        except (PermissionError, FileNotFoundError):
            return False
        return True

    def _may_remove_entries(self) -> bool:
        """Whether the entries the save makes in this folder may be removed and renamed away, as Windows decides it
        (S0151; owner, 2026-11-23: "1"; `Right.REMOVE_ENTRIES`): Windows grants deleting an entry when the folder
        grants `FILE_DELETE_CHILD` **or** the entry grants `DELETE`. So: the folder opened with `FILE_DELETE_CHILD`
        and traverse, as before; where that is refused (OneDrive denies `delete_child` to every folder in it, probed
        on the owner's PC, 2026-11-23), the folder opened for traverse and `_new_entries_deletable`. A DACL we may not
        read answers no (fails closed); any other refusal raises."""
        try:
            self._reopen(FILE_DELETE_CHILD | FILE_TRAVERSE).close()
        except (PermissionError, FileNotFoundError):
            pass
        else:
            return True
        try:
            self._reopen(FILE_TRAVERSE).close()
            return self._new_entries_deletable()
        except (PermissionError, FileNotFoundError):
            return False

    def _new_entries_deletable(self) -> bool:
        """Whether every file and folder made here, and in folders made here (`NEW_ENTRY_DEPTH`), would grant us
        `DELETE`: the security Windows would give each (`CreatePrivateObjectSecurityEx` from this folder's, chained
        through the new folders, with this process's token: what NTFS assigns a new entry with no security of its
        own), asked with `AccessCheck`. Asking writes nothing. A volume that keeps no ACL (FAT32, exFAT) has no
        entry that could grant it: no."""
        if not self._h.flags() & FILE_PERSISTENT_ACLS:
            return False
        api = self.api
        with self._reopen(READ_CONTROL) as h:
            sd = ctypes.c_void_p()
            rc = api.GetSecurityInfo(
                h.handle,
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                None,
                None,
                None,
                None,
                ctypes.byref(sd),
            )
            if rc != 0:
                raise _win_error("GetSecurityInfo", "the folder", code=rc)
        made: list[ctypes.c_void_p] = []
        try:
            with _impersonation_token(api) as token:
                mapping = (wintypes.DWORD * 4)(*FILE_GENERIC_MAPPING)
                level = sd
                for depth in range(NEW_ENTRY_DEPTH + 1):
                    kinds = (False,) if depth == NEW_ENTRY_DEPTH else (False, True)
                    for container in kinds:
                        new = ctypes.c_void_p()
                        if not api.CreatePrivateObjectSecurityEx(
                            level, None, ctypes.byref(new), None, container, SEF_NEW_ENTRY, token, mapping
                        ):
                            raise _win_error("CreatePrivateObjectSecurityEx")
                        made.append(new)
                        if not _grants(api, new, token, DELETE, mapping):
                            return False
                    level = made[-1]  # the folder made at this depth
                return True
        finally:
            for new in made:
                api.DestroyPrivateObjectSecurity(ctypes.byref(new))
            api.LocalFree(sd)

    def caps(self) -> Caps:
        return Caps(swap=False, excl=self.device() not in self._fs.no_excl, links=False, delete_by_entry=True)

    def acl(self) -> tuple[AclEntry, ...]:
        """The folder's DACL entries in order (none: empty). A volume whose flags lack `FILE_PERSISTENT_ACLS` (FAT32,
        exFAT) keeps none: empty, unasked (S0135). Elsewhere a DACL we may not read raises `PermissionError`
        (`handles.UNREADABLE_ACL`), and any other refusal `OSError` (the folder cannot be judged: the save stops)."""
        if not self._h.flags() & FILE_PERSISTENT_ACLS:
            return ()
        with self._reopen(READ_CONTROL) as h:
            dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
            rc = self.api.GetSecurityInfo(
                h.handle,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                None,
                None,
                ctypes.byref(dacl),
                None,
                ctypes.byref(sd),
            )
            if rc != 0:
                raise _win_error("GetSecurityInfo", "the folder", code=rc)
            try:
                if not dacl.value:
                    return ()  # no DACL: every access granted, nothing denied
                count = struct.unpack_from("<H", ctypes.string_at(dacl.value, 8), 4)[0]
                out: list[AclEntry] = []
                for i in range(count):
                    ace = ctypes.c_void_p()
                    if not self.api.GetAce(dacl, i, ctypes.byref(ace)):
                        raise _win_error("GetAce")
                    kind, flags, _, mask = struct.unpack_from("<BBHI", ctypes.string_at(ace.value or 0, 8), 0)
                    out.append(_ace(kind, flags, mask))
                return tuple(out)
            finally:
                self.api.LocalFree(sd)

    def swap(self, a: str, b: str) -> None:
        raise NotOffered(EXCHANGE)

    def _rename(self, src: str, dst: str, *, replace: bool) -> None:
        with self._open(check_name(src), DELETE, options=FILE_WRITE_THROUGH) as h:
            status = h.set_info(NT_FILE_RENAME_INFORMATION, _rename_info(replace, self.handle, check_name(dst)))
        if status != 0:
            if not replace and status & 0xFFFFFFFF in REFUSED_STATUS:
                self._fs.no_excl.add(self.device())
                raise NotOffered(EXCLUSIVE_RENAME)
            if replace and status & 0xFFFFFFFF == STATUS_ACCESS_DENIED:
                raise ReplaceDenied("rename", src, dst, f"(NTSTATUS {status & 0xFFFFFFFF:#010x})")
            raise _nt_error(status, "rename", src, dst)

    def rename_shared(self, src: str, dst: str) -> None:
        """The rename with POSIX semantics (`FileRenameInformationEx`, `REPLACE_IF_EXISTS | POSIX_SEMANTICS`),
        relative to the folder handle: it replaces `dst` even while readers that share deletion hold it, which the
        plain rename refuses (probed, S0130's P3, P5). A reader holding `dst` without sharing deletion still refuses
        it, and so does a read-only `dst` (the publish clears that first: `clear_readonly`). Offered by what the
        volume offers (owner, 2026-11-18; ADR-0015 amendment; S0132, BL-0294): not a network share (`remote()`,
        asked first) and the flag `FILE_SUPPORTS_POSIX_UNLINK_RENAME`; else `NotOffered` before any call, nothing
        done, never decided by the file system's name or by an error."""
        if self._h.remote() or not self._h.flags() & FILE_SUPPORTS_POSIX_UNLINK_RENAME:
            raise NotOffered(POSIX_RENAME)
        flags = FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS
        with self._open(check_name(src), DELETE, options=FILE_WRITE_THROUGH) as h:
            status = h.set_info(NT_FILE_RENAME_INFORMATION_EX, _rename_info(flags, self.handle, check_name(dst)))
        if status != 0:
            raise _nt_error(status, "rename", src, dst)

    def clear_readonly(self, name: str) -> None:
        """The read-only attribute of the entry `name` (a file, a folder or a link itself: opened with
        `FILE_OPEN_REPARSE_POINT`, never followed) cleared, through handles that share everything, so a reader that
        holds it (SQLite, the pin, another program's plain open) never refuses it (probed, S0132's P7). Asked first for
        the attributes only; written (`FileBasicInfo`, times left as they are) only when the bit is set. No entry, or
        an entry gone between the two opens: nothing done. **A file with several links is never cleared** (owner,
        2026-11-19: "Never clear a linked file (Recommended)"; S0132 round 1, verifier V1, red team F1): NTFS keeps the
        attribute on the file, not the name, so clearing it through a link in the mirror folder would unprotect the
        file the other name leads to (a read-only ledger file, whose save then no longer answers `locked`). The link
        count is read on the handle that would write the attributes; above 1, `OSError` (`EMLINK`), nothing done, which
        the cleanup counts as a removal that failed. The mirror's publish and cleanup only (BL-0294): `remove` and
        `rename` still refuse a read-only file, which the save's `locked` outcome relies on."""
        target = check_name(name)
        try:
            with self._open(target, FILE_READ_ATTRIBUTES) as h:
                attrs = h.basic()[2]
        except FileNotFoundError:
            return
        if not attrs & FILE_ATTRIBUTE_READONLY:
            return
        try:
            h = self._open(target, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES)
        except FileNotFoundError:
            return  # gone since the first open (S0132 round 1, red team F4)
        with h:
            attrs = h.basic()[2]
            if not attrs & FILE_ATTRIBUTE_READONLY:
                return
            if not attrs & FILE_ATTRIBUTE_DIRECTORY and h.links() > 1:
                raise OSError(errno.EMLINK, "a file with several links: its read-only attribute is not ours", name)
            kept = attrs & FILE_ATTRIBUTE_VALID_SET_FLAGS & ~FILE_ATTRIBUTE_READONLY
            info = struct.pack("<qqqqI4x", 0, 0, 0, 0, kept or FILE_ATTRIBUTE_NORMAL)  # 0 leaves a time as it is
            buf = ctypes.create_string_buffer(info, len(info))
            if not self.api.SetInfo(h.handle, FILE_BASIC_INFO, buf, len(info)):
                raise _win_error("clear_readonly", name)

    def rename_excl(self, src: str, dst: str) -> None:
        if self.device() in self._fs.no_excl:
            raise NotOffered(EXCLUSIVE_RENAME)
        self._rename(src, dst, replace=False)

    def rename(self, src: str, dst: str) -> None:
        """One plain rename over `dst` (ADR-0069's replace: `ReplaceIfExists`); a `dst` another program holds open
        without sharing deletion refuses it, with nothing done (probed)."""
        self._rename(src, dst, replace=True)

    def _delete(self, h: _Handle, what: str) -> None:
        """Mark the open entry deleted: POSIX semantics where the volume has them, else delete-on-close."""
        status = h.set_info(
            NT_FILE_DISPOSITION_INFORMATION_EX,
            struct.pack("<I", FILE_DISPOSITION_DELETE | FILE_DISPOSITION_POSIX_SEMANTICS),
        )
        if status & 0xFFFFFFFF in (*REFUSED_STATUS, 0xC000000D):
            status = h.set_info(NT_FILE_DISPOSITION_INFORMATION, struct.pack("<B", 1))
        if status != 0:
            raise _nt_error(status, what)

    def remove(self, name: str) -> None:
        with self._open(check_name(name), DELETE | FILE_READ_ATTRIBUTES) as h:
            attrs, tag = h.attrs_tag()
            if _kind(attrs, tag) is Kind.DIR:
                raise IsADirectoryError(errno.EISDIR, "a folder", name)
            self._delete(h, "remove " + name)

    def mkdir(self, name: str, mode: int = 0o755) -> None:
        del mode  # no mode bits on Windows: the folder inherits its parent's ACL
        self._open(check_name(name), FILE_LIST_DIRECTORY, FILE_CREATE, FILE_DIRECTORY_FILE).close()

    def symlink(self, target: str, name: str) -> None:
        check_name(target)
        check_name(name)
        raise OSError(errno.ENOTSUP, "no links are made on Windows (the mirror's pointer file: S0130)")

    def readlink(self, name: str) -> str | None:
        st = self.stat(name)
        if st is None:
            return None
        if st.kind is not Kind.LINK:
            raise OSError(errno.EINVAL, "not a link")
        raise OSError(errno.ENOTSUP, "link targets are not read on Windows (the mirror's pointer file: S0130)")

    def touch(self, name: str) -> None:
        now = ctypes.c_ulonglong()
        self.api.GetSystemTimeAsFileTime(ctypes.byref(now))
        info = struct.pack("<qqqqI4x", 0, 0, now.value, 0, 0)  # only the last write time; 0 leaves a field as it is
        with self._open(check_name(name), FILE_WRITE_ATTRIBUTES) as h:
            buf = ctypes.create_string_buffer(info, len(info))
            if not self.api.SetInfo(h.handle, FILE_BASIC_INFO, buf, len(info)):
                raise _win_error("touch", name)

    def open_child(self, name: str) -> WindowsDir:
        target = check_name(name)
        try:
            h = self._open(target, DIR_ACCESS, options=FILE_DIRECTORY_FILE)
        except NotADirectoryError:
            st = self.stat(target)
            if st is not None and st.kind is Kind.LINK:
                raise OSError(errno.ELOOP, "a link", name) from None  # a link to a file, as `O_NOFOLLOW` says
            raise
        return WindowsDir(self._fs, _vetted(h, lambda x: _a_link(x, name)))  # never followed (`O_NOFOLLOW`)

    def rule(self) -> FoldRule:
        """Ordinal upper case, except an NTFS folder with its case-sensitive flag (the module's docstring); an answer
        that cannot be read counts as upper case (folding may merge names, never split them, §15)."""
        try:
            if self.fs_type() != "ntfs":
                return UPPER
            flags = struct.unpack_from("<I", self._h.info(FILE_CASE_SENSITIVE_INFO, 4), 0)[0]
        except OSError:
            return UPPER
        return EXACT if flags & 1 else UPPER

    def rmdir(self, name: str) -> None:
        with self._open(check_name(name), DELETE | FILE_READ_ATTRIBUTES, options=FILE_DIRECTORY_FILE) as h:
            attrs, _ = h.attrs_tag()
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                raise NotADirectoryError(errno.ENOTDIR, "a link", name)  # never followed, as `unlinkat` answers
            self._delete(h, "rmdir " + name)

    def names(self) -> list[str]:
        out: list[str] = []
        size = 64 * 1024
        buf = ctypes.create_string_buffer(size)
        cls = FILE_FULL_DIRECTORY_RESTART_INFO
        while True:
            if not self.api.GetInfo(self.handle, cls, buf, size):
                if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
                    break
                raise _win_error("GetFileInformationByHandleEx")
            cls = FILE_FULL_DIRECTORY_INFO
            raw = buf.raw
            at = 0
            while True:
                (nxt,) = struct.unpack_from("<I", raw, at)
                (length,) = struct.unpack_from("<I", raw, at + 60)
                n = raw[at + 68 : at + 68 + length].decode("utf-16-le", "surrogatepass")
                if n not in (".", ".."):
                    out.append(n)
                if nxt == 0:
                    break
                at += nxt
        return sorted(out)

    def sync(self) -> None:
        """`FlushFileBuffers` on the folder, opened again for writing (probed: a read-only handle is refused). A
        network share's server answers `ERROR_INVALID_FUNCTION` (probed, S0132's 2026-11-18 run 23, the 2026-11-20
        runs 2 and 3): `SyncRefused`, only when the volume is remote; any other failure, and that code on a local
        volume, raises as it is (S0136)."""
        with self._reopen(FILE_ADD_FILE) as h:  # FlushFileBuffers needs a handle opened for writing (probed)
            if self.api.FlushFileBuffers(h.handle):
                return
            code = ctypes.get_last_error()
            if code == ERROR_INVALID_FUNCTION and self._h.remote():
                raise SyncRefused
            raise _win_error("FlushFileBuffers", code=code)


@contextlib.contextmanager
def _impersonation_token(api: _Api) -> Generator[int]:
    """This process's token, duplicated at the impersonation level `AccessCheck` asks for; closed after."""
    own, dup = wintypes.HANDLE(), wintypes.HANDLE()
    if not api.OpenProcessToken(api.GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, ctypes.byref(own)):
        raise _win_error("OpenProcessToken")
    try:
        if not api.DuplicateToken(own, SECURITY_IMPERSONATION, ctypes.byref(dup)):
            raise _win_error("DuplicateToken")
    finally:
        api.CloseHandle(own)
    try:
        yield dup.value or 0
    finally:
        api.CloseHandle(dup)


def _grants(api: _Api, sd: ctypes.c_void_p, token: int, access: int, mapping: ctypes.Array[wintypes.DWORD]) -> bool:
    """`AccessCheck`: whether the security descriptor `sd` grants `token` the access `access`."""
    privileges = ctypes.create_string_buffer(256)
    size, granted, status = wintypes.DWORD(len(privileges)), wintypes.DWORD(), wintypes.BOOL()
    if not api.AccessCheck(
        sd, token, access, mapping, privileges, ctypes.byref(size), ctypes.byref(granted), ctypes.byref(status)
    ):
        raise _win_error("AccessCheck")
    return bool(status.value)


def _ace(kind: int, flags: int, mask: int) -> AclEntry:
    perms = {word for bit, word in PERMS if mask & bit}
    named = 0
    for bit, _ in PERMS:
        named |= bit
    if mask & ~named:
        perms.add("unknown")
    fl = {word for bit, word in ACE_FLAGS if flags & bit}
    if flags & INHERITED_ACE:
        fl.add("inherited")
    if flags & ~(0x1F):
        fl.add("unknown")
    return AclEntry(deny=kind not in ALLOW_ACE_TYPES, perms=frozenset(perms), flags=frozenset(fl))


class WindowsFS:
    """The real file system on Windows (`ledger_engine.fs.port.FileSystem`). `on_fallback` is never called: Windows'
    flush has no weaker fallback to report (§6.2)."""

    def __init__(self, on_fallback: Callable[[], None] | None = None) -> None:
        self.api = _Api()
        self._on_fallback = on_fallback
        self.no_excl: set[int] = set()  # volumes (serials) that refused the exclusive rename in this job (INV-10)

    def same_path(self, now: str, recorded: str) -> bool:
        return host().same_path(now, recorded)

    def open_dir(self, path: str) -> WindowsDir:
        host().split(path)
        try:
            h = _open_path(self.api, path, DIR_ACCESS, FILE_FLAG_BACKUP_SEMANTICS)
        except PermissionError:
            h = _open_path(
                self.api, path, FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_FLAG_BACKUP_SEMANTICS
            )
        return WindowsDir(self, _vetted(h, lambda x: _not_a_folder(x, path)))

    def rule_at(self, folder: str) -> FoldRule:
        try:
            with self.open_dir(folder) as d:
                return d.rule()
        except OSError:
            return UPPER

    def resolve(self, path: str) -> Resolved:
        """`FileSystem.resolve`: the deepest ancestor that exists (opened for its attributes only, following links:
        nothing is read, a cloud placeholder is not downloaded; a dangling link counts as absent, its own name as the
        first missing component), its canonical path (`final_path`; for a file, its folder's and the entry's stored
        name, checked against the object found) and that object's identity."""
        model = host()
        parts = model.split(path)
        cut = len(parts)
        while True:
            probe = model.join(parts[:cut])
            try:
                h = _open_path(self.api, probe, FILE_READ_ATTRIBUTES, FILE_FLAG_BACKUP_SEMANTICS)
            except OSError as e:
                if cut > 1 and (
                    isinstance(e, FileNotFoundError | NotADirectoryError) or e.winerror in NOT_FOUND_ERRORS
                ):
                    cut -= 1
                    continue
                raise
            break
        cache: dict[str, FoldRule] = {}

        def rule_of(folder: str) -> FoldRule:
            if folder not in cache:
                cache[folder] = self.rule_at(folder)
            return cache[folder]

        with h:
            attrs, _ = h.attrs_tag()
            ident = h.identity()
            is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
            canonical = h.final_path() if is_dir else self._file(probe, ident, rule_of)
        existing = model.split(canonical)
        rest = parts[cut:]
        rules = [UPPER] + [rule_of(model.join(existing[:i])) for i in range(1, len(existing))]
        if rest:
            holder = canonical if is_dir else model.join(existing[:-1])
            rules.extend(rule_of(holder) for _ in rest)
        return Resolved(existing=existing, rest=rest, rules=tuple(rules), identity=ident)

    def _file(self, path: str, ident: tuple[int, int], rule_of: Callable[[str], FoldRule]) -> str:
        """The canonical path of the file `path` names (found with the identity `ident`): when its last name is not a
        link, its folder's canonical path and the entry's stored name, read from the folder's listing (never the
        file's own handle path: with several hard links it may name another); through a link, the path Windows gives
        the object, checked to name it in its folder."""
        model = host()
        head, name = model.parent_and_name(path)
        with self.open_dir(head) as d:
            st = d.stat(name)
            if st is not None and st.kind is not Kind.LINK:
                folder = d.path()
                rule = rule_of(folder)
                want = fold(name, rule)
                names = d.names()
                same = [n for n in names if fold(n, rule) == want and _names(d, n, ident)]
                if not same:  # an 8.3 short name (the 2026-11-17 run 8): the one entry that holds the object
                    same = [n for n in names if _names(d, n, ident)]
                if len(same) != 1:
                    raise OSError(errno.ENOENT, "the entry's stored name is not one name")
                return model.child(folder, same[0])
        with _open_path(self.api, path, FILE_READ_ATTRIBUTES, FILE_FLAG_BACKUP_SEMANTICS) as h:
            if h.identity() != ident:
                raise OSError(errno.ESTALE, "the path changed while it was resolved")
            final = h.final_path()
        head, name = model.parent_and_name(final)
        with self.open_dir(head) as d:
            st = d.stat(name)
            if st is None or st.identity != ident:
                raise OSError(errno.ESTALE, "the path changed while it was resolved")
        return final


def _names(d: WindowsDir, name: str, ident: tuple[int, int]) -> bool:
    """Whether the entry `name` of `d` is the object `ident` (never through a link)."""
    st = d.stat(name)
    return st is not None and st.identity == ident


class WindowsLock:
    """A legacy write lock held by this process (`WindowsLegacyLocks`): closing the handle releases it."""

    def __init__(self, handle: _Handle) -> None:
        self._h: _Handle | None = handle

    def release(self) -> None:
        if self._h is not None:
            self._h.close()
            self._h = None


class WindowsLegacyLocks:
    """The legacy write lock (`ledger_engine.fs.port.LegacyLocks`; ADR-0062 decisions 2 and 5; INV-26) on Windows:
    the file opened for reading only, never through a link, only if it exists (v0.4 never creates it), then
    `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY)` over the first 64 KiB, the range portalocker
    3.2.0's default Windows locker takes (`MsvcrtLocker`: `msvcrt.locking(fd, LK_NBLCK, 0x10000)` at offset 0; its
    shared lock, `Win32Locker`, covers that range too). `ERROR_LOCK_VIOLATION` is held; any other error raises (Copy
    refuses: fail closed)."""

    def try_lock(self, path: str) -> WindowsLock | LegacyAbsent | LegacyHeld:
        host().split(path)
        api = _Api()
        try:
            h = _open_path(api, path, GENERIC_READ, FILE_FLAG_OPEN_REPARSE_POINT)
        except OSError as e:
            if isinstance(e, FileNotFoundError) or e.winerror in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                return LegacyAbsent()
            raise
        _vetted(h, lambda x: _not_regular(api, x))
        if api.LockFileEx(
            h.handle, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0, LEGACY_LOCK_BYTES, 0, OVERLAPPED()
        ):
            return WindowsLock(h)  # nothing is written or removed
        code = ctypes.get_last_error()
        h.close()
        if code == ERROR_LOCK_VIOLATION:
            return LegacyHeld()
        raise _win_error("LockFileEx", code=code)


def take_engine_lock(app: WindowsDir, locks: str, name: str) -> None:
    """The engine lock `<app folder>/<locks>/<name>` (engine-jobs §5.1): `locks` made in the app folder's handle if
    missing (never the app folder itself), the lock file opened or made in it, each relative to the handle above it and
    never through a link, then `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK)` over the whole file, waiting. The handle is never
    closed: the lock is held for the life of the process."""
    with contextlib.suppress(FileExistsError):
        app.mkdir(locks)
    with app.open_child(locks) as folder:
        h = _nt_open(
            app.api,
            folder.handle,
            check_name(name),
            GENERIC_READ | GENERIC_WRITE,
            FILE_OPEN_IF,
            FILE_NON_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT,
            share=FILE_SHARE_READ_WRITE,  # never delete: no program removes the lock file while held (red team F6)
        )
    _vetted(h, lambda x: _not_regular(app.api, x))
    if not app.api.LockFileEx(h.handle, LOCKFILE_EXCLUSIVE_LOCK, 0, 0xFFFFFFFF, 0xFFFFFFFF, OVERLAPPED()):
        error = _win_error("LockFileEx")
        h.close()
        raise error
    h.h = None  # never closed: the lock is held for the process's life
