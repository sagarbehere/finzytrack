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
