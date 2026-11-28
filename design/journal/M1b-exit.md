# M1b exit: Write contract (2026-11-28)

The milestone's exit record (roadmap §3.2: "the milestone's journal entry"). M1b ran from 2026-11-13 (M1a's exit, 23b8b5d6) to 2026-11-28: slices S0114 to S0162 (49 journals).

## Decision

- Owner, 2026-11-28, in session, asked to close M1b on the evidence below: "Close M1b (Recommended)" (first asked before the §3.2 gates were complete; the coordinator then found the area review, T2 and the reflection missing from its plan, the owner chose "Do them first, then close (Recommended)", and confirmed "Close M1b (Recommended)" once they were done).
- `FT_MILESTONE_EXIT=M1b python3 scripts/check_invariants.py` passes at the exit code commit 78f105fa: no invariant pending and no backlog item open that is due by M1b.
- Exit code commit: 78f105fa (every later commit of this record is docs, journals and backlog only).


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

## macOS green (ADR-0061 decision 4a)
- The T0 pre-push gate passed at the exit code commit 78f105fa (the push `574629fa..78f105fa`, unmarked, so the whole gate ran).
- The acceptance suite passed locally through the harness, run by the owner (2026-11-28): **39 passed, 21 skipped** (the integrity flows A1, A2, A4 (two), A7, A10 and A13 in each of the three browser projects, `fixme` for their later milestones), 22.9 s; as at M1a's exit.

## Area review (2026-11-28)

