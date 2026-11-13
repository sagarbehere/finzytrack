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

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none changed in round A (the diagnosis step prints only, `|| true`).
- Self-mutation: not applicable.
- Population: not applicable.

## Runs
- 2026-11-13, first run (cancelled): started with the short SHA; `actions/checkout` needs the full one.
- 2026-11-13, second run at d3dfca75: six failures above.
- Privacy-scan workaround (PROCESS.md §8): GitHub's 11-digit run IDs are refused as account numbers, so runs are named by date and order here.

## Verifier

## Retro
