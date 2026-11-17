# S0126: the engine's write path on Windows (M1b slice 8b, BL-0087 with BL-0206, BL-0218, BL-0260)

## Brief
- Goal: M1b's slice 8b. Port the engine's write path to Windows, so that every write the engine makes on macOS and Linux runs on Windows with the same contract, write-intent journal, recovery, publish and texts, and the engine's real-disk write-contract tests run on a **Windows runner** in the manual CI run (`ci.yml` gains a Windows engine job on `windows-2022`; ADR-0061 decision 6 and its amendment of 2026-11-14). S0125 did Linux; `ledger_engine.fs.this_os` still gives Windows `UnsupportedPlatform` (and `engine.unsupported_platform`).
- **The ownership rule** (owner, 2026-11-16, for the ports: "Rule unchanged (Recommended)"): the port writes exactly the files and bytes the macOS write path writes, under the same contract, journal and recovery; only the OS's file primitives differ. Where Windows or a volume cannot give a primitive safely, the port behaves as macOS and Linux do in the same case: the same refusal, or the same stated, reported best-effort mode (engine-jobs §6.2, §6.8; ADR-0068 D2). Never a silent fallback.
- **Port by primitive**, as designed (ADR-0013; engine-jobs §5.2, §6.1-§6.8):
  - **the swap:** `ReplaceFileW` with a backup name (ADR-0013: "Windows `ReplaceFileW` with a backup name"), then the displaced file hashed and compared with the planned pre-hash, as on macOS. State exactly what `ReplaceFileW` guarantees on NTFS (and on ReFS, FAT, exFAT, SMB), which errors leave which names, and which volumes take the §6.8 mode instead;
  - **the exclusive rename and create:** `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING`, or `SetFileInformationByHandle` with `FileRenameInfo(Ex)` and no replace; `CreateFileW` with `CREATE_NEW`;
  - **durable sync (§6.2):** `FlushFileBuffers` on files; folders flushed where Windows allows it (a directory handle opened with backup semantics). State what Windows does and does not make durable for a rename, as a limit if it cannot be made so;
  - **names and paths:** `GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED)` for `canonical_path`; Windows' ordinal upper-case folding for `path_key` unless the folder is case-sensitive (`FILE_CASE_SENSITIVE_INFO`); reserved names, trailing dots and spaces, and `\\?\` long paths refused or handled as the spec says; links (symbolic links and junctions) as the macOS and Linux link checks;
  - **open files:** Windows refuses to rename or delete an open file; every handle is closed before its rename (spec §6.1 step 8.5's "the handle closed first, for Windows"), and a file another program holds open makes the save refuse truthfully;
  - **locks:** `LockFileEx` for the engine lock, the app-instance lock and the legacy app's lock on Windows (INV-26: as the legacy app takes it);
  - **drive and folder identity (BL-0218, Windows part):** `GetVolumeInformationByHandleW` (the volume serial) and `GetFileInformationByHandleEx(FileIdInfo)` (the 128-bit file ID) with the creation time, as Linux's birth-time identity, for the app folder and the save's own folders (B2's rule);
  - **the engine's process group (§5.2):** a Job Object nested in the shell's, with kill-on-close, in the sidecar's `engine_group`; the drain, CPU from the Job Object;
  - **the mirror publish on Windows (BL-0206):** ADR-0015's pointer file in place of the `current.db` link, since Windows cannot rename over an open file; a deletion that fails because a generation is open is retried at the next cleanup and never reported as an error (ADR-0015 amendment of 2026-10-25); `design/specs/mirror/publish.md` states the Windows variant, and the mirror-publish model's assumptions are checked against it (if the model's two properties need a Windows variant, say so and stop for the owner);
  - **the type-check pass (BL-0260):** `Windows` joins `scripts/pyright_platforms.py`'s platforms for `backend/` and `engine/`; each POSIX-only module states its platform once or selects per platform, as `this_os` does; 0 errors on all three platforms.
- **How it is tested** (owner, 2026-11-16: "Yes, start runs as needed (Recommended)"): push to `next` only commits whose macOS gates are green; start the manual CI run on `next`; read the Windows job's results with `gh`; iterate. Runs are named in the repo by date and order only, never by ID. The fake file system's sweeps and the TLA+ replays stay the durability evidence; the runner adds the real-disk tests on NTFS (and on a scratch volume of other kinds where the runner can make one: a VHD formatted FAT, exFAT or ReFS).
- **If the slice proves too large to land as one** (for example, the swap's guarantees on NTFS need a design decision, or the Job Object needs the shell), stop at a coherent point, report, and propose the split; do not land a half-ported write path that could write on Windows without its guards. Until the whole write path is ported and green on the runner, the engine on Windows keeps refusing file operations with `engine.unsupported_platform`.
- Implements: ADR-0061 and its amendment of 2026-11-14; ADR-0013 (the swap; Windows `ReplaceFileW`); ADR-0012 (journal and recovery); ADR-0015 and its amendment of 2026-10-25 (the publish's pointer file on Windows); ADR-0064 (the engine's process group); ADR-0068 D2; engine-jobs §5.1, §5.2, §6.1-§6.8, §8.1, §14, §15; publish.md; BL-0087, BL-0206, BL-0218 (Windows), BL-0260.
- Capabilities: the platform notes of the write-path and mirror rows, if they state macOS or Linux only.
- Invariants touched: INV-01, INV-22, INV-26 (the legacy lock on Windows), INV-32, INV-10 (the mirror's cleanup), INV-23, INV-24.
- Risk class / lane: High / full. Review budget row: **New write path or write outcome** (owner, 2026-11-16, for the ports: "New write path (Recommended)"): verifier + data-safety red team; a fresh round after any product fix; in it only High and (a)/(b) findings are fixed; a second fix round on one mechanism needs the owner's options note.
- The bytes it protects: every ledger, journal and mirror byte the engine writes on Windows, by the same durable order, and the truth of "saved", "not saved" and "unchanged" there.
- Mechanism questions (PROCESS.md §3 step 2): which real setup needs this? Every Windows user (owner, 2026-11-14). Can a policy forbid the rest? Volumes without a safe primitive already have the stated refusal or mode; the port follows them. Fix-round history of the family: the macOS port and the save (S0061: four rounds; S0063, S0070, S0071, S0089, S0100-S0102; S0117: five rounds), the Linux port (S0125: one fix round, three class (a) findings decided by the owner as policy options first). **At the first class (a) finding, stop: the policy option goes to the owner before the first fix round.**
- Acceptance (tests first; each fails on Windows on the base commit, which the first CI run after the tests shows):
  - on the Windows runner, the engine's real-disk write-contract tests pass: the save, the rollback and recovery, recover-all, Keep, create and copy, the publish (with the pointer file) and the open;
  - a file held open by another process makes the save refuse truthfully;
  - the swap's refusal on a volume without `ReplaceFileW`'s guarantee takes §6.8's path, tested through an injected refusal and, where the runner can make one, a real FAT or exFAT VHD;
  - the engine lock, the app-instance lock and the legacy lock on Windows;
  - the Job Object drains a plugin's child;
  - `pyright_platforms.py` reports 0 errors on Darwin, Linux and Windows;
  - the macOS and Linux gates stay green;
  - the CI runs, named by date and order, are in the journal.
- Expected doc impact: engine-jobs §5.2, §6.2, §6.3, §6.8, §14 (Windows tested on the runner), §15 (Windows limits); publish.md (the Windows variant); ADR-0015's amendment if the pointer file's rule needs one (the owner's: stop and report first); ARCHITECTURE.md (the engine's `fs` adapters, the sidecar's `engine_group`); `this_os`'s docstring; BL-0087, BL-0206, BL-0218, BL-0260 closed or narrowed; the capability rows' platform notes.
- Open questions: none at the brief; the stop rules above route any that arise.
- Base: f867c732.

## Decisions
- Slice 8b after 8a (owner, 2026-11-16: "Linux, then Windows (Recommended)"; 2026-11-17: "yes, start slice 8b").
- The ownership rule, the review row and the CI runs are the owner's decisions of 2026-11-16 for the ports (quoted in S0125's journal).
- **Probe before port (agent).** The brief's stop rules name the exact guarantees of `ReplaceFileW` and of folder durability; they are facts of the runner's Windows, not of this Mac, so the first commit adds the Windows probe (`engine/scripts/probe_windows_fs.py`, prints only) and the manual CI run's `engine-windows` job (`windows-2022`: the probe on the runner's folders and on FAT32, exFAT and ReFS VHDs it formats with `diskpart`, then the engine's tests as they stand, which gives the Windows baseline), before any adapter code.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
