# EngineScheduler.tla and the engine-scheduler spec, both ways

- **Spec:** `design/specs/engine-jobs.md` (named `engine-scheduler.md` at d8ca7823; renamed in S0047), draft 5 at d8ca7823 (its §13 lists what the model must check). Built only from that text and the ADRs and invariants it cites for meaning; the private `tla/v3/` model (a superseded two-engine design) was used for TLA+ idioms and runner shape only.
- **Model:** `EngineScheduler.tla` (one module). Configurations come from `gen_cfgs.py` (`cfg/*.cfg`, `configs.txt`); `run_all.sh` runs them (HOW_TO_RUN.md).
- **State of the model:** every small configuration passes; every seeded mutant is caught by the property it targets; four spec issues found (§5 below), each modelled as a fix that is on by default, with a finding configuration that shows the violation without it.
- `scripts/tests/test_tla_engine_scheduler.py` keeps this file honest: every action in `Next` has a row in §2 and every row names an action; every property and mutant §13 names exists in the model and in `gen_cfgs.py`; every property has at least one mutant.

## 1. Spec → model

### 1.1 The world (§13 "World")

| §13 world element | In the model |
|---|---|
| three workspaces; W1 and W2 share one file, W3 shares none | `WS`, `LS(w)`: W1 = {R, N, I}, W2 = {R, J}, W3 = {K}; R is shared |
| each has a lineage id and a registry key; W1 forgotten and added again under a new key | journal fields `lin`, `key`; `pst.reg1` (W1's key, 1 → 4); `UserForgetReadd`; `Match` (§6.5 matching) |
| a save in W1 that replaces one file and creates one, with one more include it does not write | `Tg` = {R, N}: R replaced by swap, N created exclusively; I is in the ledger set and the fingerprint only |
| per file the bytes on disk and, separately, what survives a power loss (also entries kept before their data, torn) | `fs.cur` and `fs.pl` (possible contents after a power loss, `TORN` = -1, `ABS` = 0); a pending exchange or rename couples a file and its temp (`fs.cp`, `fs.cpn`) |
| backups | `fs.bk`, `fs.bkpl` per save (R's pre bytes) |
| the save's temps, cut off at any point while written | `fs.tc`, `fs.tpl` per (journal, file); `S_TempStart` leaves `TORN` until `S_TempDone` |
| restore temps | `fs.rt` (deleted without a check, §6.4) |
| journal states `.part` `.open` `.closed` `.done` `.mismatch` `.resolved` `.kept` and the R4 attempt count | `jr[j].st` (plus `tpart`/`topen` for torn ones), `jr[j].pl`, `jr[j].att`, `jr[j].cnc` |
| two or three journals left by crashed saves at boot, one on a disk that may be unreachable | `BootJ` ⊆ {b2 (W2: R, J), b3 (W3: K)}, `BootTemps` (cut, pre or post temps beside them); K's disk: `pst.unr`, `Unreach`; with a crashed save of W1 (s1) a third |
| the queue with waited-for and background jobs | `q` (job records with `wf`, `conf`, `req`, `own`) |
| one engine slot, the engine's process group (a member stuck in the kernel) | `eng` (`p` processes, `st` none/idle/busy/sig, `user`, `stuck`, `reason`) |
| the requester of each job (present or gone) | job field `req`; `UserCloseTab` |
| the engine build, which can change between a save and its R4 | `pst.build`, journal `build`; the installer in `QuitClose` |
| one `copy` of W2 (sources named by a blocking or in-session `.open` journal) | `UserCopy`, `CP_*` |
| one `create` into an absent, empty or non-empty target; a file appears under a name the job writes, under another name, or over a written file; requester may go before or after the commit point | `CreateTarget`, `tgt` (names a, m, o, x), `EditorDrop`, `EditorOverwrite`, `UserCloseTab`, `CR_*` |
| the published mirror and what produced it | `pst.mirror[w]` = fingerprint, `src` (overlay, rebuild, open, load_file), `build` |
| the update quiesce and the installer | `UserQuit("update")`, `QuitClose` |
| the boot, the switch, Reopen, the quit with its note, the next start | `Init`/`Relaunch`, `UserSwitch` (t = open workspace is Reopen), `UserQuit`, `UserQuitAnyway` (note), `NoteStep` |

### 1.2 Actors (§13 "Actors")

| Actor | Actions |
|---|---|
| the user | `UserSave`, `UserRead` (preview and BQL), `UserCancelQueued`, `UserCancelRunning`, `UserCloseTab`, `UserSwitch` (and Reopen), `UserStay`, `UserQuit`, `UserQuitAnyway`, `UserRetry`, `UserKeep`, `UserResume`, `UserOpenAnother`, `UserBack`, `UserTryAgain`, `UserForgetReadd`, `UserCreate`, `UserCopy`, `Relaunch` |
| the app (sidecar) | `Dispatch`, `DispatchUnavail`, `SpawnStep`, `CloseA`, `QuitClose`, `Assess`, `AppBgSave`, `AppFocusCheck`, `AdviceTimer`; the outcome check inside `EngineCrash`; the quit note in `UserQuitAnyway` and `NoteStep` |
| the engine | every `S_*`, `RB_Check`, `RS_*`, `RC_*`, `R4_*`, `RD_*`, `OP_*`, `CP_*`, `CR_*`, `FreshEnd` |
| the OS | `EngineCrash`, `AppCrash`, `PowerLoss`, `Stuck`, `Hang`, `IOFail`, `Drain`, `WindowExpire` |
| an outside editor | `Editor` (any ledger file, durably, at any time, also while the app is dead), `EditorDrop`, `EditorOverwrite` |
| a path changer | `PathChange` (after a crash), `BootLink` |
| an orphaned engine holding its locks | `pst.orphan` (from `AppCrash`/`UserQuitAnyway` with a stuck or lingering engine, or `BootOrphan`), `OrphanEnd`, `OrphanEndStuck` |

### 1.3 Rules, by section

| Spec | Rule | Model |
|---|---|---|
| §1 | commit point; closed record; waited-for vs background | `S_Commit`, `CR_Commit`, `CP_Commit` set `ctl.past`; `jr.st = "closed"`; job field `wf` |
| §2 | jobs and who waits; every job idempotent at its start; work on ledger files inside a job | job kinds in `FirstPc`; `R4_Pick`/`R4_Start` do nothing without a closed record; reads, saves, recovery all run as jobs |
| §3 | the ordering rule (oldest waited-for, else oldest background); never interrupted | `PickIdx`, `Dispatch` (needs `~Busy`) |
| §3 | during a switch/Reopen/boot only its own jobs start | `SwitchExclusive`, `Elig` |
| §3 | a job for a workspace that is not open is never queued | user and app actions queue only for `ses.opened`; during a quiesce new jobs naming A are refused (`S_Release`, `S_Publish`, see §4 I2) |
| §3 | at most one queued r4 and one background save per workspace | `NeedR4`, `AppBgSave` guard |
| §4 | Cancel of a queued job; of a running job before its commit point; "Finishing" after it | `UserCancelQueued`, `UserCancelRunning` (guard `~ctl.past`), `CancelEffects` |
| §4 | the commit point decided in one table step | `S_Commit` (a Cancel before it has already ended the engine; none acts after it) |
| §4 | the one rule for another job's Cancel; advice only as the answer to that Cancel; requester-present jobs are their own requester's | `Endable`, `UserCancelQueued` (end, own-only, or `advice = "cancel"`) |
| §4 | a client that goes away cancels its read, never a save | `UserCloseTab` |
| §4, §9.3 | Quit anyway ends any job, also past its commit point | `UserQuitAnyway` |
| §5.1 | at most one engine; a new one only after the reap and an empty group | `SlotFree`, `Spawned`, `Drain` |
| §5.1 | an engine that ran user code is ended after its job; parse-only may serve the next | `Retired`, `Fresh` (reuse an idle engine with `user = FALSE`), `FreshEnd` |
| §5.2 | the reason recorded before the signal; our end not a stop | `SigEng` records `reason`, `endBy`, `ended`; `stops` counts only `EngineCrash` |
| §5.2 | a process that cannot be ended keeps the slot and its locks; the timer advice only for a signalled process | `Stuck`, `eng.st = "sig"` holds `SlotFree` false; `AdviceTimer` |
| §5.2, §15 | an orphan holds its locks after its app died | `OrphanAfter`, `pst.orphan` |
| §5.3 | crash, outcomes, bounded restart (Unavailable, Try again) | `EngineCrash`, `ses.stops`, `StopBound`, `WindowExpire`, `UserTryAgain`, `DispatchUnavail` |
| §5.4 | no time limits; "Still working…" changes only text | no timer ends a job; only `AdviceTimer` changes `ses.advice` |
| §6.1 step 1 | R4 first, if owed (re-read from disk); outcomes; attempts | `S_R4Check`, `R4_*` with `r4ctx = "save"`, `R4Failed` |
| §6.1 steps 3-6 | locks (waiting for an orphan); gate under locks; load; re-check | `S_Locks`, `S_Gate`, `S_Load`, `S_Recheck` |
| §6.1 step 8 | backups synced; `.part` synced, renamed `.open`, dir synced; per file temp, sync, swap or exclusive create, displaced check, dir sync; read-back; temps removed; close and sync | `S_Backup`…`S_CloseSync` (order in `NextS`) |
| §6.1 steps 9-11 | "saved" after the durable close; publish if fingerprint = post and no `.open`; release, retire, queue r4 | `S_Saved`, `S_Publish`, `S_Release` |
| §6.3 | the path guard | `PathMoved`, `FileBad`, `RS_Swap` |
| §6.4 | rollback after any failure past the first replace; displaced check on every swap; P1 temp rule; restore temps deleted without check | `IOFail`, `S_Compare`, `S_ReadBack`, `StartRB`, `RS_*`, `TempBad` |
| §6.5 | records matched by registry key or lineage; the gate reads files, never memory | `Match`, `GateClosed`/`GateMism` (files) |
| §6.6 | outcome after an engine death, from disk | `DiskOutcome` in `EngineCrash` |
| §6.7 | R4 steps 1-4, build change publishes load_file, nondeterministic, mismatch, attempts → could_not_check, Resume saving | `R4_Start`, `R4_Load`, `R4_Check`, `R4_Confirm`, `R4Failed`, `UserResume` |
| §7 | loads re-check the fingerprint (retry, then LEDGER_CHANGING) and refuse over `.open` | `RD_Load`, `RD_Check`, `OP_Load`, `OP_Check` |
| §8.1 | recover-all: `.part` deleted; per journal lock try, check, restore root first, temps deleted, sync, then `.resolved`; Cancel, crash and Unavailable mark blocking; every journal recovered or blocking | `RC_Init`, `RC_Next`, `RC_Lock`, `RC_Check`, `RS_*`, `UserCancelRunning`, `EngineCrash`, `SpawnStep`, `DispatchUnavail` |
| §8.1 afterwards | blocking journal blocks opens and copies using its files; Keep renames `.kept` | `Blocked`, `Assess`, `CP_Check`, `UserKeep` |
| §8.2 | create/copy: non-empty refused; copy refused over any `.open` journal; exclusive writes; only a collision with a written name stops; read-back; commit point; manifest last; delete advice from a listing | `CR_*`, `CP_*`, `CrStopGh` |
| §9.1 | quiesce, Stay, A closes, recover_all, target opens, Back | `Quiesce`, `UserStay`, `CloseA`, `Assess`, `OP_*`, `UserBack` |
| §9.2 | boot: recover_all then open; ends on success, failure or block; Retry, Open another, Keep | `Init`, `Relaunch`, `Assess`, `UserRetry`, `UserOpenAnother`, `UserKeep` |
| §9.3 | quit: quiesce, wait for saves, Quit anyway with the note; next start's three notices; close does not wait for the reap | `UserQuit`, `QuitClose`, `UserQuitAnyway`, `NoteStep` |
| §9.3, ADR-0012 | installer never over a `.open` journal | `QuitClose` (update branch) |
| §10 | what survives at each step | the power-loss model in `PowerLoss`, checked by the properties of §1.4 |
| §11 | "unchanged" only after a re-hash; messages | `Rehash`, `gh.told` |

### 1.4 Properties (§13; names as in the spec)

"Kind": INV = state invariant; ACT = action property `[][A]_vars`; LIVE = liveness under `FairSpec` (or `FairUserSpec`). Every small safety configuration checks every INV and ACT property.

| §13 property | Model definition (kind) | How it is stated | Mutants that must break it |
|---|---|---|---|
| `AtMostOneEngine` | INV | `eng.p <= 1` | `spawn_before_reap` |
| `AtMostOneJobRunning` | INV | `Cardinality(runs) <= 1` | `dispatch_while_running` |
| `AtMostOneOpenWorkspace` | INV | `Cardinality(ses.opened) <= 1` | `open_before_close` |
| `OnlyOpenWorkspaceRunsJobs` | INV | a running workspace job's workspace is open; `recover_all` runs only with none open | `recovery_while_open` |
| `WaitedForFirst` | ACT | a job that starts is waited-for, or no eligible waited-for job was queued | `dispatch_by_arrival` |
| `NoOwnEndAfterCommitPoint` | INV | our `cancel` never ends a writing job past its commit point (Quit anyway leaves no session) | `cancel_after_commit` |
| `CancelReachIsTheOneRule` | INV | another job's Cancel ended only an R4, a background save before its commit point, or a read/open whose requester is gone; the quit advice answers a Cancel only for a job the rule does not let it end | `cancel_ends_writing_job`, `r4_only` |
| `CancelBeforeCommitLeavesDiskUnchanged` | INV | a cancelled save has no journal and no post bytes on disk | `gate_after_journal` |
| `OutcomeTruthful` | INV + ACT | every told outcome (saved, not saved, unfinished, undone, the quit note's three notices, created / not created) matches the records and files; the note is deleted only once the record is saved, undone or kept; a confirmed save is never answered "not open" | `quit_says_undone`, `quit_note_two_branches`, `switch_drops_confirmed_save` |
| `SavedSurvivesPowerLoss` | INV | told "saved" ⇒ the record survives a power loss as closed or later, and its files (not named by another open journal) durably hold post | `report_before_close_sync`; finding F1 |
| `UnchangedOnlyAfterRehash` | ACT | "unchanged" is told only when the targets equal their pre bytes in that step | `unchanged_without_rehash` |
| `BackupDurableBeforeReplace` | INV | post bytes on disk before the close ⇒ the backup is durable | `no_backup_sync` |
| `JournalDurableBeforeFirstReplace` | INV | post bytes on disk before the close ⇒ the journal is durably `.open` | `no_journal_sync` |
| `PartJournalNeverBlocks` | INV | only a complete `.open` journal is ever marked blocking (a journal cut while written never blocks) | `direct_open` |
| `CommittedCombinationChecked` | INV | a save past its commit point saw no outside edit to the include it does not write during its load | `no_recheck` |
| `ExternalEditNeverSilentlyLost` | INV | the editor's latest bytes of a file are in the file, a temp beside it or a restore temp, or were the plan's base, or fall under §15's accepted loss | `no_displaced_check`, `plain_create`; findings F2, F3 |
| `NoTornLedgerAfterRecovery` | INV + ACT | a ledger file is torn only while a journal naming it is `.open`; a journal becomes `.resolved` only when its files durably hold something other than its post bytes | `resolve_before_restores_synced`, `no_temp_fsync` |
| `RecoveryNeverClobbersForeignBytes` | INV | as `ExternalEditNeverSilentlyLost`, for edits made while a journal naming the file is `.open` | `no_temp_check` |
| `RecoveryWritesOnlyRecordedPaths` | INV | recovery never writes through a replaced folder | `no_path_guard` |
| `CutTempNeverBlocks` | ACT + INV | a journal is never stopped when its only fault is a cut temp beside a file that does not hold post; no temp of a save outlives its journal (I4) | `old_temp_rule`, `temps_after_close` |
| `EveryJournalRecoveredOrBlocking` | INV | while an open runs or a workspace is open, every `.open` journal from before this open is blocking | `cancel_stops_all`, `crash_unmarked`, `untried_unmarked` |
| `NoOpenOverUnresolvedJournal` | INV | no workspace is open (or opening) whose files a pre-existing `.open` journal names | `recover_target_only` |
| `NoCopyOverOpenJournal` | INV | a copy past its check has no `.open` journal naming a source | `copy_checks_blocking_only` |
| `BlockedOnlyForCause` | ACT | a journal becomes blocking only with its cause present: foreign bytes, moved or unreachable path, bad backup, foreign temp beside post, unreadable, lock in use, Cancel, crash or I/O error, Unavailable | `block_without_cause` |
| `NoSaveWhileR4Owed` | INV | a save that writes has no other closed or mismatch record of W1 | `gate_in_memory` |
| `RecordsMatchByLineage` | INV | as above, for records under W1's old registry key (forget and re-add) | `gate_by_key` |
| `R4GateLiftedOnlyByUser` | ACT | a `.mismatch` record changes only with a Resume saving | `cnc_auto_lift` |
| `BuildChangePublishesRealLoad` | ACT | a record that becomes `.done {superseded_build}` publishes the new build's `load_file` result | `supersede_publishes_nothing` |
| `NoManifestOverIncompleteFolder` | INV | a manifest (also one that may survive a power loss) only over the job's read-back, checked files | `manifest_first` |
| `NonEmptyTargetUntouched` | INV | nothing written into a non-empty target | `no_refuse_nonempty` |
| `DroppedFileNeverReplaced` | INV | a file dropped under a name the job writes stays the dropper's | `plain_file_write` |
| `OtherNamesDoNotStop` | INV | a collision stop happens only with a foreign file under a written name | `listing_check` |
| `ForeignOverwriteCaughtByReadBack` | INV | an overwrite before the read-back never reaches the load check or the manifest | `no_readback` |
| `DeleteAdviceOnlyForOwnFiles` | ACT | "You can delete it" only when the folder holds the job's files alone | `delete_message_always` |
| `InstallerNeverOverOpenJournal` | ACT | the build changes only with no `.open` journal | `installer_ignores_journal` |
| `EveryBootEnds` | LIVE | the boot ends; a queued create then leaves the queue | `boot_waits_for_success`; finding F4 |
| every waited-for job ends | LIVE `EveryWaitedForJobEnds` | ∀ job id: waited-for queued or running ~> neither | `save_waits_r4_forever` |
| every switch ends | LIVE `EverySwitchEnds` | a switch ends or reaches its screen (I5) | `switch_waits_for_r4` |
| every quit ends | LIVE `EveryQuitEnds` | a quit ends, or shows the quit advice (I6) | `quit_waits_reap` |
| an owed R4 runs, is superseded or acknowledged before the next save lands | ACT `OwedR4BeforeNextSave` | a record becomes closed only with no other closed or mismatch record of W1 | `r4_skipped_before_save` |
| `EngineCanAlwaysBeFreed` | LIVE (`FairUserSpec`) + INV part | with the waiting user pressing Cancel: the running job ends, or the advice is shown, or the app is gone; a slot held by a signalled process gets the timer advice while someone waits; Quit anyway always enabled (`QuitAnywayAlwaysEnabled`) | `no_quit_message_for_writing` |
| `NoAdviceOnTimerForSlowWork` | INV | timer advice only while a signalled process holds the slot | `advice_on_timer` |
| `EveryWaitHasAnExit` | INV | in each wait the exit the spec names is enabled (Cancel, Quit anyway when finishing, Stay, Retry/Back/Open another/Keep on screens) | `boot_no_actions` |

## 2. Model → spec

### 2.1 Actions

| Action | Spec text it comes from |
|---|---|
| `UserSave` | §6.1 (a confirmed plan, planned on the published mirror the preview read) |
| `UserRead` | §2 `preview`, `bql` (one transient load, waited-for) |
| `UserCreate` | §2, §8.2 `create` |
| `UserCopy` | §2, §8.2 `copy` of W2 |
| `UserCancelQueued` | §4 table rows 1-2, the one rule |
| `UserCancelRunning` | §4 table rows 3-5; §8.1 Cancel per journal |
| `UserCloseTab` | §4 "a client that goes away"; §13 requester gone |
| `UserSwitch` | §9.1 (Reopen when the target is the open workspace; from no workspace, §9.2's "Open another") |
| `UserStay` | §9.1 step 3 |
| `UserRetry` | §8.1 "Retry on the blocked screen runs recover_all again"; §9.1 step 6; §9.2 |
| `UserBack` | §9.1 step 6 "Back to A" |
| `UserOpenAnother` | §9.2 "Open another workspace" |
| `UserKeep` | §8.1 afterwards, "Keep the files as they are now" |
| `UserResume` | §6.5, §6.7 Resume saving |
| `UserForgetReadd` | §6.5, §13 "W1 can be forgotten and added again under a new key" |
| `UserTryAgain` | §5.3 Try again |
| `UserQuit` | §9.3 steps 1-2 (and the update quiesce, §13) |
| `UserQuitAnyway` | §9.3 step 3, §4 last bullet |
| `Relaunch` | §9.2, the next start |
| `AppBgSave` | §2 background save (prices) |
| `AppFocusCheck` | §2 `rebuild` after the focus check |
| `Dispatch` | §3 ordering rule, §5.1 spawn |
| `DispatchUnavail` | §5.3 Unavailable; §8.1 not_tried |
| `SpawnStep` | §5.1 fresh engine after user code; §6.1 step 1, §6.7 step 4, §8.1 next journal in a new engine |
| `CloseA` | §9.1 step 4 |
| `QuitClose` | §9.3 step 4; the installer (ADR-0012) |
| `Assess` | §9.1 step 6, §9.2 |
| `AdviceTimer` | §5.2 last bullet, §5.4 display delay |
| `S_R4Check`, `S_WaitR4` | §6.1 step 1 (`S_WaitR4` exists only for the mutant `save_waits_r4_forever`) |
| `S_Locks` | §6.1 step 3 |
| `S_Gate` | §6.1 step 4 |
| `S_Load` | §6.1 step 5 |
| `S_Recheck` | §6.1 step 6 |
| `S_Commit` | §6.1 step 7, §4 one table step |
| `S_Backup`, `S_BackupSync` | §6.1 step 8.1 |
| `S_JPart`, `S_JPartSync`, `S_JOpen`, `S_JDirSync` | §6.1 step 8.2 |
| `S_TempStart`, `S_TempDone`, `S_TempSync`, `S_Swap`, `S_Compare`, `S_DirSync` | §6.1 step 8.3, §6.4 displaced check |
| `S_ReadBack`, `S_RemoveTemps` | §6.1 step 8.4 |
| `S_Close`, `S_CloseSync` | §6.1 step 8.5 |
| `S_Saved` | §6.1 step 9 |
| `S_Publish` | §6.1 step 10 |
| `S_Release` | §6.1 step 11 |
| `RB_Check` | finding F2 (recovery's step-2 temp rule applied by the rollback) |
| `RS_Write`, `RS_Swap`, `RS_Compare` | §6.4 rollback, §8.1 step 3 restore by temp and swap, displaced check, path guard |
| `RS_Temps` | §8.1 step 3 temps deleted whatever they hold; §15 accepted loss |
| `RS_Sync` | §8.1 step 3 "every changed directory is synced" |
| `RS_Resolve` | §8.1 step 4, §6.4 `.resolved {not_saved}` |
| `RC_Init` | §8.1 "A `.part` journal is deleted"; new epoch |
| `RC_Next` | §8.1 each `.open` journal on its own; unreadable journal |
| `RC_Lock` | §8.1 step 1 |
| `RC_Check` | §8.1 step 2 |
| `R4_Pick`, `R4_Start`, `R4_Load`, `R4_Check`, `R4_Confirm` | §6.7 steps 1-4 |
| `FreshEnd` | §5.1, §6.7 step 4 "that engine ends" |
| `RD_Load`, `RD_Check` | §7 (preview, BQL, rebuild) |
| `OP_Load`, `OP_Check` | §2 `open`, §7, §9.1 step 6 |
| `CP_Check`, `CP_Load`, `CP_Commit`, `CP_Manifest` | §8.2 steps 2, 5-8 for a copy |
| `CR_Target`, `CR_WriteA`, `CR_SyncA`, `CR_ReadBack`, `CR_Load`, `CR_Commit`, `CR_ManifestTemp`, `CR_ManifestRename`, `CR_ManifestSync`, `CR_Register` | §8.2 steps 1-8 for a create |
| `EngineCrash` | §5.3 crash, §6.6 outcome, §6.7 attempts, §8.1 crash |
| `AppCrash` | §10 "app"; §5.2 orphan |
| `PowerLoss` | §10 "power" |
| `Stuck` | §5.2 a process that cannot be ended |
| `Hang` | §5.4 slow or hung user code (no time limit) |
| `IOFail` | §6.4 any failure after the first replace; §8.1 step 3 I/O error |
| `Drain` | §5.2 drain and reap |
| `OrphanEnd`, `OrphanEndStuck` | §5.2, §15 orphaned engine |
| `WindowExpire` | §5.3 stops leave the 300 s window |
| `Editor`, `EditorDrop`, `EditorOverwrite` | §13 outside editor |
| `PathChange` | §13 path changer, §6.3 |
| `Unreach` | §13 a disk that may be unreachable |

### 2.2 Variables

| Variable | Spec |
|---|---|
| `fs` | ledger files, temps, backups, restore temps and their power-loss outcomes (§6.1 step 8, §6.2, §10) |
| `jr` | records in `local/*/journal/` (§6.5); `why` = the blocking reason of §8.1; `ep` = the recover-all epoch it predates |
| `pst` | durable state: engine build, W1's registry key, `local/quit-note.json`, the link, K's disk, orphans' locks, the published mirrors, the last opened workspace |
| `tgt` | the create's target folder (§8.2) |
| `ses` | the session: app phase (boot, run, switch, quit, update, dead), switch stage, open workspace, screen, recover epoch, stops (§5.3), advice (§5.2, §11), our locks, the scheduler's in-memory cache of closed records (§6.5 "a cache") |
| `q`, `runs`, `ctl` | the queue (§3), the running job, its progress |
| `eng` | the engine process and its group (§5) |
| `n` | bounds on the environment and the user (model only) |
| `gh` | ghost: what the user was told (§11), and history the properties need (editor's last bytes, accepted losses, create outcomes) |

## 3. What the model abstracts away

| Abstraction | Why it is safe here, or where it is covered instead |
|---|---|
| Beancount semantics, digests beyond equal or not | §13 "Not modelled"; R4's outcome is a nondeterministic choice in `R4Outs`; the corpus and Hypothesis model (ARCHITECTURE §8) |
| OS rename atomicity beyond the stated primitives | §13; exchange and rename are atomic steps; metadata operations in one folder reach the disk in order (journaling file systems); data not synced may be torn |
| An outside program's durable save syncs the folder | it writes, renames and syncs; our pending operations in W1's folder persist with it (`Editor`) |
| Restore temps under a power loss | `fs.rt` keeps its content; recovery deletes restore temps without a check either way, and the loss this allows is §15's accepted limit (recorded in `gh.acc`) |
| One folder per workspace; journals, backups and the note durable when their step says so | the order of their syncs is modelled per step; rename+sync of `.resolved`, `.mismatch`, `.done` and the note are one step each, as the spec states them durable |
| Legacy-lock detection (§6.1 step 2) | not in §13's world; ADR-0062's detection tests (§14 "Legacy lock") |
| `max_queued`, `ENGINE_QUEUE_FULL` (named `BUSY_HEAVY_LOAD` at d8ca7823) | bounded by the model's counters instead; §14 queue tests |
| Backoff delays, the 300 s window, the display delay | untimed: `WindowExpire` and `AdviceTimer` are fair environment steps; no property depends on durations |
| Protocol violations (§5.3 rows) | not in §13's world; §14 engine tests; a violation past the commit point behaves like a crash at the exit, which `EngineCrash` covers |
| Phase texts, "Still working…" | only the quit advice is state (`ses.advice`); §14 UI tests |
| Decision log (§12) | §14 replay tests |
| Fingerprint parts other than file contents (globs, documents listings, plugin sources) | a fingerprint is the versions of the ledger set; §14 R4 and gate tests cover plugin and glob changes |
| Copy's own target folder | the copy's checks are modelled (`CP_*`); the target mechanics are the create's (`CR_*`), which §8.2 shares |
| One user container, desktop | §3 per `UserContainer`; hosted mode repeats it per container |
| W1 is the only workspace that saves; s1-s3 its saves | the boot journals b2, b3 give W2 and W3 their unfinished saves; the save path is the same code |
| The single-instance lock, a leftover sidecar, the shell's "Quit now" | not in §13's world; ADR-0062 tests, §15 |
| Keep after a path change resolves the folder again | `UserKeep` clears `pst.lnk` (the next open resolves canonical paths afresh) |
| Two editor versions per file | each outside write differs from the file's bytes before it; more versions add no new orderings for these properties |

## 4. Interpretations where the spec is silent

- **I1** A Cancel of `recover_all` marks the journal it is on as blocking (`cancelled`) and goes on in a new engine; before a journal is in hand, "the journal it is on" is the next one it would try, and with none left the Cancel ends recover-all; so every Cancel makes progress. While recover-all only waits for a new engine it holds none and there is nothing to cancel; a stuck slot then gets the timer advice.
- **I2** A save that ends during a quiesce (switch, quit, update) does not queue its R4 or rebuild: §9.1 step 2 refuses new jobs naming A, and the closed record stays for the next open. Without this a graceful quit waits for an R4 behind a stuck retire.
- **I3** A Cancel by the waiting user also applies when the running job sits between two engines (`SpawnPcs`); ending a job that holds no engine signals nothing.
- **I4** `temps_after_close` has no named property in §13. The model attaches "no temp of a save outlives its journal" (§6.1 step 8.4: "a crash after the close leaves nothing of ours in the folder") to `CutTempNeverBlocks`. A separate name in §13 would be clearer.
- **I5** "Every switch ends (target open, Back, or Stay)": a switch whose target fails or is blocked waits for the user at its screen; the model counts reaching the screen as the end of the switch's own work, with `EveryWaitHasAnExit` requiring Retry and Back there.
- **I6** "Every quit ends" cannot hold under engine and app fairness alone when a process stuck in the kernel holds the slot that a confirmed save waits for; the model checks that the quit ends or the user is shown the quit advice, whose Quit anyway always ends the app.
- **I7** §13 names no property for "the switch drops a queued confirmed save" other than `OutcomeTruthful`; such a save answered `WORKSPACE_NOT_OPEN` would be truthful. The model reads "closing is not a cancel" into `OutcomeTruthful`: a confirmed save is never answered "not open".
- **I8** The liveness items "every waited-for job ends", "every switch ends", "every quit ends" and "an owed R4 runs … before the next save lands" have no CamelCase names in §13; the model calls them `EveryWaitedForJobEnds`, `EverySwitchEnds`, `EveryQuitEnds`, `OwedR4BeforeNextSave`.
- **I9** `EngineCanAlwaysBeFreed` is a possibility statement (some user actions lead to …), which LTL cannot state directly. The model checks it under weak fairness of the waiting user's Cancel and of the requester's Cancel (`FairUserSpec`), with tab closes limited to the running job (`CloseQueued = FALSE`) and no switch: a user who closes their own waiting tab, or whose waiting request a switch they started drops, has stopped waiting without pressing Cancel, which the property does not cover.
- **I10** The assessment of a workspace on an unreachable disk fails (the open fails), as reading its files does.

## 5. Findings: spec issues the model found

Each is modelled as a fix in `Fixes` (on in every configuration) and a finding configuration that runs the spec as written and must violate the property. Traces are TLC's shortest, shortened here.

### F1 `sync_before_report`: "saved" reported from a record that is not yet durable (§6.6, §9.3; `SavedSurvivesPowerLoss`, `OutcomeTruthful`)
- Trace (`F1_outcome_unsynced`): boot; save s1 runs to `S_Close` (`.open` renamed `.closed`, directory not yet synced); the engine crashes; the outcome check (§6.6) lists the directory, sees `.closed` and answers **saved**. A power loss now can bring back `.open` (the rename was never synced); the next start's recovery undoes the save the user was told about.
- The same holds at the next start after an app crash or Quit anyway between `S_Close` and `S_CloseSync`: the quit note's "Your last change before you quit was saved." is read from a `.closed` entry that may not be durable.
- A sibling (found by `L_save_all`): an engine death right after `.part` was renamed `.open`, before the directory sync, makes §6.6 answer "could not finish your change … Reopen"; a power loss then leaves no journal at all, so nothing is ever undone or reported again. Harmless to the ledger (nothing was replaced) but the told outcome no longer matches the disk.
- §10's last sentence ("a power loss never removes a save the user was told about, because 'saved' is sent only after the close is durable") holds for step 9 but not for §6.6 and §9.3, which read the record without syncing it.
- Model fix: the outcome check syncs the journal directory before it reports from a record, and recover-all syncs the journal directories it lists before the note is read. Suggested spec text: §6.6 and §9.3 "the record is read after a `durable_sync` of its directory".
- **Judgement:** a spec flaw, not a modelling error. The model assumes only what the spec itself states: a rename is durable after its directory sync (§6.1 step 8.5, §6.2). The window is narrow (an engine death or app exit between the close's rename and its sync, then a power loss before anything syncs that folder) but the result breaks the spec's own promise in §10. Suggested severity: Medium.

### F2 `rollback_checks_temps`: the rollback deletes displaced outside bytes (§6.4; `ExternalEditNeverSilentlyLost`)
- Trace (`F2_rollback_deletes_displaced`): save s1 past its re-check; the editor saves R (durably); the save's swap of R puts post bytes in R and the editor's bytes in the temp; an I/O error before the swap's compare starts the rollback; the rollback restores R (it holds post, so the restore's own check passes), then deletes "the save's temps … whatever they hold" (§8.1 step 3, applied by §6.4's "in recovery's durable order"). The editor's bytes are gone and no one is told.
- Recovery would have stopped (step 2's P1 rule: a temp beside a file that holds post, holding other bytes, is a displaced outside save); the in-engine rollback has no step 2.
- Model fix: before restoring, the rollback applies recovery's temp rule; a match stops the journal (unfinished, both versions kept). Suggested spec text in §6.4: "The rollback first applies §8.1 step 2's temp rule".
- **Judgement:** most likely a spec flaw (or at least a wording gap): §6.4 says the rollback runs "in recovery's durable order (§8)", which can be read as steps 3-4 only (as the model does) or as including step 2's checks. If the intended reading includes step 2, the spec should say so and there is no flaw. The model's one added assumption is an I/O error between a swap and its compare (the compare reads the temp to hash it, so an error there is plausible). Suggested severity: Medium (silent loss of an outside save).

### F3 `sync_after_swap_back`: a swap-back is never synced (§6.4, §8.1 step 3; `ExternalEditNeverSilentlyLost`)
- Trace (`F3_swap_back_unsynced`): save s1 lands both swaps; the editor saves R; the read-back fails and the rollback restores R: its swap displaces the editor's bytes, the compare sees them and swaps them back ("put back and kept"); nothing syncs the folder; a power loss can leave R with our restored pre bytes and the editor's bytes in the restore temp, which the next recovery deletes without a check.
- §15 accepts a loss only for "an outside write in the instant between a recovery's check and its own restore swap, followed by a crash at that instant". Without a sync after the swap-back, the window lasts until something else syncs that folder, which may be long after the journal stopped.
- Model fix: the swap-back is followed at once by a directory sync. Suggested spec text in §6.4: "…swapped (or renamed) back into place and the directory synced".
- **Judgement:** a spec gap whose weight depends on the model's power-loss assumptions: metadata operations in one folder persist in order, an outside save is durable, and recovery deletes restore temps without a check (§6.4 P1). Under those, the loss is real but sits next to §15's accepted limit; the owner may prefer to widen that limit's wording instead of adding a sync. Suggested severity: Low to Medium.

### F4 `switch_screen_frees_queue`: a switch's screen holds app-scoped jobs (§3, §9.1, §9.2; `EveryBootEnds`)
- Trace (`F4_switch_screen_holds_queue`): the user asks for a create during the boot; recover-all of b2 crashes, b2 blocks, the boot ends on its blocked screen (the create may now run); the user presses Retry, which is a switch with no current workspace; it is cancelled on b2 and ends on the blocked screen again. §3 keeps app-scoped jobs waiting "until the switch … has ended", and the switch ends only when the user acts, so the create waits on the user.
- §9.2 ends the boot at its screen precisely so that app-scoped requests are not held (`boot_waits_for_success`); §9.1 has no such rule for a switch's failed or blocked screen.
- Model fix: once a switch shows its failed or blocked screen, its own work is done and other jobs may start (the workspace is still closed, so only app-scoped jobs qualify). Suggested spec text in §3: "…until the switch or boot has ended or shows its failed or blocked screen".
- **Judgement:** a spec gap, partly a modelling reading. The trace uses Retry on the boot's blocked screen, which the model runs as a switch with no current workspace (§8.1 "Retry … runs recover_all again"); §9.2 says "Open another workspace" is "an ordinary switch", which gives the same wait without that reading. Liveness and user experience only (a create waits for the user's choice on a screen); no data at risk. Suggested severity: Low to Medium.

### Observations (no property breaks; for the spec's next review)
- **O1** §6.7 step 4: the confirm parses the disk without a fingerprint check. An outside edit between step 3 and the confirm makes the parse digests differ from the record's, and "anything else → mismatch" pauses saving with a false mismatch. It fails closed; a fingerprint check before the confirm (moved → superseded) would avoid it.
- **O2** I2, I5, I6, I7 and I8 above are readings the spec should state.

### Model corrections made while running
Not spec issues; listed so a reviewer can judge them: the power-loss model first let a file and its temp revert independently after an atomic exchange (fixed with the coupled pairs of §3); a few refusal branches were silently disabled by contradictory `UNCHANGED` clauses (found by the action-coverage check: every action now fires in the small tier); a hang survived the replacement of its engine; a blocking mark survived a power loss; recover-all's Cancel was read three ways before I1's final form; several properties were first stated too strictly (SavedSurvivesPowerLoss during a later recovery, NoteRule with `.part`). Each was found by TLC as a counterexample and checked against the spec text before changing the model.

## 6. Mutants

Each runs on a small world (`cfg/MUT_<name>.cfg`) and checks only its property; `run_all.sh` requires TLC to report that property violated. 35 come from the two executable models §13 cites, 5 are §13's additional ones, 11 were added so that every property has one (PROCESS.md §3).

| Mutant | Origin | Property | World | What it changes |
|---|---|---|---|---|
| `report_before_close_sync` | model 1 | `SavedSurvivesPowerLoss` | `S_save_power` | "saved" before the close's directory sync |
| `no_journal_sync` | model 1 | `JournalDurableBeforeFirstReplace` | `S_save_crash` | the `.part` neither synced nor its rename dir-synced |
| `no_backup_sync` | model 1 | `BackupDurableBeforeReplace` | `S_save_crash` | backups not synced |
| `no_temp_fsync` | model 1 | `NoTornLedgerAfterRecovery` | `S_save_power` | the temp swapped in without its fsync |
| `gate_in_memory` | model 1 | `NoSaveWhileR4Owed` | `S_save_crash` (two saves) | step 1 and the gate read the in-memory cache |
| `no_path_guard` | model 1 | `RecoveryWritesOnlyRecordedPaths` | `S_recover_lock` | recovery writes through a replaced folder |
| `recover_target_only` | model 1 | `NoOpenOverUnresolvedJournal` | `S_recover` | recover-all and the block test consider only the target's journals |
| `cancel_after_commit` | model 1 | `NoOwnEndAfterCommitPoint` | `S_cancel_commit` | Cancel ends the engine past the commit point |
| `resolve_before_restores_synced` | model 1 | `NoTornLedgerAfterRecovery` | `S_recover_power` | `.resolved` before the restored files are synced |
| `no_temp_check` | model 1 | `RecoveryNeverClobbersForeignBytes` | `S_save_crash` | recovery skips the P1 temp rule |
| `plain_create` | model 1 | `ExternalEditNeverSilentlyLost` | `S_save_crash` (editor on N) | N created by a plain rename |
| `temps_after_close` | model 1 | `CutTempNeverBlocks` | `S_save_crash` | temps removed after the close |
| `old_temp_rule` | model 1 | `CutTempNeverBlocks` | `S_recover_lock` (no link, no orphan) | any temp not pre or post stops the journal |
| `quit_says_undone` | model 1 | `OutcomeTruthful` | `S_save_quit` | the next start always says "was undone" |
| `gate_by_key` | model 1 | `RecordsMatchByLineage` | `S_forget` | records matched by registry key only |
| `quit_note_two_branches` | model 1 | `OutcomeTruthful` | `S_save_quit` | saved or undone only; the note deleted at once |
| `cancel_stops_all` | model 2 | `EveryJournalRecoveredOrBlocking` | `S_recover` | Cancel ends recover-all; later journals untried and unmarked |
| `crash_unmarked` | model 2 | `EveryJournalRecoveredOrBlocking` | `S_recover` | a crash during a journal does not mark it |
| `untried_unmarked` | model 2 | `EveryJournalRecoveredOrBlocking` | `S_recover_bound` | Unavailable leaves untried journals unmarked |
| `no_displaced_check` | model 2 | `ExternalEditNeverSilentlyLost` | `S_save_crash` | swaps do not compare what they displaced |
| `direct_open` | model 2 | `PartJournalNeverBlocks` | `S_save_power` | the journal written directly as `.open` |
| `no_recheck` | model 2 | `CommittedCombinationChecked` | `S_save_crash` | no fingerprint re-check after the load |
| `manifest_first` | model 2 | `NoManifestOverIncompleteFolder` | `S_create_empty` | the manifest written before the files |
| `no_refuse_nonempty` | model 2 | `NonEmptyTargetUntouched` | `S_create_nonempty` | a non-empty target is not refused |
| `copy_checks_blocking_only` | model 2 | `NoCopyOverOpenJournal` | `S_copy` | the copy looks only at blocking journals |
| `plain_file_write` | model 2 | `DroppedFileNeverReplaced` | `S_create_absent` | files written without `O_EXCL` |
| `no_readback` | model 2 | `ForeignOverwriteCaughtByReadBack` | `S_create_absent` | no read-back |
| `boot_waits_for_success` | model 2 | `EveryBootEnds` | `LS_boot` | the boot ends only when its open succeeds |
| `boot_no_actions` | model 2 | `EveryWaitHasAnExit` | `S_boot` | the boot's screen offers nothing |
| `cancel_ends_writing_job` | model 2 | `CancelReachIsTheOneRule` | `S_cancel` | another job's Cancel ends any writing job before its commit point |
| `no_quit_message_for_writing` | model 2 | `EngineCanAlwaysBeFreed` | `LS_free` | Cancel behind a job it cannot end shows no advice |
| `r4_only` | model 2 | `CancelReachIsTheOneRule` | `S_cancel` | another job's Cancel ends only an R4 |
| `advice_on_timer` | model 2 | `NoAdviceOnTimerForSlowWork` | `S_cancel` | the timer shows the advice behind a job the user cannot end |
| `listing_check` | model 2 | `OtherNamesDoNotStop` | `S_create_absent` | any new name in the target stops the job |
| `delete_message_always` | model 2 | `DeleteAdviceOnlyForOwnFiles` | `S_create_absent` | "You can delete it" always |
| `dispatch_by_arrival` | §13 | `WaitedForFirst` | `S_queue` | dispatch by arrival only |
| `switch_drops_confirmed_save` | §13 | `OutcomeTruthful` | `S_switch` | the quiesce drops queued confirmed saves |
| `recovery_while_open` | §13 | `OnlyOpenWorkspaceRunsJobs` | `S_switch` | the switch's recover-all may start before A closed |
| `spawn_before_reap` | §13 | `AtMostOneEngine` | `S_queue` | a new engine while the old one drains |
| `supersede_publishes_nothing` | §13 | `BuildChangePublishesRealLoad` | `S_r4_build` | a build-change supersede publishes nothing |
| `dispatch_while_running` | added | `AtMostOneJobRunning` | `S_queue` | dispatch while a job runs |
| `open_before_close` | added | `AtMostOneOpenWorkspace` | `S_switch` | the target opens before A closed |
| `gate_after_journal` | added | `CancelBeforeCommitLeavesDiskUnchanged` | `S_cancel_commit` | the commit point after the journal is `.open` |
| `unchanged_without_rehash` | added | `UnchangedOnlyAfterRehash` | `S_cancel_commit` | "unchanged" without hashing again |
| `block_without_cause` | added | `BlockedOnlyForCause` | `S_recover_lock` (no link, no orphan) | a normal displaced temp (pre beside post) counted as foreign |
| `installer_ignores_journal` | added | `InstallerNeverOverOpenJournal` | `S_quit` | the installer runs over a `.open` journal |
| `save_waits_r4_forever` | added | `EveryWaitedForJobEnds` | `LS_jobs` (two saves) | the save waits for the queued R4 instead of running it |
| `switch_waits_for_r4` | added | `EverySwitchEnds` | `LS_jobs` | A closes only once its owed R4 ran |
| `quit_waits_reap` | added | `EveryQuitEnds` | `LS_quit` | close waits for the reap |
| `cnc_auto_lift` | added | `R4GateLiftedOnlyByUser` | `S_r4_attempts` (three saves) | recover-all turns `could_not_check` back into an owed R4 |
| `r4_skipped_before_save` | added | `OwedR4BeforeNextSave` | `S_r4_outcomes` | the save skips step 1 and the gate's closed-record test |
