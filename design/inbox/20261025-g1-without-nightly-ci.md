# G1 counted "14 green nightly runs in a row"; what should it count now that CI runs by hand? And please add the dispatcher file to `main`
- Asked: 2026-10-25 by agent, after ADR-0060
- blocks: nothing now; G1 (after M1b) needs the criterion, and the first manual run needs the dispatcher file
- Rubric item: 4 (an owner-decided G1 criterion, ADR-0046) and an action only you can take (pushing `main`)
- ADRs checked: ADR-0046 (G1 go criteria), ADR-0060 (manual CI), ADR-0045 (agents never push `main`). ADR-0060 removed the nightly runs the criterion counted; none restates it.

## Context
ADR-0046's G1 go criteria include "14 green nightly runs in a row": a sign that the suite is stable, not flaky, over two weeks. With GitHub CI started by hand before each release (ADR-0060), there are no nightly runs to count. Example: at G1 the evidence pack would show zero nightly runs, so the criterion could never be met as written.

## Options
1. **Every landed slice's local gates green, plus one manual full run (`ci.yml` + `nightly.yml`) green on the G1 commit, and the same run repeated three times without a flake.** Keeps "stable, not flaky" with a few manual runs instead of fourteen nightly ones.
2. **Manual full runs at each milestone exit (M1a, M1b) and at G1, all green.** Fewer runs; flakiness shows less.
3. **Drop the criterion**; G1 relies on the other go criteria.

## Recommendation
Option 1. It costs three manual runs (about 30 minutes of runner time each, free on a public repo) and keeps the flake signal that the nightly criterion was for.

## The dispatcher file (for `main`, needed for any manual run)
GitHub shows "Run workflow" only for workflow files on the default branch. Add this one file to `main`; it runs `next`'s workflows at the commit you choose (default: the tip of `next`):
```yaml
# .github/workflows/run-next-ci.yml on main: runs next's CI by hand (ADR-0060 on next).
name: run-next-ci
on:
  workflow_dispatch:
    inputs:
      ref:
        description: "Commit or branch of next to test"
        default: "next"
permissions:
  contents: read
  actions: read
jobs:
  t1:
    uses: sagarbehere/finzytrack/.github/workflows/ci.yml@next
    with:
      ref: ${{ inputs.ref }}
  t2:
    uses: sagarbehere/finzytrack/.github/workflows/nightly.yml@next
    with:
      ref: ${{ inputs.ref }}
```
Then: GitHub → Actions → run-next-ci → Run workflow.

## Answer (owner, verbatim, with date)
