# S0135: the Windows adapter's leftovers from S0128 round 2 (BL-0285, M1b)

## Brief
- Goal: close BL-0285 before BL-0087 turns Windows writes on. Its five items, each with the owner's options in `design/inbox/20261120-windows-adapter-leftovers.md` (cheapest first). **This brief stops before tests and code** (PROCESS.md §3 step 2: the carry-over and the rollback's flush skip are S0128 round 1's own fixes, and BL-0285 is what round 2 found in them).
  1. **V2-F1 (Medium, e: privacy).** `WindowsDir.create(..., like=...)` writes the new bytes into the staging file, which Windows gives the folder's inherited ACL, and sets the file's own DACL last (`_carry_over`).
  2. **V2-F3 (Low, c).** Three runner tests missing: an unprotected DACL with an explicit entry (a mutant that always sets the protected flag survives today's test), the archive attribute, and the rollback's put-back keeping the file's own ACL.
  3. **V2-F4 (Low, c).** `restore._flush` skips the rollback's flush by `handles.FILE_ID_TYPES` (`ntfs`, `refs`), the file-system type's name only; macOS's own driver also reports `ntfs`.
  4. **V2-Q-B.** `_carry_over` skips the DACL when `GetSecurityInfo` answers `ERROR_NOT_SUPPORTED` or `ERROR_INVALID_PARAMETER`: keyed by the error, not by what the volume is.
  5. **Note.** Windows backups and journal files take the app folder's ACL (`BACKUP_MODE`, `JOURNAL_MODE` mean nothing there).
- **The bytes it protects:** for items 1, 2, 4 and 5, no byte against loss: they keep **a ledger's contents from being readable by other accounts** (privacy, the owner's rule 1a of 2026-11-17), during a save, after it and in its backups. For item 3, the truth of the rollback's "unchanged": a file the rollback did not flush is one the save never replaced.
- Implements: the owner's rule 1a (S0128 round 1, 2026-11-17: "1a, wording ok"), ADR-0069, engine-jobs §6.3 ("The Windows adapter"), §6.8, §8.1 step 4 (S0101's flush); BL-0285.
- Capabilities: none changes status (Windows does not write in the product until BL-0087).
- Invariants touched: INV-01 (item 3: the rollback's truth), INV-22 (item 4: a failed security read today passes silently instead of failing closed).
- Risk class / lane: High / full. Review budget row: **Hardening an existing write path** (PROCESS.md §3): the verifier; the data-safety red team joins when the verifier reports a class (a) finding; a fresh round after any fix that changes product code, in which only High and (a)/(b) findings are fixed.
- **Batching (agent; IMP-0072, PROCESS.md §3: "Never put … a mechanism that needed two fix rounds before, into a batch").** Items 1, 2, 4 and 5 are one mechanism, the carry-over of a file's own security (built in S0128 round 1; its first review findings are these). Item 3 is the rollback's flush, which needed two rounds in S0101 (a High in round 1) and a change in S0128 round 1: it has the history that rule names. So item 3 is **not** batched here: it becomes its own small slice (or a stated limit, if the owner takes option 1), and this slice is items 1, 2, 4 and 5. The note asks the owner to confirm.

### The mechanism questions (PROCESS.md §3 step 2)
| Item | Which real setup needs this? Can a policy forbid the rest? | Two or more fix rounds before? |
|---|---|---|
| 1 staging under the folder's ACL | A ledger file whose own ACL is **stricter** than its folder's, in a folder that lets **another non-administrator account** read. Not the default location (below). A policy can make it a stated limit: "on Windows a ledger is as private as its folder". | The carry-over: built in S0128 round 1 (owner 1a); round 2 found V2-F1, V2-F3, V2-Q-B. No fix round on it yet. Family (the Windows adapter): S0128, 2 rounds, 0 High. |
| 2 tests | Every Windows user with a file of their own ACL (tests only; no mechanism). | As item 1. |
| 3 flush skip by name | A save rolled back on macOS on an NTFS drive made writable by an unsupported setting, while another program reverts the file by a new object that reuses its inode number, then a power loss. A policy can state it, or refuse that drive. | **Yes:** the flush, S0101 (2 rounds, 1 High: the object judged), then S0128 round 1 (the skip, RT-F1). |
| 4 skip keyed by error | A local NTFS or ReFS volume refusing `GetSecurityInfo` with those two codes: not seen; possible only with a third-party driver that calls itself NTFS. Network shares never reach a save (`volume_id` None: `no_drive_id`). | As item 1. |
| 5 backups, journals | Only an app folder outside the user's profile (an `--app-root` elsewhere). The default app folder is under the profile. | No mechanism today. |

### Facts verified (code, base 08f38970)
- `WindowsDir.create` (`engine/src/ledger_engine/fs/windows.py`): `FILE_CREATE` with no security descriptor (`_nt_open` passes `SecurityDescriptor=None`), so the file gets the folder's inheritable entries; then all data written; then `_carry_over`: streams (each a new open relative to the new file's handle), attributes, DACL last; then the flush. The docstring's reason for "DACL last": "an explicit ACL that denies us writing applies only to later opens" (the stream opens are later opens).
- The same `create(..., like=...)` writes the **rollback's and recovery's put-back** (`restore.py`, the restore staging name `.<name>.ft-restore-<session>-<job>.new`): the pre bytes sit under the folder's ACL the same way. The save's staging name is `.<name>.ft-<session>-<job>.new` (`core/journal.staging_name`).
- A carry-over that raises leaves the staging file; the rollback (or, after an engine death, recovery at the next open) deletes it: a regular file at a staging name is ours whatever it holds (`core/recovery.classify_staging`). Until then it keeps the folder's ACL.
- **macOS and Linux have no such window:** `posix.create` opens with `O_CREAT | O_EXCL` and the target mode, which the umask can only narrow, then `fchmod`s it (`fs/posix.py`). On macOS a file's ACL is not carried (the 2026-10-29 rule; 1a replaced it for Windows only).
- **New files already take the folder's ACL on Windows:** a file a save creates (`like=None`) and a ledger the create job makes (`create.py`, no `like`). So the folder's ACL is what protects every ledger file the user did not restrict by hand.
- `_carry_over` skips only the DACL on the two codes; `_streams` returns no streams on `ERROR_HANDLE_EOF`, `ERROR_INVALID_PARAMETER`, `ERROR_NOT_SUPPORTED`; `WindowsDir.acl()` (the folder's DACL, read by `handles.acl_denial`) returns no entries on the same two codes. All three are keyed by the error. `WindowsDir.flags()` exists and is used for `FILE_SUPPORTS_POSIX_UNLINK_RENAME` (S0132; the owner's 2026-11-18 answer: "decide by what the volume offers").
- `restore._flush` skips when `pristine` matches and `folder.fs_type in FILE_ID_TYPES`; `FILE_ID_TYPES = {"ntfs", "refs"}` (`ledger/handles.py`); the macOS adapter's `fs_type` is `statfs`'s `f_fstypename` (`fs/darwin.py: fs_type_from`), Linux's the mount's type from `/proc/self/mountinfo`. Nothing on macOS or Linux refuses writes on an `ntfs` volume by its name (`handles.drive_of` checks only the record's pattern).
- `BACKUP_MODE` and `JOURNAL_MODE` are `0o600` (`ledger/writer.py`); on Windows `create` drops `mode` (`del mode`). Backups and journals live in the workspace's folders under the app folder (`save.py`: `_ensure_dir(..., JOURNAL)`, `BACKUPS`).
- engine-jobs §6.3 says "so a ledger made private with its own ACL stays private": true after the save, not during it (item 1).
- **A gap in BL-0285's row:** it says the verifier's proposed runner test for V2-F1 "is in the S0128 journal's round 2"; the journal holds only the finding's row, no test. The test below is this slice's own.

### Facts that need the Windows runner (probe questions; not guessed)
- P1 (item 1, option 3). `NtCreateFile(FILE_CREATE)` with the old file's self-relative descriptor (DACL only, as `GetSecurityInfo` returns it) as `OBJECT_ATTRIBUTES.SecurityDescriptor`: does `icacls` then list the new file exactly as the old one, for (a) a protected DACL, (b) an unprotected DACL with an explicit entry beside inherited ones, (c) inherited entries only? (Windows merges the parent's inheritable entries at creation; whether the old file's `(I)` entries are doubled or dropped is the question.)
- P2 (item 1, option 3). With a descriptor whose DACL does not grant the creating user write, does the `FILE_CREATE` handle still carry `GENERIC_WRITE`, and does a stream open relative to that handle then fail?
- P3 (item 1, option 2). After `SetSecurityInfo` gives the file a DACL that denies the user write, do writes, `SetFileInformationByHandle(FileBasicInfo)` and `FlushFileBuffers` through handles opened before it still succeed? (Microsoft documents that access is checked at open; to confirm on the runner, NTFS and ReFS.)
- P4 (item 4). What does `GetSecurityInfo` answer for a file on FAT32 and on exFAT: `ERROR_NOT_SUPPORTED`, `ERROR_INVALID_PARAMETER`, or success with a NULL DACL? And `FileStreamInfo`?
- P5 (item 4, option 2). The volume flags `FILE_PERSISTENT_ACLS` (0x8) and `FILE_NAMED_STREAMS` (0x40000) on the runner's NTFS (C:, D:), ReFS, FAT32 and exFAT volumes.
- P6 (likelihood, items 1 and 5). `icacls` of the profile folder, `Documents`, `%LOCALAPPDATA%`, the system drive's root, and a freshly formatted NTFS volume's root (the scratch VHD). The agent's understanding, to confirm: a profile grants SYSTEM, Administrators and the user only; a drive root grants Users read and Authenticated Users modify to new subfolders.

### Acceptance tests (written after the owner's answer; each must fail on the base, on the runner)
- Item 1 (options 2 or 3): a runner test that stops `create(..., like=...)` at its first data write (a wrapped `_Handle.write_all`) and lists the staging file with `icacls`: it equals the target's listing (protected; and unprotected with an explicit entry) before any byte of the new content is written. The same through the rollback's put-back. Must not pass: today's order (DACL after the data) fails it.
- Item 2: an unprotected DACL with an explicit entry survives a save unchanged (kills the always-protected mutant); the archive attribute (set and clear) survives; a rollback's put-back keeps a protected DACL.
- Item 4 (option 2): fake-level tests of the rule (a volume with `FILE_PERSISTENT_ACLS` whose `GetSecurityInfo` refuses: the save is "not saved", nothing replaced; a volume without the flag: the DACL skipped); the same for streams and `acl()`; runner tests that FAT32 and exFAT still save.
- Item 5 (option 1): none (a doc line).

### What each check is a second opinion on (S0122's lesson)
- The staging-ACL runner test: on "the new bytes are never readable by more accounts than the old file was", judged by Windows' own `icacls`, not by our `GetSecurityInfo` reading, so a fault in our reading cannot hide one in our writing.
- The protected-flag and archive tests: on the owner's 1a as he stated it (the DACL's protected or inherited state, hidden and archive kept), against the file as Windows lists it.
- Item 4's flag key: on the volume's own declaration that it keeps ACLs and streams (`GetVolumeInformationByHandleW`), not on an error code, which says only that one call failed.
- Item 3's key: the skip is a second opinion on "this object was never replaced", and it is only as good as the file system's promise that an ID is never given to another file; the key must name exactly the adapters that make that promise.

- Expected doc impact: engine-jobs §6.3 ("The Windows adapter": the carry-over's order, the volume rule, the stated limits chosen), the `fs/windows.py` docstring, `handles.FILE_ID_TYPES`'s docstring (if item 3 changes), BL-0285 closed or narrowed; no user docs (Windows writes are off), unless the owner takes a stated limit that BL-0087's user docs must carry.
- Open questions: `design/inbox/20261120-windows-adapter-leftovers.md` (blocks S0135).
- Base: 08f38970.

## Decisions
- Item 3 is split out of this slice (agent; IMP-0072: the flush has needed two fix rounds before, S0101 and S0128). The note asks the owner to confirm.
- The brief stops before tests (orchestrator's instruction for this turn; PROCESS.md §3 step 2).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(Not started: the slice waits on the owner's answer.)

## Verifier

## Retro
