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

## IMP-0015: verification scaled to risk; paperwork checks parked; shorter journals; EP-30 (2026-10-25)
- Trigger: owner request ("is the process already eating the development?", 2026-10-25); measurements in ADR-0058.
- What changed: PROCESS.md §3 steps 3, 5 and 7, §5, §6 (T1 contents, "not built yet"), §8; the journal template's Verifier section; ENGINEERING.md EP-30 and its checklist line; backlog status `parked` for BL-0034, BL-0036, BL-0038, BL-0040; the roadmap's M0 list.
- PROCESS.md section: §3, §5, §6, §8.
- Why: second and later verifier rounds on low-risk tooling, and checks on the process's own paperwork built before any escape, cost more than they caught.
- Expected effect: fewer fix rounds on tooling and docs; M0 ends sooner; no rise in escapes on data-touching code.
- Observed effect: measured at the end of M1a (ADR-0058 item 5).
- Status: kept (loosening, owner-approved).
- Owner approval: "I agree with your recommendations 1-4 on improving the process and re-evaulating as in 5" (2026-10-25, in session)

## IMP-0016: T1 frontend job (2026-10-25)
- Trigger: S0009, the Vue frontend skeleton (BL-0037).
- What changed: `.github/workflows/ci.yml` gains the `frontend` job (vue-tsc, eslint, prettier, vitest incl. the pinned tsconfig and lint bans, knip, the Playwright smoke flow in Chromium and WebKit) and a frontend install in `repo-checks` for the licence step; `.githooks/pre-push` uses vue-tsc and runs all frontend unit tests; the licence check leaves the app's own package out.
- PROCESS.md section: §6.
- Why: every frontend slice inherits these gates.
- Expected effect: type, lint and boundary regressions fail before landing; WebKit is exercised from the first commit.
- Observed effect: pending.
- Status: kept (tightening, agent-approved).

## IMP-0017: security exception to the 14-day dependency rule (2026-10-25)
- Trigger: the owner asked why the shell's crates were being moved to older versions (S0010).
- What changed: ENGINEERING.md EP-24 (exception and Cargo enforcement); ADR-0050 amendment; BL-0052.
- PROCESS.md section: none (engineering principle).
- Why: the 14-day delay protects against fresh regressions and compromised releases, but must not hold back a fix for a published advisory.
- Expected effect: security fixes land without waiting; everything else stays 14 days behind.
- Observed effect: pending.
- Status: kept (loosening, owner-approved).
- Owner approval: "I accept your recommendation about the security exception" (2026-10-25, in session)

## IMP-0018: T1 shell job, shell pre-push checks, Rust licences (2026-10-25)
- Trigger: S0010, the Tauri shell skeleton (BL-0037, BL-0033).
- What changed: `.github/workflows/ci.yml` gains the `shell` job (rustfmt, pedantic clippy with warnings as errors) and a Rust toolchain in `repo-checks`; `.githooks/pre-push` runs rustfmt and clippy when `shell/` exists; `scripts/check_licences.py` reads the shell's shipped crates with `cargo metadata`; `scripts/tests/test_shell_config.py` pins the shell's identifier, macOS minimum, CSP and permissions.
- PROCESS.md section: §6.
- Why: the shell runs with OS access; its config and dependencies are gated from the first commit.
- Expected effect: a loosened CSP, a new permission or a disallowed crate licence fails CI.
- Observed effect: pending.
- Status: kept (tightening, agent-approved).

## IMP-0019: agents push `next` without a prompt (2026-10-25)
- Trigger: owner request in session.
- What changed: `.claude/settings.json` moves `git push` from "ask" to "allow"; CLAUDE.md, PROCESS.md §10, `.claude/agents/implementer.md` and the slice skill say agents push `next` after each landed slice.
- PROCESS.md section: §10.
- Why: every push already passes the guard hook (no force, delete, tag, mirror or `main` pushes) and the pre-push hook (owner push gate, meta-tests, privacy scan, invariants, capability map, generated files, types and tests per package); the prompt added no check.
- Expected effect: no prompt per landed slice; the same checks on every push.
- Observed effect: pending.
- Status: kept (loosening, owner-approved).
- Owner approval: "Pushes should go through without asking, but retain the sensible and reasonable checks done before a push" (2026-10-25, in session)

