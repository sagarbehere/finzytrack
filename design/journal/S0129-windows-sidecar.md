# S0129: the sidecar on Windows: the engine's Job Object, the app folder and the locks (M1b slice 8b-3, BL-0281)

## Brief
- Goal: the third part of the Windows port. Make the main sidecar run the engine on Windows, so the product can supervise engines there as it does on macOS and Linux. Today every POSIX-only sidecar function raises `NotOnWindowsError` (`app/platform/adapters/windows_pending.py`, S0127), and `wait_engine_lock` is a no-op on Windows. **The engine still refuses every file operation in the product after this slice** (BL-0206 and BL-0087 follow). The sidecar can start, supervise, drain and end engines on Windows, and the boot answers `engine.unsupported_platform` truthfully there.
- **The engine's process group on Windows** (engine-jobs §5.2, ADR-0064): a **Job Object** per engine, nested in the shell's, with kill-on-close.
  - The drain ends every member, a plugin's child included, before the next engine starts.
  - The engine ends its own job when the sidecar is gone (the Job Object's kill-on-close when the sidecar's handle closes; spec §5.2).
  - The job's CPU is read from the Job Object's accounting (§5.2).
  - "No live member" is defined for Windows.
  - A process that left the job (`CREATE_BREAKAWAY_FROM_JOB`, where allowed) is out of reach, as `setsid` is on POSIX, and gets the advice on the timer.
  - Cancel and retire end the engine through its job.
  - The spawn's flags and environment (`-s -S -B -P`, `PYTHONHASHSEED=0`) are kept.
- **The app folder on Windows** (`app/platform/adapters/app_folder.py`): `prepare` makes and checks it, and its identity, `--app-folder-id`, uses the adapter's identity, the 64-bit volume serial and 128-bit file ID on NTFS and ReFS, never the creation time (S0128 verifier Q-C). The sidecar's computation and the engine's `this_os.folder_identity` agree, tested.
- **Locks on Windows:**
  - `wait_engine_lock` waits for `locks/engine.lock` with `LockFileEx`, as the engine takes it (S0128);
  - the app-instance lock (one running Finzytrack, ADR-0062) with `LockFileEx`;
  - the advice on the timer while the wait lasts (§5.1).
- **A test copy's recorded `born`** (the sidecar's `CopyMark` and `SaveConfine`) uses the same identity on Windows (Q-C).
- **CI:** a `backend-windows` job on `windows-2022` in the manual run runs the backend's tests there. The ones that need the engine to write skip, naming BL-0206 or BL-0087; the Job Object, app folder and lock tests run.
- Implements: ADR-0064 (one engine, its process group), ADR-0062 (one instance), ADR-0063 (the boot's recover-all), ADR-0061 and its amendment of 2026-11-14, ADR-0069; engine-jobs §5.1, §5.2, §5.5, §9.3; BL-0281.
- Capabilities: none changes status.
- Invariants touched: INV-11 (the queue on `UserContainer`), INV-31, INV-32 (recover-all before any open, which on Windows answers unsupported until BL-0087), INV-23, INV-24.
- Risk class / lane: High / full. Review budget row: **Other product code: engine** (supervision, not a write path: the engine still writes nothing on Windows): the verifier; the security red team only if a new input is added. Note: a Job Object that fails to end a plugin's child could let an engine of an earlier job run beside the next one, which ADR-0064's lock and drain exist to prevent. **At the first finding that lets two engines run at once, or an engine outlive its drain, stop and report**: it guards the write path's one-engine rule.
- The bytes it protects: none directly. It upholds the one-engine-at-a-time rule that every write relies on, on Windows.
- Mechanism questions (PROCESS.md §3 step 2): which real setup needs this? Every Windows user. Fix-round history: the process group and drain on macOS (S0053, S0057, S0073, S0076: several rounds), the ports (S0125-S0128).
- Acceptance (tests first; each fails on Windows on the base, which the first CI run shows):
  - on the Windows runner:
    - an engine spawned in a Job Object;
    - a plugin's child drained with it;
    - kill-on-close when the sidecar's handle closes;
    - CPU read from the job;
    - Cancel and retire ending the engine;
    - the engine lock waited for, with the advice on the timer;
    - the app-instance lock;
    - the app folder's identity equal between the sidecar and the engine;
    - the boot's recover-all answering unsupported;
  - the macOS and Linux gates stay green;
  - the CI runs by date and order in the journal.
- Expected doc impact: engine-jobs §5.1, §5.2 (Windows), §14, §15; ARCHITECTURE.md (the sidecar's `engine_group` on Windows); `backend/CLAUDE.md`; BL-0281 closed or narrowed.
- Open questions: none.
- Base: fcae5f30.

## Decisions
- The Windows port's part 3 (owner, 2026-11-17, in session: "1a, split yes, keep red"); the ports' CI runs are the owner's decision of 2026-11-16 (S0125's journal). This slice writes no ledger byte on any OS, so it takes the "Other product code: engine" row, not New write path; the stop rule above covers the one-engine rule it guards.
- **Tests first (agent).** The Windows acceptance tests are `backend/tests/platform/test_engine_windows.py` (it skips off Windows) and `tests/test_main.py::test_on_windows_the_boots_recover_all_runs_in_an_engine_and_answers_the_platform_unsupported`, run by a new `backend-windows` job in `ci.yml`. So that the test commit type-checks for Windows (the pre-push hook runs `pyright_platforms.py`), it carries the interface they name and nothing else: `app.platform.adapters.windows_job.EngineJob`, whose every method raises `NotOnWindowsError` as the base's POSIX-only functions do there, and `Reaped.cpu_ns` (default None, unused on POSIX). No behaviour changes on any OS in that commit; on Windows the tests fail as on the base.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
