# S0003: Legacy API tests out of process (spike S12)

## Brief
- Goal: measure whether the legacy v0.3 backend's API-level tests can run against the legacy app as a separate process through a thin fixture shim, so they can serve as characterization tests for the new app's verticals. Spike S12's criterion (review:C §5, DEP-12): ≥ 80% of the API-level tests run (and pass) through the shim. Fallback if it fails: they stay reference reading for each vertical's characterization slice, not an M0 deliverable.
- Implements: review:27 §1(d) (spikes S9 and S12); review:C DEP-12; PROCESS.md §3 step 3 (characterization tests against the legacy app on copies).
- Capabilities: none (process tooling).
- Invariants touched: INV-24 (the legacy tests run from a copy of the legacy backend in a work folder, never in the legacy worktree; every legacy process runs on a test's temp root through the launcher's guards; the launched app gets no outbound network), INV-23 (nothing from the run enters the repo except counts).
- Risk class / lane: Medium / full (test tooling that starts the legacy app; no ledger of the owner's is involved).
- Acceptance tests (`scripts/tests/test_legacy_api_tests.py`, stdlib):
  - the outcome comparison: the denominator is API-level tests that pass in process; a test counts as passing out of process only if it passed **and** the shim really served it (a test whose module builds its own in-process client is "not shimmed");
  - the backend copy never writes into the legacy worktree: a destination inside the legacy worktree, the repo or the live data folder is refused; `__pycache__` is not copied; the source is byte-identical afterwards;
  - the launcher blocks outbound HTTP for the legacy process (proxy variables set, loopback exempt).
  - end to end (local tier): the full in-process run and the out-of-process run of the API-level tests, with counts.
- Expected doc impact: this journal (the measured ratio and the failure classes); `design/backlog.md` WS-QA (the result); ARCHITECTURE scripts list. No user docs.
- Open questions: none.
- Base SHA: a41c27a (rebased onto the S0002 fixes; the offline launcher change was written on top of aff2f22).

## Decisions
- The legacy runtime venv has no pytest and lives in the read-only worktree, so the run builds a disposable venv in the work folder from the legacy's pinned `requirements.txt` plus `pytest==8.4.2` (the legacy CI never ran the tests, so there is no pinned test toolchain) (agent).
- The shim patches only the conftest's `_create_test_client(root)`: each fixture built on it gets an `httpx.Client` for a legacy process started on the test's temp root. Tests whose modules build their own `TestClient` are counted as not shimmed rather than made to pass with more patching (agent; keeps "thin" honest).
- The app under test is the legacy worktree's code started by `launch_legacy.LegacyApp`; the tests themselves run from the copy (agent).

## Result (spike S12: FAIL against its criterion; the fallback applies)
Corrected after the verifier's F2. The first count chose API-level tests by fixture name and missed about 100 tests whose clients have other names. It reported 107 / 165 = 65%, which was wrong.
- Legacy suite in process (copy, disposable venv): 1109 tests, 1099 passed, 9 failed, 1 skipped.
  - About 5 of the 9 failures depend on pytest's working directory ("Backup directory does not exist: data/backups"), not on the legacy code. They pass out of process, where the working directory is the root. They are outside the denominator either way.
- API-level tests: **280**. A test counts when an in-process FastAPI `TestClient` was built during it or handed to it, whatever the fixture is called. **269** pass in process (the denominator).
- Out of process through the thin shim: **107 of 269 pass (40%)**, against the ≥ 80% criterion. About 226 s, one legacy process per test.
- The 162 that do not run out of process are structural, none flaky:
  - **142 not shimmed:** their modules build their own in-process client. By module: documents 29, open deposit 26, prices 26, metadata 23, statement import 15 (a cut feature, ADR-0030), isolation 12, commodities 6, static cache headers 3, setup 2.
  - **15 depend on in-process stubs** that cannot cross a process boundary: the AI/LLM client (natural-language parser 9, LLM investment parse 4) or a mock's recorded call arguments (categorize 2).
  - **4 stub the AMFI price source.** Out of process they hit the offline proxy and fail, which also shows the egress block works.
  - **1 captures in-process log records.**
- Of the 107, **105 are offered as characterization tests**. The other 2 use a `monkeypatch`/`mocker` stub that never reached the legacy process, so they may pass for the wrong reason (F5).
  - Limit: tests that stub with `mock.patch` decorators or context managers are not detected. The verifier found 7 such tests among the 107. A vertical's first slice reads each test it adopts.
- Decision (agent, by the spike's own fallback, review:C §5 S12): the legacy API tests are **reference reading for each vertical's characterization slice**, not an M0 deliverable. The runner stays: `scripts/oracle/legacy_api_tests.py` runs them out of process against v0.3 on demand and lists which ones are usable. Raising the ratio would need per-module client patching and a stub LLM endpoint, which is no longer a thin shim.

## Verifier
- Round 1, head e4d1f90: **PASS WITH FINDINGS** (risk class Medium kept). Attacks tried (11): the full runner end to end with counts compared; JUnit-to-node-id mapping (all 170 found); the API-level rule against every client fixture in the suite; the work-folder guard through `main()` with and without `--skip-setup`; a mutant removing the offline wiring; egress for every network client the legacy app uses; shimmed tests whose stubs were silently ignored; writes into the legacy worktree; `ProcessClient`'s error path and port races; tests flipping outcome; the rubric and ADR check for the fallback.
- Findings and resolution:
  - F1 Medium (INV-24): `main()` created `--work` before checking it, and `--skip-setup` skipped the check. Fixed: `refuse_dir(work)` runs first, always. A test covers both modes and shows the legacy tree is unchanged.
  - F2 Medium: the API-level population was wrong (by fixture name). Fixed: the shim records every test during which a `TestClient` is built or received. The corrected numbers are in Result. The verdict is the same, and the recorded ratio is now true.
  - F3 Low: a mutant removing the offline wiring survived. Fixed: `LegacyApp.start` builds its environment with `legacy_env` (tested, including that `start` uses it). The shim itself runs only inside the legacy test venv and is covered by the end-to-end run, not by a stdlib meta-test.
  - F4 Low: the proxy only covers HTTP clients, and IMAP reads credentials from the inherited environment. Fixed: a legacy process now gets an allowlisted environment (PATH, HOME, locale, TMPDIR, TZ, user names) and none of the caller's other variables, so no credential or `${ENV}` value is there to use. The docstring says exactly that.
  - F5 Low: passing tests whose in-process stub was ignored were offered as characterization tests. Fixed for `monkeypatch`/`mocker` stubs; the limit for `mock.patch` is stated in Result.
  - Question: the about 5 legacy in-process failures that depend on the working directory are noted in Result.

## Retro
- The first count was wrong because the population was chosen by name rather than by behaviour. Recording what a test actually does (here, building a `TestClient`) is the cheaper and correct rule.
- Process cost: implementation about 50 min; two full runs about 5 min each; brief, docs and verifier about 25 min; fixes about 25 min.