## IMP-0020: INV-02's enforcement list names the main sidecar's Beancount ban (2026-10-25)
- Trigger: S0006 verifier doc check; `design/inbox/answered/20261025-inv-02-enforced-by.md`.
- What changed: `design/INVARIANTS.md` INV-02 "Enforced by" (the rule text is unchanged; status stays pending until the engine side exists, BL-0041).
- PROCESS.md section: none (invariant content).
- Why: after ADR-0054 the enforcement description named only the single-process design.
- Expected effect: none on behaviour.
- Observed effect: pending.
- Status: kept.
- Owner approval: "my answer is Option 1" (2026-10-25, in session; covers `design/inbox/answered/20261025-inv-02-enforced-by.md`)

## IMP-0021: INV-03 gains a stated exception for encrypted ledgers (2026-10-25)
- Trigger: S0015 verifier F1; `design/inbox/answered/20261025-encrypted-ledgers.md`.
- What changed: `design/INVARIANTS.md` INV-03 rule gains a stated exception (ADR-0059); CAP-DATA-ENCRYPTED `cut` → `deferred`.
- PROCESS.md section: none (invariant content).
- Why: no ADR decided the map's cut; the owner chose not to read encrypted ledgers in v0.4.0 and to build encryption support later.
- Expected effect: the M1a overlay load refuses encrypted files with a plain message.
- Observed effect: pending.
- Status: kept (weakening, owner-approved).
- Owner approval: "I think that for v0.4.0 Finzytrack should not even read encrypted ledgers. Supporting encryption can be done as its own small-ish(?) feature later." (2026-10-25, in session)

## IMP-0022: GitHub CI runs by hand before each release (2026-10-25)
- Trigger: the owner's question whether GitHub CI adds anything over the local gates.
- What changed: `ci.yml`, `packaged.yml`, `nightly.yml` have no push or schedule triggers (manual, or called by a dispatcher on `main`); the pre-push hook verifies new Cargo publish dates; PROCESS.md §6 T1/T2 rows; ADR-0060.
- PROCESS.md section: §6.
- Why: on a single-owner repo the per-push jobs mostly repeat the local hooks.
- Expected effect: no CI noise per push; Windows/Linux regressions found at the next manual run.
- Observed effect: pending.
- Status: kept (loosening, owner-approved).
- Owner approval: "I think we should do a manual GitHub CI run prior to each release." (2026-10-25, in session)

## IMP-0023: the pre-push hook runs the INV-21 goldens (2026-10-25)
- Trigger: S0022 (the INV-21 goldens).
- What changed: `.githooks/pre-push` runs `scripts/oracle/tests/test_inv21_goldens.py` in the oracle environment; PROCESS.md §6 T0 pre-push row.
- PROCESS.md section: §6.
- Why: the goldens pin how existing ledgers' entries are identified; with GitHub CI manual (ADR-0060), the pre-push hook is the only per-push place a drift between the spec, the goldens and the key inventory is caught. The test takes under 1 s.
- Expected effect: a spec or golden edit that breaks agreement fails before it reaches the remote.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9).

## IMP-0024: the pre-push hook runs the Beancount domain vectors (2026-10-25)
- Trigger: S0024 (the Beancount domain vectors).
- What changed: `.githooks/pre-push` runs `scripts/oracle/tests/test_domain_vectors.py` in the oracle environment; PROCESS.md §6 T0 pre-push row.
- PROCESS.md section: §6.
- Why: the vectors pin what Beancount says about tolerance, grammar, prices, precision and booking (INV-02); with GitHub CI manual (ADR-0060), the pre-push hook is the only per-push place a hand edit, a case changed without regenerating, or a drift in the oracle environment is caught. The test takes about 1 s.
- Expected effect: a vector that no longer equals Beancount's output fails before it reaches the remote.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9).

