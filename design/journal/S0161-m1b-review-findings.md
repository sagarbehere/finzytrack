# S0161: the M1b milestone reviews' findings

## Brief
- Goal: dispose of every finding of the M1b milestone reviews of 2026-11-27 (range `23b8b5d6..ac6fc485`): the data-safety, finance and security red teams and the spot audit of S0136. No High anywhere. Mostly stated limits and docs; one tool fix (the oracle's I10 classifier), one sidecar guard tightening (`writes.is_inside` refuses `..`), and two product text changes (the best-effort notices, under the owner's DS-F1 answer, and Keep's confirmation, DS-F3, wording under IMP-0078).
- Implements: ADR-0068 D2 (a dated note), ADR-0014 (a dated note on I10), engine-jobs §2, §6.3, §6.8, §8.1, §15; ARCHITECTURE.md §9 (the save flow), §13 note.
- Capabilities: CAP-DATA-ENGINE-JOBS (texts and limits only), CAP-DATA-WRITE-GATE (rule 5's statement).
- Invariants touched: INV-01, INV-22 (stated limits of the best-effort mode; the promotion guard's `..` refusal), INV-23 (the M1b-exit record carries counts and IDs only).
- Kind: product code (shipped) for the two texts and `is_inside` (one guard tightening, fails closed); a tool for the oracle (the owner and agents run it on copies at milestone ends); docs for the rest. Review row: one verifier round (the orchestrator's choice for this slice, proportionate: a text change, a refusal that only tightens, a tool fix, limits).
- Risk class / lane: Medium / full (texts of the write path; no write mechanism changes).
- The bytes it protects: none new. The notices and Keep's text keep the outcome truthful (the user is told the best-effort window and that a kept save's backup is pruned); `is_inside` keeps an unpromoted mutator's plan inside the test copy (the engine refuses `..` already).
- Which real setup needs this, and can a policy forbid the rest? No new mechanism; the `..` refusal is a policy (refuse) already applied by the engine and on Windows. Two or more fix rounds before on this family? The promotion guard: S0117 four rounds, S0131 two; this change only refuses more.
- Acceptance tests (new, each failing on the base `ac6fc485`):
  - `backend/tests/platform/test_save_job.py::test_S0161_the_best_effort_notices_name_the_whole_save_not_one_moment`
  - `backend/tests/platform/test_recovery_text.py::test_a_replaced_file_found_missing_is_named_on_the_screen_and_in_the_keep_confirmation` and `test_a_missing_file_whose_backup_is_not_known_good_is_named_without_promising_the_backup` (goldens changed: the backup's path and the 16-kept-saves sentence); `test_S0161_keeps_count_is_the_history_trims` (new; it fails on the base only by import, `KEPT_BACKUPS` is new)
  - `backend/tests/workspaces/test_writes.py::test_inside_the_copy_is_judged_as_each_os_spells_paths` (four `..` rows; two fail on the base)
  - `scripts/oracle/tests/test_booked.py`: `test_a_bake_that_swaps_two_lots_between_postings_is_not_i10` (both halves fail on the base: the swap accepted, the correct bake a false alarm) and `test_a_bake_that_swaps_two_lots_within_one_account_is_not_i10`
  - `engine/tests/ledger/test_share_saves.py`: `test_S0161_on_a_share_kept_on_exfat_a_crash_at_every_effect_then_every_power_loss_recovery_ends_at_pre` and `…_an_io_error_at_every_effect_is_told_truthfully` (`@regression-guard`: they hold on the base)
- Expected doc impact: engine-jobs §2, §6.3, §6.8, §8.1, §15; ARCHITECTURE.md; ADR-0068 and ADR-0014 notes; backlog rows BL-0001, BL-0115, BL-0157, BL-0166, BL-0186, BL-0268; `design/generated/engine-job-texts.md` regenerated; `core/entry_keys.py` and `workspaces/writes.py` docstrings; the docs repo's `v0.4` branch (two pages: the best-effort window); `design/journal/M1b-exit.md`'s "Milestone reviews" section.
- Open questions: none (the three owner questions were answered in session, below).

## Decisions
- **DS-F1** (owner, 2026-11-27, in session; offered: "No code: correct the limit's wording in ADR-0068 D2, spec §6.8 and the best-effort notice to say 'during the save's durable phase (a fraction of a second)'."): **"State it honestly"**. Lands in ADR-0068's note of 2026-11-27, engine-jobs §6.8 "Limit (no swap)" and its Windows line, ARCHITECTURE.md §9 step 4.7, `save_text.BEST_EFFORT_DRIVE` and `BEST_EFFORT_WINDOWS`, and the docs repo's two pages. Agent's wording choices: "usually less than a second" in the notices and "a fraction of a second on a small ledger but several disk syncs, longer for a large file or a slow drive" in the spec (pushback on the offered words: "a fraction of a second" alone is false for a 15 MB file on a slow USB stick or a share); "can be lost" kept, since a program that saves by renaming is caught. The share notice (`BEST_EFFORT_SHARE`) is about the server's durability and is unchanged; BL-0166 now says a share that shows only it would not tell the user this, so a merged notice keeps the sentence. The user docs also said "it reads the file again; if another program changed it, Finzytrack does not save", which is false (the check is the entry's identity): corrected in the same docs commit.
- **DS-F3** (owner, 2026-11-27, in session; offered: "Keep's confirmation says the backup is pruned after 16 kept saves and to copy it out if needed."): **"Say it in the text"**. Lands in `recovery_text._missing_kept` (§11's `msg:recovery.keep_missing_backup*`). The agent added the backup's path: "copy it out" is not actionable without it, and the engine already sends it (`InspectedFile.backup`, as the drive-stop texts show). The count is `recovery_text.KEPT_BACKUPS`, held equal to the engine's `history.KEEP` by a test that reads the engine's source (the sidecar never imports the engine).
- **DS-F2** (owner, 2026-11-27, in session): **"State it as a limit (Recommended)"**. Lands in engine-jobs §8.1 "Limit (later temps)" and §15.
- **Security F1** (agent): normalize by refusal. `is_inside` refuses any `..` name on POSIX, as `windows_paths.split` does on Windows and the engine's `change_path` does: a one-line change with table rows, cheaper and more honest than a docstring caveat. `_where_written`'s docstring now says a `..` below a folder not made yet stays in its result. It refuses `/c/sub/../main.bean` too, which is inside: fails closed, and the engine refuses it anyway.
- **Security F2** (agent): rule 5's docstring states the trust (the engine creates an unconfined `pre: null` file at any absolute path the fingerprint does not list, `save._check`); the promotion row is **BL-0001** (its "left: the old-app oracle comparison per mutator before promotion"), which now carries the condition.
- **Security notes for M3** (agent): (a) the Host/Origin check already has a row, **BL-0115** (M2, owner's option 1 of 2026-10-28), which lands before the confirm route; the note is on BL-0115 and BL-0268 (the confirm's route) rather than a duplicate row. BL-0267 is the test-copy mark, not a route row; left unchanged. (b) the UNC/NTLM note is on **BL-0157** (the user docs' "open only ledgers you trust" page).
- **Finance F1** (agent; a tool): pair booked postings by account and place within the account, and take a lot only when the booked units are the written ones. The second condition came from a surviving mutant: booking regroups inside one account too (probed: `-1 YY {EUR}` before `-1 XX {USD}` books XX first). Its residual false alarm (a correct bake of two reductions of one account in two currencies, in the other order) is a stated limit in `erase_i10` and ADR-0014's note. The M1b local tier's 15 cases had no I10 case (5 EXPECTED_DIVERGENCE: FIX-L3-02, FIX-L3-05+M28, M28), so its result stands without a rerun.
- **Finance F2** (agent): engine-jobs §2 "Limit (location metadata)" and §15; `core/entry_keys.py`'s docstring. Probed with the engine's Beancount: `lineno: 1` on a later same-date `price` moves it first and the price map keeps the other; `filename:` replaces the file named; a non-numeric `lineno:` parses alone but two such entries on one date make Beancount's own parse raise (`TypeError` in its sort).
- **Finance F3** (agent): the same-date limit (§2) now says it also decides which same-day price the price map keeps.
- **Spot audit** (agent): (1) ARCHITECTURE.md's save flow and the "Not modelled" line corrected for shares; (2) BL-0166 gates the v0.4 release, with both owner answers quoted; (3) the auditor's exFAT-backed share sweep added (its file was still in the scratch folder; logic copied, not the file). `check_told` is not used there: its power-loss oracle refuses a missing file, which RT1 accepts on this server; (4) §6.3 "Limit (a share's file ID)" extended.
- Not done: the docs repo's "Where to keep your ledger" still tells the user to press Retry before Keep when a file is missing, a rule ADR-0068 D2 dropped; recorded on BL-0186 (that page's row, M2), not fixed here (outside the findings).

## Findings

| Source | ID | Severity | Class | Disposition |
|---|---|---|---|---|
| data-safety red team | DS-F1 | Medium | a (accepted as a limit) | Owner: "State it honestly". Wording corrected in ADR-0068 (note), engine-jobs §6.8, ARCHITECTURE.md, both best-effort notices, the user docs; no code |
| data-safety red team | DS-F2 | Low | d | Owner: "State it as a limit (Recommended)". engine-jobs §8.1 "Limit (later temps)", §15 |
| data-safety red team | DS-F3 | Low | d | Owner: "Say it in the text". Keep's confirmation names the backup's path and that only the last 16 kept saves keep theirs |
| finance red team | F1 | Medium | c | Fixed (the oracle): booked postings matched by account and units; two tests; limit stated |
| finance red team | F2 | Low | d | Stated limit: engine-jobs §2 "Limit (location metadata)", §15, `entry_keys.py` |
| finance red team | F3 | Low | d | The same-date limit extended to same-day prices |
| security red team | F1 | Low | c | Fixed: `is_inside` refuses `..` on POSIX; four table rows |
| security red team | F2 | Low | c | Rule 5's docstring states the trust; BL-0001 carries the condition for promotion |
| security red team | note (a) | — | e | BL-0115 (M2) and BL-0268 (M3) |
| security red team | note (b) | — | d | BL-0157 |
| S0136 spot audit | 1 | Low | d | ARCHITECTURE.md corrected |
| S0136 spot audit | 2 | Low | d | BL-0166: gates the v0.4 release |
| S0136 spot audit | 3 | Low | c | The exFAT-backed share crash, power-loss and I/O sweeps added (`@regression-guard`) |
| S0136 spot audit | 4 | Low | d | §6.3 "Limit (a share's file ID)" extended |

Severities: the reviews' own (DS-F1 Medium, DS-F2 and DS-F3 Low; the spot audit's all Low; none High), filled in by the coordinator.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `writes.is_inside` refuses a `..` name (POSIX) → `test_inside_the_copy_is_judged_as_each_os_spells_paths[darwin-/c-/c/new/../../etc/main.bean-False]` and `[darwin-/c-/c/sub/../main.bean-False]`.
  - `classify.erase_i10` accepts a lot only from the same account's booked posting with the written units → `test_a_bake_that_swaps_two_lots_between_postings_is_not_i10` (first half), `test_a_bake_that_swaps_two_lots_within_one_account_is_not_i10`; its must-pass twin is the second half of the first test.
  - The notices and Keep's text (no check; pinned by `test_S0161_the_best_effort_notices_name_the_whole_save_not_one_moment`, the Keep goldens and `test_S0161_keeps_count_is_the_history_trims`).
- Self-mutation (`scripts/mutants.py`, plans in the session scratchpad): backend 4 tried, 4 killed (`..` check dropped, `..` checked only in the folder, the backup's path dropped from Keep's text, the count off by one). Oracle 3 tried: `i10-by-index` killed; `i10-cost-units-guard-dropped` survived at first, killed by the added within-one-account test; `i10-count-mismatch-accepted` survives, equivalent: booking never changes a posting's account and the caller already requires equal posting counts, so a per-account shortfall with equal totals cannot occur.
- Population: none claimed. The exFAT sweeps cover every effect of the fake's clean save (crash and I/O error at each), as the existing sweeps do.

## Verifier
- Round 1, head 7b389d23 (range ac6fc485..7b389d23): **PASS WITH FINDINGS**, no High, no Medium. Two Low (d), fixed by the coordinator in texts and their goldens (no further round: wording, IMP-0078).

| ID | Sev | Class | Finding | What was done |
|---|---|---|---|---|
| L1 | Low | d | The two best-effort notices said "(usually less than a second)" without the spec's and docs' "longer for a large file or a slow drive"; the drive notice is shown exactly on the slow drives in question | Both notices now say "(usually less than a second, longer for a large file or a slow drive)"; the test pins the clause; the texts reference regenerated |
| L2 | Low | d | After a drive stop, Keep's confirmation names each missing file's backup but not that the backup is trimmed after 16 kept saves (DS-F3 covered only the other Keep path) | The drive stop's Keep adds the same sentence (`KEPT_BACKUPS`), singular and plural; its golden updated |

- Golden changes (round 1): `test_save_job.py::test_S0161_the_best_effort_notices_name_the_whole_save_not_one_moment` (the added clause) and `test_recovery_text.py::test_a_drive_stop_names_the_backup_of_each_file_on_that_drive` (the trim sentence), both from L1 and L2 above.

## Retro
- About 1 h 40 min (the slice about 19 min, the verifier about 6 min, the round's text fixes by the coordinator). Findings in the reviews: 0 High across four reviews (data safety, finance, security, spot audit); this slice's own verifier 0H / 0M / 2L. The one code fix of the round is the oracle's I10 pairing (a tool) and the sidecar's `..` refusal; every data-safety finding became honest wording or a limit on the owner's choice.
