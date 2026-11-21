# S0137: Windows drain and a dead member's lock

## Brief
- Base: `53db6dacade00f245c2ba0076345af685cb833cf` (`next`).
- Goal: close BL-0291. (1) State in engine-jobs §5.2 that on Windows a dead member's byte-range locks are freed shortly after it ends, not when its process object is signalled, and that the drain does not wait for them. (2) Fix the runner test that assumed the opposite (`test_a_plugins_child_holding_a_lock_is_ended_with_the_engine`; its `[close]` case failed 4 times since 2026-11-18, always `(lock_free, child_gone) == (False, True)`), and the two other runner tests that assert the same thing at once. (3) Add the two fake-API tests S0129's round 2 asked for (R2-1 a failed `IsProcessInJob`, R2-2 a job with more than 64 members) and run their mutants. No product code change.
- Owner's decision (verbatim): the orchestrator brought the owner the diagnosis and two options. Owner, 2026-11-21, in session: "Yes" (to the diagnosis and to option 1: state the limit in the spec and fix the test; no product code change).
- Diagnosis (orchestrator, checked against the code and the runner logs): the close returned after the drain, not at its 2 s bound (the whole close case took about 0.65 s in the two runs with timestamps). The drain's Windows "no live member" treats a signalled member as having released its file locks. Microsoft's LockFileEx documentation: the OS unlocks a terminated process's locks, but "the time it takes for the operating system to unlock these locks depends upon available system resources", and access may be denied until then. Why only the close case failed (0 failures of cancel, retire and crash in about 105 tries) is **unexplained**; the code does not show a reason.
- Consequence: no engine runs beside a live member. The engine lock is waited for (the engine's `take_engine_lock` and the sidecar's `wait_engine_lock` both wait, no `LOCKFILE_FAIL_IMMEDIATELY`; checked in `ledger_engine.fs.windows` and `app.platform.adapters.windows_files`), so a lingering engine lock only delays the next engine. A lock a plugin child took on a user's file can make a save refuse for that moment (the legacy-lock check uses `LOCKFILE_FAIL_IMMEDIATELY`: `locked`, or an I/O error): fail closed, never a wrong write. The row's earlier "fail closed (a refusal)" for the next engine was wrong: for the engine lock it is a wait.
- Implements: engine-jobs §5.2 (a new **Limit (lock release on Windows)**), §12's test list, §15's index; ADR-0064.
- Capabilities: CAP-DATA-ENGINE-JOBS (unchanged status).
- Invariants touched: none (INV-10's fresh engine after user code is unchanged; the drain's rule is unchanged).
- Risk class / lane: Low / full lane's engine area, test and spec only. Review: verifier, one round (tooling/docs row).
- The bytes it protects: none new. The drain keeps the rule "no engine beside a live member"; the limit is about a lock that outlives its dead owner for a moment, which can only refuse (fail closed).
- Which real setup needs this, and can a policy forbid the rest? Every Windows user whose plugin starts a child that locks a file (rare), and every engine on Windows for the engine lock itself (common, harmless: a wait). The owner chose the policy-style option (a stated limit, no new mechanism).
- Has this mechanism or its family needed two or more fix rounds before? The drain family has: S0057, S0073/S0075 (POSIX listing), S0129 rounds 1-2 (Windows). This is the first fix round on this test, and it changes no mechanism; the owner's option was taken before any edit.
- Acceptance tests:
  - `test_engine_windows.py::test_a_plugins_child_holding_a_lock_is_ended_with_the_engine[cancel|retire|crash of the engine alone|close]` (runner only): the child is gone at the report (at `close()`'s return for close); the lock is free within the file's relative `wait_until` (DEADLINE). Must not pass: a report or a close return before the drain (child alive).
  - `test_engine_windows.py::test_the_drain_ends_a_plugins_child_and_every_handle_it_held_is_closed_at_the_reap` and `::test_a_new_engine_starts_only_after_the_previous_engines_job_is_drained`: the same limit (child gone and handles closed at once; lock free within the wait).
  - `test_windows_job_fake.py` (every OS): a failed `IsProcessInJob` leaves the member not judged (the job is not ended, the drain does not reap while it fails); a job of more than 64 members is listed whole (the `ERROR_MORE_DATA` retry and the buffer growth) and drained.
- Expected doc impact: engine-jobs §5.2, §12, §15; BL-0291 closed; roadmap M1b's next-items line. No user docs (no behaviour change), no ADR (the owner's decision is recorded here and on the row).
- Open questions: none (the owner answered).

## Decisions
- Option 1: a stated limit, the test fixed, no product code change (owner: "Yes", 2026-11-21, in session).
- The two other runner tests that assert a dead child's lock is free at once (the 20-run reap test and the drain-before-spawn test) get the same limit: they make the same claim the spec now withdraws, so they would be the next misread flake. Their checks of what the spec still claims at once (the child gone, every handle closed: `open_exclusively`) stay at once (agent).
- The new fake tests pass on the base (the product is right there); they are tagged `@regression-guard` and their evidence is the mutants they kill (as S0129's control test). PROCESS.md §3 step 3's fail-on-base rule exempts the tag (agent).
- The relaxed runner test cannot fail on base in the sense of step 3 (it loosens a check that was wrong); it keeps its must-not-pass shape (child alive at the report or the close) (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test: no product check is added or changed. The test checks changed:
  - the four-case runner test: "the child gone at the report / at `close()`'s return" must not pass a close or a report before the drain (the child alive: `watched[0].gone()` False); "the lock free within `wait_until`" must not pass a lock never freed. The lock's at-once check is dropped (the stated limit).
  - the 20-run reap test: `open_exclusively` and `child.gone()` stay at once (what the spec still claims); the lock within the wait.
  - the drain-before-spawn test: the child gone at the spawn (must not pass a spawn beside a live child); the lock within the wait.
  - the two fake tests: the job not ended and no reap while `IsProcessInJob` fails (must not pass the mutant that takes it as judged); the job listed whole with 72 members and drained (must not pass a listing that answers None or never grows).
- Self-mutation (`scripts/mutants.py`, plan in the session scratchpad; `cmd` runs `test_windows_job_fake.py` with the copy's `src` first on `PYTHONPATH`): 4 tried, 4 killed: `in-job-failure-judged` (the failed call's `judged = False` dropped), `in-job-failure-held` (the failed call's member held as a member: killed, the drain then ends the job while the call fails), `more-data-answers-none` (`room *= 4` → `return None`), `more-data-no-growth` (`room *= 1`: killed by timeout, the listing loops). Control: the same plan with the two new tests deselected, 4 tried, 0 killed, 4 survived, so the new tests are what kill them.
- Population: the claims "the lock is free at the drain's end" on Windows, found by grepping `lock_free` in `test_engine_windows.py` (12 uses: 3 at a drain's end or a spawn, all changed; the others are before the end, the engine lock while held, or the sidecar's own release, unchanged) and the spec's Windows text in §5.1, §5.2, §12, §15 (§5.2's "No live member" and §12's two Windows test lines changed; §5.1's Windows engine-lock text claims nothing at the drain's end). Product docstrings (`windows_job`, `engine_group.live_member_in_job`) say a signalled member has closed its handles, which stays true; not changed (no product code change).

## Verifier

## Retro
