# S0144: the Cloud Files probe on the Windows runner (BL-0087's split, part 1; BL-0283's facts, M1b)

## Brief
- **Goal:** a prints-only probe on the Windows CI runner, `engine/scripts/probe_windows_cloud.py` (Python `ctypes` over `cldapi.dll`, in the style of `probe_windows_fs.py` and `probe_windows_share_save.py`), run in a new step of the `engine-windows` job in `.github/workflows/ci.yml`. It never fails the job on a finding; a crash of the probe itself fails the step, so it is seen. It records:
  1. whether the runner can run the cloud filter (`fltmc filters`; loading or starting `cldflt` if it is absent), register a sync root (`CfRegisterSyncRoot`) under `RUNNER_TEMP` and connect to it (`CfConnectSyncRoot`, with a FETCH_DATA callback that serves the bytes with `CfExecute` TRANSFER_DATA), with no Microsoft account;
  2. a placeholder file (cloud-only, hydrated, hydrated and pinned) and a placeholder folder as our Windows adapter's own primitives see them: the attributes (`REPARSE_POINT`, `OFFLINE`, `RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN`, `PINNED`, `UNPINNED`), the reparse tag, and what `ledger_engine.fs.windows._kind` answers, through the adapter's own calls (`WindowsFS().open_dir` on the sync root, then `stat`, `open_child`, `read`, `sha256` through the port), so the probe sees what the save would;
  3. whether a read through our handle (opened with `FILE_OPEN_REPARSE_POINT`, as `open_child` and `sha256` open entries) hydrates the file through the provider's fetch callback or reads the bare placeholder, compared with Python's plain `open()` (Beancount's read);
  4. the exact error (NTSTATUS, Win32 code, `errno` as the adapter maps it) a read gets while the provider is disconnected (`CfDisconnectSyncRoot`): the "offline" case;
  5. the whole save on a hydrated placeholder, not only its primitives: `allowed` (delete access, and the attribute read the staging copy needs), the file's own sync, the backup's read, a staging file created in the sync-root folder (with `like=` the placeholder, as the save makes it), our rename over the placeholder (`FileRenameInformation` with `ReplaceIfExists`, through the adapter), the folder's sync, the read-back, and what is at the name afterwards (attributes, tag, bytes); the same on a cloud-only placeholder;
  6. how the process's placeholder compatibility mode (`RtlSetProcessPlaceholderCompatibilityMode` and `RtlQueryProcessPlaceholderCompatibilityMode` in `ntdll`) changes items 2 to 5: the default mode and the expose mode.
  The sync root is unregistered at the end (`CfUnregisterSyncRoot`), also on failure.
- **What it is a second opinion on:** Windows' own cloud filter, on which OneDrive is built; not OneDrive's sync engine (the owner's check by hand in S0149 covers that).
- **Implements:** ADR-0069; ADR-0061 and its 2026-11-14 amendment; engine-jobs §6.3, §6.8, §15; BL-0283's decision (`design/inbox/answered/20261121-pace-and-scope.md`, change D); BL-0087's split of 2026-11-22 (owner, in session: "Split OK. Do V4-1, let's go with your lean").
- **Capabilities:** none changes (Windows still does not write in the product).
- **Invariants touched:** none (no product code). INV-23 and INV-24: the probe writes only under `RUNNER_TEMP`; no home path and no run ID enter the repository.
- **The bytes it protects:** none (a probe writing scratch files only); the policy is decided (support the typical OneDrive setups).
- **Which real setup needs this, and can a policy forbid the rest?** Documents redirected to OneDrive, which Windows promotes (common, by the owner's words of 2026-11-21); every other reparse point stays refused.
- **Has this family needed two or more fix rounds before?** No: the probes of S0126, S0132 and S0136 landed in one round. Lesson applied: probe the whole operation on the new kind of volume, not only its primitives; a probe's own bug can look like a finding, so a surprise is first treated as a possible probe bug.
- **Risk class / lane:** Low / light. Review row: tooling, gates, measurement (the verifier, one round, the stop rule). No mutants (prints only).
- **Acceptance tests:** a green manual CI run whose `engine-windows` log answers items 1 to 6. The answers are recorded below as facts marked "probed", each with its consequence for S0148 (the OneDrive slice). If item 1 fails (no sync root on the runner), the journal and the report say so plainly, and the orchestrator brings the owner options before S0148.
- **Expected doc impact:** this journal; one line in the CI job's comment; engine-jobs only if the probe contradicts what it states.
- **Open questions:** none.

## Decisions
- **No unit test is written first** (PROCESS.md §3 step 3; agent, 2026-11-22): the slice adds no product behaviour, and the script imports only on Windows (`raise SystemExit` elsewhere, as the other Windows probes), so a macOS test could check nothing it does. Its acceptance is the CI log; pyright (strict, Windows platform) and ruff check it at every push.
- **The provider and the reader are two processes** (agent): the probe's main process registers and connects the sync root and serves the fetches; every look and read runs in a child process (the same script, another command). In the product the engine is never the provider: OneDrive's sync engine is another process, and the cloud filter treats the provider's own reads otherwise (`CF_CONNECT_FLAG_BLOCK_SELF_IMPLICIT_HYDRATION`). The placeholder compatibility mode is per process, so each mode gets its own child, and its files (each mode has its own set, so a read in one mode never hydrates what the other mode looks at first).
- **A finding never fails the step; a probe bug does** (agent): every Windows call's refusal (`OSError`, a failed `HRESULT`) is printed as a finding and the probe goes on; any other exception (a `ctypes` or Python error in the probe's own code) is printed as `probe_error` and makes the script end with exit code 1 after the cleanup, so the step fails and the run shows it. A child that runs longer than 15 minutes is stopped and counted as a probe error (a fetch callback that never answers would otherwise hold the job to its 60-minute limit; the cloud filter's own timeout is 60 s).
- **The tests step still runs after a probe bug** (agent): `ci.yml`'s tests step takes `if: success() || steps.cloud_probe.outcome == 'failure'`, so a failing probe makes the job red without hiding the suite's results; any earlier failure still skips the tests, as before.
- **The sync root is also looked at from its parent** (agent): a ledger in a folder below a OneDrive folder is reached by `open_child` through the sync root and the folders under it, and the adapter's `open_child` refuses any reparse point on a folder (`_a_link`: `ELOOP`), so the probe asks it of the sync root and of the placeholder folder.
- **The save is probed as its effects, not by `ledger.save.run`** (agent): the product's save would stop at `not_a_file` wherever `_kind` answers `OTHER`, which item 2 already shows; to answer item 5 the probe runs the save's own sequence on the file, in `writer._check_target`, `_backups` and `_best_effort`'s order (`stat`, `allowed` for delete and for the attribute read, `sync_file`, `read`, `create(staging, like=the file)`, `rename`, `sync`, `read` back), each through the adapter, and goes on past a refusal to show the next step. The backup file itself is not written (it goes to the app's own folder, never the sync root).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test: none (prints only; the probe decides nothing).
- Self-mutation: not applicable (no check; tooling).
- Population: none claimed.

## Verifier

## Golden changes
- None.

## Retro