## IMP-0025: the pre-push hook checks the pushed commits, not the working tree (2026-10-26)
- Trigger: the M0 gate red team (review:27 §1(d)), findings H1, H2, M2, M3, M4, M5, M6, L2; journal `design/journal/S0031-gate-hardening.md`.
- What changed: gates and tests. `.githooks/pre-push` first runs `scripts/check_push.py` (every pushed ref is HEAD; the tree is clean: no change, no untracked file, no assume-unchanged or skip-worktree entry, no ignored code outside the caches and build folders; else refused before any check), then `scripts/check_staged.py --range` and `scripts/privacy_scan.py --range` over every blob the pushed commits add or change (via `scripts/git_range.py`), every oracle test as one discover, and ruff format, ruff check, Prettier and ESLint over the whole backend and frontend trees. New `.githooks/pre-merge-commit` runs pre-commit. `scripts/check_invariants.py`: only git hooks count as runners (not the manual `ci.yml`), a test counts only in a folder the pre-push hook runs, no test-disabling form in an enforcement file or a conftest above it, and the fixme/skip scan covers `scripts/tests` and `scripts/oracle/tests`. `ci.yml` has a `milestone_exit` input. `.claude/hooks/session_start.py` warns and `.claude/hooks/stop_checks.py` fails when `core.hooksPath` is not `.githooks`. Verifier round 1 (journal): replacement objects and grafts refused and ignored, the tree re-checked at the end of the hook, commit and tag messages scanned (new `.githooks/commit-msg`), a whole-tree staged-file guard, more skip forms refused. PROCESS.md §6 T0 rows.
- PROCESS.md section: §6.
- Why: with the hooks installed and pushing on, a change that broke an enforced invariant could reach `origin/next` (commit a violation and restore the file uncommitted; cherry-pick from a hookless clone; skip the enforcement test; partial staging past lint; an INV-24 test run only by the manual CI).
- Expected effect: each of the red team's probes is refused at push (re-run end to end in scratch clones; the journal lists them). Pre-push time on this machine, warm: 57 s before, 67 s after (budget 2 min); the range scan of a one-commit push takes about 2 s.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9).

## IMP-0026: the rest of seeded-defect run 1's misses become gates (2026-10-26)
- Trigger: seeded-defect run 1 (BL-0075), misses E01-E04, A05, A06, A11, E06, E10; journal `design/journal/S0033-seeded-misses-2.md`.
- What changed: gates and tests. `scripts/tests/test_privacy_cli.py` runs `privacy_scan.py` in a scratch repository and requires exit 1 on a planted finding in every mode (`--staged` with a Markdown file, `--all`, `--range`, paths). `scripts/tests/test_hook_pins.py` pins each git hook byte for byte against its copy in `scripts/tests/hook_pins/` (comments and blank lines included; verifier F1, R2-1) and refuses, also with the pin updated, the shapes that switch a check off (verifier R2-2). `scripts/privacy_scan.py` gains `upi-handle`, 6-10 digits right after an account word, and `statement-excerpt` (Markdown, text files, commit and tag messages; an ISO-dated line with a Beancount account name, or ending in one amount and an ISO 4217 currency code, does not count; verifier F4, R2-3). `scripts/check_invariants.py` and `scripts/check_deferrals.py` refuse a backlog ID on two rows (verifier F2). `scripts/check_invariants.py` holds every test under `backend/tests/architecture/` to the enforcement-file skip ban. New `scripts/check_deferrals.py`, run by `.githooks/pre-push` per pushed range: an open backlog item moved to a later milestone, parked, deferred, removed or deleted must cite an existing ADR or an owner quote on its row in the same push. PROCESS.md §6 T0 pre-push row and "Privacy gate".
- PROCESS.md section: §6.
- Why: each planted defect passed every gate in run 1: a privacy scan that always exits 0 or skips staged Markdown, a hook that lost its invariants line or chained the privacy scan with `; true`, a UPI address, an 8-digit account number, a statement excerpt in a journal note, a backlog row and its fixme re-dated M6 to M12.
- Expected effect: each of those plants is refused (re-run in a scratch clone; the journal lists the gate that catches each). Pre-push time: see the journal.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9).

