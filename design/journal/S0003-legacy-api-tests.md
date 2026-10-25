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
- Legacy suite in process (copy, disposable venv): 1109 tests, 1099 passed, 9 failed, 1 skipped in about 25 s. The 9 legacy failures are the legacy's own.
- API-level tests (a client fixture in their closure): 170. Passing in process: 165 (the denominator).
- Out of process through the thin shim: **107 of 165 pass (65%)**, criterion ≥ 80%. 220 s wall time, about 1.3 s per test (one legacy process per test).
- The 58 that do not run out of process are all structural, none flaky:
  - 38 not shimmed: their modules build their own in-process client (open deposit 25, statement import 12, prices 1). Statement import is cut (ADR-0030).
  - 15 depend on in-process stubs that cannot cross a process boundary: the AI/LLM client (natural-language parser 9, LLM investment parse 4) or a mock's recorded call arguments (categorize 2).
  - 4 stub the AMFI price source; out of process they hit the offline proxy (a 502 or "unavailable"), which shows the egress block works.
  - 1 captures in-process log records.
- Decision (agent, by the spike's own fallback, review:C §5 S12): the legacy API tests are **reference reading for each vertical's characterization slice**, not an M0 deliverable. The runner stays: it runs 107 of them out of process against v0.3 on demand (`scripts/oracle/legacy_api_tests.py`), which a vertical's first slice can use as a starting characterization set for its endpoints. Raising the ratio would need a stub LLM endpoint and per-module client patching, which is no longer a thin shim.

## Verifier
- Head SHA verified:
- Attacks tried:
- Findings:

## Retro
-
