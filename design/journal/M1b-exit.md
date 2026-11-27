# M1b exit: Write contract (in progress)

The milestone's exit record (roadmap §3.2: "the milestone's journal entry"), started 2026-11-27 with the owner's local-tier runs; the exit decision, the red teams, the spot audit, the area review and the green manual CI run are added as they happen.

## Local tier (T-local) on copies of the owner's ledgers (2026-11-27)

Copies made by the owner with `cp -RL` into the T-local folder (`FT_LOCAL_TIER_DIR`), outside the repo; counts and case IDs only (PROCESS.md §8). The owner's two ledger roots are L1 and L2 below.

- **The copy's setup, fixed by the owner on the copy only:** each root sets `option "documents"` to a folder outside its own (`../…`); the first oracle run, on a copy without that folder, gave 15 of 15 cases UNEXPLAINED, one constant difference each, unrelated to the edit (no booked difference). With the documents folder copied beside the ledgers and the option pointed at it, and the old ledger backups (`*.backup*`, which still named the old folder) removed from the copy, both runs below pass. The D2 runner's plain-copy rule refused the copy until then (S0158; a stated cost).
- **D2 equivalence** (`engine/scripts/d2_local.py`, S0158): **2 EQUAL, 0 DIFFERENT, 0 REFUSED, 0 ERROR**, exit 0. L1: entries 10253, errors 0, options 35. L2: entries 10175, errors 1673 (the same in both loads), options 35. Run by the coordinator on the owner's word ("you can do Step 3 yourself").
- **Differential oracle** (`scripts/oracle/harness.py`, BL-0046), L1, 5 cases per mutator: **15 cases, 0 UNEXPLAINED**, exit 0: 10 MATCH (upd-001..004, ren-000..004), 5 EXPECTED_DIVERGENCE (upd-000 FIX-L3-02, the legacy's refusal of a cost edit; imp-000 FIX-L3-05+M28; imp-001..004 M28, the legacy's import format). No booked difference in any case. The owner ran the first run; the coordinator the reruns.

## Milestone reviews (2026-11-27)

Range `23b8b5d6..ac6fc485`. Four reviews; no High in any. IDs and counts only. Every finding's disposition is in `design/journal/S0161-m1b-review-findings.md`, which landed them.

- **Data-safety red team:** no High; 3 findings. DS-F1, the best-effort window (an in-place write by another program from the backup's read to the rename is lost while the save says "saved"; the undo's put-back likewise): owner, "State it honestly", wording corrected, no code. DS-F2, a foreign temp of a later file after a crash is not moved to a kept name: owner, "State it as a limit (Recommended)". DS-F3, a kept save's backup is pruned: owner, "Say it in the text".
- **Finance red team:** no High; 3 findings. F1 (Medium, c; the oracle's I10 paired booked postings by index): fixed. F2 (Low; `lineno:`/`filename:` metadata changes Beancount's order without changing an entry key): stated limit. F3 (Low, d; the same-date order also picks the same-day price): limit extended.
- **Security red team:** no High; 2 findings and 2 notes. F1 (Low, c; `is_inside` did not refuse `..` on POSIX): fixed. F2 (Low, c; an unconfined `pre: null` create at any path, held only by the sidecar): docstring and a promotion condition on BL-0001. Notes for M3: the Host/Origin check (BL-0115, BL-0268) and UNC paths on Windows (BL-0157).
- **Spot audit of S0136:** no High; 4 findings: ARCHITECTURE.md's save flow out of date for shares (corrected); BL-0166 gates the v0.4 release (recorded); the exFAT-backed share sweep added as a regression guard; a share whose server gives one file ID for every file (stated limit, §6.3).

## The first milestone-exit CI run (2026-11-27, at 574629fa): red on Windows
Run started with `milestone_exit: M1b`. Green: `repo-checks` (the exit check included), `backend`, `engine` (Linux), `frontend`, `shell`, `oracle-unit`. Red: `engine-windows` (5 failed, 12541 passed, 49 skipped) and `backend-windows` (1 failed, 3207 passed), six failures from M1b slices that had not met the Windows runner, none in product code:
- `tests/ledger/test_v_backups_and_key_folders_born.py` (S0156, 4): a module over the fake through `test_save_outcome`'s worlds, which names no fake module itself, so on Windows it ran with Windows' path model (`not_absolute`); now in `windows_pending.POSIX_PATH_MODULES`, beside its S0125 sibling (the S0150 lesson: inventory by dependency).
- `tests/ledger/test_d2_local.py::test_a_second_hard_link_refuses_the_run` (S0158, 1): on Windows `DirEntry.stat()` leaves `st_nlink` 0, so the plain-copy rule's hard-link check never fired there; the walk now asks `os.lstat` (a real gap of the tool on Windows, closed).
- `backend/tests/test_selfcheck.py::test_a_failed_view_probe_reports_its_exit_code_and_error_output` (S0153, 1): a Windows child writes `\r\n`; the self-check's report now reads line ends as `\n` on every OS.

## The milestone-exit CI run (2026-11-27, at 78f105fa): green
Run started with `milestone_exit: M1b`: **success**, every job green: `repo-checks` (meta-tests, privacy scan, staged-file guard, invariants with the M1b exit check, capabilities, generated artifacts, licences), `backend`, `engine` (Linux, the write-contract tests on real scratch volumes), `engine-windows` (12546 passed, 49 skipped: the first run's 12541 plus its 5 fixed, the skips unchanged), `backend-windows` (3208 passed, 49 skipped, 23 xfailed), `frontend` (the acceptance flows through the harness), `shell`, `oracle-unit`. The engine's write-contract tests green on a Windows runner and a Linux runner (roadmap §4 M1b exit; ADR-0061 and its amendment of 2026-11-14).