## IMP-0027: gate time limits are hang detectors from history, not budgets (2026-10-26)
- Trigger: owner-reported bug, diagnosed with the owner: the Stop hook ran the meta-tests under a fixed `timeout=110`; at about 95 s unloaded and 160 s under load the timeout raised uncaught, Claude Code treats a crashed Stop hook as non-blocking, and the invariant, capability and privacy checks after it never ran. Journal `design/journal/S0034-gate-time.md`.
- What changed: gates and tests. New `scripts/gate_timer.py`: each check runs in its own process group under a hang limit of 8x the median of its last 5 green runs (3x as first built; raised after the verifier measured 3.7x from three concurrent parallel runs) in this clone (at least 60 s; no limit before 3 green runs), kept in the git directory; a check over it is stopped and fails with the numbers; only green runs are recorded. New `scripts/run_meta_tests.py`: the meta-tests in parallel, one process per test class (per module when it has module fixtures or `load_tests`), checked to run exactly the tests `unittest discover -s scripts/tests` finds. `.claude/hooks/stop_checks.py`: a green cache keyed on the working tree's content (a temporary index, the real one untouched) and the Python version; every check through the timer; a canary that fails when the timer passes a failing command; a 1500 s backstop of its own; it never crashes. `.claude/settings.json`: Stop timeout 120 s to 1800 s (a backstop only). `.githooks/pre-push`: the pinned `run` calls the timer, a canary line follows it, the meta-tests use the runner, and a time report ends the checks; the "<= 2 min" header is gone. `.github/workflows/ci.yml` runs the runner. `scripts/check_invariants.py`: the pre-push marker for `scripts/tests/` is the runner. PROCESS.md §6 T0 pre-push / Stop hook row and "Thresholds catch accidents". Independent verifier round (S0034 R2 F1): a check whose time grows with its input (each pushed range or tag, the new Cargo.lock entries, the Stop hook's changed files) is marked `--size-dependent` and has no history limit, since one history mixes input sizes and a long push was stopped as hung; it never makes a check pass (a hung check still never finishes, so the push does not either).
- PROCESS.md section: §6.
- Why: a fixed time budget is exceeded as the suites grow and then either switches a gate off (the Stop hook) or blocks it for no reason (owner, 2026-10-26: "An arbitrary budget, especially for things like test timings, inevitably gets exceeded as the development continues and more tests are added. The solution isn't to just bump up the numbers, because they'll eventually be exceeded again."). Approval of the design, verbatim (owner, 2026-10-26): "Go ahead with 1–4 for the stop hook and apply the same pattern to the pre-push as well." The 30 s pre-commit ceiling is the owner's and is unchanged.
- Expected effect: the Stop hook never fails open on time; on an unchanged tree it answers in under a second; the meta-tests take about the time of their slowest test class instead of their sum; a hung check fails a gate with numbers instead of hanging it. Measurements: the journal.
- Observed effect: pending.
- Status: kept (owner-approved process change).

