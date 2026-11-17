# ADR-0069: The replace on Windows (Windows has no one-step swap)

This record decides how a save replaces a ledger file on Windows, now that the runner's probe shows that `ReplaceFileW`, which ADR-0013 names for Windows, is not a one-step swap.

- **Status:** Accepted (owner, 2026-11-17, in session: "1a, split yes, keep red", choosing option 1). Supersedes ADR-0013's Windows primitive ("Windows `ReplaceFileW` with a backup name") and the "NTFS (`ReplaceFileW`)" entry of `design/specs/engine-jobs.md` §6.8's list
- **Date:** 2026-11-17
- **Deciders:** owner (2026-11-17); drafted by the agent in S0126.
- **Area:** write path, data safety, platforms

## Context

ADR-0013 replaces a ledger file by an atomic swap: the new file and the old one exchange names in one operation, so a power loss keeps one of the two whole at the file's name, and the old file, now at the temp name, is hashed to detect another program's save made at the same instant. macOS has `renamex_np(RENAME_SWAP)`, Linux `renameat2(RENAME_EXCHANGE)`; for Windows ADR-0013 named `ReplaceFileW` with a backup name.

The S0126 probe (`engine/scripts/probe_windows_fs.py`, the manual CI run's `engine-windows` job on `windows-2022`, Windows Server 2022 build 20348; the 2026-11-17 run 1) ran each replace primitive 300 times while another thread asked for the file's name:

| Primitive | NTFS (D:) | NTFS (C:) | FAT32 | exFAT | ReFS |
|---|---|---|---|---|---|
| `ReplaceFileW` with a backup name | name missing in 469 of 21 911 looks | 1 332 of 11 535 | 421 of 6 027 | 441 of 6 573 | 739 of 18 176 |
| `MoveFileExW(REPLACE_EXISTING \| WRITE_THROUGH)` | never missing (8 555 looks) | (not measured: a probe fault) | never (3 126) | never (3 431) | never (8 022) |
| rename with replace, POSIX semantics (`FileRenameInfoEx`) | never missing (8 829) | (not measured) | not offered | not offered | not offered |
| rename aside, then rename in (both exclusive) | missing 1 324 of 15 271 | (not measured) | (not measured) | (not measured) | (not measured) |

So `ReplaceFileW` is two renames (the old file to the backup name, then the new file to the file's name), and between them the file's name is empty; a power loss there keeps that state. It gives the swap's detection (the old file ends at the backup name) but not its "keeps or drops whole". Windows has no exchange of two names. A plain rename over the file (`MoveFileExW` with `REPLACE_EXISTING`) is one operation (on NTFS and ReFS one logged metadata operation; FAT and exFAT keep no log, as on macOS), but it removes the old file, so another program's save made at the same instant is lost unseen: that is §6.8's best-effort mode.

The same probe found what the rest of the port can rely on: folders and files can be made durable (`FlushFileBuffers` on a folder handle opened for writing with backup semantics, on all four file systems; a file handle opened read-only refuses it); the exclusive create and rename refuse an existing name on all four; opens and renames relative to a folder handle work through `NtCreateFile` and `NtSetInformationFile` (not through `SetFileInformationByHandle`); a file another program holds open without sharing delete (the usual open) cannot be renamed, replaced or removed (the replace fails with nothing changed); and a folder held open, or holding an open entry, cannot be renamed, nor can any folder above it.

## Decision

**Option 1** (owner, 2026-11-17, in session: "1a, split yes, keep red"): Windows takes §6.8's best-effort mode on every volume. The options as they were put, cheapest first (the inbox note has them in plain language):

1. **Windows takes §6.8's best-effort mode on every volume** (the ownership rule's "same stated, reported best-effort mode", owner 2026-11-16): journal, synced backups and read-back kept; the new bytes put over the file by one plain rename (`MoveFileExW` with `REPLACE_EXISTING` and `WRITE_THROUGH`, or its handle-relative form); `writer._check_replaced` just before. No change to the save's model (§13 already covers this mode by the fake's sweeps). Lost against macOS: the displaced-file check, so another program's save in the same instant can be lost (§6.8 "Limit (no swap)"). A power loss is expected to keep or drop the rename whole on NTFS and ReFS (by NTFS's metadata log and ReFS's copy-on-write); that the folder's flush commits it is relied on, not documented by Microsoft, and not power-cut tested (the runner cannot cut power): engine-jobs §6.2 "Limit (Windows durability)". The probe measured `MoveFileExW`; the adapter's own handle rename was measured in S0128 round 1 (its journal). §6.8's notice at open (`msg:save.best_effort_drive`) would show for every Windows ledger unless its wording changes for Windows.
2. **A locked rename aside, then an exclusive rename in** (Windows only): the file opened with delete access and shared for reading only, so no other program can write, rename or remove it; its bytes checked against the planned pre-hash; renamed aside by that handle to a name of ours; the new file renamed in exclusively (an entry another program put there in between stops the save and is kept). No other program's save is ever lost, but the file's name is empty between the two renames, so a power loss can leave it missing, which recovery puts back from the aside copy. A new write mode: the save's model (engine-jobs TLA+), its recovery rule, the spec and a full review change with it.
3. **`ReplaceFileW` as ADR-0013 says**: not recommended. It has option 2's empty-name window (measured), takes paths rather than a folder handle, copies the old file's ACL and streams onto the new one, and has no lock against a writer between the check and the replace.

## Consequences

- Until the Windows port lands (S0126's split: paths and types, the adapter with this replace, the Job Object, the publish's pointer file), the engine on Windows keeps refusing every file operation (`engine.unsupported_platform`), and `design/specs/engine-jobs.md` §6.8 no longer lists NTFS among the disks that keep the swap.
- Whatever the choice, the Windows port needs the engine's paths in Windows form (drive letters and UNC roots, `\`, the `\\?\` prefix, reserved names and trailing dots or spaces refused: without the prefix Windows opens the device for `aux.txt` and drops a trailing dot or space, probed), which S0126 proposes as its own slice.
- Folder identity on Windows cannot rest on the creation time: NTFS and ReFS give a folder or file removed and made again at once its predecessor's creation time (tunnelling, probed), though a new file ID; FAT and exFAT give no `FileIdInfo` at all. BL-0218's Windows part takes the 64-bit volume serial and the 128-bit file ID on NTFS and ReFS, and FAT and exFAT state their limit (as on macOS, §6.3).

## References

ADR-0013, ADR-0061 and its 2026-11-14 amendment, ADR-0068 D2; `design/specs/engine-jobs.md` §6.2, §6.3, §6.8, §13, §15; S0126 (journal), S0125; BL-0087, BL-0218.
