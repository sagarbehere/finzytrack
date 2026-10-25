# Process improvement log

Append-only. One entry per process improvement or heal (`PROCESS.md` §9). Each kept entry names the `PROCESS.md` section it changed.

Format:

```
## IMP-nnnn: <title> (YYYY-MM-DD)
- Trigger: <escape ID | metric | retro | owner request>
- What changed: <gate | test | prompt | rubric | skill | doc>, with paths
- PROCESS.md section: §n
- Why:
- Expected effect:
- Observed effect: <filled in at the next reflection>
- Status: kept | reverted | superseded by IMP-nnnn
- Owner approval: "<verbatim quote>" (date, commit)   ← only where the change class needs one
```

---

## IMP-0001: Seed the process kit at `next` setup (2026-10-24)
- Trigger: owner request (review:RESUME, "Next session: unattended start").
- What changed: `PROCESS.md`, `escalation-rubric.md`; T0 hooks `.githooks/pre-commit` and `.githooks/pre-push`; check scripts `scripts/privacy_scan.py`, `scripts/check_staged.py`, `scripts/check_invariants.py`, `scripts/check_capabilities.py`, `scripts/check_generated.py` with meta-tests in `scripts/tests/`; Claude Code guard hooks and settings in `.claude/`; agents `implementer` and `verifier`; skills `orient`, `slice`, `escalate`.
- PROCESS.md section: all (initial version).
- Why: M0 "thin" kit (review:27 §1(d), review:F §3.12): only what the first slices need.
- Expected effect: the first slices run under the loop with privacy and real-data guards from the first commit.
- Observed effect: pending (first reflection).
- Status: kept.

## IMP-0002: Capability map source is TOML, not YAML (2026-10-24)
- Trigger: setup.
- What changed: `design/capabilities.toml` is the source; `scripts/gen_capabilities.py` generates `design/capabilities.md`.
- PROCESS.md section: §2, §7.
- Why: Python's standard library reads TOML (`tomllib`), so T0 checks need no third-party package or network. Same human-editable data as the YAML the plan named (review:27 §1(d), B-1.5). Agent-level choice.
- Expected effect: the capability check runs in the pre-push hook with zero setup.
- Observed effect: pending.
- Status: kept.

## IMP-0003: Owner push gate (2026-10-24)
- Trigger: owner instruction for the unattended setup ("commit next locally only, never push to the public repo").
- What changed: `.githooks/pre-push` refuses every push until `git config finzytrack.allowPush true` is set in the clone; `.claude/settings.json` makes agents ask before `git push`.
- PROCESS.md section: §10.
- Why: the owner reviews `next` before its first public push. A tightening (agent-approved); the owner removes it by setting the config.
- Expected effect: no accidental public push before review.
- Observed effect: pending.
- Status: kept.

