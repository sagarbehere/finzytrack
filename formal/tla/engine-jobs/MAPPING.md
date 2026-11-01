# EngineJobs.tla and the engine-jobs spec, both ways

- **Spec:** `design/specs/engine-jobs.md` §13 (the model's scope since ADR-0068 D5) and the sections it cites: §6.1 step 8 (the durable order), §6.4 (the rollback and the kept-name design), §6.6 (the outcome after an engine death), §8.1 (recovery, recover-all, Keep), §8.2 (create and copy). The code decides where the spec's prose is older: the model follows `engine/src/ledger_engine/ledger/writer.py`, `restore.py` and `core/recovery.py` as built (S0072, BL-0161).
- **Model:** `EngineJobs.tla` (one module, durability only since S0072: EP-19). Configurations come from `gen_cfgs.py` (`cfg/*.cfg`, `configs.txt`); `run_all.sh` runs them (HOW_TO_RUN.md).
- **State:** on this model the small tier ran to completion, all as expected: 14 base worlds pass, 28 mutants each break their property, 23 vacuity witnesses are each reached (65 configurations; HOW_TO_RUN.md has the times). The medium and large tiers are the owner's (BL-0110).
- `scripts/tests/test_tla_engine_jobs.py` keeps this file honest: every action in `Next` has a row in §2.1 and every row names an action; every property and mutant §13 names exists in the model and in `gen_cfgs.py`, and every one they hold is named in §13; every property has at least one mutant; every witness is a predicate of a small world.

## 1. Spec → model

### 1.1 The world (§13 "World")

| §13 world element | In the model |
|---|---|
| W1 = {R, N, I}, W2 = {R, J} sharing R, W3 = {K} on a disk that may become unreachable | `LS(w)`; K's disk: `env.unr`, `Unreach` |
| saves of W1 that replace R and create N (a later save replaces N) and leave I alone | `Tg` = {R, N}; `StartSave` plans on the disk now (`FP(1)`, the preview's fingerprint); s1..s3 (`MaxSaves`) |
| per file the bytes and what survives a power loss | `fs.cur`, `fs.pl` (`TORN` = -1, `ABS` = 0); a pending swap or exclusive rename couples a file and its temp (`fs.cp`, `fs.cpn`, `fs.cpj`) |
| per journal and file: temp, staging file, restore temp, kept copies | `fs.tc`/`fs.tpl`, `fs.sg`/`fs.sgpl`, `fs.rt`, `fs.kp` (a set of the outside bytes kept), all over `TSlots` |
| backups | `fs.bk`, `fs.bkpl` per save (R's pre bytes) |
| journal states | `jr[j].st` (`part`, `open`, `closed`, `resolved`, `kept`, and `tpart`/`topen` for torn ones), `jr[j].pl`; `jr[j].why` (blocking reason: `stopped`, `failed`); `jr[j].old` (on disk when the session started) |
| journals left by crashed saves at boot, one torn, one on the unreachable disk | `BootJ` ⊆ {b2 (R, J), b3 (K)}; `BootTemps` (what a crashed save left beside: a cut staging write, a staged temp, or the old object at the temp name); `BootTorn` (b3's file list unreadable) |
| a copy of W2; a create into an absent, empty or non-empty target with files dropped or overwritten | `DoCopy`, `CP_*`; `CreateTarget`, `tgt` (names a, m = `.finzytrack`, o, x), `EditorDrop`, `EditorOverwrite`, `CR_*` |

### 1.2 Actors (§13 "Actors")

| Actor | Actions |
|---|---|
| the user | `StartSave`, `StartCreate`, `StartCopy`, `StartRecover` (Retry, Back or a switch: recover-all again), `Keep`, `Relaunch` (the next start) |
| the engine | the job steps `S_*`, `RS_*`, `RC_*`, `CP_*`, `CR_*` (one job at a time: `run`) |
| the OS | `EngineCrash`, `IOFail`, `AppCrash`, `PowerLoss` |
| an outside editor | `Editor` (any ledger file, durably or not: `EdDurable`), `EditorDrop`, `EditorOverwrite` |
| a path changer | `PathChange` (after a crash, R's folder replaced by a link) |

### 1.3 Rules, by section

| Spec rule | Model |
|---|---|
| §8.1, INV-32: every unfinished save recovered before any other work | `Init` and `Relaunch` start with recover-all (`app = "recover"`); work starts only in `app = "run"` |
| §6.1 steps 1-4, 7: the records, the gate, the commit point; no load, no lock (ADR-0068 D1, D3, D4) | `S_Gate` (an `.open` journal naming a file of W1's set refuses, which stands for the save's own check and the open that recover-before-open blocks) |
| §6.1 step 8.0-8.1: a created path free, a replaced file synced, read and at its pre bytes; backups synced | `S_Backup`, `S_BackupSync` |
| §6.1 step 8.2: `.part` synced, renamed `.open`, folder synced | `S_JPart`, `S_JPartSync`, `S_JOpen`, `S_JDirSync` |
| §6.1 step 8.3: the staging name, synced, renamed to the temp, folder synced; swap or exclusive rename; displaced check; folder synced | `S_TempStart`, `S_TempDone`, `S_TempSync`, `S_Swap`, `S_Compare`, `S_DirSync` |
| §6.4: an outside save met at a name of ours is moved at once to its kept name and the save stops | `S_Compare`, `S_RemoveTemps`, `RS_Check`, `RS_Compare`, `RS_Temp` (`fs.kp`) |
| §6.1 step 8.4-8.5: read-back, temps checked and removed, close, "saved" | `S_ReadBack`, `S_RemoveTemps`, `S_Close`, `S_CloseSync`, `S_Saved` |
| §6.4: any failure from the `.open` rename on rolls back at once with the restore | `StartRB`, then `RS_*` with `InRollback`; `IOFail`'s first two branches (at the swap or exclusive rename, before it or after it took place) |
| §6.4, §11: the rollback's outcome (resolved: not saved; not resolved: Unfinished once a replace was asked for, else not saved with the journal open) | `RestoreStop`, `RestoreDone` (`ctl.repl`, set from the swap's call on, an I/O error there included; `ctl.rbt`) |
| §6.6: after an engine death the outcome is read from the disk after the journal folder's sync | `EngineCrash`'s save branch, `DiskOutcome` |
| §8.1 step 2: the per-file judgement (kept copy, temp rule, at pre, at post with a good backup, a created path taken, anything else stops) | `RS_Check`: `Stops`, `AtPost`, `Taken`, `FirstStop`; the outside saves at the stopping file's names moved to its kept name first |
| the temp rule (owner, 2026-10-30, "Staging name"): a staging file is ours whatever it holds; a temp name only at pre or post | `TempOurs`, `StagingOurs`, `Held` |
| §8.1 step 3: every folder synced first; names of ours removed but the temp beside a file to put back; root first, the restore temp written, swapped, synced, what it displaced settled, the temp removed; every folder synced; a created path taken stops here | `RS_Sync`, `RS_Clean`, `RS_Write`, `RS_Swap`, `RS_Compare`, `RS_Temp`, `RS_All` |
| §8.1 step 4: only then `.resolved` | `RS_Resolve` |
| §6.3: the path guard | `PathMoved` in `Stops`; `RS_Swap`'s outside write only without it |
| §8.1: recover-all deletes `.part` journals, tries each `.open` one, a torn one blocks; an engine death marks the journal in hand failed | `RC_Init`, `RC_Next`, `EngineCrash`'s recover branch |
| §8.1 "Afterwards": Keep syncs every file and folder the journal names, then renames it `.kept` | `Keep` |
| §8.2: copy never over a file an unresolved journal names | `CP_Check` |
| §8.2 create: the target must not exist or be empty; files exclusive; synced; read back; the manifest last, exclusive, synced | `CR_Target`, `CR_WriteA`, `CR_SyncA`, `CR_ReadBack`, `CR_ManifestRename`, `CR_ManifestSync`, `CR_Register` |
| §13 file-system assumptions | `Couple`, `Pairs`, the `pl` sets; `PowerLoss` picks each unsynced outcome independently; `EdDurable = FALSE` (the weaker file system) |

### 1.4 Properties (§13; names as in the spec)

INV = state invariant, ACT = action property (`[][...]_vars`).

| Property | Kind | Meaning in the model | Mutants that must break it |
|---|---|---|---|
| `SavedSurvivesPowerLoss` | INV | told "saved" ⇒ the record survives a power loss as closed, and its files (not named by another unresolved journal) durably hold post | `report_before_close_sync`, `outcome_unsynced` |
| `BackupDurableBeforeReplace` | INV | post bytes of R on disk (or possible) before the close ⇒ the backup is durable | `no_backup_sync` |
| `JournalDurableBeforeFirstReplace` | INV | post bytes on disk before the close ⇒ the journal is durably `.open` | `no_journal_sync` |
| `PartJournalNeverBlocks` | INV | a journal of ours that never became a durable `.open` is never blocking | `direct_open` |
| `ExternalEditNeverSilentlyLost` | INV | the editor's latest bytes of a file not previewed by a save are in the file, at a name of ours beside it, or in a kept copy that a journal naming it still stops on (`.open`) or that the user kept (`.kept`) | `no_displaced_check`, `plain_create`, `rollback_skips_temp_rule`, `x_keep_and_go_on` |
| `NoTornLedgerAfterRecovery` | INV + ACT | a torn ledger file only while an unresolved journal names it; `.resolved` only when every file durably holds neither post nor torn bytes | `no_temp_fsync`, `resolve_before_restores_synced`, `no_restore_temp_fsync` |
| `RecoveryNeverClobbersForeignBytes` | INV | `ExternalEditNeverSilentlyLost` for the files a journal names | `no_temp_check` |
| `RecoveryWritesOnlyRecordedPaths` | INV | the restore never writes through a replaced folder | `no_path_guard`, `fb_no_guard` |
| `CutTempNeverBlocks` | ACT + INV | a journal is never stopped when its only fault is a cut write of ours at a staging name; no temp or staging file of a save outlives its journal | `old_temp_rule`, `temps_after_close` |
| `EveryJournalRecoveredOrBlocking` | INV | in a running session every journal from before its start that is unresolved has a reason | `crash_unmarked`, `rv_boot_skips_recovery` |
| `BlockedOnlyForCause` | ACT | a journal becomes blocking only with its cause present (written apart from the check's own predicate) | `block_without_cause` |
| `KeptFilesDurable` | ACT | at Keep, every file the journal names and its names of ours durably hold what they hold | `keep_no_sync` |
| `NoCopyOverOpenJournal` | INV | a copy past its check has no unresolved journal naming a source | `copy_checks_blocking_only` |
| `NoManifestOverIncompleteFolder` | INV | the manifest (and its durable form) only over complete, durable files | `manifest_first`, `rv_create_no_sync` |
| `NonEmptyTargetUntouched` | INV | a non-empty target gets nothing of ours | `no_refuse_nonempty` |
| `DroppedFileNeverReplaced` | INV | a file dropped under a name the job writes stays as it is | `plain_file_write`, `manifest_overwrite` |
| `ForeignOverwriteCaughtByReadBack` | INV | a file overwritten before the read-back stops the job before its manifest | `no_readback` |

## 2. Model → spec

### 2.1 Actions

| Action | Spec | Code (`@tla_action`, or the backlog row that builds it) |
|---|---|---|
| `StartSave` | §6.1, a confirmed save planned on the preview's fingerprint | the sidecar's save job (user action) |
| `StartCreate`, `StartCopy` | §8.2 | BL-0153 (user actions) |
| `StartRecover` | §8.1, §9.1: Retry, Back and a switch recover first | the sidecar (user action) |
| `Keep` | §8.1 "Afterwards" | `ledger.recover_all` (user action) |
| `Relaunch` | §9.2: the next start recovers first | the sidecar's boot (user action) |
| `S_Gate` | §6.1 steps 1-4, 7 | `ledger.save._gated` (with `S_Saved`) |
| `S_Backup`, `S_BackupSync` | §6.1 step 8.0-8.1 | `writer._backups` |
| `S_JPart`, `S_JPartSync`, `S_JOpen`, `S_JDirSync` | §6.1 step 8.2 | `writer.durable_commit` |
| `S_TempStart`, `S_TempDone`, `S_TempSync` | §6.1 step 8.3, the staging name | `writer._stage` |
| `S_Swap`, `S_DirSync` | §6.1 step 8.3 | `writer._replace` |
| `S_Compare` | §6.4 | `writer._check_displaced` |
| `S_ReadBack` | §6.1 step 8.4 | `writer._read_back` |
| `S_RemoveTemps` | §6.1 step 8.4 | `writer._remove_temps` |
| `S_Close`, `S_CloseSync` | §6.1 step 8.5 | `writer.durable_commit` |
| `S_Saved` | §6.1 step 9 | `ledger.save._gated` |
| `RS_Check` | §8.1 step 2 | `restore._check` |
| `RS_Sync`, `RS_Clean`, `RS_Temp`, `RS_All` | §8.1 step 3 | `restore._restore_all` |
| `RS_Write`, `RS_Swap` | §8.1 step 3 | `restore._put_back` |
| `RS_Compare` | §8.1 step 3, §6.4 | `restore._settle` |
| `RS_Resolve` | §8.1 step 4 | `restore.restore` |
| `RS_Late` | none: the seeded bug `resolve_before_restores_synced`'s own step | none (`MUTANT_ONLY`) |
| `RC_Init`, `RC_Next` | §8.1 | BL-0164 (recover-all's tries through the engine) |
| `CP_Check`, `CP_Load`, `CP_Manifest` | §8.2 copy | BL-0153 |
| `CR_Target`, `CR_WriteA`, `CR_SyncA`, `CR_ReadBack`, `CR_ManifestRename`, `CR_ManifestSync`, `CR_Register` | §8.2 create | BL-0153 |
| `EngineCrash` | §5.3, §6.6, §8.1 | OS |
| `IOFail` | §6.4, §8.1 step 3 | OS |
| `AppCrash`, `PowerLoss` | §10, §13 | OS |
| `Editor`, `EditorDrop`, `EditorOverwrite` | §13 | outside editor |
| `PathChange`, `Unreach` | §6.3, §13 | environment |

### 2.2 Variables

| Variable | Holds |
|---|---|
| `fs` | the files, the names of ours beside them, kept copies, backups and pending swaps (§1.1) |
| `jr` | the records |
| `env` | `lnk` (R's folder replaced by a link), `unr` (K's disk unreachable), `outside` (bytes the restore wrote through a link: only without the guard) |
| `tgt` | the create's target folder |
| `app` | `recover`, `run`, `dead` |
| `run` | the running job (`k`, `id`), one at a time |
| `ctl` | the running job's progress: `pc`, the save's journal `s` and `plan`, the file index `fi`, the restore's journal `rj`, `tried`, the rollback's message `rbt`, `repl` (a replace was asked for), `put` (the files to put back), `taken` (a created path is taken) |
| `n` | bounds of the environment and the user |
| `gh` | ghost: what each save was told, its plan, the editor's latest bytes per file and whether a save previewed them, the create's dropped and overwritten names, a write outside the recorded paths |

## 3. What the model abstracts away

| Left out or simplified | Why it is safe, or where it is checked |
|---|---|
| The queue, Cancel, the switch, the boot screens, quit, the update, the advice, the engine's processes and the engine lock | not durability (ADR-0068 D5): the queue's table and stateful tests (`backend/tests/platform/test_engine_queue.py`, `test_engine_scheduler.py`). The model runs one job at a time (`run`), which the engine lock guarantees in the code |
| The open of a workspace | stands as the save's gate refusing on an unresolved journal naming a file of W1's set (§1.3) |
| A volume without the swap (exFAT, FAT, HFS+, network shares; spec §6.8) | **not covered: the model assumes the swap.** The best-effort mode (ADR-0068 D2, S0071) gives up `ExternalEditNeverSilentlyLost` for another program's save in the same instant, by the owner's policy; checked by the fake's sweeps in both no-swap modes (`engine/tests/ledger/test_writer_best_effort.py`) and on real volumes (`engine/tests/fs/test_darwin.py`, opt-in) |
| Permissions, ACLs, lock flags, hard links | the code's refusals and their tests only (`test_writer.py`, `test_restore.py`, `test_acl_rule.py`); every folder is assumed to allow our writes |
| The restore's staging write, its rename and that rename's sync are one step (`RS_Write`); a restore temp is durable once written; the restore swap and its folder's sync are one step (`RS_Swap`) | a cut write at the restore's staging name is the staging rule's, as the save's (`S_TempStart`), which the model checks; the two syncs inside these steps are checked in the code by the save replay (`FUSED_SYNC`: the code must make each sync before its next step, `test_a_skipped_restore_sync_is_caught`; S0072 verifier r1 F6) and by `test_restore.py`'s sweeps |
| `RS_Write` takes the pre bytes from the record (`JPre`), not from the backup | a backup that does not hold the pre bytes stops the journal at the check (`BackupBad` in `Stops`), and the code hashes the backup again before writing it back (`restore._put_back`, `backup_bad`). So `RS_Clean`'s rule that keeps the temp beside a file to put back (it holds the only other copy of the pre bytes until they are back) cannot matter in the model: a clean-up that removes that temp too breaks no property (`x_clean_all`, S0072 verifier r1 F5); the rule is the code's and `test_restore.py`'s |
| The outside editor writes only the ledger files' own names, never a name of ours | the clean-up's re-checks (`S_RemoveTemps`, `RS_Clean`) then never meet outside bytes but after a seeded bug; a write through a descriptor held on the old object is `test_writer.py`'s and `test_restore.py`'s |
| An editor's unsynced write is kept or dropped whole, never torn | its own loss if a power loss tears it (§13); the save syncs a replaced file before reading it for the backup (`S_Backup`) |
| The save backs up R only | N exists only from the second save; recovery's backup check for N is the code's (`test_restore.py`) |
| The user's delete of a kept copy or of a file at a created path (the blocked screen's actions) | not modelled: only Keep leaves a stopped journal; deleting is the user's own write |
| The rollback after a failed close (`.closed` renamed back to `.open`); an I/O error at the journal's renames | `test_writer.py`'s sweeps (every effect, every fault) |
| An I/O error comes before its step's effect, except at the swap or exclusive rename (`s_sw`), where it may also come after the operation took place; either way the replace counts as asked for there (`ctl.repl`, the code's `progress.replaced`, set before the call) | an operation that takes place and then answers an error elsewhere (a write, a sync, a journal rename, a removal) is the writer's and the restore's sweeps' (`test_writer.py` `test_an_effect_that_happens_but_answers_an_error_is_told_truthfully`, `test_restore.py`). The branch after the swap reaches the states the swap followed by an error at the displaced check reaches; it is there so the replay drives the code's own path (the swap raising, the displaced check never run) and its told outcome (S0072 verifier r1 F2: before it, the model's error at the swap said "not saved" with the journal open where the code says Unfinished; `test_writer.py` `test_an_io_error_at_the_first_swap_with_an_outside_save_waiting_is_unfinished`) |
| The save's gate refusal on an `.open` journal naming a file of W1's set (`S_Gate`'s `OpenOn`) is guarded by no property since `NoOpenOverUnresolvedJournal` left (S0072): a gate that ignores it breaks none (`x_gate_ignores_open`, S0072 verifier r1 F3) | no committed byte is lost without it: the save's displaced check and the later restore's per-file judgement stop on the other journal's bytes (`RecoveryNeverClobbersForeignBytes`, `ExternalEditNeverSilentlyLost`). The refusal itself (INV-32, ADR-0063) is the code's: `engine/tests/ledger/test_save.py` (`unfinished_save`) and the replay's branch "refused: unfinished_save" |
| Folders the save makes, and their removal | `test_writer.py`, `test_restore.py` |
| The rebuild after a save, the mirror publish | write no ledger byte; the publish gets its own model (BL-0113) |
| Several kept names per file (`-2`, `-3`) | `fs.kp` is a set of the kept bytes; the numbering is `journal.kept_name`'s |

## 4. Interpretations where the spec is still silent

- A save in the model plans on the disk at its start, which stands for the preview's fingerprint; an outside edit after it makes the gate refuse.
- `StartRecover` may run whenever no job runs and an unresolved journal exists: Retry, Back and a switch all recover first (§9.1), and the model does not need to tell them apart.
- The rollback's told outcome follows the code (`writer.durable_commit`): resolved, "not saved" (an I/O error) or "changed"; not resolved, Unfinished once a replace was asked for (from the swap's or exclusive rename's call on, as an operation that answers an error may still have happened), else the same as resolved with the journal left open. No property checks that the told outcome is true (EP-19); the replay checks the code's against the model's disk (§7).

## 5. History: what left the model

The model before S0072 (`git show fcf96576:formal/tla/engine-jobs/MAPPING.md`) also checked the queue, Cancel's one rule, the switch, the boot and its screens, quit and its notes, the update, the advice and liveness, with the findings of its reviews (F1-F4, O1, N1-N3, fidelity reviews 2-4, simplification B, Retry recovers first, ADR-0067), each recorded there with its date.

**S0072 (ADR-0068 D5, owner 2026-11-01; EP-19): durability only, and the code's save as built (BL-0161).**
- Actions removed: the queue's (`Dispatch`, `DispatchUnavail`, `SpawnStep`), the user's Cancel and closed tab, the switch, Stay, Retry, Back, open another, forget and add again, Try again, quit, Quit anyway and the quit notes, the update, the app's background save and focus check, the advice timer, the drain and the orphaned engine, the window, the open and the reads (`OP_*`, `RD_*`), the job after a save (`R4_Start`, `R4_Load`, `R4_Check`: the code's rebuild, which writes no ledger byte), the save's `S_Locks`, `S_Load`, `S_Recheck` (no effect since S0070 and S0071), `S_Commit` (now the end of `S_Gate`), `S_Publish` (BL-0113), `S_Release`, recovery's `RC_Lock` (no locks since ADR-0068 D3), the create's `CR_ManifestTemp`, `CR_Load`, `CR_Commit` and the copy's `CP_Commit` (no disk effect).
- Changed to the code: the save's temp is written at a staging name (`S_TempStart` cuts it there); `S_Backup` syncs and reads the file and refuses a created path taken; `S_Compare` moves an outside save to its kept name (the swap-back is gone); the restore is one restore for the rollback and recovery (`RB_Check` and `RC_Check` became `RS_Check`; `RS_Temps` became `RS_Sync`, `RS_Clean`, `RS_Temp`, `RS_All`), in the code's order; a created path is never touched (`created_path_taken`); recovery stops while a kept copy exists; `gh.acc` (an outside save lost in a restore temp) is gone, as nothing deletes a restore temp unchecked.
- Properties removed (not durability): `AtMostOneEngine`, `AtMostOneJobRunning`, `AtMostOneOpenWorkspace`, `OnlyOpenWorkspaceRunsJobs`, `WaitedForFirst`, `NoOwnEndAfterCommitPoint`, `CancelReachIsTheOneRule`, `CancelBeforeCommitLeavesDiskUnchanged`, `OutcomeTruthful`, `UnchangedOnlyAfterRehash`, `CommittedCombinationChecked` (the save's load, ADR-0068 D1), `StrictRecovered`, `BlockedScreenHasReason`, `KeepOnlyJudged`, `NoOpenOverUnresolvedJournal`, `OtherNamesDoNotStop`, `DeleteAdviceOnlyForOwnFiles`, `InstallerNeverOverOpenJournal`, `NoAdviceOnTimerForSlowWork`, `EveryWaitHasAnExit`, `QuitAnywayAlwaysEnabled`, `AssessNeverWaits`, `OpeningHasItsJob`, `NoEngineStall`, `AppScopedWaitsForRecovery`, `NotesAnsweredByRecovery`, `JobIdsBounded`, and the liveness properties `EveryBootEnds`, `EveryWaitedForJobEnds`, `EverySwitchEnds`, `EveryQuitEndsOrOffersQuitAnyway`, `EngineCanAlwaysBeFreed`. Added: `KeptFilesDurable` (below).
- Mutants removed, each with why (54 of 81; 27 stayed, and S0072 round 1 added `x_keep_and_go_on`, so 28 run):
  - the queue and the engine: `dispatch_by_arrival`, `newest_first` (the order of the queue), `dispatch_while_running`, `spawn_before_reap`, `no_engine_lock` (one engine, one job: the queue's and the engine lock's tests), `read_refusal_stuck` (a stall of a read);
  - Cancel: `cancel_after_commit`, `cancel_ends_writing_job`, `r4_only`, `cancel_stops_all`, `rv_quiesce_ends_confirmed`, `gate_after_journal` (Cancel's reach and its "unchanged" outcome), `unchanged_without_rehash` (an outcome's wording);
  - the switch, the open and the boot: `recovery_while_open`, `open_before_close`, `switch_drops_confirmed_save`, `switch_close_never`, `switch_job_not_cancelable`, `stay_disabled`, `retry_disabled`, `boot_no_actions`, `boot_waits_for_success`, `screen_holds_queue`, `cancel_open_keeps_opening`, `rv_cancel_keeps_stage`, `assess_waits_for_work`, `app_scoped_during_recovery`, `appscoped_runs` (a reachability check of the old wording), `rv_retry_skips_recovery`, `back_skips_recovery` (its three configurations: the screens' reasons), `recover_target_only` (the open's target; the per-file judgement stops a journal over another save's bytes, `RecoveryNeverClobbersForeignBytes`), `untried_unmarked`, `recover_cancel_before_listing` (the stop bound and Cancel);
  - quit, its notes and the update: `quit_says_undone`, `quit_note_two_branches`, `quit_waits_reap`, `quit_anyway_only_while_quitting`, `finishing_confirmed_only` (a reachability check), `rv4_quit_drops_create`, `notes_only_at_boot`, `rc_unsynced` (the note's answer), `installer_ignores_journal`;
  - the advice: `advice_on_timer`, `advice_only_to_waiting`, `no_quit_message_for_writing`, `orphan_no_advice`;
  - outcomes told: `rv4_outcome_open_notsaved`, `create_crash_msg_from_memory`, `delete_message_always`, `listing_check` (a name the job does not write stopping it: not a loss), `rv4_no_cancel_recover`;
  - the save's load and the R4 job: `no_recheck` (ADR-0068 D1), `save_waits_r4_forever`;
  - the kept-name design: `swap_back_unsynced` (F3). BL-0161 asked for it to become `kept_move_unsynced` and still fail; it is equivalent in the kept-name design: the kept move is one rename, so whatever a power loss keeps of the swap and that rename, the outside bytes stand at the file, the temp or the kept name, and the rollback that follows stops on the kept copy at its check before it removes anything (S0072 journal).
- `keep_no_sync` (N1) breaks `KeptFilesDurable` now, not `SavedSurvivesPowerLoss`: since S0061 round 4 every restore swap is synced at once and every folder before its first removal, so the trace N1 found (a restore swap left unsynced when Keep ran) is gone; what Keep can still meet unsynced is the save's own swap cut before its folder sync, and without Keep's sync a power loss could bring back a mix of that save's pre and post bytes that no journal names any more.
- `no_displaced_check` disables both of the save's displaced checks (the swap's and the clean-up's), as the code has one function for them, and the restore's.
- Configurations removed (23): `S_save_quit`, `S_save_quit_power`, `S_quit` (quit), `S_cancel_commit`, `S_cancel` (Cancel), `S_r4_ends` (R4; its two saves are `S_save_two`), `S_forget` (forget; Keep's world is `S_keep`), `S_recover_bound` (the stop bound), `S_boot`, `S_stuck_opening`, `S_switch`, `S_switch_fail`, `S_switch_hung_create` (the boot and the switch), `S_create_close` (the requester), `S_queue`, `L_session`, `LS_boot`, `LS_jobs`, `LS_quit`, `LS_free`, `LS_free_cc`, `LS_free_orphan`, `LM_free` (liveness). `S_recover_lock` is `S_recover_link` (the engine lock is not the model's; the path guard and the temps are). Added: `S_save_two`, `S_keep`, and the 23 witnesses (§6.2).
- The queue's trace replay (`backend/tests/platform/test_engine_jobs_conformance.py`) is removed with the queue's actions; its check that the sidecar's decision after a save's engine death is the model's `DiskOutcome` is the sidecar's own table test (`backend/tests/platform/test_save_job.py` `test_save_outcome_is_the_disks_answer`).

**S0072 round 1 (verifier F1-F6, 2026-11-01).**
- F1: the save replay no longer compares the code's answer with `gh.told` (no property checks that it is true since `OutcomeTruthful` left, EP-19; `SavedSurvivesPowerLoss` checks only that a "saved" is durable): it checks it against the model's disk at the end, §6.6's table (§7).
- F2: `IOFail` at `s_sw` sets `ctl.repl` (the code's `progress.replaced` is set before the call), and a branch after the swap took place was added; the corpus samples both (`S_save_swap_io`). §3 lists the I/O-error limit.
- F4: `EditKept` counts a kept copy only while a journal naming it is `.open` or `.kept`; `x_keep_and_go_on` (the save keeps the outside save at the swap, then goes on to "saved" and `.closed`) must break `ExternalEditNeverSilentlyLost` and does. Every base world still passes.
- F3, F5, F6: recorded in §3 (the gate's open-journal refusal, `RS_Write`'s pre bytes, the syncs inside `RS_Write` and `RS_Swap`, which the replay now checks).

## 6. Mutants and witnesses

### 6.1 Mutants

Every mutant runs on a small world and checks only the property it must break (`gen_cfgs.py` `MUTANTS`).

| Mutant | Property | World | The bug |
|---|---|---|---|
| `report_before_close_sync` | `SavedSurvivesPowerLoss` | `S_save_power` | "saved" before the close is synced |
| `outcome_unsynced` | `SavedSurvivesPowerLoss` | `S_save_power` (+ a crash) | the outcome after an engine death read without the journal folder's sync |
| `keep_no_sync` | `KeptFilesDurable` | `S_keep` | Keep renames the journal without syncing the files |
| `no_backup_sync` | `BackupDurableBeforeReplace` | `S_save_crash` | the backup folder not synced |
| `no_journal_sync` | `JournalDurableBeforeFirstReplace` | `S_save_crash` | the `.open` rename not synced |
| `direct_open` | `PartJournalNeverBlocks` | `S_save_power` | the journal written straight as `.open`, unsynced |
| `no_displaced_check` | `ExternalEditNeverSilentlyLost` | `S_save_crash` | what a swap displaced (and a temp at clean-up) always taken for ours |
| `plain_create` | `ExternalEditNeverSilentlyLost` | `S_save_crash` (editor on N) | a created file put over one that appeared |
| `rollback_skips_temp_rule` | `ExternalEditNeverSilentlyLost` | `S_save_crash` (editor on R, no crash) | the rollback takes every temp for ours |
| `x_keep_and_go_on` | `ExternalEditNeverSilentlyLost` | `S_save_crash` (editor on R, no crash) | the save moves the outside save it met at the swap to its kept name, then goes on to "saved" (verifier S0072 r1 F4's own) |
| `no_temp_fsync` | `NoTornLedgerAfterRecovery` | `S_save_power` | the staging file's data not synced before its rename |
| `temps_after_close` | `CutTempNeverBlocks` | `S_save_crash` | the temps removed after the close |
| `no_temp_check` | `RecoveryNeverClobbersForeignBytes` | `S_save_crash` | recovery takes every temp for ours |
| `no_path_guard`, `fb_no_guard` | `RecoveryWritesOnlyRecordedPaths` | `S_recover_link` | the restore without the path guard (`fb_no_guard`: model review 1's own, the same bug) |
| `resolve_before_restores_synced` | `NoTornLedgerAfterRecovery` | `S_recover_power` | the restore swap not synced, `.resolved` before the folders' sync |
| `no_restore_temp_fsync` | `NoTornLedgerAfterRecovery` | `S_recover_power` | the restore temp's data not synced before its swap |
| `old_temp_rule` | `CutTempNeverBlocks` | `S_recover_link` (no link) | a staging file judged by its bytes, as a temp name is (the rule before 2026-10-30) |
| `block_without_cause` | `BlockedOnlyForCause` | `S_recover_link` (no link) | the old object at pre beside a file at post taken for an outside save |
| `crash_unmarked` | `EveryJournalRecoveredOrBlocking` | `S_recover` | an engine death in recover-all leaves the journal in hand without a reason |
| `rv_boot_skips_recovery` | `EveryJournalRecoveredOrBlocking` | `S_recover` (b3) | the start works before recover-all (fidelity review 3's own) |
| `copy_checks_blocking_only` | `NoCopyOverOpenJournal` | `S_copy` | the copy checks only blocking journals, not an in-session `.open` one |
| `manifest_first` | `NoManifestOverIncompleteFolder` | `S_create_empty` | the manifest before the files |
| `rv_create_no_sync` | `NoManifestOverIncompleteFolder` | `S_create_empty` | a created file not synced before the manifest (fidelity review 3's own) |
| `no_refuse_nonempty` | `NonEmptyTargetUntouched` | `S_create_nonempty` | a non-empty target not refused |
| `plain_file_write` | `DroppedFileNeverReplaced` | `S_create_absent` | a file written over one dropped under its name |
| `manifest_overwrite` | `DroppedFileNeverReplaced` | `S_create_empty` | the manifest put over a dropped one |
| `no_readback` | `ForeignOverwriteCaughtByReadBack` | `S_create_absent` | no read-back before the manifest |

### 6.2 Vacuity witnesses

A base world that passes could pass because it never reaches the branch a property is about. Each witness (`W_*` in `EngineJobs.tla`, `gen_cfgs.py` `WITNESSES`, configurations `WIT_*`, kind `witness`) is the negation of a branch as an invariant on a small world; TLC must report it violated, so the branch is reached. The branches: a save lands (`W_Saved`), a rollback resolves (`W_RollbackResolved`), puts pre bytes back (`W_RollbackPutsBack`), cannot resolve (`W_RollbackUnfinished`), keeps an outside save met at the swap (`W_KeptCopySave`), stops on a taken created path with the other files undone (`W_CreatedPathTaken`); an outside save after the gate is displaced into the temp (`W_EditorAfterGate`); recovery resolves a boot journal (`W_RecoverResolves`), keeps an outside save (`W_RecoverKeptCopy`), fails a try (`W_RecoverFailed`), stops on the path guard (`W_LinkStop`); Keep (`W_Keep`) and Keep of a torn journal (`W_KeepTorn`); a cut staging write meets recovery (`W_StagingCutSeen`) and the save cut by a power loss is undone (`W_CutStagingDeleted`, `W_PowerAfterSaveRecovered`); a copy reaches its manifest (`W_CopyDone`) and meets an in-session `.open` journal (`W_CopyRefusedInsession`); a create registers (`W_CreateDone`); the second save lands (`W_SecondSaved`) and its rollback puts both files back (`W_SecondRollbackTwoFiles`); on the weaker file system an unsynced outside save is that program's loss (`W_WeakLossAccepted`) and an outside save is kept (`W_WeakRecoverKept`).

## 7. The code's conformance (S0064, S0072)

`traces/corpus.json` holds TLC-simulated traces of small save worlds (`gen_traces.py`: the worlds, the seed, the depth, and the actions left out: create, copy, recover-all again, Keep, the next start, the app's crash, a power loss, the path changer). `engine/tests/ledger/test_save_conformance.py` replays each save through `ledger.save.run` over the in-memory file system and compares, at each step the code reaches, the files, the save's record, its temps and staging files, restore temps, kept copies and backup with the model's; at the end it checks what the code answered against the model's disk (§6.6's table over the save's record, else the re-hash: `told_on_disk`), never against `gh.told`, whose truth no property checks (EP-19; S0072 verifier r1 F1).

| Model | Code hook |
|---|---|
| `S_Gate` | the observation (step 4) |
| `S_Backup`, `S_BackupSync` | the backup folder's effects, a replaced file's `sync_file` |
| `S_JPart` .. `S_JDirSync`, `S_Close`, `S_CloseSync`, `RS_Resolve` | the journal folder's effects |
| `S_TempStart`, `S_TempDone`, `S_TempSync`, `S_Swap`, `S_DirSync`, `S_RemoveTemps` | the ledger folder's effects on the save's staging and temp names |
| `S_Compare`, `RS_Check`, `RS_Compare`, `RS_Temp` | a move to a kept name (`<name>.kept-<session>-<job>`) and its sync; otherwise reads, passed when they change nothing |
| `RS_Sync`, `RS_Clean`, `RS_Write`, `RS_Swap`, `RS_All` | the restore's effects (its staging and restore temp names, the removals, the syncs; `RS_Write` and `RS_Swap` must each include their folder's sync, `FUSED_SYNC`) |
| `S_Saved` | `job.committed()` |
| `EngineCrash` | an engine death raised at the code's hook; the outcome read by `save.outcome` (`DISK`) |
| `IOFail` | an `OSError` raised at the code's hook, before the effect; at the swap or exclusive rename after it, when the model's `IOFail` took the swap's effect (`MemFS.inject(after=True)`) |

No save path step stops the replay (S0072): only a save the walk cut is not followed. Not replayed: recover-all's tries over boot journals (BL-0164), create and copy (BL-0153).
