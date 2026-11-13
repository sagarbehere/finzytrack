# S0112: the M1a exit CI run made green (pyright's platform, the engine environment in CI, Linux and Windows packaging)

## Brief
- Goal: the manual CI run at the M1a exit (ADR-0060, ADR-0061 decision 4b) is green. The first run since M0 (the second run of 2026-11-13 at d3dfca75, `milestone_exit: M1a`) failed six jobs: (1) `backend` and `engine`: strict pyright on Linux refuses the tests' macOS-only names (`fcntl.F_FULLFSYNC`, `F_GETPATH`, `os.chflags`, `st_birthtime`; 7 and 70 errors); (2) `repo-checks` and `frontend`: the acceptance harness refuses because the job never built `engine/.venv` (needed since S0038, when the sidecar began starting its engine); (3) `packaged` on Ubuntu: the AppImage's smoke test fails "the bundled engine failed: engine view: CalledProcessError"; (4) `packaged` on Windows: the build's verify finds no handshake, and flags the interpreter's own standard library (`python313.zip`, `DLLs`, `Lib`, the prefix) as "the main sidecar's site-packages". Passed: `oracle-unit`, `shell`, `cargo-age`, `packaged` on macOS. The T2 54k performance and memory job does not exist yet.
- Implements: ADR-0061 decision 4 (what a green exit means; a platform-only failure that is not cheap and not a safety test is `fixme` due M11 with a backlog row), decision 3 (the three-OS packaged smoke run stays, to catch drift while it is a one-line fix).
- Capabilities: none changed.
- Invariants touched: none.
- Risk class / lane: Low / light. Review budget row: tooling, gates (one verifier round; no guard of real data or privacy is changed).
- Mechanism questions (§3 step 2): none; CI configuration and type-checker settings.
- Acceptance tests: the next manual CI run (T1 and T2) green, or every remaining red a `fixme` due M11 with its row.
- Expected doc impact: the journal; `design/backlog.md` rows for any platform failure deferred to M11; the M1a exit record.
- Open questions: none yet (the Linux and Windows engine causes are diagnosed by the next run).
- Base: d3dfca75.