A fresh reviewer at head 78f105fa (PROCESS.md §4, altitude 2). **Verdict: no High; nothing in class (a) or (b)**, so no finding became a slice (PROCESS.md §4's triage: integrity, security and wrong-number findings only). Four Medium, seven Low, an overflow list, and five simplification proposals. Landed in `design/journal/S0162-m1b-area-review-triage.md`; IDs and dispositions only.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Medium | e | The write-path README's `insert_meta`, `delete_meta`, `option_line`, `new_file`, `replace_file` are not built (only `Insert`, `Replace`, `Delete`, `ReplaceToken`); step 2 refuses every option change; the preview cannot plan a create. Rows BL-0312 (M3: `option_line` and the step-2 exception with `doc.documents-option`, `replace_file` with `ws.refresh-demo`, `new_file` and planned creates with `ws.create-ledger`) and BL-0313 (M4: the two metadata primitives with `account.edit`, their first consumer, not M3); README marks each "not built" |
| F2 | Medium | d | ARCHITECTURE.md §9.2 steps 1-2, §2.3 and §12 rewritten to what is built (producers in the engine's `preview.PRODUCERS`, untyped request, the whole fingerprint read, no `parse_files`, promotion in `app.workspaces.writes.PROMOTED`); the old→new `TransactionId` map (ADR-0026) added to BL-0059 (M5), its consumer, instead of a new row |
| F3 | Medium | e | The no-double-write rule for a resent confirm written into engine-jobs §11 and ARCHITECTURE.md §9.2 step 4 (the confirm's route asks the plan's fate before the edge's `WARMING`, or is exempt); BL-0268's lead corrected (`PLAN_UNKNOWN` since S0117 round 3); `app.platform.plans`'s docstring corrected too |
| F4 | Medium | e | Already tracked: BL-0257 (M2, M1a area review F3) is this decision; BL-0257 and BL-0154 carry a pointer, engine-jobs §8.1 "Afterwards" names it as M2's open question. No new row |
| F5 | Low | e | Row BL-0314 (M3, the first new whole-file caller) |
| F6 | Low | d | engine-jobs Orientation fixed: the preview is built; `ledger.preview`, `ledger.contract`, `app.workspaces.writes`, `app.platform.plans` in its code map |
| F7 | Low | d | §15's index: the 16 missing limits added, "request size" removed (no such limit since S0120; the line guard is §5.5's rule); `scripts/tests/test_engine_jobs_limits.py` holds the index to the body both ways |
| F8 | Low | d | Row BL-0318 (M2, a docs slice) |
| F9 | Low | e | Row BL-0315 (M3, with the first mutator's preview) |
| F10 | Low | c | Row BL-0316 (M3, before the next new world) |
| F11 | Low | e | Row BL-0317 (M2, pytest-xdist) |
| Overflow: `PLAN_UNKNOWN`, `PLAN_ALREADY_CONFIRMED` missing from ARCHITECTURE §6.1 | — | d | Added to the table and the "Built" line |
| Overflow: BL-0256's limit | — | d | Noted on BL-0256's row for M4's brief |
| Overflow: the save checks for an `.open` journal at step 1 (`save.py`, around 467) and again at step 4 (around 504); step 1 is redundant with one job at a time | Low | e | Dropped: the step-1 check refuses before any read and costs one listing; removing it buys nothing a user sees |
| Overflow: `ENCRYPTED_SUFFIXES` exists in both the sidecar and the engine, held by no shared test | Low | c | Dropped: across the process boundary a copy is expected (ADR-0054); the engine's own refusal is the authority |

**Simplification proposals 1-5** (each narrows an ADR, so the owner's): `design/inbox/20261128-m1b-simplifications.md`; nothing implemented.

## T2 at the exit (2026-11-28)
- **Packaged builds on three OSes** (`packaged.yml`, the run of 2026-11-28 at 78f105fa): **success**. macOS, Windows and Linux built, installed and smoke-tested strictly; Linux's AppImage now strict too (S0153, BL-0263), the `.deb`'s must-not-pass runs refused.
- **Cargo dates** (`nightly.yml`'s `cargo-age`): `shell/src-tauri/Cargo.lock` is unchanged since M1a's exit (23b8b5d6), whose run re-read every date; no new crate to verify. `nightly.yml` itself cannot be started by hand: GitHub runs a manual workflow only when it exists on the default branch, and it exists only on `next` (agents never push `main`); its two jobs were covered as above.
- **Performance and memory against the baseline:** no CI job exists yet (roadmap §6: "the other T2 items arrive with the code they check"); the write path's costs are measured by the measurement scripts, each run in the suites on every OS (`measure_save.py`, `measure_write.py`, `measure_protocol.py`).

## Reflection and process metrics
- **Span:** 2026-11-13 to 2026-11-28; slices S0114 to S0162 (49 journals) plus this record.
- **Process changes from the milestone's reflection** (IMPROVEMENTS.md): IMP-0075 (refuse first; a Low (c)/(d) is never a backlog row), IMP-0076 (the push gate parses workflow files), IMP-0077 (the lighter mid-slice push gate), IMP-0078 (the outcome is exact, its cause best effort), IMP-0079 (one-off tools get one review round; the stated limit is offered at the first High outside product code).
- **What cost the most:** the file-system edge cases of Windows and OneDrive (S0144 to S0152: refused folder rules, held files, cloud placeholders, read-only folders), and S0158 (a one-off tool's guard: three failed rounds, about 4 h, stopped by the owner; IMP-0079 followed). Most rounds were spent on which cause a refusal names and on hostile inputs to tools, not on lost bytes: across the four milestone reviews and 49 slices no ledger byte was found lost, torn or overwritten in product code, and the one false outcome found in the milestone (S0149 V1) was fixed in its round.
- **What worked:** measuring on the runner or the owner's PC before building (S0151's OneDrive probe, S0152's hand check, S0153's AppImage phase 1); policy before a second fix round (S0158's plain-copy rule, BL-0249 as a limit); the owner's hand checks on real OneDrive.
- **Agent misses recorded:** the coordinator wrapped sandbox-exempt commands and misread the failure as a sandbox limit (S0151 retro; memory updated); left the area review out of the exit plan until roadmap §3.2 was reread (this record).
- **Owner time:** not measured in M1b (as in M1a); the owner's decisions are quoted in each slice's journal. Subagent token totals per slice were not collected in M1b.


## The architecture explainer
- Refreshed after this record (2026-11-28; link in `design/README.md`): the write contract's checks, the best-effort mode and its stated limit, Windows (OneDrive included) and Linux, the oracles at M1b's exit, the status table (M1b built; G1 next; M2 beside it).
- **Next:** G1, the go/no-go on the write core (roadmap §4 G1, ADR-0046): an evidence pack for the owner (old-vs-new comparisons, refusals on copies, write cost at demo size and 54k, seeded-defect catch rate, the cross-cutting lens reviews and a spot audit).
