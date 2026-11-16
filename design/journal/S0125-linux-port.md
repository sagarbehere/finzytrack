# S0125: the engine's write path on Linux (M1b slice 8a, BL-0266 with BL-0218, BL-0261, BL-0262)

## Brief
- Goal: M1b's slice 8a. Port the engine's file-system port to Linux (`ledger_engine.fs`), so that every write the engine makes on macOS runs on Linux, with the same contract, journal, recovery, publish and texts, and the engine's real-disk write-contract tests run on the Linux runner of the manual CI run (`ci.yml`'s `engine` job, `ubuntu-24.04`) instead of skipping. Today `ledger_engine.fs.this_os` gives macOS `ledger_engine.fs.darwin` and every other system `UnsupportedPlatform`.
- **The ownership rule** (owner, 2026-11-16: "Rule unchanged (Recommended)"): the port writes exactly the files and bytes the macOS write path writes, under the same contract, write-intent journal and recovery; only the operating system's file primitives differ. Where the OS or the volume cannot give a primitive safely, the port behaves as macOS does in the same case: the same refusal (`not_saved` with its reason), or the same stated, reported best-effort mode where macOS has one (engine-jobs §6.2's `fsync` fallback reported once per volume through `on_fallback`; §6.8's mode without the swap, ADR-0068 D2). Never a silent fallback.
- **Port by primitive** (`ledger_engine.fs.port`'s `FileSystem`, `DirHandle`, `LegacyLocks`; mirror `darwin.py` one method at a time, sharing POSIX code where the two are the same, and keeping each OS's adapter stating its platform once, as `this_os` and `tests/architecture/test_platform_selection.py` require):
  - **the one-step swap:** `renameat2(RENAME_EXCHANGE)` through `ctypes` (glibc's `renameat2`, or the raw syscall where glibc lacks it); a volume that refuses it (`EINVAL`, `ENOSYS`, `EOPNOTSUPP`: some FUSE, network and older file systems) is the §6.8 case exactly as macOS's;
  - **durable sync (§6.2):** `fsync` on files and folders (Linux's `fsync` reaches stable storage; no `F_FULLFSYNC`), with the same `durable_sync` contract;
  - **exclusive create, exclusive rename** (`renameat2(RENAME_NOREPLACE)`, falling back as §6.8 says where refused), `O_NOFOLLOW` and `openat` walks, the path guard, the link checks;
  - **drive identity (BL-0218, Linux part):** the file system UUID for `DirHandle.volume_id` (from `/dev/disk/by-uuid` matched to the folder's `st_dev`, or `blkid`; `statfs`'s `f_fsid` only where stated stable), with a probe of which volume kinds report one, and the save's `no_drive_id` refusal where none does;
  - **folder identity that survives inode reuse (BL-0262):** the app folder's and the journal folder's identity on Linux uses `statx`'s birth time with the inode number (or another stated mark that survives reuse), carried by `--app-folder-id` and the engine lock's check (`server.hold_engine_lock`, `app.platform.adapters.app_folder`); the `replaced` cases of `test_engine_lock.py` then run on Linux too;
  - **case folding and names:** Linux volumes are case-sensitive as a rule; `FoldRule` and the case-twin check follow the volume's real behaviour (a case-insensitive ext4 folder, `casefold`, is probed, not assumed);
  - **ACLs and rights:** the POSIX permission checks (`allowed`, `acl`), with ACLs on Linux stated as not read (a stated limit, macOS's ACL rule's counterpart) unless cheap;
  - **the legacy app's lock (Copy, INV-26):** `flock`, as the legacy app takes it on Linux;
  - **the engine's process group (§5.2)** and the app folder's identity check passing `UnsupportedPlatform` through (BL-0261: the boot names the platform, not "the app folder is missing").
- **The runner:** the manual CI run's `engine` job (`ubuntu-24.04`) runs the engine's real-disk write-contract tests on Linux. A test that is macOS-only says why, in one line, and is not a write-path safety test (ADR-0061 decision 4(b): "safety, write-path, INV-26 and integrity-core tests are never deferred"). The backend job's Linux-skipped tests (`test_main.py`'s boot recover-all, `test_engine_lock.py`'s `replaced`) run too.
- **How it is tested (owner, 2026-11-16: "Yes, start runs as needed (Recommended)"):** this Mac has no Linux. The implementer pushes to `next` only commits whose macOS gates are green (the pre-push hook runs them; the Linux code is not reached on macOS), starts the manual CI run on `next` with `gh workflow run ci.yml --ref next`, waits for it, reads the result with `gh run view`, and iterates. A run is named in the repo by date and order only, never by its ID (the privacy scan reads run IDs as account numbers). The fake file system's sweeps (crash, power loss, I/O error at every effect) and the TLA+ replays stay the durability evidence; the runner adds the real-disk tests.
- Implements: ADR-0061 and its amendment of 2026-11-14 (Linux joins decision 6; a platform-only failure waits at most one milestone); ADR-0013 (the swap), ADR-0012 (journal and recovery), ADR-0068 D2 (the mode without the swap), engine-jobs §5.1, §5.2, §6.1-§6.8, §8.1, §14 ("Limit (Windows)" and the Linux line); BL-0266, BL-0218 (Linux), BL-0261, BL-0262.
- Capabilities: CAP-DATA-ENGINE-JOBS and the write-path rows: their platform notes, if they state macOS only.
- Invariants touched: INV-01, INV-22 (every write passes the contract and fails closed, on Linux too), INV-26 (the legacy lock on Linux), INV-32 (recovery on Linux), INV-23, INV-24 (generated data only; no run ID in the repo).
- Risk class / lane: High / full. Review budget row: **New write path or write outcome** (owner, 2026-11-16: "New write path (Recommended)"): verifier + data-safety red team; a fresh round after any fix that changes product code; in it only High and (a)/(b) findings are fixed; a second fix round on one mechanism needs the owner's options note.
- The bytes it protects: every ledger and journal byte the engine writes on Linux, by the same durable order as macOS (backups, journal, replaces, close), and the truth of "saved", "not saved" and "unchanged" there.
- Mechanism questions (PROCESS.md §3 step 2): which real setup needs this? Every Linux user (owner, 2026-11-14: "I want Finzytrack to work well on Linux, Windows, and MacOS"). Can a policy forbid the rest? Volumes without the swap or a drive UUID already have macOS's stated refusal or mode; the port follows them. Fix-round history of the family: the macOS port and the save (S0061: four rounds; S0063, S0070, S0071, S0089, S0100-S0102; S0117: five rounds on the confined save). **At the first class (a) finding in a rare setup, the policy option goes to the owner before the first fix round** (IMP-0065; the owner's instruction for M1b).
- Acceptance (tests first; each fails on the base commit on Linux, which the first CI run after the tests shows):
  - on the Linux runner, the engine's real-disk write-contract tests pass: the save, the rollback and recovery, recover-all, Keep, create and copy, the publish, and the open;
  - the swap's refusal on a volume without `RENAME_EXCHANGE` takes §6.8's path, tested through an injected refusal;
  - `test_engine_lock.py`'s `replaced` cases pass on Linux (BL-0262);
  - the boot's recover-all on an unsupported platform names it (BL-0261);
  - the drive identity probe's result is recorded per volume kind the runner offers;
  - the macOS gates stay green;
  - the CI run's result, named by date and order, is in the journal.
- Expected doc impact:
  - `design/specs/engine-jobs.md` §6.2 (Linux's sync), §6.3 (drive identity on Linux), §6.8, §14 (Linux tested on the runner), §15 (Linux limits);
  - ARCHITECTURE.md (the engine's `fs` adapters);
  - `this_os`'s docstring;
  - BL-0266, BL-0218 (Linux part), BL-0261, BL-0262 closed or narrowed;
  - the capability rows' platform notes.
- Open questions: none.
- Base: 19d389d3.

## Decisions
- Slice 8 is split: 8a Linux (this slice), 8b Windows (BL-0087, BL-0206, BL-0218's Windows part, BL-0260), 8c the AppImage's engine (BL-0263) (owner, 2026-11-16: "Linux, then Windows (Recommended)").
- The ownership rule is unchanged (owner, 2026-11-16: "Rule unchanged (Recommended)", to the proposal that the ports write exactly what the macOS write path writes, under the same contract, journal and recovery, only the OS's file primitives differing, a primitive the OS cannot give safely refusing as on macOS, never falling back silently). As written above, "as on macOS" includes macOS's stated, reported best-effort modes (§6.2, §6.8), which are not silent.
- The review row is New write path (owner, 2026-11-16: "New write path (Recommended)").
- The agent may start the manual CI run as needed while building the ports (owner, 2026-11-16: "Yes, start runs as needed (Recommended)", to the proposal: trigger the manual run on `next` after each pushed change to the ports, read the results with `gh`, never put a run ID in the repo).
- Tests first, through the port only (agent): the Linux adapter's tests (`engine/tests/fs/test_linux.py`) reach it through `this_os.for_this_os()` and patch its internals by name (`ledger_engine.fs.linux._renameat2`, `SWAP_TYPES`, `os.fsync`), so the tests-first commit type-checks for Linux before `fs/linux.py` exists (the pre-push hook runs pyright for Linux). The real-volume helpers of the macOS tests move to `engine/tests/fs/real_volume.py`, shared by both adapters' tests, so Linux runs the same best-effort crash and recovery checks on its own drives.
- Every real-disk test that skipped off macOS now skips only off macOS and Linux, with the reason "Windows is BL-0087"; the macOS-only ones that stay test a macOS primitive (`F_FULLFSYNC`, `F_GETPATH`, macOS ACLs, `hdiutil` images) and have a Linux counterpart in `test_linux.py` where Linux has the primitive.
- The manual CI run's engine job formats scratch volumes in loop images (vfat, exFAT, btrfs, xfs, ext2, a case-folding ext4) with `sudo` on the runner, runs the probe (`engine/scripts/probe_linux_fs.py`, prints only) on them and on the runner's own folders, and passes them to the tests (`FT_TEST_VOLUMES`, `FT_TEST_CASEFOLD_DIR`), as the macOS disk-image tests take them (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