## Decisions
- Round A (agent): (1) the pyright errors on Linux (51 in `ledger_engine.fs.darwin` itself, 19 in its tests, 7 in `backend/tests/test_main.py`) wait for the owner: `pythonPlatform: "Darwin"` changes the pinned strict configuration (`tests/architecture/test_gates.py`, `test_type_gate.py` refuse any change, and the pre-push gate refused it), a gate change that is the owner's; the other way is a platform guard in the write path's file-system adapter, product code. (2) `ci.yml`: `repo-checks` builds the engine environment before the meta-tests (the harness meta-test starts the sidecar's engine; the later step that built it for the schema and licence checks is folded into it), and `frontend` builds it after the backend's. (3) `packaged.yml`: on Linux and Windows, when a step failed, a diagnosis-only step runs the bundled interpreter with the engine's flags and site and prints `sys.path`, whether the engine imports, and the interpreter's `getsitepackages()`, so the next run shows the causes instead of an exception's name. No product code changes in round A.

- Round B (agent, after the third run of 2026-11-13 at 9528d7f5: `frontend` green; `repo-checks` failed only on `corpora/ledgers/mirror-checksums.json`, out of date on Linux; the packaged diagnosis printed nothing, since under the runner's `pipefail` the `ls` of a missing candidate ended the step): the diagnosis picks the interpreter with a loop; `repo-checks` prints the diff of the mirror checksums as the runner builds them, when a step failed. The pyright errors are S0113's (landed before this round).

- Round C (agent, after the fourth run of 2026-11-13 at 7219c44b: `frontend`, `shell`, `oracle-unit`, `cargo-age` and macOS packaged green; pyright green on Linux after S0113): (1) `repo-checks`: the diff showed one value, `parity/plugins-local` `ledger_errors`, whose two plugin-error rows carry Python tracebacks (the interpreter's install path, standard-library line numbers; Python 3.13.16 on the owner's Mac, 3.13.18 on the runner): a weakness of the snapshot, not of the mirror (every other table, postings and balances included, matched). `engine/tests/mirror/checksums.py` now cuts a traceback inside a value to its last line, and the snapshot is regenerated (that one value changes; reviewed with `--rows`: the first line and the exception line stay). (2) `backend`: `test_the_boots_recover_all_starts_the_first_engine_through_the_scheduler` skips off macOS (its recover-all lists through the file-system port, macOS-only until BL-0087), and BL-0261 records that off macOS the boot then says the app folder is missing instead of the platform being unsupported. (3) The packaged diagnosis also looks under `shell/src-tauri/target/release/sidecar/` (the Linux build's layout). Windows' diagnosis (round B) showed the engine's path is right and the engine imports; the build check's "main sidecar's site-packages" complaint is a false positive (Windows' `getsitepackages()` lists the prefix itself), and (as read then) the handshake still crashed; the verifier (F1) showed the self-check at 7219c44b and after reports no engine, handshake or ping failure, only the path false positive: the crash was the pre-S0113 one. (4) The engine's 10 Linux test failures are S0113's (its round 2).

- Round D (agent): the diagnosis finds the unix build's `bin/python3.13`. The packaged-only run at d8b76072 showed the Linux build tree right (the engine's path is the bundled standard library and its site-packages; the engine imports) and the failure only inside the AppImage.
- Round E (owner, 2026-11-13: "Defer to M11 (Recommended)"; ADR-0061 decision 4b): `packaged.yml`'s matrix marks Windows and Linux `fixme: true`, and the job is `continue-on-error: ${{ matrix.fixme }}`; macOS stays strict. Rows BL-0263 (Linux AppImage engine, M11) and BL-0264 (Windows: the build check's path false positive; the "handshake crash" in the question to the owner was the pre-S0113 one, verifier F1; M11). The deferral is a loosening of the packaged check that was green at M0, hence the owner's words.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: round A none (the diagnosis steps print only, `|| true`); round C changes the mirror snapshot's normalization (`engine/tests/mirror/checksums.py`: a traceback in a `ledger_errors` message cut to its last line; must-not-pass: every other table and column hashed as before, the snapshot unchanged but for that one value); round E loosens the packaged check (Linux and Windows `continue-on-error`, owner-approved; its must-not-pass: macOS still fails the run, shown by run 6's macOS job with `fixme: false`).
- Self-mutation: not applicable.
- Population: not applicable.

## Runs
- 2026-11-13, first run (cancelled): started with the short SHA; `actions/checkout` needs the full one.
- 2026-11-13, second run at d3dfca75: six failures above.
- Third run, at 9528d7f5 (round A): `frontend` green; `repo-checks` (mirror snapshot), `backend` and `engine` (pyright), packaged Linux and Windows (diagnosis printed nothing) red.
- Fourth run, at 7219c44b (round B, S0113 round 1): pyright green; `backend` (one boot test), `engine` (S0113's ten Linux failures), `repo-checks` (the snapshot's traceback value), packaged Linux and Windows red.
- Fifth run, at 9c251c7f (round C, S0113 round 2): all of T1 green; packaged Linux and Windows red.
- A packaged-only run at d8b76072 (round D): the Linux build tree is right; the AppImage fails.
- Sixth run, at 70d7d512 (round E): **success**, the M1a exit run; packaged Linux and Windows ran and failed, `fixme`.
- Privacy-scan workaround (PROCESS.md §8): GitHub's 11-digit run IDs are refused as account numbers, so runs are named by date and order here.

## Verifier
- Round 1, head 70d7d512: pass with findings. Attacks: 8 (macOS strictness through the workflow_call nesting; the fixme's reach per step; the diagnosis steps print only; the environment order; the snapshot recomputed and its cut attacked; the backend skip against the log; BL-0263 and BL-0264 against the logs; the flag's link to its rows).

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Medium | d | Fixed (a false statement): since S0113 the Windows self-check reports only the build check's path false positive, no handshake crash; BL-0264, this journal and M1a-exit.md corrected, and the owner asked again on the corrected facts: "Fix the check now (Recommended)" (S0114) |
| F2 | Medium | c | The fixme hid the whole job, so later steps and a new failure were invisible; owner, 2026-11-13: "Narrow it (Recommended)": S0114 makes each fixme job pass only on its known failure |
| F3 | Low | c | Fixed (cheaper than the limit): the traceback cut applies only to the `ledger_errors` message column; the chained-exception case is a stated limit in its docstring; the snapshot is unchanged |
| F4 | Low | d | Fixed: the runs listed in order (the exit run is the sixth), the Hand-off names round C's and round E's checks |

## Retro
- Build about 90 min over five rounds, fix 20 min, 1 verifier round, findings 0H/2M/2L. Cheaper gate: a manual CI run at least once mid-milestone (or the per-platform pyright pass, now in T0) would have shown these four classes while each was one change; and reading every run's log before stating a cause to the owner would have avoided F1. Process checks: invariants 0 catch / 0 false positive; generated 1 catch / 0 false positive (the machine-specific snapshot); the privacy scan 0 catch / 1 false positive (GitHub run IDs read as account numbers, worked around by naming runs).
