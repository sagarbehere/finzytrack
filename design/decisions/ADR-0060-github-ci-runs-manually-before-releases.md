# ADR-0060: GitHub CI runs manually, before each release; the local hooks are the per-push gates

This record moves the per-push and nightly gates off GitHub Actions: the workflows stay in the repo and run when started by hand, before each release.

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** owner: "I think we should do a manual GitHub CI run prior to each release." (2026-10-25, in session, after "I don't think we need to run any CI jobs on GitHub at this time. What possible advantage would it give over everything that we are already doing?")
- **Area:** process, CI
- **Amends:** ADR-0045 ("CI runs deterministic gates" now means: when started by hand), ADR-0007 ("a packaged-build matrix runs nightly"), PROCESS.md §6 (T1 per push and T2 nightly on GitHub). ADR-0046's G1 criterion "14 green nightly runs in a row" is restated by the owner separately (`design/inbox/20261025-g1-without-nightly-ci.md`).

## Context

On a single-owner repo where only agents push, the T1 jobs on GitHub mostly repeat the T0 pre-push hook, which already runs the check-script meta-tests, privacy scan, invariant, capability and generated checks, the backend gates, rustfmt, clippy and Rust tests, vue-tsc, vitest and knip. What GitHub adds is a clean checkout, Windows and Linux runners (the packaged builds; two Windows-only bugs were found that way in S0012), and slow jobs (Playwright in two engines, the full crates.io check, packaged builds). The agent recommended keeping T1 and running the packaged builds nightly; the owner chose a manual run before each release.

## Decision

(owner, 2026-10-25, quoted above)
- **No GitHub workflow runs on push or on a schedule.** `ci.yml`, `packaged.yml` and `nightly.yml` keep their jobs and are started by hand (`workflow_dispatch`, or `workflow_call` from a dispatcher).
- **A full manual run is required before each release** and must be green on the release commit: `ci.yml` and `packaged.yml` (the three-OS packaged build with `--smoke-test`). It may also be started at any time, for example at a milestone exit or after a packaging change. (agent: the G1 and milestone uses are proposals in the inbox item above.)
- **The T0 pre-push hook is the per-push gate**, as it already runs on every push. Two checks that only ran in CI move into it where they fit the budget: the Cargo publish dates of entries new since the remote branch are verified against crates.io (network only when the lock gained entries). Playwright stays in the manual run (agent).
- Because GitHub offers "Run workflow" only for workflow files on the default branch, the manual start goes through a small dispatcher file on `main` that calls `next`'s workflows; only the owner pushes `main` (ADR-0045). Its text is in the inbox item.

## Alternatives considered and rejected

- **Keep T1 per push and run the packaged builds nightly** (the agent's recommendation). Rejected by the owner: the per-push jobs mostly repeat the local hooks.
- **Remove the workflows.** Rejected: the release run needs them, and the Windows and Linux packages cannot be built on the owner's Mac.

## Consequences

- Windows and Linux regressions are found at the next manual run instead of within a day; a packaging slice should ask for a manual run before it is called done.
- `scripts/check_cargo_age.py`'s CI mode ("since the last green CI run") is replaced by the pre-push mode (since the remote branch); the full re-read runs in the manual `nightly.yml`.
- The hooks must stay installed (`git config core.hooksPath .githooks`); a clone without them has no gate at all until the release run.

## References

ADR-0007, ADR-0045, ADR-0046, ADR-0056; PROCESS.md §6; journal S0012, S0013.
