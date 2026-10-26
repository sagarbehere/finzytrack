# M0 exit: Foundations (2026-10-26)

The milestone's exit record (roadmap §3.2: "the milestone's journal entry"). Milestone exits are recorded here, one file per milestone, not as backlog rows (S0036).

## Decision

- Owner, 2026-10-26: "Agreed, close M0 on this run plus the fix" (in session, after the second manual CI run).
- `FT_MILESTONE_EXIT=M0 python3 scripts/check_invariants.py` passes (exit 0) with BL-0076 closed: no invariant pending and no backlog item open that is due by M0.

## Evidence

- **Manual CI runs (ADR-0060):**
  - First run, at `8434091d`: red. The repo-checks job failed on a Linux-only harness test (inode reuse; fixed in S0036). The frontend job hit its fixed 15-minute limit while installing browsers (fixed in S0036: browser cache, limits as hang backstops). A `milestone_exit: M0` run could never pass while BL-0076 was open (S0036; roadmap §3.2).
  - Second run, at `0dc00e55`, started without `milestone_exit`: every job green except one backend test, `test_the_engine_is_told_its_parent`. Linux `ps` cut the engine's command line at 80 columns; the engine itself was right. Fixed in `0a407014` (`ps -ww`). That fix has not yet run on Linux; the next manual run (M1a exit) confirms it. Green in that run: repo-checks, engine, backend apart from that test, frontend (Chromium, WebKit, Kolkata), shell, oracle-unit, cargo-age, and the packaged build with `--smoke-test` on macOS 15, Ubuntu 22.04 and Windows 2022.
  - Closing on the second run plus the fix is the owner's decision above (ADR-0061 allows a cheap platform-only test fix).
- **macOS green (ADR-0061 decision 4a), at `0dc00e55`:** the T0 pre-push gate passed (the push of `55424560..0dc00e55`, 111 s), and the acceptance suite passed locally through the harness: 39 passed, 21 skipped (the six P1 integrity flows, `fixme` since S0029 and due in later milestones, in each of the three browser projects), 23 s.

## Roadmap M0 exit items

| Item | Where |
|---|---|
| CI fails on planted bugs (I2, I5, L4-03, L9-01) | S0029 fixme tests, S0032 (a fixme passes only while its planned module is missing) |
| The empty window passes `--smoke-test` on three OSes | `packaged.yml`, green in both manual runs |
| Every spike has a verdict; S13 decided Vue | S0001; ADR-0053, ADR-0056 |
| The pilot ADR | ADR-0057 |
| A red-team session on the gates | S0031 (BL-0074) |
| A first seeded-defect run (≥ 20 plants, ≥ 4 classes) | S0032: 72 plants, 69% caught before fixes; the misses became gates in S0032 and S0033 |

## §3.2 items, as they apply to M0

- **Release-blocking capability rows, demo fragments, user docs:** M0 has no user-facing capability.
- **Area review by a fresh reviewer:** for M0's only area, the gates, this was the red-team session (S0031) and the seeded-defect run (S0032). No separate area review was run.
- **UX red team:** no vertical with UI exists yet.
- **Reflection cycle:** IMP-0025 to IMP-0029 this session. The process re-measurement of ADR-0058 item 5 is scheduled for M1a's end.
- **Owner time and process metrics:** owner time was not measured in M0. The M1a re-measurement records build time against review time, and process code against product code.

## Carried into M1a

- BL-0087: a Windows job for the engine's write-contract tests, due M1b.
- BL-0085: the legal review of TatSu-LTS, regex and dateutil, due M12.
- The open inbox items: ADR wording on prices and booking, the privacy denylist (before M1b), INV-24's "Enforced by" text.