## IMP-0004: INV-27 enforcement list matches the v0.4.0 scope (2026-10-25)
- Trigger: owner question Q6 (the Export → Import archive is deferred past v0.4.0, so INV-27's planned archive round trip cannot run in v0.4.0).
- What changed: `design/INVARIANTS.md` INV-27 "Enforced by": the raw-tar folder round trip, layout, path-field schema and portability corpus checks; the archive round trip joins when the archive ships. The rule text is unchanged.
- PROCESS.md section: none (invariant content).
- Why: an enforcement list must be buildable within the release scope (ADR-0043).
- Expected effect: INV-27 can leave "pending" in M3.
- Observed effect: pending.
- Status: kept.
- Owner approval: "I'm accepting your recommendations/actions on questions 5-11." (2026-10-25, in session; covers Q6)

## IMP-0005: Safety outranks every number (2026-10-25)
- Trigger: owner question while deciding spike S4's write budget ("arbitrary budget/performance numbers end up hurting more than they help").
- What changed: `PROCESS.md` §6 states that no performance target, budget or baseline may be met by weakening a safety check; an agent that cannot meet a user-facing absolute escalates with numbers.
- PROCESS.md section: §6.
- Why: the cheapest way to hit a write-time target is to weaken the re-check; the invariants forbid it, but the process text did not say so where the numbers are defined.
- Expected effect: performance work never trades away safety; misses come to the owner as escalations (as S4 did).
- Observed effect: pending.
- Status: kept (tightening, agent-approved).

## IMP-0006: T1 runs the oracle harness's unit tests (2026-10-25)
- Trigger: slice S0002 (spike S9) adds `scripts/oracle/` with its own pinned Beancount environment; its semantic diff and classifiers decide whether a legacy divergence blocks promotion.
- What changed: `.github/workflows/ci.yml` gains the `oracle-unit` job (uv 0.13.0, `uv run --locked`, the tests in `scripts/oracle/tests/`). The copy-guard meta-tests (`scripts/tests/test_oracle_copies.py`) run in the existing repo-checks job.
- PROCESS.md section: §6 (T1 contents).
- Why: a wrong classifier silently turns a real divergence into an "expected" one; the end-to-end runs need the legacy worktree and stay local, so the logic is pinned by unit tests that CI can run.
- Expected effect: classifier and diff regressions fail CI.
- Observed effect: pending.
- Status: kept (tightening, agent-approved).

## IMP-0007: INV-24 enforcement list names the copy guards (2026-10-25)
- Trigger: S0002 verifier finding F10 (the guards moved into `scripts/oracle/copies.py` and gained containment checks).
- What changed: `design/INVARIANTS.md` INV-24 "Enforced by" adds `scripts/oracle/copies.py`, `scripts/tests/test_oracle_copies.py` and `scripts/oracle/tests/test_harness.py`. The rule text is unchanged.
- PROCESS.md section: none (invariant content).
- Why: "where is this enforced?" must stay accurate.
- Expected effect: none on behaviour; the invariant check also requires the new files to mention INV-24.
- Observed effect: pending.
- Status: kept.
- Owner approval: "I accept your recommendations for the four questions in the design/inbox/" (2026-10-25, in session; covers `design/inbox/answered/20261025-inv-24-enforced-by.md`)

## IMP-0008: reviewers check that tests test the spec, not the code (2026-10-25)
- Trigger: the owner's comment after S0002–S0004, where two tests had been bent to the code (an asserted price the legacy never writes; an over-match the spec forbids, asserted as expected).
- What changed: EP-29 in `design/ENGINEERING.md` (with its checklist line); the verifier's "Review the tests" rule and a report line in `.claude/agents/verifier.md`; the journal template's Verifier section; `PROCESS.md` §4 rules.
- PROCESS.md section: §4.
- Why: a test that copies the code's output protects the code's bugs; only a spec-derived expectation catches them.
- Expected effect: verifier findings against tests rise at first; escapes of "test agreed with the bug" go to zero.
- Observed effect: pending.
- Status: kept (tightening).
- Owner approval: "The reviewers should also review the tests and carefully check that the tests are testing the spec and not bending themselves to the code as it is written" (owner, 2026-10-25, in session)

## IMP-0009: the unused Write deny rule for the live data folder is removed (2026-10-25)
- Trigger: Claude Code warned at session start that the `Write(...)` deny rule never matches (an `Edit(...)` rule covers every file-writing tool).
- What changed: that line is removed from `.claude/settings.json`; the `Read(...)` and `Edit(...)` deny rules and the guard hooks stay.
- PROCESS.md section: §8 (agents and real data).
- Why: a rule that matches nothing gives false assurance and a warning on every start.
- Expected effect: no change in what is blocked; the startup warning goes away.
- Observed effect: pending.
- Status: kept.
- Owner approval: "remove it" (2026-10-25, in session)

## IMP-0010: must-not-pass list and self-mutation before hand-off (2026-10-25)
- Trigger: pilot measurements (ADR-0057): all five High findings in S0002–S0004 were checks that accepted too much, and the verifiers found most of them with mutants.
- What changed: PROCESS.md §3 step 4 ("Before hand-off"); `.claude/agents/implementer.md`; `.claude/skills/slice/SKILL.md` step 6; the journal template's Hand-off section; new `scripts/mutants.py` with `scripts/tests/test_mutants.py`.
- PROCESS.md section: §3.
- Why: a loosening mutant the implementer's own tests miss is the cheapest sign of a missing must-not-pass test; finding it before hand-off saves a verifier round.
- Expected effect: fewer High findings of the "accepts too much" class reach the verifier; fewer verifier rounds per slice.
- Observed effect: in S0005 itself, 3 of the first 8 self-mutants of `scripts/mutants.py` survived; two showed missing tests (a tracked symlink out of the copy; copies left in `--work`), one was dead code and was removed.
- Status: kept (tightening).
- Owner approval: "I'll go with your recommendation for the PROCESS.md changes" (2026-10-25, in session; covers `design/inbox/answered/20261025-pilot-process-changes.md`)

## IMP-0011: population sweep by behaviour (2026-10-25)
- Trigger: S0003 F2 (API tests chosen by fixture name, about 100 missed) and S0004 F1 (13 ledger writers missed; the capability map was not swept).
- What changed: PROCESS.md §3 step 4 (third bullet); the journal template's Hand-off section; BL-0049 for the mechanical check.
- PROCESS.md section: §3.
- Why: a list built by name silently drops members whose names differ.
- Expected effect: no population-completeness finding from the verifier.
- Observed effect: pending.
- Status: kept (tightening).
- Owner approval: "I'll go with your recommendation for the PROCESS.md changes" (2026-10-25, in session; covers `design/inbox/answered/20261025-pilot-process-changes.md`)

## IMP-0012: re-verification after High or several Medium fixes (2026-10-25)
- Trigger: pilot measurements: round 2 found 6 more findings in S0002 and 8 in S0004.
- What changed: PROCESS.md §3 step 5; `.claude/agents/implementer.md`; `.claude/skills/slice/SKILL.md` step 7.
- PROCESS.md section: §3.
- Why: fixes are new code that no verifier has seen.
- Expected effect: no defect introduced by a fix lands unverified.
- Observed effect: pending.
- Status: kept (tightening).
- Owner approval: "I'll go with your recommendation for the PROCESS.md changes" (2026-10-25, in session; covers `design/inbox/answered/20261025-pilot-process-changes.md`)

## IMP-0013: "ADRs checked" line in every escalation (2026-10-25)
- Trigger: S0004 F7 (a merge question partly answered by ADR-0049 nearly reached the owner).
- What changed: `design/inbox/README.md` template.
- PROCESS.md section: §5.
- Why: the owner's time goes only to questions no ADR settles.
- Expected effect: no escalation that an existing ADR answers.
- Observed effect: pending.
- Status: kept (tightening).

## IMP-0014: T1 backend job and backend pre-push checks (2026-10-25)
- Trigger: S0006, the main sidecar's package skeleton (BL-0037).
- What changed: `.github/workflows/ci.yml` gains the `backend` job (ruff format and lint, pyright strict, lint-imports, deptry, pytest incl. the architecture and gate tests) and uv plus a backend sync in `repo-checks` for the licence step; `.githooks/pre-push` adds lint-imports and deptry and runs all backend tests; `scripts/check_licences.py` counts only the lockfile's runtime closure and judges SPDX expressions (AND, OR, WITH, parentheses), failing closed.
- PROCESS.md section: §6 (T0 pre-push and T1 contents).
- Why: every later backend slice inherits these gates from its first commit.
- Expected effect: layering, purity, Beancount-ban, type and lint regressions fail before landing.
- Observed effect: pending.
- Status: kept (tightening, agent-approved).