## IMP-0028: the engine boundary and the engine's licence list become checks (2026-10-26)
- Trigger: S0035 (the ledger engine as its own process, ADR-0054); journal `design/journal/S0035-engine-process.md`.
- What changed: checks and tests. New `scripts/check_engine_boundary.py` (with `scripts/tests/test_engine_boundary.py`): `beancount`/`beanquery` imported only in `engine/`, `scripts/oracle/` and `corpora/`; inside the engine only `ledger_engine/ledger/beancount_port.py`; the engine never imports `app`; nothing outside `engine/` imports `ledger_engine`; literal names reached through an alias, a keyword, `runpy`, `pkgutil.resolve_name` or `exec`/`eval` of a literal count as imports; inside `engine/src` only the gateway may import or run code by name at all (S0035 independent verifier F5). `scripts/check_licences.py` judges the engine by its own list (`[engine]` in `licences.allow.toml`: GPL-2.0-compatible, plus the reviewed Beancount dependencies, each passing only while its licence is the reviewed expression) and reads trove-classifier licences through a reviewed per-version `[mapping]` (BL-0050, part). `scripts/check_generated.py` checks `contracts/engine-protocol.schema.json` (a JSON file's header is its first key, `$comment`; a generator that cannot run is a reported problem, not a crash). `backend/tests/architecture/test_globals.py` accepts a `StateEntry(...)` record whose arguments are all constants (the registry's first entry; before, the registry could only be empty), with planted cases that still fail. The engine's own gates (ruff, pyright, pytest) run at push and in CI, and staged engine files are formatted and linted at commit (BL-0084, wired when S0035 landed after S0034).
- PROCESS.md section: §6 (T0 pre-commit and pre-push rows, the T1 jobs).
- Why: ADR-0054 decision 5 asks for per-environment boundary tests and two licence lists; without them, a Beancount import in the main sidecar's scripts or an Apache-2.0 package in the engine would pass.
- Expected effect: each planted case in the meta-tests is refused; the engine's runtime closure is judged by the GPL-2.0-compatible list.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9), except the `StateEntry` acceptance in the globals scan, which accepts more than before and is owner-approved (owner, 2026-10-26: "The loosened gate is approved.").

## IMP-0029: implementers never start the verifier (2026-10-26)
- Trigger: owner, 2026-10-26, after S0034 and S0035 were verified by verifiers their own implementers had started: "make sure that future implementers don't start a verifier themselves. That's a problem if it ever happens."
- What changed: `.claude/agents/implementer.md` frontmatter `disallowedTools: Agent, Task` (the implementer has no tool to start any agent) and its hand-off text (commit with green gates, stop, report the range); a backstop PreToolUse hook `.claude/hooks/guard_agent.py` on `Agent|Task|SendMessage|Bash` that, when the hook input's `agent_type` is `implementer`, refuses those tools and any Bash command that runs the `claude` CLI (verifier F1: a nested `claude -p --agent verifier ...` starts an agent without the Agent tool), and refuses input it cannot read; SendMessage is denied too, since a message to a running verifier would steer it (registered in `.claude/settings.json`; meta-tests `scripts/tests/test_claude_hooks.py` `ImplementersNeverStartAgents`); PROCESS.md §3 step 5 and the slice skill's step 7 say the orchestrator starts the verifier.
- PROCESS.md section: §3 step 5.
- Why: the verifier's independence rests on it seeing only the range, the slice ID and the brief; an implementer choosing the verifier's prompt can steer it, and nobody can check what it was given.
- Expected effect: every verifier round is started by the orchestrator. Checked live: a fresh implementer had no Agent tool (a first probe right after the edit still had it, before the definition reloaded; the hook covers such a window).
- Verifier (orchestrator-started, head 2019985b): PASS WITH FINDINGS; F1 Medium (the nested claude CLI) and F2-F5 Low (docstring, fail-open on odd input types, tests that passed with the hook missing, the test class below `unittest.main()`) fixed in the next commit. Stated limit: the Bash check matches `claude` as a command word or quoted argument; a deliberately obfuscated invocation (assembled from parts, an alias) is review's, like the other shell guards (S0028).
- Observed effect: pending.
- Status: kept (owner-requested tightening). Owner approval: "make sure that future implementers don't start a verifier themselves. That's a problem if it ever happens." (2026-10-26, in session)

