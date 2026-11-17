# S0127: Windows paths and types (M1b slice 8b-1, BL-0279 with BL-0260)

## Brief
- Goal: the first part of the Windows port (owner, 2026-11-17: "1a, split yes, keep red"). Make the engine, the write-intent journal, the protocol and the sidecar handle **Windows paths** correctly, make the engine's test suite **load and run on Windows**, and bring the **Windows type-check pass** to 0 errors. Windows keeps refusing every file operation (`engine.unsupported_platform`) after this slice: no write is turned on (BL-0280, BL-0281, BL-0206 follow).
- **Paths.** Today the engine's paths are POSIX strings throughout: the journal, `core/paths.py`, the writer, `posixpath` in the save, create, copy and copy layout, the protocol, and the sidecar's `engine_protocol.py`. The work:
  - **One path model, selected per OS:** drive letters and UNC roots, `\` separators, the `\\?\` prefix where Windows needs it, absolute and normalized as each OS defines it.
    - On macOS and Linux every path, journal record and protocol message is **byte-for-byte as today**: no format change, no journal version change. A test pins this with the existing journal and protocol fixtures.
  - **Names Windows cannot hold safely are refused** before anything is written, with a named reason: reserved device names (`con`, `prn`, `aux`, `nul`, `com1`-`com9`, `lpt1`-`lpt9`, with or without an extension) and names ending in a dot or a space. S0126's probe shows that without the prefix Windows opens the device or drops them.
    - State whether this check runs on every OS (a ledger that names `aux.beancount` cannot move to Windows) or only on Windows. That is the agent's call, recorded with its reason. It must never change what macOS or Linux write today.
  - **Path keys and folding:** `path_key` folds by ordinal upper case on Windows unless the folder is case-sensitive (engine-jobs §6.3), and `same_path` compares as Windows does. The real-disk side comes with BL-0280; this slice builds and tests the pure functions.
  - **The protocol** carries Windows paths unchanged, and the sidecar's codec reads them.
- **Tests on Windows:**
  - the engine suite loads on Windows: `tests/conftest.py`'s `time.tzset()` has a Windows equivalent, or the time-zone setting is done another way, so tests still run in Los Angeles (INV-07);
  - every real-disk write-contract test that cannot run before BL-0280 skips on Windows with one line naming BL-0280;
  - pure tests run;
  - the `engine-windows` CI job then runs and passes what can run.
- **Types (BL-0260):** `Windows` joins `scripts/pyright_platforms.py`'s platforms. Each POSIX-only module states its platform once or selects per platform, as `ledger_engine.fs.this_os` does, so `pyright --pythonplatform Windows` reports 0 errors in `engine/` (166 on 2026-11-17) and `backend/` (265). No `# type: ignore` to silence a platform name; a module that is POSIX-only says so at its top.
- Implements: ADR-0061 and its amendment of 2026-11-14; ADR-0069 (the Windows split); engine-jobs §6.3 (paths and keys), §14; BL-0279, BL-0260.
- Capabilities: none changes status.
- Invariants touched: INV-01, INV-22 (no write path changes behaviour on macOS or Linux: pinned by their unchanged gates and the byte-for-byte fixtures), INV-07 (tests still in Los Angeles on Windows), INV-23, INV-24.
- Risk class / lane: High / full: it touches the write path's paths on every OS. Review budget row: **Hardening an existing write path** (the path handling of the existing write path; no write is turned on): the verifier; the data-safety red team joins when the verifier reports a class (a) finding; a fresh round after any product fix.
- The bytes it protects: every path the journal records and every path the writer touches on macOS and Linux, which must not change; on Windows, nothing is written yet.
- Mechanism questions (PROCESS.md §3 step 2): which real setup needs this? Every Windows user (owner, 2026-11-14). Can a policy forbid the rest? Names Windows cannot hold are refused, not supported. Fix-round history of the family: the paths (S0061's `path_key` and canonical path rounds, S0089, S0100-S0102), the ports (S0125, S0126). **At the first class (a) finding, the policy option goes to the owner before the first fix round.**
- Acceptance (tests first; each fails on the base commit on Windows or macOS, as it applies):
  - the Windows path model's pure tests: drive letters, UNC, `\\?\`, normalization, reserved names and trailing dots and spaces refused, and `path_key` and `same_path` on Windows;
  - the journal and protocol fixtures of macOS and Linux byte-for-byte unchanged;
  - `pyright_platforms.py` 0 errors on Darwin, Linux and Windows;
  - the `engine-windows` CI job: the suite loads, pure tests pass, and real-disk tests skip naming BL-0280;
  - the macOS and Linux gates green;
  - the CI run, named by date and order, in the journal.
- Expected doc impact:
  - engine-jobs §6.3 (Windows paths and the refused names), §14, §15 (a Windows limit if any);
  - ARCHITECTURE.md (the path model's place);
  - `backend/CLAUDE.md` or `engine/CLAUDE.md` if a module's platform rule needs a line;
  - BL-0279 and BL-0260 closed or narrowed.
- Open questions: none.
- Base: 2e5b312f.

## Decisions
- The Windows port in four parts, this the first (owner, 2026-11-17, in session: "1a, split yes, keep red"); the ownership rule, the CI runs and the review of the ports are the owner's decisions of 2026-11-16 (S0125's journal). This slice changes no write on any OS, so it takes the Hardening row, not New write path.
- **Tests first (agent).** On the base 2e5b312f, macOS: `tests/fs/test_pathmodel.py`, `tests/ledger/test_journal_windows.py`, `tests/test_protocol_paths.py`, `tests/ledger/test_copy_layout.py` (its new Windows test; the module's import) and the backend's `tests/platform/test_engine_protocol.py` (`is_absolute`) fail at import: no path model, no codec function. The byte pins (`tests/test_posix_path_pins.py` with `tests/fixtures/posix_path_pins.json`, written once on the base by the module's own writer), the Los Angeles summer-time and child-process checks (`tests/test_runs_west_of_utc.py`) and the architecture guard against `simulating` in the product pass on the base: `@regression-guard`. Windows on the base: the 2026-11-17 run 1 (S0126; no engine, backend, scripts or CI change between its head and the base) shows the suite does not load (`time.tzset`), so every Windows acceptance test fails there by not running.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
