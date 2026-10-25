# S0005: The pilot's process ADR (rubric and gates after S0002–S0004)

## Brief
- Goal: record the pilot's result as an ADR (ADR-0046 "after 3–5 slices the rubric and gates are adjusted and the result is recorded as an ADR"; review:20 §10), using the measurements in `S0004-intent-table.md`. Adjust the process where the measurements point, give the rough slice count for M1–M12 that ADR-0046 asks for, and build the one tool that makes the cheapest adjustment cheap: a self-mutation runner the implementer uses before hand-off.
- Implements: ADR-0046 (pilot), ADR-0001 (process), PROCESS.md §9 (records: "the pilot result is an ADR"; tightenings agent-approved, loosenings and rubric changes owner-only).
- Capabilities: none (process).
- Invariants touched: none changed. INV-24 binds the new runner (it works on a copy of the working tree and never writes into the checkout).
- Risk class / lane: Medium / full (process change plus a new check script with meta-tests; no ledger, finance or security impact).
- Proposed adjustments (each is a tightening; none loosens a gate, narrows the verifier or changes the rubric's items):
  1. Negative-case list before hand-off: the implementer lists every check that accepts, tags or refuses and names its must-not-pass test (makes EP-29's third bullet a hand-off step, not only a verifier question).
  2. Self-mutation before hand-off: for those checks, the implementer runs at least one loosening mutant each (High and Medium slices) with `scripts/mutants.py` and records "tried / killed" in the journal; a survivor gets a test before hand-off.
  3. Population sweep: a spec or check that claims to cover a whole population (every mutator, every API test, every error class) names the inventory it was built from and builds it by behaviour, row by row from the capability map and the code, never by name; the journal records counts in and out.
  4. Re-verification: after fixing a High finding, or three or more Medium findings, a fresh verifier round runs on the fix range before landing (S0002 and S0004 practice; round 2 found 6 and 8 further findings).
  5. Inbox template gains "ADRs checked", so a question an ADR already answers is caught before it reaches the owner (S0004 F7).
  6. Rubric items: unchanged.
- Acceptance tests (`scripts/tests/test_mutants.py`, stdlib, expected values from the ADR's rule text):
  - a mutant the tests catch is reported killed; one they miss is reported survived and the run exits 1;
  - must not pass: a baseline that already fails on the unmutated copy is an error (exit 2), and no mutant is counted as killed;
  - must not pass: a `find` text absent from the file, or present more than once, is an invalid mutant (exit 2), never a survivor or a kill;
  - mutants are independent (each starts from the unmutated copy);
  - the copy contains tracked and untracked-not-ignored files (uncommitted work is tested), and no ignored files;
  - the real checkout is byte-identical after a run, even when the test command writes files;
  - a test command that runs past the timeout counts as killed, and is reported as a timeout;
  - a mutant file outside the copy (absolute or `../` path) is refused.
- Expected doc impact: new `design/decisions/ADR-0057-pilot-result.md` and its index line; `PROCESS.md` §3 steps 4, 5 and its "Last change" header; `IMPROVEMENTS.md` IMP-0010–0013; `.claude/agents/implementer.md`; `.claude/skills/slice/SKILL.md`; `design/journal/README.md` template; `design/inbox/README.md` template; `ARCHITECTURE.md` scripts list; `design/backlog.md` (new item for a ledger-writer tag on capability rows); an inbox file asking the owner to approve the PROCESS.md edits. No user docs.
- Open questions: approval of the PROCESS.md edits (owner-only per the rubric's "approving changes to PROCESS.md"; agents may apply tightenings first). Batched in `design/inbox/`, blocks nothing.
- Base SHA: 0e98a20.

## Decisions
- No loosening is proposed, so nothing waits for the owner before it applies; the PROCESS.md edits are tightenings, applied first and sent for approval (PROCESS.md §9) (agent).
- Seeded mutants are not kept in the repo: tied to exact source text, they would go stale on ordinary edits; T2 mutation testing does that job once the packages exist (agent; ADR-0057 alternatives).
- The runner copies the working tree afresh for every run (baseline and each mutant), so a test command that writes files cannot leak into the next run (agent).
- The slice count uses the planned rows only (313 of 340) (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse, each with its must-not-pass test (`scripts/tests/test_mutants.py`):
  - baseline must pass → `test_failing_baseline_is_an_error_and_kills_nothing`;
  - `find` exactly once → `test_find_text_absent_is_invalid`, `test_find_text_twice_is_invalid`;
  - mutant file inside the copy → `test_mutant_outside_the_copy_is_refused`, `test_tracked_symlink_to_outside_is_refused`;
  - `--work` outside the checkout → `test_work_folder_inside_the_checkout_is_refused`;
  - killed vs survived → `test_mutant_the_tests_miss_survives_and_fails_the_run` beside `test_mutant_the_tests_catch_is_killed`.
- Self-mutation: 8 tried on `scripts/mutants.py`, 5 killed at first. Survivors: `no-containment` (no test for a tracked symlink pointing out of the copy → test added), `reuse-copy` (copies left in `--work` untested → test added), `no-absolute-check` (equivalent: an absolute path is never a working-tree file, so the check was dead code → removed). Re-run with a replacement mutant for `--work` inside the checkout: 8 tried, 8 killed.
- Population: the slice count's rows come from `design/capabilities.toml`, counted by `milestone` and `status` (313 planned, 15 cut, 12 deferred).

## Verifier
- Round 1, head 3b8f1f8: **PASS WITH FINDINGS** (Medium kept). Attacks tried (11): gates at head; tests fail on base; the runner on this repo with 7 mutants of its own; a baseline timeout; a non-UTF-8 target; a CRLF target; a tracked absolute symlink the command writes through; path escapes (absolute, `../`, symlinked, `sub/../x`); ignored and untracked targets; the ADR's numbers against the journals and the capability map; doc impact.
- Tests reviewed against the spec: every brief bullet has a test with hand-recomputable expectations; missing: baseline timeout, plan errors, bytes outside `find`, writes through a symlink, plan validation.
- Findings and resolution (fix commit below):
  - F1 Medium: a timed-out baseline was untested (a `== "fail"` mutant survived). Fixed: `test_baseline_timeout_is_an_error_not_a_kill`.
  - F4 Medium (INV-24): a tracked absolute or escaping symlink let the test command write into the checkout through the copy. Fixed: `check_links` refuses such trees before anything runs; tests for absolute, relative-escaping and in-tree links (the last must pass and stays a link). The containment check in `target()` became unreachable and is removed.
  - F5 Medium: ADR-0057 said "5 High and 15 Medium/Low"; the journals hold 39 findings, 5 High and 34 Medium/Low. Fixed in the ADR (not yet landed, so fixed in place) and noted in S0004's measurements.
  - F2 Low: plan errors (non-UTF-8 file, wrong field types) ended in a traceback with exit 1, which reads as "survived". Fixed: fields are type-checked, files are read as bytes, every plan error exits 2; tests per error.
  - F3 Low: text I/O rewrote CRLF line endings. Fixed: the mutant replaces bytes; CRLF test.
  - F6 Low: the ADR named PROCESS step 7, which did not change. Fixed.
  - F7 Low: the inbox example described a Medium finding as a High one. Fixed: it now describes S0002 F4.
  - F8 Low: plan-validation refusals were untested. Fixed: `PlanErrors` tests.
  - Question (Hand-off section unseen by the verifier): deliberate; the journal template now says so.
- Self-mutation after the fixes: 19 tried (the verifier's 7 re-targeted plus 12), 19 killed.
