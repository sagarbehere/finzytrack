# S0159: A boot that finds the app folder gone says quit and reopen (BL-0248)

## Brief
- Goal: M1b exit plan step 4 (roadmap §1). When the boot's own engine-lock wait finds the app folder removed or replaced (just after the start made it and synced its parent), the boot's `recover_all` job ends `ENGINE_UNAVAILABLE {app_folder_missing}` with no report. `blocking_for(None, …)` turns every report-less end into `NotRecovered(None, None, "no_report")`, so the blocked screen says "could not check for unfinished saves … Press Retry", and Retry meets the same folder. Carry the job's `ENGINE_UNAVAILABLE` reason into the `NotRecovered` when there is no report, so the screen uses the text S0102 round 1 already gives this cause ("Its data folder was removed or replaced while Finzytrack was open. Quit Finzytrack and open it again.", no Retry). No new text.
- Implements: `design/specs/engine-jobs.md` §8.1 "Afterwards" (no report blocks every open, fail closed) and §11 (`ENGINE_UNAVAILABLE {app_folder_missing}`: quit and reopen); ADR-0063 decision 2; ADR-0064; S0102 (BL-0244) and its round 1 narrowing.
- Capabilities: CAP-DATA-ENGINE-JOBS (status unchanged).
- Invariants touched: none changed. The fail-closed rule of recover-all (a run that never listed blocks every open; INV-32's neighbourhood) must hold for every reason, so it gets a must-not-pass test.
- Kind: product code (shipped): the sidecar's recovery mapping and its screen text. Not a new write path or write outcome (nothing is written; the outcome "no workspace opens" is unchanged), so the review row is "Other product code": the verifier only.
- The bytes it protects: none (no write). The outcome it keeps truthful: every open stays blocked; only the cause text (class (d), IMP-0078: best effort) and the action list change, and only for `app_folder_missing`.
- Real setup / policy: no mechanism is added or hardened; a reason already computed is passed on. Fix-round history: the app-folder family had rounds in S0100 and S0102; this slice adds no check to that family.
- Risk class / lane: Low / light (text routing, no write; the verifier may raise it).
- Acceptance tests (fail on base 3cb50b79):
  - `backend/tests/platform/test_app_folder_identity.py::test_S0159_a_boot_whose_lock_wait_finds_the_folder_gone_says_to_quit_and_reopen`: the queue's `NoEngine(…, "app_folder")` → the scheduler's reason `app_folder_missing` → the job's `Failed` error → `blocking_for(error, …)` → `not_recovered_screen`: the quit-and-reopen text, no `retry`.
  - `backend/tests/platform/test_recover_all.py::test_S0159_an_ended_job_with_no_report_blocks_every_open_whatever_its_error` (must-not-pass: fail closed for each `ENGINE_UNAVAILABLE` reason and for any other error).
  - `@regression-guard` `backend/tests/platform/test_recovery_text.py::test_S0159_other_unavailable_reasons_keep_the_retry_screen`: `stopped_often`, `incompatible`, `cannot_start`, `closing` and any other error keep today's "could not check … Press Retry" screen with `retry`.
- Expected doc impact: `recovery.py` docstrings; the generated `engine-job-texts.md` row description of `msg:recovery.app_folder_missing` (texts unchanged); spec §8.1 one line; backlog BL-0248 closed; roadmap §1 step 4 done. User docs: none (the screen is M2's, BL-0092).
- Open questions: none on the rubric.

## Decisions
- **The fix is in `blocking_for`, not in `not_recovered_screen`** (agent; pushback on the request, which named `not_recovered_screen`). The screen already maps `app_folder_missing` (both spellings, `APP_FOLDER_CAUSES`) to the quit-and-reopen text since S0102 round 1. What lost the reason was its input: `blocking_for` took `Report | None` and wrote `"no_report"` for every report-less end. It now also takes the job's error (`DomainError`): an `ENGINE_UNAVAILABLE` error's reason becomes the `NotRecovered`'s `why`; anything else stays `"no_report"`. Every report-less end still yields exactly one `NotRecovered`, so every open stays blocked.
- **No new text** (agent; IMP-0078): the existing `APP_FOLDER_GONE` after "Finzytrack could not check for unfinished saves." Both sentences are true here: the listing never ran, and the folder is not the one of the start.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `recovery.blocking_for`, a report-less end (fail closed: every open blocked) → `test_recover_all.py::test_S0159_an_ended_job_with_no_report_blocks_every_open_whatever_its_error` (each of the five `ENGINE_UNAVAILABLE` reasons, `JOB_CANCELLED`, `ENGINE_STOPPED`, `INTERNAL`: exactly one `NotRecovered`, no folder, `why` the reason or `no_report`); the existing `test_a_recover_all_that_never_listed_blocks_every_open` (None, unlisted report) still passes.
  - `recovery_text.not_recovered_screen`, the tag "quit and reopen, no Retry" → `test_S0159_a_boot_whose_lock_wait_finds_the_folder_gone_says_to_quit_and_reopen` (the whole chain from the queue's `NoEngine(app_folder)` and the scheduler's `no_engine_reason`), and its guard `test_recovery_text.py::test_S0159_other_unavailable_reasons_keep_the_retry_screen` (`@regression-guard`: the other reasons keep Retry).
- Self-mutation (the check guards the fail-closed open, so ledger data; `scripts/mutants.py`, plan `s0159-mutants.toml` in the session scratchpad, the copy's `src` first on `PYTHONPATH`): 4 tried, 4 killed: an error falling through to the report branch, an error letting every open through, the reason dropped (`no_report` always), the screen matching only the engine's spelling `engine.app_folder_missing` (which also shows the copy's source ran).
- Population: the report-less ends of a recover-all job are the scheduler's `Failed` outcomes: `ENGINE_UNAVAILABLE` (its five reasons, `EngineUnavailableDetails.reason`), `JOB_CANCELLED` (`Cancelled` effect), `ENGINE_STOPPED {crashed}` (`_lost_outcome`), `INTERNAL` (a raised work); found by reading `scheduler._effect`, `_closing` and `_lost_outcome`. In: 8 parametrized cases, one per reason or code; out: none. Any error whose details are not `EngineUnavailableDetails` takes the `no_report` branch, by type, not by code.

## Verifier

## Golden changes
- `design/generated/engine-job-texts.md`, row `msg:recovery.app_folder_missing`: only its description column (when the text shows) gains the lock-wait case and BL-0248; the text itself is unchanged. No screen text changed.

## Retro