## IMP-0030: the failure-mode spec cannot drift from its tests and backlog rows (2026-10-26)
- Trigger: S0041 (BL-0090, the cross-process failure-mode spec); journal `design/journal/S0041-failure-modes.md`.
- What changed: a check. New `scripts/check_failure_modes.py` (with `scripts/tests/test_check_failure_modes.py`, which also runs it on the real spec, so it runs with the meta-tests at every push): `design/specs/failure-modes.md` has exactly one row per (program, failure) pair; each row's Test cell holds only references to tests that exist (a Python `def test_…`, inside its class when named; a Rust `#[test] fn`), to backlog rows that are open with the stated milestone, a `same as` another real row, or an `n/a` with a reason; each engine row names its transitions in one form (`From` → `To` on `Event`), each a transition of the generated statechart reference. After verifier round 1 (S0041 F1, F3-F5): a row of an unknown program (any letters then digits, any case) or of the wrong shape is refused, `same as` stays within its program, rows inside code fences and HTML comments do not count, and a cited test must lie inside the repo.
- PROCESS.md section: none (a spec check run by the existing meta-test step of §6).
- Why: the spec answers an owner question about confidence; a row whose test was renamed, or whose backlog row closed without the spec citing the new test, would claim cover that is not there.
- Expected effect: a slice that closes a backlog row the spec cites (BL-0065, BL-0073, BL-0091 to BL-0097) fails at push until the spec cites the test that closed it.
- Observed effect: pending.
- Status: kept (tightening, agent-approved, PROCESS.md §9); narrowed by IMP-0032 (S0043): the format rules were dropped, the rules above that keep the spec honest stay.

## IMP-0031: a review cap never drops a High; findings beyond it go to an Overflow list (2026-10-26)
- Trigger: owner request. Starting the first area review on `next` (the engine and backend core), the orchestrator capped its report at 15 findings; the owner asked for the rationale ("Curious why we are capping the findings. What's the rationale there?"), and on the answer (a cap forces ranking and bounds fix cost, but a plain cap can silently drop a real finding) decided: "The cap rule should be a standing one for all reviewers".
- What changed: prompt and doc. PROCESS.md §4 (review altitudes): altitudes 2–4 default to at most 15 ranked findings; for every reviewer, a cap never drops a High and every finding beyond the cap is listed one line each in an Overflow list. `.claude/agents/verifier.md`: the same rule in the report format (the slice verifier is uncapped by default; the rule binds it whenever a prompt caps it).
- PROCESS.md section: §4.
- Why: the cap exists so reviews rank and so low-value findings do not drive fix rounds (ADR-0058); without the Overflow list it would also hide findings, and a High must never be lost to a count.
- Expected effect: no review report drops a finding silently; triage sees the overflow and decides each line.
- Observed effect: pending (first use: the engine and backend core area review, 2026-10-26).
- Status: kept (owner decision). Owner approval: "The cap rule should be a standing one for all reviewers" (2026-10-26, in session).

