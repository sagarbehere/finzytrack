# EngineJobs.tla and the engine-jobs spec, both ways

- **Spec:** `design/specs/engine-jobs.md` at c87ce786: draft 5 (d8ca7823, then named `engine-scheduler.md`) with the model's findings F1-F4, O1 and N1-N3, the spec items of model review 1 and fidelity review 2 (31afbd82: H1, M4, M5, `OpeningHasItsJob`, "Finishing"), the model's I14 (e91bbcef) and fidelity review 3's L1, L9, L10 (c87ce786) folded in. §13 lists what the model must check. Built only from that text and the ADRs and invariants it cites for meaning. Fidelity review 4 (of the model at c6c2e5c8) found model gaps only; their fixes are in this model, with the spec unchanged (§5). **Simplification B** (owner, 2026-10-28: "Agreed with B, back always recovers first, and the read core next"; the spec's §5.1, §8.1, §9.1, §13-§15 and the ADR-0064 and ADR-0063 amendments of that date): the engine lock, and Back always recovers first; this model follows it, and §5 "Simplification B" lists what it removed and why.
- **Model:** `EngineJobs.tla` (one module; at 84c8b8c9 it had the folder's and the spec's old name, renamed in BL-0111). Configurations come from `gen_cfgs.py` (`cfg/*.cfg`, `configs.txt`); `run_all.sh` runs them (HOW_TO_RUN.md).
- **No fix switch.** F1-F4, O1, N1-N3 and the items of fidelity reviews 2-4 are rules of the spec now and of the model; each one's old rule is a mutant that must fail its property (§5; §13 "The model's former fix switch").
- **State:** on this model (after simplification B) the small tier ran to completion, all as expected (30 safety worlds, 5 of the 6 small liveness worlds, all 92 mutant configurations: 127 configurations), except `LS_jobs`, which takes longer than one agent tool call (642 s on an earlier model) and was not re-run by the agent: the owner's `--tier S` run covers it. The medium and large tiers have not been run on it (HOW_TO_RUN.md).
- `scripts/tests/test_tla_engine_jobs.py` keeps this file honest: every action in `Next` has a row in §2 and every row names an action; every property and mutant §13 names exists in the model and in `gen_cfgs.py`; every property has at least one mutant.

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
| restore temps (torn by a power loss until their data sync, §8.1 step 3) | `fs.rt`, written from the backup and synced in one step (`RS_Write`; H1); a restore temp is deleted without a check (§6.4), so its own power-loss outcomes are not tracked: what matters is what a swap puts in place. With the seeded bug `no_restore_temp_fsync` the swapped-in bytes may be torn after a power loss (`fs.ud`) |
| journal states `.part` `.open` `.closed` `.done` `.mismatch` `.resolved` `.kept` and the R4 attempt count | `jr[j].st` (plus `tpart`/`topen` for torn ones), `jr[j].pl` (a rename not yet synced keeps the old state possible), `jr[j].att`, `jr[j].cnc`; `jr[j].old`: the journal existed when the current boot or switch began (`MarkOld`; fidelity review 3, H1) |
| two or three journals left by crashed saves at boot, one on a disk that may be unreachable | `BootJ` ⊆ {b2 (W2: R, J), b3 (W3: K)}, `BootTemps` (cut, pre or post temps beside them), `BootTorn` (b3's journal torn: its file list cannot be read, world `S_recover_torn`); K's disk: `pst.unr`, `Unreach`; with a crashed save of W1 (s1) a third |
| the queue with waited-for and background jobs | `q` (job records with `wf`, `conf`, `req`, `own`) |
| one engine slot, the engine's process group (a member stuck in the kernel) | `eng` (`p` processes, `st` none/idle/busy/sig, `user`, `stuck`, `reason`) |
| the engine lock (an engine of an earlier session may still hold it at the next start) | `pst.orphan` (held), `pst.ostuck` (by a process stuck in the kernel); `BootOrphan` (held at the first boot); `EngLockFree` in `SlotFree` |
| the requester of each job (present or gone) | job field `req`; `UserCloseTab` |
| the engine build, which can change between a save and its R4 | `pst.build`, journal `build`; the installer in `QuitClose` |
| one `copy` of W2 (sources named by a blocking or in-session `.open` journal) | `UserCopy`, `CP_*` |
| one `create` into an absent, empty or non-empty target; a file appears under a name the job writes (a ledger file or the manifest `.finzytrack`), under another name, or over a written file; requester may go before or after the commit point | `CreateTarget`, `tgt` (names a, m = `.finzytrack`, o, x), `EditorDrop`, `EditorOverwrite`, `UserCloseTab`, `CR_*` |
| the published mirror and what produced it | `pst.mirror[w]` = fingerprint, `src` (overlay, rebuild, open, load_file), `build` |
| the update quiesce and the installer | `UserQuit("update")`, `QuitClose` |
| the boot, the switch, Reopen, the quit with its notes (one per job, §9.3), the next start | `Init`/`Relaunch`, `UserSwitch` (t = open workspace is Reopen), `UserQuit`, `UserQuitAnyway` (`pst.notes`), `NoteNotes`/`NoteGh` |
| §13 file-system assumptions | data durable after its fsync; an entry durable after its folder's sync, which makes everything before it there durable; an exchange or exclusive rename kept or dropped whole (`fs.cp`: the pairs a pending exchange leaves possible); everything else unsynced is kept or dropped on its own. `EdDurable = FALSE` (worlds `S_fs_weak*`): an outside save is not durable and does not sync our pending operations |

### 1.2 Actors (§13 "Actors")

| Actor | Actions |
|---|---|
| the user | `UserSave`, `UserRead` (preview and BQL), `UserCancelQueued`, `UserCancelRunning`, `UserCloseTab`, `UserSwitch` (and Reopen), `UserStay`, `UserQuit`, `UserQuitAnyway`, `UserRetry`, `UserKeep`, `UserResume`, `UserOpenAnother`, `UserBack`, `UserTryAgain`, `UserForgetReadd`, `UserCreate`, `UserCopy`, `Relaunch` |
| the app (sidecar) | `Dispatch`, `DispatchUnavail`, `SpawnStep`, `CloseA`, `QuitClose`, `Assess`, `AppBgSave`, `AppFocusCheck`, `AdviceTimer`; the outcome check inside `EngineCrash`; the quit note in `UserQuitAnyway` and `NoteStep` |
| the engine | every `S_*`, `RB_Check`, `RS_*`, `RC_*`, `R4_*`, `RD_*`, `OP_*`, `CP_*`, `CR_*`, `FreshEnd` |
| the OS | `EngineCrash`, `AppCrash`, `PowerLoss`, `Stuck`, `Hang`, `IOFail`, `Drain`, `WindowExpire` |
| an outside editor | `Editor` (any ledger file, durably, at any time, also while the app is dead), `EditorDrop`, `EditorOverwrite` |
| a path changer | `PathChange` (after a crash), `BootLink` |
| an engine of an earlier session holding the engine lock | `pst.orphan` (from `AppCrash`/`UserQuitAnyway` with a stuck or lingering engine, `QuitClose` with a stuck one, or `BootOrphan`), `OrphanEnd`, `OrphanEndStuck`; its own steps after the crash are not modelled (simplification B: §3, §5) |

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
| §5.1 (simplification B) | the engine lock: the sidecar waits for it before its first engine, so no engine of this session runs while an engine of an earlier session holds it; meanwhile jobs stay queued and the boot's own job can be cancelled | `EngLockFree` in `SlotFree` (used by `Dispatch` and `SpawnStep`); `SwitchJobBehind`; `AtMostOneEngine`; mutant `no_engine_lock` |
| §5.1 | an engine that ran user code is ended after its job; parse-only may serve the next | `Retired`, `Fresh` (reuse an idle engine with `user = FALSE`), `FreshEnd` |
| §5.2 | the reason recorded before the signal; our end not a stop | `SigEng` records `reason`, `endBy`, `ended`; `stops` counts only `EngineCrash` |
| §5.2 | a process that cannot be ended keeps the slot and its locks; the timer advice only for a signalled process, shown whether or not a job waits (N3) | `Stuck`, `eng.st = "sig"` holds `SlotFree` false; `AdviceTimer` |
| §5.1, §5.2, §15 (simplification B) | an engine of an earlier session (stuck, or lingering for a moment) holds the engine lock after its app died; the advice while the sidecar waits for it, whether or not a job waits; it goes when that engine ends | `OrphanAfter`, `pst.orphan`, `pst.ostuck`, `AdviceTimer`, `OrphanEnd`, `OrphanEndStuck`; mutant `orphan_no_advice` |
| §5.3 | crash, outcomes, bounded restart (Unavailable, Try again) | `EngineCrash`, `ses.stops`, `StopBound`, `WindowExpire`, `UserTryAgain`, `DispatchUnavail` |
| §5.4 | no time limits; "Still working…" changes only text | no timer ends a job; only `AdviceTimer` changes `ses.advice` |
| §6.1 step 1 | R4 first, if owed (re-read from disk); outcomes; attempts | `S_R4Check`, `R4_*` with `r4ctx = "save"`, `R4Failed` |
| §6.1 steps 3-6 | locks (no other holder: the engine lock); gate under locks; load; re-check | `S_Locks`, `S_Gate`, `S_Load`, `S_Recheck` |
| §6.1 step 8 | backups synced; `.part` synced, renamed `.open`, dir synced; per file temp, sync, swap or exclusive create, displaced check, dir sync; read-back; temps removed; close and sync | `S_Backup`…`S_CloseSync` (order in `NextS`) |
| §6.1 steps 9-11 | "saved" after the durable close; publish if fingerprint = post and no `.open`; release, retire, queue r4 | `S_Saved`, `S_Publish`, `S_Release` |
| §6.3 | the path guard | `PathMoved`, `FileBad`, `RS_Swap` |
| §6.4 | rollback after any failure past the first replace; displaced check on every swap; P1 temp rule; restore temps deleted without check | `IOFail`, `S_Compare`, `S_ReadBack`, `StartRB`, `RS_*`, `TempBad` |
| §6.5 | records matched by registry key or lineage; the gate reads files, never memory | `Match`, `GateClosed`/`GateMism` (files) |
| §6.6 | outcome after an engine death, from disk | `DiskOutcome` in `EngineCrash` |
| §6.7 | R4 steps 1-4, build change publishes load_file, nondeterministic, mismatch, attempts → could_not_check, Resume saving | `R4_Start`, `R4_Load`, `R4_Check`, `R4_Confirm`, `R4Failed`, `UserResume` |
| §7 | loads re-check the fingerprint (retry, then LEDGER_CHANGING) and refuse over `.open` | `RD_Load`, `RD_Check`, `OP_Load`, `OP_Check` |
| §8.1 | recover-all: `.part` deleted; per journal lock try, check, restore root first, temps deleted, sync, then `.resolved`; Cancel, crash and Unavailable mark blocking; every journal recovered or blocking | `RC_Init`, `RC_Next`, `RC_Lock`, `RC_Check`, `RS_*`, `UserCancelRunning`, `EngineCrash`, `SpawnStep`, `DispatchUnavail` |
| §8.1 afterwards | blocking journal blocks opens and copies using its files; Keep `durable_sync`s every file and folder the journal names, then renames it `.kept` (N1) | `Blocked`, `Assess`, `CP_Check`, `UserKeep` |
| §8.2 | create/copy: non-empty refused; copy refused over any `.open` journal; exclusive writes; only a collision with a written name stops; read-back; commit point; manifest last; delete advice from a listing | `CR_*`, `CP_*`, `CrStopGh` |
| §9.1 | quiesce, Stay, A closes, recover_all, target opens, Back | `Quiesce`, `UserStay`, `CloseA`, `Assess`, `OP_*`, `UserBack` |
| §9.2 | boot: recover_all then open; ends on success, failure or block; Retry, Open another, Keep | `Init`, `Relaunch`, `Assess`, `UserRetry`, `UserOpenAnother`, `UserKeep` |
| §9.3 | quit: quiesce, wait for saves, Quit anyway with the note; next start's three notices; close does not wait for the reap | `UserQuit`, `QuitClose`, `UserQuitAnyway`, `NoteStep` |
| §9.3, ADR-0012 | installer never over a `.open` journal | `QuitClose` (update branch) |
| §10 | what survives at each step | the power-loss model in `PowerLoss`, checked by the properties of §1.4 |
| §11 | "unchanged" only after a re-hash; messages | `Rehash`, `gh.told` |
| §6.6 (F1) | the journal directory synced before an outcome is told from it; saved without the publish queues a rebuild | `EngineCrash` (save branch) |
| §8.1, §9.3 (F1) | recover-all syncs the journal directories it lists; the quit note answered only from durable records | `RC_Init`, `SyncCL` in `RecoverEndEffects`, `NoteGh` |
| §6.4 (F2) | the rollback first runs recovery's step-2 checks, the temp rule included | `StartRB` → `RB_Check` |
| §6.4 (F3) | a swap-back (save, rollback, recovery) is synced at once | `S_Compare`, `RS_Compare` |
| §3 (F4) | a switch or boot showing its failed or blocked screen has finished: other jobs may start | `SwitchExclusive` |
| §6.7 step 4 (O1) | after the confirm, the fingerprint once more: moved → superseded | `R4_Confirm` |
| §8.2 step 7 | a `.finzytrack` that appeared before the manifest's exclusive rename: ours removed, nothing registered, the foreign one left | `CR_ManifestRename` |
| §9.3 | one quit note per job (`local/quit-notes/<job>.json`); a copy's note too | `UserQuitAnyway`, `NoteNotes`, `NoteGh` |
| §6.5 | a workspace's records: registry key or lineage id | `Match` |
| §6.5, §6.7 (M5) | every rename of an R4 record (`.done`, `.mismatch`, `could_not_check`) is synced before its outcome is acted on or shown; after an engine death the sidecar syncs before it acts (I16); Resume saving's rename and sync are one step | `R4Rename` → `R4_Sync` → `R4Finish`; `R4Failed` (`R4SyncJ`); `UserResume` |
| §6.4, §8.1 step 3 (H1) | a restore temp is written from the backup and its data synced before the swap | `RS_Write` (one step), `RS_Swap` |
| §6.4 last bullet (L6) | a full disk before the journal is durable: not a rollback, nothing replaced, "not saved" | `IOFail` at `PreJournalPcs` |
| §9.1 step 3, §9.2 (M4) | the switch's or the boot's own queued job (`recover_all`, the open), waiting behind other work, is the user's to cancel: "Switch cancelled." / "Opening cancelled." with Retry and Back or Open another workspace; the one rule's answer for the work ahead | `SwitchJobBehind`, `CancelableQ`, `UserCancelQueued`, `CancelledSes` (screen `cancelled`), `UserRetry` |
| §13 `OpeningHasItsJob` | in the recover or opening stage, the switch's or boot's own job is queued or running; another job's Cancel that ends an open applies the open's failed screen (M1) | `OpeningHasItsJob`; `UserCancelQueued` (then-branch) |
| INV-32, §8.1, §9.1 step 5, §9.2 (review 3, H1) | the journals a boot or a switch must recover are the ones on disk when it begins: marked then (`MarkOld`), not by recover-all, so a boot or Retry that skipped recover-all would be caught | `Init` (`BootJRec`), `Relaunch`, `CloseA`, `UserSwitch` (from no workspace), `UserRetry`, `UserOpenAnother` |
| §6.5 (review 3, M1) | an R4 step ended by a Cancel or the quiesce between its rename and the sync, or by a crash: the record synced before anything acts on it (I16); a gate refusal for a `.mismatch` shows the pause | `SyncR4End` in `CancelEffects`, `UserCancelQueued`, `Quiesce`; `R4SyncJ` in `EngineCrash`; `S_Gate` (`gh.paused`) |
| §10, §8.2 (review 3, M2) | an engine death past the manifest's rename: the job registers the complete workspace (from disk, the parent synced) | `EngineCrash` (create branch) |
| §9.1 step 2 (review 3, M3, L3) | the quiesce ends only a read, an R4 or an unconfirmed save before its commit point, also between two engines | `QuiesceEndsRunning`; `CancelReachIsTheOneRule` |
| §8.1 "Afterwards", §9.3 (c87ce786, L1) | the quit notes are answered at the end of every recover-all (the boot's, a Retry's, a switch's; also one refused as Unavailable) | `RecoverEndEffects`, `DispatchUnavail` |
| §9.3 step 2 (c87ce786, L9) | a quit keeps queued and running creates and copies, with or without an open workspace; they count as "Finishing" | `QuiesceKeep`, `Finishing` |
| §6.4 (c87ce786, L10) | a failure after the durable `.open` and before the first replace follows the rollback: "not saved" | `IOFail` (any `WritePcs`) |
| §8.1 (M2) | recover-all never ends without its listing (with the directory sync); a Cancel before the listing marks nothing (I14); the Unavailable paths list after the sync too | `UserCancelRunning`, `SpawnStep`, `DispatchUnavail` |
| §5.1, §8.1 step 1, §10, §13 Actors (simplification B; it replaced review 4's H1 row) | an engine that outlives its app finishes or dies before the new session's first ledger work, so its remaining steps are a cut at a later step, which the cut of the save in the session already reaches; recovery's lock try always succeeds (no other holder); the journal it tries is the one it listed | `OrphanAfter` keeps only the engine lock; `RC_Lock` (no held-lock branch), `RC_Check` (no I19 branch); see §3 and §5 |
| §9.1 step 6, ADR-0063 decision 1 step 6 and its amendment of 2026-10-28, §14, INV-32 (review 4, M1; simplification B) | Back opens the previous workspace "by the same path": Back is Retry with A as the target, recover-all first, then the open, after a cancelled, a failed and a blocked switch alike; a recover-all queued clears the reasons an earlier one gave, so only this boot's or switch's recover-all can make a journal blocking | `UserBack`, `MarkOld`, `UserRetry`; `BlockedScreenHasReason`, `KeepOnlyJudged`, `StrictRecovered`; mutant `back_skips_recovery` (Back only assesses) |
| §2, §9.1 step 3, §9.2 (review 4, M2) | the assessment runs outside the queue: it waits only for the switch's or boot's own job, so behind a create or copy the open is queued and its Cancel applies | `Assess`; `AssessNeverWaits` (was `AssessWaitHasCancel`; targeted review 5, L-4); mutant `assess_waits_for_work` (the old guard) |
| §9.1 step 1 (review 4, L1) | with a workspace open, the target is assessed read-only first; one whose files cannot be read (I10) shows its screen with Stay and nothing changes | `UserSwitch` (no switch starts) |
| §9.1 step 2, §9.3 step 2 (c87ce786 L9; review 4, L2) | a quit or switch never drops a queued create or copy | `QuiesceKeep`, `DroppedTold` (`gh.ccDrop`), `TruthInv`; mutant `rv4_quit_drops_create` |

### 1.4 Properties (§13; names as in the spec)

"Kind": INV = state invariant; ACT = action property `[][A]_vars`; LIVE = liveness under `FairSpec` (or `FairUserSpec`). Every small safety configuration checks every INV and ACT property.

| §13 property | Model definition (kind) | How it is stated | Mutants that must break it |
|---|---|---|---|
| `AtMostOneEngine` | INV | `eng.p <= 1`, and no engine of this session while an engine of an earlier session holds the engine lock (`pst.orphan => eng.p = 0`; simplification B) | `spawn_before_reap`, `no_engine_lock` |
| `AtMostOneJobRunning` | INV | `Cardinality(runs) <= 1` | `dispatch_while_running` |
| `AtMostOneOpenWorkspace` | INV | `Cardinality(ses.opened) <= 1` | `open_before_close` |
| `OnlyOpenWorkspaceRunsJobs` | INV | a running workspace job's workspace is open; `recover_all` runs only with none open | `recovery_while_open` |
| `WaitedForFirst` | ACT | a job that starts is the oldest eligible waited-for job, or, with none, the oldest eligible job | `dispatch_by_arrival`, `newest_first` |
| `NoOwnEndAfterCommitPoint` | INV | our `cancel` never ends a writing job past its commit point (Quit anyway leaves no session) | `cancel_after_commit` |
| `CancelReachIsTheOneRule` | INV | another job's Cancel ended only an R4, a background save before its commit point, or a read/open whose requester is gone; the quit advice answers a Cancel only for a job the rule does not let it end ; the quiesce ends only a read, an R4 or an unconfirmed save before its commit point ("closing is not a cancel", review 3 M3) | `cancel_ends_writing_job`, `r4_only`, `rv_quiesce_ends_confirmed` (review 3) |
| `CancelBeforeCommitLeavesDiskUnchanged` | INV | a save ended by any Cancel before its commit point (its own, another job's, the quiesce's) has no journal and no post bytes on disk | `gate_after_journal` |
| `OutcomeTruthful` | INV + ACT | every told outcome (saved, not saved, unfinished, undone, the quit note's three notices, created / not created) matches the records and files; the note is deleted only once the record is saved, undone or kept; a confirmed save is never answered "not open"; a quit or switch never drops a queued create or copy (review 4, L2: `gh.ccDrop`) | `quit_says_undone`, `quit_note_two_branches`, `switch_drops_confirmed_save`, `recover_cancel_before_listing` (M2), `rc_unsynced` (M2, the reviewer's), `create_crash_msg_from_memory` (review 3 M2: the in-session create outcome is also stated: "not created" never over a manifest in place, "created" only with one), `rv4_quit_drops_create` (review 4, L2), `rv4_outcome_open_notsaved` (review 4) |
| `SavedSurvivesPowerLoss` | INV | told "saved" ⇒ the record survives a power loss as closed or later, and its files (not named by another open journal) durably hold post | `report_before_close_sync`, `outcome_unsynced` (F1), `keep_no_sync` (N1) |
| `UnchangedOnlyAfterRehash` | ACT | "unchanged" is told only when the targets equal their pre bytes in that step | `unchanged_without_rehash` |
| `BackupDurableBeforeReplace` | INV | post bytes on disk before the close ⇒ the backup is durable | `no_backup_sync` |
| `JournalDurableBeforeFirstReplace` | INV | post bytes on disk before the close ⇒ the journal is durably `.open` | `no_journal_sync` |
| `PartJournalNeverBlocks` | INV | only a complete `.open` journal is ever marked blocking (a journal of ours cut while written never blocks; a boot journal torn by something else blocks, §8.1, `BootTorn`) | `direct_open` |
| `CommittedCombinationChecked` | INV | a save past its commit point saw no outside edit to the include it does not write during its load | `no_recheck` |
| `ExternalEditNeverSilentlyLost` | INV | the editor's latest bytes of a file are in the file, a temp beside it or a restore temp, or were the plan's base, or fall under §15's accepted loss | `no_displaced_check`, `plain_create`, `rollback_skips_temp_rule` (F2), `swap_back_unsynced` (F3) |
| `NoTornLedgerAfterRecovery` | INV + ACT | a ledger file is torn only while a journal naming it is `.open`; a journal becomes `.resolved` only when its files durably hold something other than its post bytes | `resolve_before_restores_synced`, `no_temp_fsync`, `no_restore_temp_fsync` (H1) |
| `RecoveryNeverClobbersForeignBytes` | INV | as `ExternalEditNeverSilentlyLost`, for edits made while a journal naming the file is `.open` | `no_temp_check` |
| `RecoveryWritesOnlyRecordedPaths` | INV | recovery never writes through a replaced folder (its restore swap does write through the link when the guard is missing) | `no_path_guard`, `fb_no_guard` |
| `CutTempNeverBlocks` | ACT + INV | a journal is never stopped when its only fault is a cut temp beside a file that does not hold post; no temp of a save outlives its journal (I4) | `old_temp_rule`, `temps_after_close` |
| `EveryJournalRecoveredOrBlocking` | INV | while an open runs or a workspace is open, every `.open` journal that existed when this boot or switch began (`old`) is blocking, by a reason this boot's or switch's recover-all gave (the reasons are cleared when it is queued, `MarkOld`; review 4, M1) | `cancel_stops_all`, `crash_unmarked`, `untried_unmarked`, `rv_boot_skips_recovery`, `rv_retry_skips_recovery` (review 3, H1) |
| `NoOpenOverUnresolvedJournal` | INV | no workspace is open (or opening) whose files a pre-existing `.open` journal names; skipping recover-all does not break it (the open's own block test refuses), so review 3's two mutants are checked against `EveryJournalRecoveredOrBlocking` | `recover_target_only` |
| `NoCopyOverOpenJournal` | INV | a copy past its check has no `.open` journal naming a source | `copy_checks_blocking_only` |
| `BlockedOnlyForCause` | ACT | a journal becomes blocking only with its cause present: foreign bytes, moved or unreachable path, bad backup, foreign temp beside post, unreadable, Cancel, crash or I/O error, Unavailable | `block_without_cause` |
| `NoSaveWhileR4Owed` | INV | a save that writes has no other closed or mismatch record of W1 | `gate_in_memory` |
| `RecordsMatchByLineage` | INV | as above, for records under W1's old registry key (forget and re-add) | `gate_by_key` |
| `R4GateLiftedOnlyByUser` | ACT + INV | a `.mismatch` record changes only with a Resume saving, except that a power loss may drop a pause not yet synced and never acted on or shown; a pause acted on or shown (`gh.paused`: saving paused, the backup shown) is durable (M5) | `cnc_auto_lift`, `r4_rename_unsynced` (M5), `rv_r4_crash_nosync` (review 3), `r4_cancel_nosync` (review 3 M1; a gate refusal for a `.mismatch` also counts as shown) |
| `BuildChangePublishesRealLoad` | ACT | a record that becomes `.done {superseded_build}` publishes the new build's `load_file` result | `supersede_publishes_nothing` |
| `NoManifestOverIncompleteFolder` | INV | a manifest (also one that may survive a power loss) only over the job's read-back, checked files | `manifest_first`, `rv_create_no_sync` (review 3) |
| `NonEmptyTargetUntouched` | INV | nothing written into a non-empty target | `no_refuse_nonempty` |
| `DroppedFileNeverReplaced` | INV | a file dropped under a name the job writes (a ledger file or `.finzytrack`) stays the dropper's | `plain_file_write`, `manifest_overwrite` |
| `OtherNamesDoNotStop` | INV | a collision stop (also the manifest's) happens only with a foreign file under that written name | `listing_check` |
| `ForeignOverwriteCaughtByReadBack` | INV | an overwrite before the read-back never reaches the load check or the manifest | `no_readback` |
| `DeleteAdviceOnlyForOwnFiles` | ACT | "You can delete it" only when the folder holds the job's files alone | `delete_message_always` |
| `InstallerNeverOverOpenJournal` | ACT | the build changes only with no `.open` journal | `installer_ignores_journal` |
| `EveryBootEnds` | LIVE | the boot ends; a queued create then leaves the queue | `boot_waits_for_success`, `screen_holds_queue` (F4) |
| `EveryWaitedForJobEnds` | LIVE | ∀ job id: waited-for queued or running ~> neither | `save_waits_r4_forever` |
| `EverySwitchEnds` | LIVE | a switch reaches the target open or its failed or blocked screen, or ends by Stay or Back | `switch_waits_for_r4` |
| `EveryQuitEndsOrOffersQuitAnyway` | LIVE + ACT | a quit ends, or reaches "Finishing your last change…" with Quit anyway enabled; "Finishing" is §13's: any running save, create or copy, background saves included, or a queued confirmed one (M3); Quit anyway always ends the app | `quit_waits_reap`; `finishing_confirmed_only` (M3: the reading before M3, a reachability check) |
| `OwedR4BeforeNextSave` | ACT | a record becomes closed only with no other closed or mismatch record of W1 | `r4_skipped_before_save` |
| `EngineCanAlwaysBeFreed` | LIVE (`FairUserSpec`) | as §13 states it: `(a waited-for job is queued) ~> (the engine slot is free, or the quit advice is shown, or the app has quit)`, under weak fairness of the waiting user's Cancel, a requester's Cancel of its own job and Quit anyway once the advice is shown; the slot is not free while an engine of an earlier session holds the engine lock (simplification B; world `LS_free_orphan`); also `[]QuitAnywayAlwaysEnabled` | `no_quit_message_for_writing`, `advice_only_to_waiting` (N3), `orphan_no_advice` (simplification B) |
| `NoAdviceOnTimerForSlowWork` | INV | timer advice only while a signalled process holds the slot, or an engine of an earlier session holds the engine lock | `advice_on_timer` |
| `EveryWaitHasAnExit` | INV with `ENABLED` | in each wait, the exit action itself is enabled (`ENABLED UserCancelQueued`, `UserCancelRunning`, `UserStay`, `UserRetry`, `UserBack`, `UserOpenAnother`, `UserKeep`, `UserQuitAnyway`); a switch's or boot's own job queued behind other work can be cancelled (M4, `SwitchJobBehind`); none of these actions has a counter or bound in its guard in the safety worlds. The Quit-anyway clauses hold because Quit anyway is always enabled (I11); `quit_anyway_only_while_quitting` shows they catch a narrower guard | `boot_no_actions`, `stay_disabled`, `retry_disabled`, `switch_job_not_cancelable` (M4), `quit_anyway_only_while_quitting`, `rv4_no_cancel_recover` (review 4) |
| `OpeningHasItsJob` | INV | in a switch's or the boot's recover or opening stage, its `recover_all` or its target's `open` is queued or running (a stall check) | `cancel_open_keeps_opening` (M1), `rv_cancel_keeps_stage` (review 3) |
| `AppScopedWaitsForRecovery` | ACT | as §13 states it (N2): no create or copy *starts* while recover-all runs, or between a switch's close and its target's open or screen (boot included); one started earlier runs on (`AppScopedStarts`) | `app_scoped_during_recovery`; `appscoped_runs` (N2: the old wording, a reachability check, §5) |

Model-only properties (not named in §13; each with a mutant):

| Property | Kind | What | Mutant |
|---|---|---|---|
| `NoFalseMismatch` | ACT | O1: a record turns `.mismatch` (not `could_not_check`) only with the disk at its post fingerprint | `confirm_no_recheck` |
| `NotesAnsweredByRecovery` | INV | c87ce786 L1: no workspace is open while a quit note that says saved or undone still waits (review 3's `NotesAnswered`) | `notes_only_at_boot` |
| `StrictRecovered` | INV | INV-32, §8.1 (review 3's check, permanent since review 4): while an open runs or a workspace is open, every boot journal (b2, b3) still `.open` is blocking; boot journals exist before every boot and switch, so it needs no `old` flag and cross-checks how the model sets it | `back_skips_recovery` (on `S_recover` with b3 and a switch) |
| `BlockedScreenHasReason` | INV | §8.1 "Afterwards", §11 (review 4, M1): on the `RECOVERY_BLOCKED` screen every journal from before this boot or switch (`old`) that blocks the target has the reason this boot's or switch's recover-all gave | `back_skips_recovery` (on `S_switch` with a crash) |
| `KeepOnlyJudged` | ACT | §8.1 "Afterwards" (review 4, M1): "Keep the files as they are now" is applied only to a journal (from before this boot or switch) that its recover-all judged | `back_skips_recovery` (on `S_switch` with a crash) |
| `AssessNeverWaits` | INV with `ENABLED` | §2, §9.1 step 3, §9.2 (review 4, M2): a switch or boot in its assess stage while other work runs assesses (outside the queue). Was `AssessWaitHasCancel`, whose second disjunct (its own queued job can be cancelled) was never needed: no job of the switch is queued or running in that stage (targeted review 5, L-4); the small tier checks that claim, since the invariant would fail otherwise | `assess_waits_for_work` (on `S_switch_hung_create` with a crash and an unreachable disk) |
| `NoEngineStall` | INV with `ENABLED` | a running engine that is neither hung nor stuck always has a step; it makes `CHECK_DEADLOCK FALSE` safe (the app may end with nothing left to do, which TLC would call a deadlock) | `read_refusal_stuck` |
| `QuitAnywayAlwaysEnabled` | INV with `ENABLED` | while the app is alive, Quit anyway is enabled | (via `EngineCanAlwaysBeFreed`) |
| `JobIdsBounded` | INV | the configuration's job-id bound is large enough | — |

## 2. Model → spec

### 2.1 Actions

| Action | Spec text it comes from |
|---|---|
| `UserSave` | §6.1 (a confirmed plan, planned on the published mirror the preview read) |
| `UserRead` | §2 `preview`, `bql` (one transient load, waited-for) |
| `UserCreate` | §2, §8.2 `create` |
| `UserCopy` | §2, §8.2 `copy` of W2 |
| `UserCancelQueued` | §4 table rows 1-2, the one rule; §9.1 step 3 and §9.2 (the switch's or boot's own queued job, M4) |
| `UserCancelRunning` | §4 table rows 3-5; §8.1 Cancel per journal; before the listing, I14 |
| `UserCloseTab` | §4 "a client that goes away"; §13 requester gone |
| `UserSwitch` | §9.1 (Reopen when the target is the open workspace; from no workspace, §9.2's "Open another"); §9.1 step 1: with a workspace open, a target the read-only assessment cannot read starts no switch (review 4, L1) |
| `UserStay` | §9.1 step 3 |
| `UserRetry` | §8.1 "Retry on the blocked screen runs recover_all again"; §9.1 steps 3 and 6; §9.2 (after a cancelled switch or boot, recover-all runs again, I15); after a failed open, the target is assessed again |
| `UserBack` | §9.1 step 6 "Back to A ... by the same path", ADR-0063 decision 1 step 6 and its amendment of 2026-10-28, §14 (recover-all first on every screen that offers Back; simplification B) |
| `UserOpenAnother` | §9.2 "Open another workspace" |
| `UserKeep` | §8.1 afterwards, "Keep the files as they are now" (syncs what it keeps first, N1) |
| `UserResume` | §6.5, §6.7 Resume saving |
| `UserForgetReadd` | §6.5, §13 "W1 can be forgotten and added again under a new key" |
| `UserTryAgain` | §5.3 Try again |
| `UserQuit` | §9.3 steps 1-2 (and the update quiesce, §13) |
| `UserQuitAnyway` | §9.3 step 3, §4 last bullet |
| `Relaunch` | §9.2, the next start |
| `AppBgSave` | §2 background save (prices) |
| `AppFocusCheck` | §2 `rebuild` after the focus check |
| `Dispatch` | §3 ordering rule, §5.1 spawn (after the engine lock is free) |
| `DispatchUnavail` | §5.3 Unavailable; §8.1 not_tried |
| `SpawnStep` | §5.1 fresh engine after user code; §6.1 step 1, §6.7 step 4, §8.1 next journal in a new engine |
| `CloseA` | §9.1 step 4 |
| `QuitClose` | §9.3 step 4; the installer (ADR-0012) |
| `Assess` | §9.1 step 6, §9.2; §2 (outside the queue: it waits only for the switch's or boot's own job; review 4, M2) |
| `AdviceTimer` | §5.2 last bullet (whether or not a job waits, N3), §5.4 display delay; §5.1 (while an engine of an earlier session holds the engine lock) |
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
| `RS_Write`, `RS_Swap`, `RS_Compare` | §6.4 rollback, §8.1 step 3 restore by temp (written from the backup, its data synced, H1) and swap, displaced check, path guard |
| `RS_Temps` | §8.1 step 3 temps deleted whatever they hold; §15 accepted loss |
| `RS_Sync` | §8.1 step 3 "every changed directory is synced" |
| `RS_Resolve` | §8.1 step 4, §6.4 `.resolved {not_saved}` |
| `RC_Init` | §8.1 "A `.part` journal is deleted"; every journal now existing is marked `old`; the directories synced (F1) |
| `RC_Next` | §8.1 each `.open` journal on its own; unreadable journal |
| `RC_Lock` | §8.1 step 1 (the try always succeeds: no other holder, §5.1) |
| `RC_Check` | §8.1 step 2 |
| `R4_Pick`, `R4_Start`, `R4_Load`, `R4_Check`, `R4_Confirm` | §6.7 steps 1-4 |
| `R4_Sync` | §6.5 and §6.7 (M5): the record's rename synced before the outcome is acted on or shown |
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
| `IOFail` | §6.4 any failure after the journal is durable (before the first replace too, c87ce786 L10), and a full disk before it is durable (L6); §8.1 step 3 I/O error |
| `Drain` | §5.2 drain and reap |
| `OrphanEnd`, `OrphanEndStuck` | §5.1, §5.2, §15: the engine of an earlier session ends and frees the engine lock (a stuck one only when the disk answers; not fair) |
| `WindowExpire` | §5.3 stops leave the 300 s window |
| `Editor`, `EditorDrop`, `EditorOverwrite` | §13 outside editor |
| `PathChange` | §13 path changer, §6.3 |
| `Unreach` | §13 a disk that may be unreachable |

### 2.2 Variables

| Variable | Spec |
|---|---|
| `fs` | ledger files, temps, backups, restore temps and their power-loss outcomes (§6.1 step 8, §6.2, §10); `ud` only for the seeded bug `no_restore_temp_fsync` |
| `jr` | records in `local/*/journal/` (§6.5); `why` = the blocking reason of §8.1; `old` = it existed when the last recover-all started |
| `pst` | durable state: engine build, W1's registry key, the quit notes `local/quit-notes/<job>.json`, the link, K's disk, the engine lock held by an engine of an earlier session (`orphan`, `ostuck`), the published mirrors, the last opened workspace |
| `tgt` | the create's target folder (§8.2) |
| `ses` | the session: app phase (boot, run, switch, quit, update, dead), switch stage, open workspace, screen, how the app last ended (`endedBy`), stops (§5.3), advice (§5.2, §11), our ledger-set locks (`locks`: recorded as §6.1 step 3 and §8.1 step 1 take and release them; since simplification B nothing reads them, as no other process can hold one, §3), the scheduler's in-memory cache of closed records (§6.5 "a cache") |
| `q`, `runs`, `ctl` | the queue (§3), the running job, its progress (`r4res`: an R4 outcome waiting for its rename's sync) |
| `eng` | the engine process and its group (§5) |
| `n` | bounds on the environment and the user (model only) |
| `gh` | ghost: what the user was told (§11), and history the properties need (editor's last bytes, accepted losses, create outcomes, `paused`: the R4 pauses acted on or shown, `ccDrop`: a create or copy a quiesce dropped) |

## 3. What the model abstracts away

| Abstraction | Why it is safe here, or where it is covered instead |
|---|---|
| Beancount semantics, digests beyond equal or not | §13 "Not modelled"; R4's outcome is a nondeterministic choice in `R4Outs`; the corpus and Hypothesis model (ARCHITECTURE §8) |
| OS rename atomicity beyond the stated primitives | §13; an exchange or exclusive rename is kept or dropped whole by a power loss; nothing else is assumed about the order of unsynced operations (§13 "File-system assumptions"; the `S_fs_weak*` worlds drop the one stronger assumption the other worlds make, next row) |
| A durable outside save syncs its folder (worlds with `EdDurable = TRUE`) | §13's own assumption: a sync makes everything done before it in that folder durable, so our pending operations there persist with it. With `EdDurable = FALSE` an outside save is not durable and does not sync, and a power loss may drop it (that program's loss, excused by `PowerLoss` only while the save was not durable) |
| Our own folder syncs make an outside program's earlier unsynced save durable (§13) | modelled, also in the `EdDurable = FALSE` worlds: our directory syncs (`S_DirSync`, `RS_Sync`, `UserKeep`, the swap-back) make the file's current bytes durable, an outside save in them included, as §13 says a sync does (review 3 L7 corrected this row) |
| Restore temps under a power loss | a restore temp's own content is not tracked by `PowerLoss`; recovery deletes restore temps without a check either way, and the loss this allows is §15's accepted limit (recorded in `gh.acc`). Its data is synced before its swap (H1), so the swapped-in bytes are never torn; the seeded bug shows the opposite |
| One folder per workspace; journals, backups and the note durable when their step says so | the order of their syncs is modelled per step; rename+sync of `.resolved`, `.kept`, Resume saving's `.done`, the sidecar's `could_not_check` and the note are one step each, as the spec states them durable; the R4 job's renames are a rename step and a sync step (M5) |
| Legacy-lock detection (§6.1 step 2) | not in §13's world; ADR-0062's detection tests (§14 "Legacy lock") |
| `max_queued`, `ENGINE_QUEUE_FULL` (named `BUSY_HEAVY_LOAD` at d8ca7823) | bounded by the model's counters instead; §14 queue tests |
| Job ids | canonical: a new job takes the lowest id not in use (`NewId`), so equal queues are equal states |
| A next start after a quit | only in worlds with `RestartAfterQuit` (quit-note, update and large worlds): a bound on what follows the exit, never on the exit itself, so that Quit anyway can be enabled in every state without each one starting a second session |
| Backoff delays, the 300 s window, the display delay | untimed: `WindowExpire` and `AdviceTimer` are fair environment steps; no property depends on durations |
| Protocol violations (§5.3 rows) | not in §13's world; §14 engine tests; a violation past the commit point behaves like a crash at the exit, which `EngineCrash` covers |
| Phase texts, "Still working…" | only the quit advice is state (`ses.advice`); §14 UI tests |
| Decision log (§12) | §14 replay tests |
| Fingerprint parts other than file contents (globs, documents listings, plugin sources) | a fingerprint is the versions of the ledger set; §14 R4 and gate tests cover plugin and glob changes |
| Copy's own target folder | the copy's checks are modelled (`CP_*`); the target mechanics are the create's (`CR_*`), which §8.2 shares |
| One user container, desktop | §3 per `UserContainer`; hosted mode repeats it per container |
| W1 is the only workspace that saves; s1-s3 its saves | the boot journals b2, b3 give W2 and W3 their unfinished saves; the save path is the same code |
| The single-instance lock, a leftover sidecar, the shell's "Quit now" | not in §13's world; ADR-0062 tests, §15 |
| The drain of a crashed engine's group (review 3 L4) | an engine crash frees the slot at once (`CrashEng`): a member of the crashed group that lingers is not modelled; a stuck member is (`Stuck` before the crash, then an orphan only after an app crash). §5.1's "a new one only after the reap" is checked for our own ends (`Drain`) |
| The manifest's temp and its sync, directories a create makes (review 3 L8) | `CR_ManifestTemp` is one step (the temp written and synced); `CR_ManifestRename` is the exclusive rename, `CR_ManifestSync` the parent's sync; the directories a create makes are not separate names: the files' sync (`CR_SyncA`) stands for the files and their folders |
| Keep after a path change resolves the folder again | `UserKeep` clears `pst.lnk` (the next open resolves canonical paths afresh) |
| Two editor versions per file | each outside write differs from the file's bytes before it; more versions add no new orderings for these properties |
| The steps an engine takes after its app died (simplification B; it replaced review 4's three orphan rows) | not modelled: the model keeps only its engine lock (`pst.orphan`, `pst.ostuck`). The engine lock puts all of them before the new session's first ledger work (`AtMostOneEngine` checks that no engine of this session runs meanwhile), so the files and records they leave are those of the same save cut at a later step, which `AppCrash` already reaches at every step of the live save, with the outside editor and a power loss interleaved there as they could be with the orphan's steps. Nothing of the new session reads or writes a ledger file or a record before its first engine runs: the assessment and the quit notes come after recover-all, and every job needs the engine. A process stuck in the kernel takes no step at all |
| Which engines linger (simplification B) | after an app crash or Quit anyway, any live engine may linger (bounded by `MaxOrphan`); a stuck one always holds the lock. After a graceful quit only a stuck one is kept (`QuitClose`): an idle or retiring engine that `close()` signalled may linger for a moment too, which is the same wait as after Quit anyway |
| §8.1 step 1's held-lock branch (simplification B) | not reachable: with the engine lock no other process of ours holds a ledger-set lock, and an outside program takes none. The spec keeps it as a second line (reason `failed`) should the engine lock ever be bypassed; `ses.locks` is still recorded but nothing reads it |
| R4 for W1 only | the model's R4 serves W1, the one workspace that saves |
| §9.1 step 1: a target that "needs the user" | the read-only assessment's only such target here is one whose files cannot be read (I10); other needs (a missing folder, a migration) are not in §13's world |

## 4. Interpretations where the spec is still silent

Readings of draft 5 that 1cf1fb4c made spec text are gone from this list (I5, I6, I8 and I9 of 84c8b8c9); I12 (the "runs" wording) went with N2 at 4fb67b23; I6' (what "Finishing" covers) went with §13's text at 31afbd82 (M3); I14 (a Cancel of recover-all before its listing ends the switch or boot on its cancelled screen) is spec text since e91bbcef and stays here only as a pointer.

- **I1** A Cancel of `recover_all` marks the journal it is on as blocking (`cancelled`) and goes on in a new engine; after the listing and before a journal is in hand, "the journal it is on" is the next one it would try, and with none left the Cancel ends recover-all (its listing already synced the records); while recover-all only waits for a new engine it holds none and there is nothing to cancel (a stuck slot then gets the timer advice). Before the listing, see I14.
- **I2** A save that ends during a quiesce (switch, quit, update) does not queue its R4 or rebuild: §9.1 step 2 refuses new jobs naming A, and the closed record stays for the next open.
- **I3** A Cancel by the waiting user also applies when the running job sits between two engines (`SpawnPcs`); ending a job that holds no engine signals nothing.
- **I4** `temps_after_close` has no named property in §13; the model attaches "no temp of a save outlives its journal" (§6.1 step 8.4) to `CutTempNeverBlocks`.
- **I7** A confirmed save is never answered "not open" by a switch ("closing is not a cancel"); §13 maps the switch-drop mutant to `OutcomeTruthful`, which is where the model states it.
- **I10** The assessment of a workspace on an unreachable disk fails (the open fails), as reading its files does.
- **I11** The user may quit, and press Quit anyway, in every state while the app is alive (the shell's quit, then Quit anyway, §9.3); so `EveryWaitHasAnExit`'s Quit-anyway clauses and `QuitAnywayAlwaysEnabled` hold by this guard, and the mutant `quit_anyway_only_while_quitting` shows they would catch a narrower one. The open's requester (the window of a switch or boot) may go away, which leaves the open running and endable by another job's Cancel (§4's list), like a read whose requester has gone; that Cancel then ends the switch or boot on the failed screen (M1).
- **I13** The liveness worlds driven by engine and app fairness (`LS_boot`, `LS_jobs`, `LS_quit`) give the user a budget of exits (`UserBudget`: Retry, Back, Open another, Keep, Stay, Try again), assuming the user eventually stops restarting work; otherwise pressing Retry and Cancel forever starves a queued create, which is not a system failure. Safety worlds and `EngineCanAlwaysBeFreed`'s worlds leave the exits unbounded.
- **I14** Spec since e91bbcef (§8.1): a Cancel of `recover_all` before its listing marks nothing and ends the switch or boot on its cancelled screen; recover-all never ends unlisted. The model's `UserCancelRunning`; mutant `recover_cancel_before_listing`.
- **I15** After "Switch cancelled." or "Opening cancelled.", Retry runs the switch's own work again (recover-all, then the open), as Retry on the blocked screen does. After a failed open, Retry assesses the target again (this switch's recover-all has run). Back is spec text since simplification B (§9.1 step 6): Retry with the previous workspace as the target, recover-all first, on every screen that offers it (the old reading, Back only assessing, is the mutant `back_skips_recovery`).
- **I16** An R4 step that ends between a record's rename and its sync (an engine death, a Cancel, the quiesce): the sidecar syncs the journal directory before it acts on the record (§6.5: "before anything acts on the new state or shows it"), as §6.6 does for a save; the pause is then durable before anything shows it (fidelity review 3, M1).
- **I17** `LS_jobs` and `LS_boot` have no stuck process and no hang (`MaxStuck` = `MaxHang` = 0): one that never ends would hold a waited-for job, a switch or the boot for ever, which `EveryWaitedForJobEnds`, `EverySwitchEnds` and `EveryBootEnds` cannot allow for (the spec answers it with Quit anyway and the advice, which `EngineCanAlwaysBeFreed` checks in `LS_free*` with both). `LS_quit` has one of each: its property allows for them (Quit anyway offered).
- **I18** `LS_free`, `LS_free_cc` and `LM_free` set `CloseQueued = FALSE`: there the waiting user's exits are Cancel and Quit anyway; a closed waiting tab ends that user's wait, which `EveryWaitedForJobEnds` covers (§13 says so for `EngineCanAlwaysBeFreed`). With queued tab closes, a behaviour where the waiting user closes the tab and nobody waits any more fails the property as stated (review 3's `RV3_free_closequeued`) without a system fault.
- **I19** withdrawn by simplification B (§5): it read the journal again under recovery's locks, for a journal an orphaned engine closed after the listing. With the engine lock no engine of an earlier session runs after the listing, so the journal tried is the one listed; `RC_Check`'s branch for it is gone, and §8.1 step 1 no longer says it.

## 5. The model's findings: now spec rules

F1-F4 and O1 of 84c8b8c9 are spec rules at 1cf1fb4c (§6.6, §8.1, §9.3; §6.4; §6.4; §3; §6.7 step 4). The model encodes them with no switch; each finding's configuration is now a mutant that must fail its property: `outcome_unsynced` (F1, `SavedSurvivesPowerLoss`), `rollback_skips_temp_rule` (F2, `ExternalEditNeverSilentlyLost`), `swap_back_unsynced` (F3, `ExternalEditNeverSilentlyLost`), `screen_holds_queue` (F4, `EveryBootEnds`), `confirm_no_recheck` (O1, `NoFalseMismatch`).

### Findings N1-N3 at 1cf1fb4c: adopted by the spec at 4fb67b23

The model at 145aed17 checked the spec at 1cf1fb4c as written, with the fixes below behind a `Proposals` switch. The spec adopted all three at 4fb67b23 (§4, §5.2, §8.1, §9.2, §13; ADR-0064's amendment), so they are now the model's rules with no switch, and every base world passes. Each old rule is a mutant that must fail the property it broke: `keep_no_sync` (N1), `appscoped_runs` (N2), `advice_only_to_waiting` (N3). The traces below are the ones the spec as it was produced.

**N1 — "Keep the files as they are now" does not make "now" durable** (§8.1 afterwards, §9.2; breaks `SavedSurvivesPowerLoss`, worlds `S_r4_attempts`, `S_forget`).
- Trace (`S_r4_attempts`, shortened): save s1 lands and is reported "saved" (its engine dies after the close; the outcome check syncs and answers "saved"); save s2 runs its R4 first, then swaps N (s1's post → s2's post); the app crashes; at the next start recover-all restores N (an exchange, not yet synced) and its engine crashes, so s2's journal blocks (`failed`); the user presses "Keep the files as they are now", which renames the journal `.kept` and writes nothing. A power loss can now bring back s2's N: s1's saved bytes in N are gone and the ledger mixes s1's R with s2's N, with no journal left to undo it.
- Judgement: a spec gap, not a modelling error: §8.1 step 3 itself says a restore cut by an error leaves "each file whole at pre or post" (not durable), and Keep only renames the journal. Narrow (a crash during a restore, Keep, then a power loss before anything syncs that folder). Adopted (§8.1 afterwards, §9.2): Keep `durable_sync`s every file and folder the journal names, then renames it `.kept`. Model: `UserKeep`; mutant `keep_no_sync` (Keep writes no sync) must break `SavedSurvivesPowerLoss` on `S_forget`.

**N2 — `AppScopedWaitsForRecovery` says "runs" where §3 and §9.1 say "start"** (§13 "Also safety" against §3 and §9.1 step 2; worlds `S_boot`, `S_copy`, `S_switch`).
- Trace (`S_switch`, shortened): W1 open; the user asks for a create, which starts; the user switches to another workspace; A closes (the create is app-scoped, "not A's job and runs on", §9.1 step 2); the switch's recover-all is queued behind the running create. The create now "runs between a switch's close and its target's open", which §13 forbids. Same with a create or copy started while a boot or switch shows its failed or blocked screen (F4 now lets it start there) when the user then presses Retry.
- Judgement: a contradiction inside the spec, not a safety problem: recover-all never runs beside a create (one job at a time) and waits until it ends. Adopted (§13 "Also safety"): "no create or copy *starts* while …", matching §3; behaviour unchanged. Model: `AppScopedStarts`. The seeded bug for the new wording stays `app_scoped_during_recovery`. Since N2 changed no behaviour, `appscoped_runs` is not a seeded bug: it checks the old wording (`AppScopedRuns`) on `S_switch` and must fail, which shows the model still lets a create that started before a switch's close run on through the window (§9.1 step 2); if a model change ever made the close wait for it, this check would pass and be reported as a surprise.

**N3 — `EngineCanAlwaysBeFreed` as stated fails when the waiting user cancels while a stuck process holds the slot** (§13 Liveness, §4, §5.2; worlds `LS_free`, `LS_free_cc`, `LM_free`).
- Trace (`LS_free`, shortened): a job's engine is signalled (the requester's Cancel) and gets stuck in the kernel; the user queues a save and a read; under the property's own fairness the waiting user presses Cancel on their queued jobs. §4 gives the quit advice only for a Cancel behind a *running* job, and §5.2's timer advice needs a job that waits; with the queue now empty, nothing shows the advice, the slot is never free, and the app has not quit: `(a waited-for job is queued) ~> (slot free ∨ advice ∨ quit)` is violated.
- A second path to the same end (found with a narrower first fix that showed the advice only after the waiting user's Cancel): the waiting user's Cancel removes only their own job (the running read's requester is present, §4), then that requester closes their tab; the read's engine is signalled and stuck, and nobody waits.
- Judgement: a gap between §4/§5.2 and §13: §13 lists the ways a wait ends outside the property (a closed tab, a switch) but not "the waiting user's own Cancel", after which nothing explains a slot held for ever. No data is at risk; the user gets no explanation and the next request waits again. Adopted (§4, §5.2, §11, §13): §5.2's advice ("Finzytrack cannot stop the earlier ledger work. Quit and reopen Finzytrack.") appears whenever a signalled process holds the slot past the display delay, whether or not a job waits (in the waiting job's place, or as a notice). Model: `AdviceTimer`; mutant `advice_only_to_waiting` (the advice only while a job waits) must break `EngineCanAlwaysBeFreed` on `LS_free`. It still never appears on a timer for work that is merely slow (`NoAdviceOnTimerForSlowWork`), and the `no_quit_message_for_writing` mutant is still caught. Weakening the property instead ("… or nobody waits any more") would let that mutant pass.

### Fidelity review 2 (of cac8ad13 against the spec at 4fb67b23)

Verdict: not faithful (1 High, 5 Medium, 8 Low). The spec took its spec items at 31afbd82; this model encodes them and fixes the model's own bugs. Each old rule is a mutant that must fail; each of the reviewer's failing cases is a small world that now passes.
- **H1** (spec): a restore temp's data synced before its swap (§6.4, §8.1 step 3). The model already assumed it; `RS_Write` now says so, and `no_restore_temp_fsync` (swapped-in bytes torn after a power loss) must break `NoTornLedgerAfterRecovery` on `S_recover_power`.
- **M1** (model): another job's Cancel that ended an `open` (its requester gone) left the switch or boot in its opening stage for ever. It now applies the open's failed screen; `OpeningHasItsJob` (§13 at 31afbd82) is the stall check; `cancel_open_keeps_opening` must break it on `S_stuck_opening` (the reviewer's `RV_stuck_opening`); `LS_boot` gained a tab close (`MaxClose = 1`).
- **M2** (model): a Cancel of recover-all before its listing marked the first journal (a mark the listing then erased) or, with none left, ended recover-all without the listing's sync, so a quit note could be answered from a record a power loss then took back (`OutcomeTruthful`; the reviewer's `RV_save_quit_power`, now the world `S_save_quit_power`). Now recover-all never ends unlisted, the Unavailable paths list after the sync, and a Cancel before the listing ends the switch or boot on its cancelled screen (I14). Mutants `recover_cancel_before_listing` (the old rule) and the reviewer's `rc_unsynced` (recover-all reads records without the sync) must break `OutcomeTruthful` on `S_save_quit_power`.
- **M3** (spec wording): §13's "Finishing" covers any running save, create or copy, background saves included. `LS_quit` gained a background save (`MaxBg = 1`, the reviewer's `RV_quit_bg`); `finishing_confirmed_only` (the old reading) must fail there, which shows that a running background save past its commit point does hold a quit.
- **M4** (spec): the switch's or boot's own queued job can be cancelled while it waits behind other work ("Switch cancelled." / "Opening cancelled."). Model: `SwitchJobBehind`, `CancelableQ`, `CancelledSes`; Retry runs recover-all again (I15); a new `EveryWaitHasAnExit` clause; world `S_switch_hung_create` (the reviewer's `RV_switch_hung_create`), where `switch_job_not_cancelable` must break `EveryWaitHasAnExit`. The Quit-anyway clauses hold by I11; `quit_anyway_only_while_quitting` must break them on `S_cancel_commit`.
- **M5** (spec): every R4 record rename is synced before its outcome is acted on or shown. Model: `R4Rename`, then `R4_Sync`; the sidecar syncs after an engine death (I16); `R4GateLiftedOnlyByUser` now also says a shown pause is durable; `r4_rename_unsynced` must break it on `S_r4_outcomes`.
- **L1** `run_all.sh`'s summary marks a log of an older model, `.cfg`, tool or simulation size as stale (not counted as a result); the resume key includes `SIM_TRACES` and `SIM_DEPTH` for the simulation; the stale `P_` logs were deleted.
- **L2** world `S_create_close`: a confirmed create and copy with the requester present or gone, and a read waiting behind them.
- **L3** world `S_recover_torn` (`BootTorn`): a journal whose file list cannot be read blocks every open and every copy; Keep syncs only what readable journals name. `PartJournalNeverBlocks` now exempts a torn boot journal (not ours).
- **L4** the comment on exchanges no longer claims ordered metadata; §6's `quit_waits_reap` row names `EveryQuitEndsOrOffersQuitAnyway`.
- **L5** HOW_TO_RUN.md: disk needs, `--only` for `L_session` and `L_recover_all`, honest totals.
- **L6** a full disk before the journal is durable (§6.4's last bullet): `IOFail` at the backup and `.part` steps refuses with nothing replaced ("not saved"). A failure between the durable `.open` and the first replace is spec text since c87ce786 (L10) and modelled (fidelity review 3).
- **L7** I17.
- **L8** `S_fs_weak_recover` gained an I/O error during the restore (`MaxIO = 1`).

### Fidelity review 3 (of the model at df1a06ea against the spec at e91bbcef)

Verdict: not faithful (1 High, 3 Medium, 11 Low). The owner chose its option A; the spec took L1, L9 and L10 at c87ce786. Each old rule is a mutant that must fail, and the reviewer's mutants are now permanent (§6).
- **H1** (model): the INV-32 properties saw only journals marked `old` by recover-all itself, so a boot or Retry that skipped recover-all passed them. Journals are now marked when the boot or switch begins (`MarkOld`); `rv_boot_skips_recovery` and `rv_retry_skips_recovery` must break `EveryJournalRecoveredOrBlocking` (on `S_recover` with only b3 at boot). They cannot break `NoOpenOverUnresolvedJournal`: the open's own block test refuses a workspace whose files an `.open` journal names, with or without recover-all.
- **M1** (model): an R4 rename ended by a Cancel or the quiesce was never synced, and the gate then acted on it. Now `SyncR4End` on every such end; a gate refusal for a `.mismatch` counts as a shown pause; world `S_r4_ends`; mutants `rv_r4_crash_nosync` and `r4_cancel_nosync`.
- **M2** (model): an engine death past the manifest's rename told "Not created... You can delete it" over a complete workspace. Now the job registers it (§10's row); `OutcomeTruthful` states the in-session create message; mutant `create_crash_msg_from_memory` on `S_create_absent`.
- **M3** (model): the quiesce ending a confirmed save went unchecked. `CancelReachIsTheOneRule` now states it; mutant `rv_quiesce_ends_confirmed` on `S_switch`.
- **L1** (spec, c87ce786): quit notes answered at the end of every recover-all; model-only property `NotesAnsweredByRecovery`, mutant `notes_only_at_boot`.
- **L2** spec pointers updated; I14 is spec text.
- **L3** the quiesce also ends a background save or an R4 between two engines.
- **L4**, **L8** stated in §3.
- **L5** `StuckSigOnly` removed (dead); I18 lists `CloseQueued = FALSE`.
- **L6** `run_all.sh` runs an `ERROR` log again on resume.
- **L7** §3's `EdDurable` row corrected.
- **L9** (spec, c87ce786): a quit keeps creates and copies, with or without an open workspace (`QuiesceKeep`).
- **L10** (spec, c87ce786): a failure after the durable `.open` and before the first replace rolls back ("not saved"): `IOFail` at every `WritePcs`.
- **L11** documented (I11).

### Fidelity review 4 (of the model at c6c2e5c8 against the spec at c87ce786)

Verdict: not faithful (1 High, 2 Medium, 4 Low), all in the model; the owner chose option B (fix the model, no fifth full review: a mechanical confirmation). Each old rule is a mutant that must fail, and the reviewer's mutants and checks are now permanent (§1.4, §6).
- **H1** (model): the orphaned engine never wrote, so §8.1 step 1's lock try and §6.1 step 3's lock wait protected nothing (`rv4_rc_ignores_orphan`, `rv4_save_ignores_orphan` survived). Now an engine left by an app crash goes on with its save past the commit point, step by step, on the files it holds, until it ends (`OrphanStep` applies the save's own step effects to `pst.oc`; the boot orphan of `BootOrphan` goes on with b2's save, `BootOC`). `rv4_rc_ignores_orphan` breaks `NoTornLedgerAfterRecovery` on `S_recover_lock` without the link (recovery restores beside the orphan, which swaps a file back to post before the journal is resolved). `rv4_save_ignores_orphan` breaks `NoSaveWhileR4Owed` on the new world `S_save_orphan` (§15's case: the app crashes after a save's backup, the next start's recover-all lists nothing, the next save's gate passes, the orphan then writes and closes its journal while that save writes). `S_save_quit` cannot show it: it has no app crash, and an engine left by Quit anyway has been signalled and writes nothing more (§3). Writing orphans exposed one silence of the spec, then I19. (Simplification B, below, removed the writing orphan, these two mutants, `S_save_orphan` and I19.)
- **M1** (model): Back after "Switch cancelled." only assessed the previous workspace, skipping recover-all (the old I15), against ADR-0063 decision 1 step 6, INV-32 and §14 ("by the same path"). Now Back is Retry with A as the target (`UserBack`), and a queued recover-all clears the reasons an earlier one gave (`MarkOld`), so `EveryJournalRecoveredOrBlocking` no longer accepts a stale reason. Retry after a failed open assesses again without marking. Mutant `back_skips_recovery` (the old I15) breaks `StrictRecovered`, `BlockedScreenHasReason` and `KeepOnlyJudged`.
- **M2** (model): `Assess` waited for any running job, which §2 does not say (the assessment runs outside the queue), so a switch or boot could sit in its assess stage behind a create or copy with no Cancel. Now it waits only for its own job; behind other work the open is queued and `SwitchJobBehind`'s Cancel applies. `AssessWaitHasCancel`; mutant `assess_waits_for_work`.
- **L1** §9.1 step 1 modelled: with a workspace open, a target whose files cannot be read starts no switch (`UserSwitch`).
- **L2** the L9 rule is checked: `TruthInv` says a quiesce never drops a queued create or copy (`gh.ccDrop`); `rv4_quit_drops_create` breaks `OutcomeTruthful`.
- **L3** `R4_Confirm` publishes only while no orphaned engine holds a lock of W1 (§7: a publish happens under the locks). (Gone with simplification B: no such engine runs beside an R4.)
- **L4** `results/SUMMARY.md` lists every configuration of `configs.txt`, those never run as "not run" (not counted).
- The reviewer's `rv4_no_cancel_recover` (`EveryWaitHasAnExit`, `S_recover`) and `rv4_outcome_open_notsaved` (`OutcomeTruthful`, `S_save_crash`) were already caught and join the suite. Reachability of the new paths was checked with witnesses (a writing orphan closes its save, a later save waits on its lock, recover-all meets it `in_use`, the §15 late journal, the boot orphan finishes b2; Back after "Switch cancelled." runs recover-all; the assess stage with a create running; Keep of a judged journal).

### Simplification B (owner, 2026-10-28)

Targeted review 5 (of the model at 75702f70 and the spec at a6b0c230; private archive `m1a/scheduler-design/targeted-review-5/`) found 0 High, 3 Medium, 4 Low. M-1 (Keep and the blocked screen acting on a journal the orphan closed after the listing), M-2 (no property for I19), M-3 (a copy beside a writing orphan) and L-2 (the orphan's journal built from the new session's registry key and build) all came from an orphaned engine writing beside the new session. The owner chose a policy instead of a fix per window: "Agreed with B, back always recovers first, and the read core next". The spec took it at this commit (§5.1 the engine lock, §8.1 step 1, §9.1 step 6, §13-§15; ADR-0064 and ADR-0063 amendments).

**Removed, and why each had no case left:**
- `OrphanStep` and its helpers (`OEff`, `OStop`, `OrphanWriter`, `OrphanPcs`), `pst.oc`, `BootOC`, the `WO` order for b2 and b3, the `writes` argument of `OrphanAfter`: the engine lock puts every step an engine takes after its app died before the new session's first ledger work, so those steps are a cut at a later step, which `AppCrash` already reaches (§3). Interleaving them with the new session was the only thing they added, and the lock forbids it.
- `pst.orphan` as a set of files: it is now one flag (the engine lock held), with `pst.ostuck`. The per-file guards that read it went with it: `S_Locks` (the save's lock wait), `RC_Lock` (the `in_use` branch), `R4_Check`'s and `R4_Confirm`'s publish, `RD_Check`'s rebuild publish and `OP_Check`'s open: none of them can meet an engine of an earlier session now.
- The `in_use` reason (`RC_Lock`, `Cause`, `BlockedOnlyForCause`) and I19 (`RC_Check`'s branch for a journal no longer `.open` when tried; §4): the journal tried is the one listed. `E_Close`'s clearing of a reason (only an orphan closing a journal recover-all had judged could need it). `NoEngineStall`'s exemption for an orphan's lock.
- The world `S_save_orphan` (an app crash during a save past its commit point, whose engine wrote on while the next start's recover-all and save ran): its only purpose was that interleaving.
- The mutants `rv4_rc_ignores_orphan` (`NoTornLedgerAfterRecovery`) and `rv4_save_ignores_orphan` (`NoSaveWhileR4Owed`): they ignored an orphan's per-file lock, which no longer exists as a case. Their protection is now the engine lock, which `no_engine_lock` checks.
- From the spec, and so from the model's mapping: §15's two orphan lines (the late journal after the listing; Keep beside a writing orphan), §11's `in_use` row.

**Added:**
- The engine lock: `EngLockFree` in `SlotFree` (`Dispatch`, `SpawnStep`), `AtMostOneEngine` across a restart (`pst.orphan => eng.p = 0`), the advice while the sidecar waits for it (`AdviceTimer`), the advice cleared when that engine ends (`OrphanEnd`, `OrphanEndStuck`), `EngineSlotFree` false while it is held, `NoAdviceOnTimerForSlowWork` allowing that case.
- Mutants `no_engine_lock` (the sidecar does not wait: `AtMostOneEngine` on `S_recover_lock`) and `orphan_no_advice` (no advice while an engine of an earlier session holds the lock: `EngineCanAlwaysBeFreed` on the new small liveness world `LS_free_orphan`, where the app crashes with its engine stuck; the trace: the boot's recover-all is cancelled and "Open another workspace" queues another, for ever, with nothing explained). Both are the decision's own two clauses; neither adds behaviour.
- Back always recovers first: `UserBack` queues recover-all on every screen that offers Back (before, only after "Switch cancelled."; targeted review 5, L-3). The mutant `back_skips_recovery` (Back only assesses) still breaks `StrictRecovered`, `BlockedScreenHasReason` and `KeepOnlyJudged`. No property fails when Back only assesses after a failed or blocked open: that reading was safe (the open's block test refuses), so decision 2 is checked by `UserBack`'s definition, not by a property.

**The other findings:** L-1 (the I19 sentence against "none is skipped") went with I19. L-4: `AssessWaitHasCancel` is now `AssessNeverWaits`, without the disjunct that was never needed. Overflow: `markold_keeps_why` (§6 note). The reviewer's open question "who renames R4 records, and an orphaned R4 engine renaming `.closed` → `.mismatch` after the next start's gate" has no case left: that engine has ended before the next start's first job.

**Effect on the worlds:** where an orphan wrote, the world shrank (`S_recover_lock` 151,810 → 31,173 distinct states); worlds without one kept their size (`S_save_crash` 2,814,748, `S_save_power` 101,371, as after review 4). The 30 small safety worlds hold 38.6 million states together (40.3 million in 31 before); HOW_TO_RUN.md has the timings. A lingering engine now counts whether or not it held ledger-set locks, since any engine holds the engine lock.

### Model corrections made while running
Not spec issues; listed so a reviewer can judge them. At 84c8b8c9: the power-loss model first let a file and its temp revert independently after an atomic exchange (fixed with the coupled pairs of §3); refusal branches silently disabled by contradictory `UNCHANGED` clauses (found by the action-coverage check); a hang that survived the replacement of its engine; a blocking mark that survived a power loss; recover-all's Cancel read three ways before I1; properties first stated too strictly. In this update: the recover-all epoch counter grew without bound once the exits lost their counters (replaced by the journal flag `old`); a coupling left by an earlier save's temp was reused by a later save's exchange of the same file (now dissolved into independent outcomes first); the outside editor's durable save no longer stands in for an ordering assumption (it syncs its folder, which §13 now states).

## 6. Mutants

Each runs on a small world (`cfg/MUT_<name>.cfg`) and checks only its property; `run_all.sh` requires TLC to report that property violated. 35 come from the two executable models §13 cites, 5 are §13's additional ones, 11 were added at 84c8b8c9 so that every property has one (PROCESS.md §3), 8 are the model's former findings (F1-F4, O1, N1-N3), 7 come from model review 1 or the update after it, 8 from fidelity review 2, 9 from fidelity review 3, 5 from fidelity review 4 (7 before simplification B removed `rv4_rc_ignores_orphan` and `rv4_save_ignores_orphan`) and 2 from simplification B (90 in all; `back_skips_recovery` runs on three properties, so 92 configurations). `appscoped_runs` and `finishing_confirmed_only` are reachability checks rather than seeded bugs (§5).

**The reviewers' own mutants are a permanent part of this suite** (each must fail its property in every run): model review 1's `fb_no_guard`, `stay_disabled`, `retry_disabled`, `newest_first` (finding 9) and `confirm_no_recheck` (its `NoFalseMismatch` witness, O1); fidelity review 2's `rc_unsynced`; fidelity review 3's `rv_boot_skips_recovery`, `rv_retry_skips_recovery`, `rv_r4_crash_nosync`, `rv_quiesce_ends_confirmed`, `rv_cancel_keeps_stage`, `rv_create_no_sync`; fidelity review 4's `rv4_quit_drops_create`, `rv4_no_cancel_recover`, `rv4_outcome_open_notsaved` (none of review 4's is equivalent after its fixes: each fails its property; its `rv4_rc_ignores_orphan` and `rv4_save_ignores_orphan` left the suite with simplification B, §5, because the case they seeded no longer exists). Targeted review 5's `markold_keeps_why` (a queued recover-all keeps earlier reasons) survived on the model before simplification B (`S_switch`, `S_recover`), where the reviewer judged it equivalent once Back queued recover-all; it is not in the suite and was not run again here. Review 3's `rv_publish_over_open` (the publish skips its `.open` test) is equivalent in this model and not in the suite: the save's gate refused any `.open` journal of W1 and no other job runs until the save ends, so the test can never fail at the publish (checked on `S_save_crash` with a publish-only property: 3,107,308 states, no violation); its switch stays in `S_Publish` for a later reviewer.

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
| `quit_waits_reap` | added | `EveryQuitEndsOrOffersQuitAnyway` | `LS_quit` | close waits for the reap |
| `cnc_auto_lift` | added | `R4GateLiftedOnlyByUser` | `S_r4_attempts` (three saves) | recover-all turns `could_not_check` back into an owed R4 |
| `r4_skipped_before_save` | added | `OwedR4BeforeNextSave` | `S_r4_outcomes` | the save skips step 1 and the gate's closed-record test |
| `outcome_unsynced` | finding F1 | `SavedSurvivesPowerLoss` | `S_save_power` (+ crash) | the outcome check and recover-all read records without syncing their directory |
| `rollback_skips_temp_rule` | finding F2 | `ExternalEditNeverSilentlyLost` | `S_save_crash` (editor on R) | the rollback skips recovery's step-2 temp rule |
| `swap_back_unsynced` | finding F3 | `ExternalEditNeverSilentlyLost` | `S_save_power` (+ I/O error) | a swap-back is not synced |
| `screen_holds_queue` | finding F4 | `EveryBootEnds` | `LS_boot` | a switch's failed or blocked screen still holds app-scoped jobs |
| `confirm_no_recheck` | observation O1 | `NoFalseMismatch` | `S_r4_outcomes` | R4's confirm does not re-check the fingerprint |
| `keep_no_sync` | finding N1 | `SavedSurvivesPowerLoss` | `S_forget` | Keep renames the journal `.kept` without syncing the files it names |
| `appscoped_runs` | finding N2 | `AppScopedWaitsForRecovery` | `S_switch` | checks the old wording ("no create or copy *runs*" in the window); must fail, so a create that started earlier still runs on (a reachability check) |
| `advice_only_to_waiting` | finding N3 | `EngineCanAlwaysBeFreed` | `LS_free` | the timer advice only while a job waits |
| `no_restore_temp_fsync` | fidelity review 2, H1 | `NoTornLedgerAfterRecovery` | `S_recover_power` | a restore temp swapped in before its data was synced |
| `r4_rename_unsynced` | fidelity review 2, M5 | `R4GateLiftedOnlyByUser` | `S_r4_outcomes` | R4's record renames (and `could_not_check`) acted on and shown before the sync |
| `cancel_open_keeps_opening` | fidelity review 2, M1 | `OpeningHasItsJob` | `S_stuck_opening` | another job's Cancel ends the open and leaves the boot in its opening stage |
| `recover_cancel_before_listing` | fidelity review 2, M2 | `OutcomeTruthful` | `S_save_quit_power` | a Cancel before the listing marks a journal or ends recover-all unlisted (the old I1) |
| `rc_unsynced` | fidelity review 2 (reviewer's), M2 | `OutcomeTruthful` | `S_save_quit_power` | recover-all reads the records without syncing their directories |
| `switch_job_not_cancelable` | fidelity review 2, M4 | `EveryWaitHasAnExit` | `S_switch_hung_create` | the switch's queued recover-all cannot be cancelled |
| `quit_anyway_only_while_quitting` | fidelity review 2, M4 | `EveryWaitHasAnExit` | `S_cancel_commit` | Quit anyway offered only during a quit |
| `finishing_confirmed_only` | fidelity review 2, M3 | `EveryQuitEndsOrOffersQuitAnyway` | `LS_quit` | "Finishing" read as confirmed jobs only: must fail (a reachability check) |
| `rv_boot_skips_recovery` | fidelity review 3 (reviewer's), H1 | `EveryJournalRecoveredOrBlocking` | `S_recover` (only b3) | the boot opens without recover-all |
| `rv_retry_skips_recovery` | fidelity review 3 (reviewer's), H1 | `EveryJournalRecoveredOrBlocking` | `S_recover` (only b3) | Retry after a cancelled boot or switch only assesses |
| `rv_r4_crash_nosync` | fidelity review 3 (reviewer's), M1 | `R4GateLiftedOnlyByUser` | `S_r4_ends` | after an R4 engine death the record is not synced |
| `r4_cancel_nosync` | fidelity review 3, M1 | `R4GateLiftedOnlyByUser` | `S_r4_ends` | an R4 ended by a Cancel or the quiesce leaves its rename unsynced |
| `create_crash_msg_from_memory` | fidelity review 3, M2 | `OutcomeTruthful` | `S_create_absent` | an engine death past the manifest's rename says "Not created" |
| `rv_quiesce_ends_confirmed` | fidelity review 3 (reviewer's), M3 | `CancelReachIsTheOneRule` | `S_switch` | the quiesce ends a confirmed save before its commit point |
| `rv_cancel_keeps_stage` | fidelity review 3 (reviewer's) | `OpeningHasItsJob` | `S_switch_hung_create` | cancelling the switch's queued job leaves the switch in its stage |
| `rv_create_no_sync` | fidelity review 3 (reviewer's) | `NoManifestOverIncompleteFolder` | `S_create_empty` | the create's files are not synced |
| `notes_only_at_boot` | fidelity review 3, L1 | `NotesAnsweredByRecovery` | `S_save_quit_power` | quit notes answered only by the boot's recover-all |
| `back_skips_recovery` | fidelity review 4, M1 (the old I15) | `StrictRecovered`; `BlockedScreenHasReason`, `KeepOnlyJudged` (`_blocked`, `_keep`) | `S_recover` (only b3, a switch); `S_switch` (a crash, one switch) | Back only assesses the previous workspace (on every screen) |
| `assess_waits_for_work` | fidelity review 4, M2 (the old guard) | `AssessNeverWaits` | `S_switch_hung_create` (a crash, an unreachable disk) | the assessment waits for any running job |
| `rv4_quit_drops_create` | fidelity review 4 (reviewer's), L2 | `OutcomeTruthful` | `S_create_absent` (quit, no drops) | a quit with no open workspace drops a queued create or copy |
| `rv4_no_cancel_recover` | fidelity review 4 (reviewer's) | `EveryWaitHasAnExit` | `S_recover` | recover-all cannot be cancelled |
| `rv4_outcome_open_notsaved` | fidelity review 4 (reviewer's) | `OutcomeTruthful` | `S_save_crash` | after an engine death an `.open` journal is reported "not saved" |
| `no_engine_lock` | simplification B | `AtMostOneEngine` | `S_recover_lock` (no link) | the sidecar does not wait for the engine lock: an engine of this session starts while one of the crashed session holds it |
| `orphan_no_advice` | simplification B | `EngineCanAlwaysBeFreed` | `LS_free_orphan` | no advice while an engine of an earlier session holds the engine lock |
| `stay_disabled` | model review 1 | `EveryWaitHasAnExit` | `S_switch` | Stay is never offered |
| `retry_disabled` | model review 1 | `EveryWaitHasAnExit` | `S_boot` | Retry is never offered |
| `fb_no_guard` | model review 1 | `RecoveryWritesOnlyRecordedPaths` | `S_recover_lock` (no orphan) | the path guard is missing from recovery's check |
| `newest_first` | model review 1 (finding 9) | `WaitedForFirst` | `S_queue` | dispatch picks the newest eligible job |
| `manifest_overwrite` | model review 1 (finding 5) | `DroppedFileNeverReplaced` | `S_create_empty` | the manifest's rename replaces a `.finzytrack` that appeared |
| `app_scoped_during_recovery` | model review 1 (finding 14) | `AppScopedWaitsForRecovery` | `S_boot` | a create or copy may start during a switch or boot |
| `read_refusal_stuck` | this update | `NoEngineStall` | `S_save_crash` | a read meeting an `.open` journal cannot refuse |
