# How should the nightly workflow get its schedule, given that GitHub only runs schedules from `main`?
- Asked: 2026-10-25 by agent, slice S0012-packaged-builds
- blocks: nothing now (the packaged builds run on every push to `next` that touches packaging); G1's "14 green nightly runs in a row" needs a real nightly before M1b ends
- Rubric item: 8 (and an action only you can take: agents never push `main` or change repository settings)
- ADRs checked: ADR-0045 (direct commits on `next`, agents never push `main`), ADR-0007 ("a packaged-build matrix runs nightly"), ADR-0046 (G1 counts nightly runs). None says how a schedule reaches `next`; ADR-0045 rules out an agent doing either option itself.

## Context
GitHub Actions starts a scheduled (`on: schedule`) workflow only from the copy of the workflow file on the repository's **default branch**, which is `main` (the legacy line). `nightly.yml` on `next` therefore never fires by itself. Example: the cron line `0 3 * * *` in `next`'s `nightly.yml` is ignored; nothing runs at 03:00.

## Options
1. **You add one small file to `main`** that runs `next`'s nightly at night. `main` keeps its legacy face; the file only points at `next`, so the job definitions never drift (`nightly.yml` on `next` builds `next` by default):
   ```yaml
   # .github/workflows/nightly-next.yml on main: runs next's T2 suite (design/process/PROCESS.md §6 on next).
   name: nightly-next
   on:
     schedule:
       - cron: "17 3 * * *"
     workflow_dispatch:
   permissions:
     contents: read
   jobs:
     nightly:
       uses: sagarbehere/finzytrack/.github/workflows/nightly.yml@next
   ```
2. **Make `next` the default branch** in the repository settings. Simplest technically, but visitors and `git clone` then land on the unreleased rebuild instead of the released line.
3. **No schedule**: keep running the packaged builds only on pushes that touch packaging. G1's nightly criterion would then have to be redefined (an ADR change).

## Recommendation
Option 1. It costs one commit on `main` by you, keeps the public front page as it is, and the schedule calls whatever `nightly.yml` says on `next`, so later slices change the nightly contents without touching `main` again.

## Answer (owner, verbatim, with date)
"I think we should do a manual GitHub CI run prior to each release." (2026-10-25, in session). Superseded by ADR-0060: no schedule; GitHub CI runs by hand before each release. The dispatcher file for `main` is in `design/inbox/20261025-g1-without-nightly-ci.md`.