## IMP-0032: the failure-mode spec check keeps its honesty rules and stops policing the Markdown (2026-10-26)
- Trigger: area review finding, triage approved by the owner: the first area review of the engine and backend core (2026-10-26, finding F11, Low, proportion: the check and its tests, 322 and 337 lines, mostly policed formatting); journal `design/journal/S0043-review-followups.md`.
- What changed: a check (loosening). `scripts/check_failure_modes.py` keeps: every (program, failure) pair has exactly one row; a row of an unknown program is refused; rows in code fences and HTML comments do not count; every cited test exists inside the repo (with its class when named); every cited backlog row is open with the stated milestone; `same as` names a row of the same program that itself cites a test; every engine transition a row names is in the statechart reference's Transitions table. Dropped: the cell count per program, the row number matching the failure's number, a row ID used twice (the pair rule still refuses two rows for one failure), a refused unknown failure name, the four-word minimum for an `n/a` reason, every cell of a `same as` row repeating it. `scripts/tests/test_check_failure_modes.py`: one must-not-pass per kept rule, a test that the dropped rules no longer refuse, and the real spec passing. Checker 322 to 234 lines, tests 337 to 229.
- PROCESS.md section: none (the check runs in the existing meta-test step of §6).
- Why: the spec's value is that what it claims is tested is tested; rules about the table's shape cost upkeep and catch no false claim (a missing failure is still caught by the pair rule).
- Expected effect: the same 28 rows pass with the same counts; a spec edit that only changes formatting no longer fails the push.
- Observed effect: pending.
- Status: kept (loosening, owner-approved). Owner approval, 2026-10-26, in session, on the whole triage of the area review (recorded in `design/inbox/20261026-engine-concurrency-and-cancel.md`): "I agree with all your recommendations." and "The rest of the recommendations can be implemented".

## IMP-0033: INV-26 drops the shared-ledger exception; its three-OS interleaving test narrows to a detection test (2026-10-27)
- Trigger: owner decision, 2026-10-27, in session (ADR-0062), from the engine-scheduler design work (`design/inbox/20261026-engine-concurrency-and-cancel.md`); journal `design/journal/S0044-single-instance-policy.md`.
- What changed: `design/INVARIANTS.md` INV-26: rule (no write under a legacy root with no exception, `.write.lock` included; legacy ledgers only by Copy; a held legacy lock refuses the copy or save, never waited for), why, and the enforcement list (the lock-interleaving test against the packaged v0.2.2 build on three OSes becomes the legacy-lock detection test). Status unchanged: pending (BL-0026, by M3).
- PROCESS.md section: none (invariant content).
- Why: Share workspaces are removed, so nothing interleaves with a legacy app any more; what remains to check is that v0.4 writes nothing there and sees a held lock.
- Expected effect: the scheduler loses the legacy-lock waits (draft 3); BL-0026 builds a smaller test.
- Observed effect: pending.
- Status: kept (a weakening and a tightening together, owner-approved).
- Owner approval: "Drop the coexistence policy; I approve the needed changes to INV-26 and ADR-0020. The rest of the pending work you have identified should be done." (2026-10-27, in session)

## IMP-0034: INV-28 and INV-31 reworded for one open workspace; INV-32 added: no file in two workspaces, by territory (2026-10-27)
- Trigger: owner decision, 2026-10-27, in session (ADR-0063), after the fourth review of the engine-scheduler design; the S0045 verifier's round 1 (F1-F3) replaced the proposed file-by-file overlap with the territory rule; journal `design/journal/S0045-one-workspace-at-a-time.md`.
- What changed: `design/INVARIANTS.md` INV-28: rule (served only when the named workspace is the open one, in hosted mode in its user container; any other registered workspace refused with `WORKSPACE_NOT_OPEN`), why, and enforcement (a switch test). INV-31: title, rule (one open workspace per instance or user container; the switch) and enforcement (the switch and quiesce tests). INV-32 new: the territory rule, pending (BL-0103, by M2).
- PROCESS.md section: none (invariant content).
- Why: INV-28's "no server-side active workspace" was contradicted by the one open workspace; the scheduler drops its protections for shared files and relies on INV-32 instead.
- Expected effect: M2 cannot exit without the switch and territory tests (BL-0028, BL-0031, BL-0102, BL-0103).
- Observed effect: pending.
- Status: kept (tightenings, owner-approved).
- Owner approval: "reword INV-28 and INV-31 and add INV-32 as proposed" and "I agree with the territory rule" (2026-10-27, in session); for INV-28's hosted wording, "Yes INV-28 should say the same." (2026-10-27, in session). Relayed by the orchestrator.
