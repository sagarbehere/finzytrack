# Engine scheduler: one engine, one job at a time

## Orientation
- **What this is:** draft 5 of the engine-scheduler spec (2026-10-27): how the main sidecar runs ledger work on the ledger engine, how a save becomes durable, how Cancel, a crash, a quit and a workspace switch end each job, and how interrupted saves are recovered. It records the owner's return to **one engine doing one job at a time** (ADR-0064). Drafts 1 to 4 (parallel engines) and their reviews are in the owner's private archive (`m1a/scheduler-design/`); they are reference only, not the design of record. Status: **not yet golden** (PROCESS.md §3 "Golden specs"): one full independent review must find no High and no Medium before the TLA+ model is built from it.
- **What exists:** the engine process, its protocol (S0035) and its supervision as a statechart with bounded restart (S0040, `app.platform.engine_supervision`, driven by `app.platform.adapters.engine_client.EngineClient`); the request write deadline (S0042). Nothing else here is built. §5.5 lists what changes in the built code.
- **Decisions it applies:** ADR-0064 (this design), ADR-0008 (write-safety contract; R4 after the reply), ADR-0010 (overlay load), ADR-0011 (no resident ledger), ADR-0012 (journal and recovery, as amended by ADR-0063), ADR-0013 (atomic swap), ADR-0015 (mirror publish), ADR-0054 (engine process; decision 4 as amended by ADR-0064), ADR-0062 (single instance; legacy-lock detection), ADR-0063 (one open workspace; the switch; recover-all-before-open).
- **Invariants:** INV-01 and INV-22 (every write passes the contract and fails closed), INV-10 (no resident ledger; bounded caches), INV-11 (the queue hangs off `UserContainer`), INV-20 (performance absolutes), INV-28 (`WORKSPACE_NOT_OPEN`), INV-29, INV-30 (recovery is the one write on open), INV-31 (only the open workspace runs jobs), INV-32 (recover every unfinished save before any workspace opens).
- **Requirement tags:** every mechanism below names what it serves, in brackets: an invariant, an ADR clause, or the owner's words. "Owner A" is the owner's design of 2026-10-27 quoted in ADR-0064: "one engine process, one job at a time; a FIFO queue where a job the user is waiting for goes ahead of background work; Cancel ends that job's engine and is not counted as a crash; a crash is noticed by the process exiting, with S0040's bounded restart; no automatic killing of slow jobs; one load at a time bounds memory; the save reports 'saved' after commit + read-back; the R4 re-check runs right after and gates only the next save".
- **Status of this text:** draft 5 with the fixes of its first golden review (review 5: 1 High, 8 Medium; owner, 2026-10-27: "I agree with all the recommendations. Proceed on that basis."); a fresh full review follows.
- **Traps:** a job that writes (save, create, copy) is never ended by us past its commit point, except by "Quit anyway" (§4). The R4 gate is a file on disk, never a flag in memory (§6.5). `-I` must not be used to start the engine: it ignores `PYTHONHASHSEED` (§5.1). A file a save creates gets the same `path_key` before and after it exists (§6.3). On macOS a process group whose only member is a zombie answers `EPERM`, not `ESRCH` (§5.2). A recover-all that is cancelled never lets a workspace open over a journal it did not try (§8).
- **Main tests:** §14. The model obligations: §13.

## 1. Terms

- **Job:** one unit of engine work, for the open workspace or app-scoped (for no workspace). States: `queued → running → ended(outcome)`.
- **Waited-for job:** a user request is waiting for its result (an HTTP request in flight, the open, the switch, the boot). **Background job:** nobody waits: R4, and a save the app started itself (prices). [Owner A]
- **Runs user code:** the job loads the ledger with plugins (`insert_pythonpath`, plugin modules): preview, save, rebuild, BQL, the R4 load. Parse-only work (planning, R4's confirm, facts from options) and recovery run none.
- **Commit point:** the moment a job that writes (a save, a `create`, a `copy`) asks to start its first durable write (§6.1 step 7, §8.2). Before it, the job has written nothing; after it, it finishes or is resolved from disk.
- **Closed record:** the file a save's journal becomes when the save has landed (§6.5). It says "saved" and that an R4 is owed, and it pauses the next save of that workspace until R4 ends.
- **`canonical_path` and `path_key`:** §6.3.
- **Fingerprint:** ADR-0008 step 4's full extended fingerprint with a SHA-256 per file, keyed by `path_key`: every loaded file and glob match, each glob's match list, the absent paths the ledger names, the documents-root listings, and the source files of plugins imported from outside the engine's own packages (a plugin edited after a save makes its R4 superseded, never a false mismatch).
- **Reopen:** a switch from the open workspace to itself (§9.1): it closes the workspace, recovers every journal, and opens it again.

## 2. Jobs

| Job | Does | Who waits | User code | Cancel (§4) |
|---|---|---|---|---|
| `open` | the open's mirror build when the fingerprint differs from the published generation (ADR-0015), and the ledger facts the open needs | waited-for (switch, boot) | yes | yes; the open stops and offers Retry (and Back after a switch) |
| `rebuild` | a mirror build after the focus check saw an outside change, or after a save whose publish failed | waited-for (writes are refused while the mirror is stale, ADR-0015) | yes | yes; the workspace stays `WARMING` on the old generation, with **Update now** to queue it again |
| `preview` | plan, then the contract's step-3 overlay load (ARCHITECTURE §9.2 steps 1-3) | waited-for | yes | yes; nothing to undo |
| `save` | a confirmed plan: §6.1 | waited-for; background when the app started it (prices) | yes | before the commit point: yes; after: no, it finishes |
| `r4` | the re-check of the last save (§6.7) | background; run inside a save that finds it owed | yes (its load), then none (its confirm) | yes; the closed record stays, and the next save runs it first |
| `bql` | a BQL query over one transient load (ADR-0011) | waited-for | yes | yes |
| `recover_all` | §8, while no workspace is open | waited-for (start, switch, Reopen) | no | yes, per journal: the journal it is on counts as blocking, and recovery goes on with the next one (§8) |
| `create`, `copy` | app-scoped: create or adopt a workspace, a test copy, "New workspace from settings of…" (ADR-0063 decision 1); they build the new folder under a staging name and rename it when complete (§8.2) | waited-for | yes (the copy's "loads the same" check, the create's seeding) | before their commit point: yes; after: they finish |

- Price fetching itself is network work in the sidecar, outside the queue; only its save is a job. [ADR-0054 decision 1]
- Every job is idempotent at its start: a `rebuild` whose fingerprint equals the published generation does nothing; an `r4` whose closed record is gone does nothing. So the queue needs no merging or deduplication. [Owner A: one job at a time, nothing more]
- Any work that reads or writes ledger files on behalf of a job (copy reads included) runs inside that job, so it never overlaps a save. [ADR-0012 Q29: writes serialized with reads of the same files]
- Outside the queue, the sidecar's cheap probe, `assess_workspace` and the focus check read files without writing them; a file that moves under them is caught by the job that follows (§7, the gate). [ADR-0063 decision 1 step 2; ADR-0037]

## 3. The queue

- **One queue, one scheduler thread and one engine per `UserContainer`** (on desktop, one per app; in hosted mode, per user container). The queue is in the sidecar; the scheduler is a pure table `queue.transition(state, event) -> (state, effects)` (§12). [INV-11; owner A; the owner's replayable-decisions requirement]
- **The ordering rule:** when the engine is free, the scheduler starts the oldest queued waited-for job; if there is none, the oldest background job. A running job is never interrupted to start another; it ends by finishing, by Cancel (§4), by a crash, or by "Quit anyway". [Owner A: "a FIFO queue where a job the user is waiting for goes ahead of background work"]
- **During a switch, a Reopen and the boot,** from the moment the current workspace has closed (at boot, from the start) until the switch or boot has ended, only its own jobs start (`recover_all`, then the target's `open`). Other jobs, app-scoped ones included, wait behind them. [ADR-0063 decision 2: "app-scoped work waits for recovery"; INV-31]
- **A job for a workspace that is not open is never queued:** its request gets `WORKSPACE_NOT_OPEN` (INV-28). [INV-28, INV-31]
- **Bound:** at most `max_queued` jobs (a setting, generous by default). A request beyond it gets `BUSY_HEAVY_LOAD` and is not queued. The app keeps at most one queued `r4` and one queued background save per workspace. [INV-10: every in-memory structure is bounded]
- **Memory:** one engine runs one load at a time, so peak memory is one load's peak plus the sidecar. There is no admission or memory check. [Owner A: "one load at a time bounds memory"; ADR-0011's purpose for the semaphore]

## 4. Cancel

Cancel is the user's way to stop waiting, and the only way a slow job ends early. [Owner A: "Cancel ends that job's engine and is not counted as a crash"; "no automatic killing of slow jobs"]

| The job is | Cancel does | The user sees |
|---|---|---|
| queued | removes it; nothing ran | nothing more, or "Cancelled." |
| queued behind a running background job | removes it, and also ends that background job if it is not a save past its commit point (a background job loses nothing: §2) | as above; the engine is free at once |
| running, not a writing job, or a writing job before its commit point | ends the engine (§5.2); the outcome is `cancelled`, not a stop | for a save: "Your change was not saved." (with "Your ledger is unchanged." only by §11's rule) |
| a writing job past its commit point | nothing: the job finishes | "Finishing your change…", then its outcome |
| `recover_all` | ends the engine; the journal being recovered counts as blocking, and recovery goes on with the next journal in a new engine (§8) | the open goes on when every journal is recovered or blocking; workspaces and copies using a blocking journal's files get `RECOVERY_BLOCKED` with Retry and "Keep the files as they are now" |

- **Every job that writes has the same commit-point step:** a save, a `create` and a `copy`. **The commit point is decided in one table step** (owner, 2026-10-26: "Cancel on a save that has started writing files shows 'Finishing your change…' and lets it complete", `design/inbox/answered/20261026-engine-concurrency-and-cancel.md`; ADR-0063 decision 1 leaves the rest to this spec). The engine sends `gate` and waits. In one step the scheduler either ends the engine (a Cancel is pending: outcome `cancelled`, disk unchanged) or marks the job past the commit point and sends `proceed`. A Cancel that arrives after that step is answered "too late" and changes nothing. [INV-01; ADR-0063 decision 1 step 4]
- **The second row** is what makes every wait end on the user's word: with no time limits, a background job that hangs would otherwise hold the engine for ever. [ADR-0063 decision 1: "every wait has an exit"]
- **A client that goes away** (a tab closed, a request aborted) cancels its queued or running preview, rebuild or BQL. It never cancels a save: confirming is the user's decision, and closing is not a cancel. [ADR-0063 decision 1 step 3]
- **"Quit anyway"** (§9.3) is the one case where we end a writing job past its commit point; the journal (or the staging record) makes it safe and the next start recovers it. [ADR-0063 decision 1 step 4]

## 5. The engine process

### 5.1 One engine at a time, fresh after user code

- **At most one engine process exists at any moment** (per user container, §3), and it runs at most one job. A new engine is spawned when a job needs one, only after the previous one is reaped and its process group has no live member (§5.2). A quit is the one exception: it does not wait (§9.3). [Owner A; memory bound]
- **An engine that ran user code is ended after its job**, after its final reply; the next job gets a fresh engine. A plugin can leave threads, patches to Beancount or memory behind; none of it may reach the next job's checks. An engine that ran only parse-only work or recovery may serve the next job. [owner, 2026-10-26, decision (5): "a fresh engine process per heavy job"; INV-10]
- **Spawn:** flags `-s -S -B -P -X pycache_prefix=<bytecode directory>`, never `-I`: `-I` implies `-E`, which ignores `PYTHONHASHSEED` (checked with Python 3.13: two runs under `-I` with `PYTHONHASHSEED=0` gave different `hash('abc')`, two under `-s -S -B -P` the same). Environment: the parent's, minus `FT_*` and every `PYTHON*` variable, plus `PYTHONHASHSEED=0`; plugins keep `PATH`, `HOME`, `LANG` and `TZ`. The packaged bootstrap adds only the engine's site-packages (ARCHITECTURE §2.0). [ADR-0054 decision 5 (the engine's own closure only); R4's digests must not depend on hash order (§6.7); INV-30 (`-B`: no `__pycache__` written into a user's folder)]
- **Bytecode directory:** private to the engine build (`app_local_data/engine/bytecode/<build>/`, or inside the packaged install), filled once per build with the bytecode of the engine's interpreter library, site-packages and code, read-only afterwards. A user's plugin is compiled from its source on every load and never cached. Without it each fresh start costs about 0.24 s instead of 0.05-0.10 s (review 4 spike N, owner's Mac). [ADR-0008's speed targets, given a fresh engine per job]

### 5.2 Ending an engine

- **The reason is recorded before the signal**, and a recorded reason wins over the exit status: `cancel`, `retire` (after a job that ran user code), `quit_anyway`, `close`. [S0040: only real failures count as stops]
- **Each engine leads its own process group** (POSIX; a Job Object nested in the shell's, with kill-on-close, on Windows). Our end signals the whole group, so a plugin's child processes go too and cannot keep the ledger-set locks: a `flock` lock is shared with every child that inherited its descriptor. [ADR-0012 Q29/Q30: locks released when the job ends]
- **The engine ends its own group when its parent is gone.** Its group is not the sidecar's, so a group signal the shell sends to the sidecar no longer reaches it. On POSIX the parent-death watchdog and the end-of-input exit therefore signal the engine's whole group (`killpg(0, SIGKILL)`, the engine included) instead of only exiting the engine; on Windows the Job Object's kill-on-close does it when the sidecar's handle closes. So a plugin's child cannot outlive a dead sidecar holding a ledger lock, and an orphan is gone within the watchdog's 0.25 s unless it is stuck on a disk (§15). [ADR-0012 Q30; ADR-0063 Consequences (the orphan); review 5 M8]
- **Drain before the reap, never a group signal after it:** wait for the leader with `waitid(P_PID, pid, WEXITED | WNOWAIT)` so it stays an unreaped zombie and its group id cannot be reused; signal the group until it has **no live member**; then `wait4(pid)` for the status, `ru_maxrss` and CPU. After the reap no signal is sent to that group: its id may already belong to another program. [review 4 M5; never harm a foreign process]
- **"No live member", per OS** (zombies do not count): macOS: `killpg(pgid, 0)` fails with `ESRCH` or `EPERM`; with the leader held as a zombie and nothing else left it answers `EPERM` (review 5 spike, Darwin 25.6), and since every member is our own descendant under our user, `EPERM` cannot mean a live member we may not signal. Linux: no process in `/proc` has that group id in a state other than `Z` (a zombie still answers `killpg(pgid, 0)` there). Windows: the Job Object's active process count is 0. [review 5 M3]
- **CPU of a job** is the engine's own plus its reaped children's, read with `RUSAGE_CHILDREN` in the leader before it exits (Linux `cutime`/`cstime`; Windows the Job Object total), and logged with the job (§12). A plugin child the engine never waited for is not counted, so on POSIX it is a lower bound. It decides nothing; it is the measurement INV-20's performance suite and BL-0082 need. [INV-20; review 4 M1: macOS process listings miss short-lived children]
- **A process that cannot be ended** (in uninterruptible sleep on a dead disk) keeps the engine slot and its locks until it ends; no new engine starts meanwhile. The user reads "Finzytrack is waiting for a disk and cannot stop this yet." Quitting never waits for it (§9.3). [honest limit; §15]

### 5.3 Crash and bounded restart

- **A crash is noticed by the engine's exit**: the reader sees the output end (`Ended`), or the reap sees the exit. Nothing polls or times the engine. [Owner A]
- **How an engine ended:**

| Observation | Outcome | Counted as a stop (S0040 bound) |
|---|---|---|
| final reply, then our `retire` or `close` | `answered` | no |
| ended by us with `cancel` or `quit_anyway` | `cancelled` | no |
| a signal or exit we did not cause; SIGKILL (often the OS, for memory) is `killed_by_system` | `crashed` | yes |
| a reply that breaks the protocol before the commit point | `violation` | yes |
| a reply that breaks the protocol after the commit point | read and discard every further byte, wait for the exit, resolve from disk (§6.6) | yes, at the exit |
| the start fails (spawn, or a refused handshake) | `start_failed` | yes |
| another protocol version | `incompatible` | no; never retried |

- **Bounded restart** stays as S0040 built it: the next job starts a new engine after 0.5 s, 2 s, then 8 s by recent stops; three stops in 300 s make the engine `Unavailable` until the oldest leaves the window, or **Try again**. [Owner A: "S0040's bounded restart"]
- **The crashed job is not run again by itself.** A read job ends with `ENGINE_STOPPED` and Try again; a save's outcome is resolved from disk (§6.6); an `r4` leaves its closed record, so the next save runs it first. Queued jobs stay queued and run on the next engine. [S0035: a request is never resent; a crash is often repeatable]
- **The protocol-violation rule after the commit point** exists because the engine may be in the middle of a replace: ending it then needs a recovery, while letting it run lets the save finish or roll itself back. Discarding the output keeps the pipe from filling and blocking the engine. Meanwhile the user sees "Finishing your change…"; if the engine never exits, Quit anyway (§9.3) and the switch's Stay remain. [INV-01; review 3 F5]

### 5.4 No time limits; "Still working…"

- **No engine job has a time limit.** A slow job and a hung one look the same from outside; ending either is the user's decision. [Owner A: "no automatic killing of slow jobs"; the owner's rule against fixed time budgets]
- **What the user sees:** at once, the job's phase with Cancel where §4 allows it ("Starting the ledger engine…", "Reading your ledger…", "Checking your change…", "Saving…", "Updating your views…", "Checking your last change…"); when it is queued, "Waiting for: <the running job's phase>". After a short display delay (a UI constant, default 2 s) the text becomes "Still working…" with the same actions. The delay only changes the text; it ends nothing. [Owner A: "after a while a long job shows 'Still working…' with Cancel"]
- The engine reports its phase in progress messages on the protocol; a phase change is shown, never judged. [ADR-0039: a long operation shows progress]

### 5.5 What changes in the built code

The scheduler slice changes S0035/S0040/S0042's code as follows (each with a test, §14):
- `EngineClient.call` takes no timeout; `TimedOut` and the handshake time limit (`start_timeout`) leave the statechart; `Cancelled` (our end with reason `cancel`, not a stop) and `Retired` join it. A start that never completes is ended by the waiting job's Cancel. `RestartPolicy.longest_*` bounds go with the timeouts.
- `close()` runs after the quiesce (§9.3): it signals the engine's group once and returns without waiting for the reap, instead of waiting `close_timeout` up to three times.
- The engine is spawned as the leader of its own process group (`start_new_session`), and its parent-death watchdog and end-of-input exit signal that whole group (§5.2); today `watch_parent` calls `os._exit`, which ends only the engine.
- The engine command drops `-I` for `-s -S -B -P` with the environment of §5.1 (`EngineCommand.for_packaged` and the development form).
- The protocol gains progress messages, `gate`/`proceed`, `committed` (§6.1), and a `decision` object in replies (§12); version bump. S0042's writer thread stays: it lets Cancel end an engine in the middle of a large request.
- Failure-modes rows E2, E3, E5 and E7 change with it (hang and slow become Cancel; the engine's group ends with it when the sidecar dies; close follows the quiesce).

## 6. The save

### 6.1 Steps

Steps 2-11 run in one fresh engine (§5.1); step 1 uses its own engines first (§6.7). Python's garbage collector is paused for the job (ADR-0008 amendment).
1. **R4 first, if owed.** If this workspace has a closed record (re-read from disk), the save first runs that `r4` (§6.7) inside this job. A `pass`, `superseded` or `nondeterministic` outcome lets the save go on. A `mismatch` refuses it with `WRITES_PAUSED {check_failed}`; an R4 that could not finish (its engine stopped) refuses it with `WRITES_PAUSED {check_pending}`, nothing written. A Cancel during this step cancels the save. [ADR-0008 amendment: R4 "completes before the next write"]
2. **Legacy-lock detection** (ADR-0062 decision 2, as written there): every `data/.write.lock` of a known, not-retired legacy install whose config names a file of this ledger set, deduplicated by `path_key`, tried once each without waiting, before any ledger-set lock. Free: held until the save ends. Absent: go on. Held, or any other outcome: refused with `LEGACY_APP_SAVING`, nothing written, not a stop. With one engine, two of our own jobs never try the same lock. [ADR-0062; INV-26; INV-22]
3. **Ledger-set locks:** one lock file per `path_key` in `app_local_data/locks/<sha256(path_key)>`, for every file of the workspace's ledger set (Q29), the files the save creates and the absent paths, taken in `path_key` order, waiting (the user sees "Waiting for your ledger files…" with Cancel). Lock files are never deleted. Only an orphaned engine of a crashed session can hold one. [ADR-0012 Q29; ADR-0063 Consequences: the per-file locks exclude an orphaned engine]
4. **Gate, under the locks:** the disk's fingerprint equals the plan's; the published mirror's equals the plan's; this workspace has no `.open` journal, no closed record and no `.mismatch` record, each re-read from disk. Refusals: a moved fingerprint → `LEDGER_EXTERNALLY_CHANGED` (re-plan through a fresh mirror, ADR-0026); a stale mirror → `WORKSPACE_NOT_READY {WARMING}`; a `.open` journal → `WRITES_PAUSED {unfinished_save}`; a `.mismatch` → `WRITES_PAUSED {check_failed}`. [ADR-0008 step 4; the R4 gate survives a crash (model mutant `gate_in_memory`)]
5. **Load:** the contract's step 3 on the candidate, the commit's one full load (ADR-0011), under the locks.
6. **Re-check, under the locks:** the disk's full fingerprint again, now that the load has run (seconds at 54k). Moved → refused with `LEDGER_EXTERNALLY_CHANGED`, nothing written. The locks keep our own writers out, but an editor or a sync client does not take them: without this check an outside edit to an include the save does not write, made during the load, would land beside our change in a combination nothing checked. [ADR-0013: "re-check the fingerprint immediately before each replace, under the lock"; review 5 M1; model 2 mutant `no_recheck`]
7. **Commit point:** the engine sends `gate`; §4's one table step answers `proceed` or ends the engine.
8. **Durable order** (every sync is `durable_sync`, §6.2):
   1. backups written, each file synced, and their directory synced;
   2. the journal written as `local/<key>/journal/<session>-<job>.part` and synced, renamed `.open`, then its directory synced, so an `.open` journal is never torn (a power loss can keep a new directory entry before its data; a `.part` is only ever removed by recovery, since no replace has happened yet). It names every file the save replaces or creates by `canonical_path` and `path_key`, with pre and post hashes, backup and temp names, the directories it creates, and every field of the future closed record (post fingerprint, adopted and parse digests, engine build, workspace registry key and lineage id);
   3. per file, children first and the root last: the temp file `.<name>.ft-<job>.tmp` beside it written and synced; then a **replace by atomic swap** (ADR-0013), or for a file the save creates an **exclusive create** (`RENAME_EXCL` / `RENAME_NOREPLACE`) that fails if a file appeared; the displaced file's hash compared with the pre hash (§6.4: every swap does this); the directory synced. A directory the save creates is made here and its parent synced;
   4. read-back: every written file equals its post bytes; then our temp files (they now hold the displaced pre bytes, which the backups also hold) are removed and their directories synced, so a crash after the close leaves nothing of ours in the folder;
   5. **journal close:** a rename of `.open` into `.closed` (the handle closed first, for Windows), then its directory synced. **The save has landed exactly when the closed record is durable.**
   [ADR-0008 step 5; ADR-0012 fsync order and F-4; review 4 H2 and L3; review 5 M2. Model mutants killed: `no_backup_sync`, `no_journal_sync`, `no_temp_fsync`, `report_before_close_sync`, `plain_create`, `temps_after_close`, `direct_open`]
9. **"Saved":** the engine sends `committed`; the sidecar records the outcome and answers the user's request: **saved**. [Owner A: "the save reports 'saved' after commit + read-back"]
10. **Publish:** the mirror generation is built from the step-5 load result, still in memory, and published if, under the locks, the disk's fingerprint equals the post fingerprint and no `.open` journal exists (§7). The views update when it lands. A refused or failed publish queues a `rebuild` and leaves the save saved. [ADR-0015; ADR-0008 amendment: the 5 s target includes the mirror update]
11. **Release** the locks (and a held legacy lock), send the final reply, end the engine (`retire`), queue the `r4` as background work. [§5.1]

### 6.2 `durable_sync`

macOS `fcntl(F_FULLFSYNC)` (it falls back to `fsync` only where the volume refuses it, logged once per volume); Linux `fsync`; Windows `FlushFileBuffers`, and `MOVEFILE_WRITE_THROUGH` on renames. Cost on the owner's Mac: about 4.5 ms per sync, about 11-12 syncs for a one-file save (about 50 ms), 3-4 more per further file (review 4 spike I). A directory sync with nothing to flush costs nothing. [owner, 2026-10-27: "I agree with your recommendations for all three questions" (F_FULLFSYNC among them); `fsync` on macOS does not reach the platter]

### 6.3 Paths

- **`canonical_path(p)`:** the absolute path with every symlink resolved and each existing component in the volume's stored spelling (macOS `F_GETPATH` on a descriptor opened with `O_EVTONLY`, so a cloud placeholder is not downloaded; Windows `GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED)`; Linux `realpath`). It is used to open files and in messages.
- **`path_key(p)`:** the canonical spelling of the deepest existing ancestor, joined with the remaining components as written, then every component folded by its volume's rule: macOS NFD always, plus `str.casefold()` when the volume is case-insensitive (`pathconf(_PC_CASE_SENSITIVE)` = 0); Windows ordinal upper-case unless the directory is case-sensitive; Linux none, except in a case-folding directory or on a folding mount (vfat, exfat, cifs `nocase`, ntfs3). **The key of a path does not depend on whether the file exists:** an absent `Prices.beancount` and the same file after a save created it, under any spelling the volume treats as equal, have one key. Where Python's folding differs from a volume's, two names can only fold into one key, never split. It is the identity of every lock, fingerprint entry, journal match (`RECOVERY_BLOCKED`, ADR-0063) and registry deduplication. [ADR-0063 decision 2 ("the scheduler spec draft 5 defines it"); review 4 H1]
- **Path guard for writes:** the save writes exactly the canonical paths its journal names, and recovery writes a recorded path only while it still resolves to that canonical path; otherwise the save rolls back, and the journal counts as unrecoverable. The check and the write go through one descriptor of the parent directory (`renameatx_np` on macOS, `renameat2` on Linux), so the directory cannot be swapped for a link between them; Windows is designed with BL-0087. [ADR-0063 decision 2; INV-32; model mutant `no_path_guard`]

### 6.4 Rollback inside the engine

- **Any failure after the first replace** (an exception, a full disk, a read-back mismatch, a path that moved) rolls back at once from the journal, in recovery's durable order (§8): files restored and synced, then the journal renamed `.resolved {not_saved}`. If the rollback fails, the journal stays `.open`: this workspace's engine jobs are refused with `WRITES_PAUSED {unfinished_save}` and the user is offered **Reopen** (§6.6). [ADR-0012 in-process rollback; ADR-0063 decision 2, "the open workspace's own failed rollback"]
- **Every swap hashes what it displaced, in the save, the rollback and recovery alike.** A save's swap expects the pre bytes; a rollback's or a recovery's restore swap expects the post bytes. Removing a file the save created is done the same way: the file is first renamed aside to its temp name and hashed, and deleted only if it holds the post bytes. On any other bytes, an outside program wrote the file in the moment between the check and the swap: the displaced bytes are swapped (or renamed) back into place and kept. In a save, the other files are rolled back and the journal resolved; the user reads "Your editor saved this file at the same moment. Finzytrack did not save your change. Review this file." In a rollback or a recovery, that journal stops (foreign bytes, both versions shown). [ADR-0013; review 5 M6; model 2 mutant `no_displaced_check`]
- **Recovery also checks each temp path the journal names:** a temp holding bytes other than the pre or post bytes is an outside save displaced at the moment of a crash, so that journal stops instead of restoring over it. [ADR-0013; model mutant `no_temp_check`: without it, a crash right after such a swap loses the outside save silently]
- A full disk before the journal is durable is not a rollback: nothing was replaced; "not saved, unchanged".

### 6.5 Records on disk

All live in `local/<key>/journal/`, never in the workspace folder (INV-27, INV-30). One save, one name `<session>-<job>`:

| Suffix | Means | Who reads it |
|---|---|---|
| `.part` | a journal being written; no replace has happened | recover-all removes it |
| `.open` | the save may be half-applied | recover-all (§8); the gate (refuses); every load of this workspace (refuses, §7) |
| `.closed` | saved; R4 owed | the gate (the save runs R4 first); the open (queues R4); the outcome check (§6.6) |
| `.done` | R4 passed, was superseded, or found the ledger's plugins nondeterministic | history only |
| `.mismatch` | R4 failed or could not decide; saving paused | the gate; the user acknowledges with **Resume saving**, which renames it `.done` |
| `.resolved` | recovery or rollback put the files back: not saved | the outcome check; history |
| `.kept` | the user chose "Keep the files as they are now" (ADR-0063) | history only |

- **The gate is the files, never memory:** every gate check lists the directory again; the scheduler's view is a cache. [ADR-0008 amendment; model mutant `gate_in_memory`]
- The last 16 history records of each kind are kept per workspace; older ones are deleted. [INV-10]

### 6.6 The outcome after an engine death in the session

When a save's engine ends without its final reply, the sidecar decides the outcome from disk by a pure function (§12) of the records and file hashes:
- the job's record is `.closed` or later → **saved** (R4 owed; a `rebuild` is queued if the publish did not land);
- `.resolved` → **not saved**, files put back;
- `.open` → **unfinished**: this workspace's engine jobs are refused with `WRITES_PAUSED {unfinished_save}`, reads continue from the published mirror (it never held the half-applied state), and the user reads "Finzytrack could not finish your change. Reopen the workspace so Finzytrack can put your files back." with **Reopen**. Reopen runs the switch path to the same workspace, where recover-all restores the files (§9.1);
- no record → **not saved**; the targets are hashed again, and "Your ledger is unchanged" is said only if they equal the pre hashes (ADR-0012 F-2); otherwise "…and a file was changed by another program".

There is no recovery inside a running session: ADR-0012 Q30 has a restarted engine refuse writes and ask the user to reopen, and Reopen does exactly that. This answers BL-0091's question on in-session recovery without an ADR amendment. [ADR-0012 Q30; INV-31; BL-0091]

### 6.7 R4

R4 checks that the overlay load's prediction (the adopted digest in the closed record) equals what a fresh Beancount load of the disk gives. It always runs after "saved" and gates only the next save, in every mode (desktop, dogfooding and a future hosted mode alike), and it is never switched off (ADR-0010 guardrail 3 as amended by ADR-0064; owner, 2026-10-27: "I agree with all the recommendations. Proceed on that basis."). [ADR-0008 amendment; ADR-0010 guardrail 3]
- **Digests:** the adopted digest is the SHA-256 of a canonical encoding of the load result (entries in Beancount's order, ties broken by canonical text; the error keys; the options plugins can read); the parse digest is the same over the parser's output. File names inside entries, errors and options are encoded as `path_key`s. [no false mismatch from spelling or hash order]
- **Steps:** (1) the disk's fingerprint equals the record's post fingerprint, the engine build equals the record's, and no `.open` journal exists; otherwise `superseded`. (2) In a fresh engine, a full `load_file` of the disk and its digest. (3) The fingerprint again: moved → `superseded`; digest equal → `pass`. (4) Digest different, fingerprint unchanged → **confirm**: that engine ends, and a fresh engine that runs no user code parses the disk with Beancount's parser and with the overlay's replica on the same bytes. Both parse digests equal each other and the record's → `nondeterministic` (the plugins vary run to run): a notice, the `load_file` result is published, saving is not paused (owner, 2026-10-26, answer 4 on review 2: "non-deterministic plugins do not block saving"). Anything else → `mismatch`: the record becomes `.mismatch`, saving is paused, the backup is shown, the `load_file` result is published (ADR-0010).
- **Outcomes `pass`, `superseded`, `nondeterministic`** rename the record `.done`. A Cancel or crash leaves it `.closed`; nothing is shown, and the next save runs it first (§6.1 step 1).
- **When:** queued as background work right after a save; at an open, if the workspace has a closed record; inside a save that finds one. A switch does not wait for it: the record stays for the next open. [ADR-0063 decision 1 step 3: owed work "stays durably recorded for its next open"]
- **Locks are not taken across R4's load**; the fingerprint checks before and after the load give the same guarantee against everyone but an orphaned engine, which cannot outlive its sidecar's death by more than its watchdog's 0.25 s.

## 7. Loads that read the ledger

`open`, `rebuild`, `preview`, `bql` and R4's load read the files without locks and record the fingerprint of the bytes they read. Before returning a result or publishing, the job re-computes the fingerprint and checks that this workspace has no `.open` journal; for a publish, under the locks. This replaces the "snapshot reads take the locks" of ADR-0012's F-6 tightening (an agent-level item, ADR-0064 decision 9): with one job at a time no save of ours runs beside a load, and the check after the load is what catches an outside program, which takes no locks anyway.
- A moved fingerprint discards the result and runs the job again, at most 3 times in a row; then `LEDGER_CHANGING`: "Another program keeps changing your ledger. Finzytrack will update when it stops." with Try again. [ADR-0015: publish only the current state; ADR-0013: outside programs are detected]
- A `.open` journal refuses the job with `WRITES_PAUSED {unfinished_save}` (§6.6). [ADR-0012 F-6: reads refuse while a journal is open]
- Generation ids, the pointer swap and GC are ADR-0015's.

## 8. Recovery and recover-all-before-open

### 8.1 Journals

**When:** at start before the first open, at every switch and Reopen after the current workspace has closed, while no workspace is open. It is the switch's (or boot's) `recover_all` job. [INV-32; ADR-0063 decision 2; INV-31]

**What:** every `local/*/journal/*.open`, found by listing `local/`, whichever workspace it belongs to and whether or not its registry row still exists, and every staging record (§8.2). A `.part` journal is deleted: no replace happens before a journal is `.open` (§6.1 step 8.2), so it names nothing that needs undoing and never blocks. Each `.open` journal is tried on its own, in an engine that has run no user code:
1. take the locks of the files it names without waiting; a held lock means a live writer (an orphaned engine): the journal stays [ADR-0012 Q30];
2. check every file it names: the path guard (§6.3); the file holds its pre or post bytes (pre "absent" for a created file); its temp holds nothing or the pre or post bytes (§6.4); a backup needed for a restore holds the pre bytes (ADR-0012 F-1). Any failure, or a file that cannot be reached: the journal stays and counts as unrecoverable;
3. restore, root first (ADR-0012 F-5): a file holding post bytes gets its pre bytes back by temp and swap from its backup, or is removed if the save created it, each hashing what it displaced (§6.4: other bytes are put back and the journal stops); a directory the save created is removed if it is empty; our temp files are removed; every changed directory is synced;
4. **only then** rename the journal `.resolved {not_saved}` and sync its directory. A recovery cut before this step runs again from the start and finds the files already at pre: it is idempotent. [review 4 H2; model mutant `resolve_before_restores_synced`]

**Every journal ends recovered or blocking; none is skipped.** [INV-32: no workspace opens over an unfinished save; review 5 H1 and M5]
- **Cancel** during a journal ends the engine; that journal counts as blocking (reason `cancelled`), and recovery goes on with the next journal in a new engine. So a journal on a dead disk costs the user one Cancel, and the journals after it are still tried.
- **An engine crash** during a journal makes that journal blocking (reason `failed`) and counts as a stop (§5.3); recovery goes on with the next journal after the backoff.
- **When the engine cannot run** (`Unavailable` after the stop bound, `Incompatible`), every journal not yet tried counts as blocking (reason `not_tried`). Workspaces and copies that use none of their files open normally, reading their published mirrors (failure-modes E6).
- The open of the target starts only after every journal has been recovered or marked blocking. Retry on the blocked screen runs `recover_all` again for every journal still `.open`.

**Afterwards**, by ADR-0063 decision 2 as written: a one-line notice names each workspace whose save was undone ("Finzytrack undid an interrupted save in *Personal*."); a blocking journal blocks every open of a workspace whose ledger set uses a file it names by `path_key`, its own workspace included and its folder opened by Locate or "Open folder…" (matched by the registry key and lineage id the journal records), and every copy that would read such a file: `RECOVERY_BLOCKED {path, workspace, reason}` with Retry, Stay or Back, and "Keep the files as they are now", which renames the journal `.kept`, writes nothing to the ledger files and is logged. A journal whose file list cannot be read blocks every open and every copy. Forget keeps journals. [INV-32; ADR-0063 decision 2; model mutants `recover_target_only`, `cancel_stops_all`, `crash_unmarked`, `untried_unmarked`]

### 8.2 Create and copy

A `create` or `copy` (§2) writes a folder no workspace uses yet. It never writes under its final name until the folder is complete. [ADR-0008 (creation passes the contract); ADR-0063 decision 1 (app-scoped work writes only the new, unregistered folder); review 5 M7]
1. Plan and check without writing: the copy reads its sources (refused with `RECOVERY_BLOCKED` if a blocking journal names one, ADR-0063; the legacy-lock try and the Copy re-check of ADR-0062), and every file of the new folder passes the write-safety contract's checks.
2. **Commit point:** the same `gate`/`proceed` step as a save (§4).
3. A staging record `local/staging/<job>.json` naming the staging folder `<target>.ft-staging-<job>` (beside the target, on the same volume) is written as `.part`, synced, renamed and its directory synced.
4. The staging folder is created and filled; every file and directory synced.
5. The staging folder is renamed to the target name with an exclusive rename (`RENAME_EXCL` / `RENAME_NOREPLACE`), which fails if the name is taken: then the staging folder is removed and the user reads "A folder with this name already exists. Nothing was changed." The parent directory is synced.
6. The new workspace is registered (INV-29: after `assess_workspace` says `READY`), the staging record removed.
**Recovery:** recover-all removes a staging folder its record names, if it still exists, then the record. A record whose target exists and holds the complete folder (the rename happened, the registration did not) is removed with the notice "Your new workspace at <folder> was created. Open it with Open folder…". A partial folder is therefore never visible under the target name and never registered. [model 2 mutants `direct_write`, `no_record`, `plain_rename`]

## 9. The switch, Reopen, quit and boot

### 9.1 The switch (and Reopen)

ADR-0063 decision 1's steps, with this spec's jobs:
1. The leave guard (frontend), then the target is assessed read-only; a target that needs the user shows its screen with Stay, and nothing changes.
2. **Quiesce the current workspace A:** the open-workspace record gets `switching`, so new requests naming A get `WORKSPACE_NOT_OPEN {switching}` (INV-28). Queued jobs of A that lose nothing are dropped: previews, rebuilds, BQL, `r4` (its record stays on disk), background saves (prices fetch again later); their requests get `WORKSPACE_NOT_OPEN {switching}`. Queued **confirmed** saves of A stay and run. The running job: any save past its commit point finishes, and a confirmed save before it finishes too; a background save before its commit point and any other job are ended (a `cancel`, not a stop; they lose nothing). [ADR-0063 decision 1 step 3: "closing is not a cancel"; owed R4 "stays durably recorded"]
3. The user reads "Just a moment… switching workspaces."; while A's saves finish, "Finishing your change before switching…". **Stay** cancels the switch until A has closed; A stays open and its dropped jobs are simply gone (their requests already ended). A save past its commit point is never ended by the switch.
4. **A closes** when none of its jobs is queued or running: its container, caches and handles are released (INV-31); its folder is not written (INV-30).
5. `recover_all` (§8) runs while no workspace is open; app-scoped jobs wait (§3).
6. **The target opens** by the normal path: assessment again on the recovered files, the `open` job if the fingerprint differs, "last opened" recorded only on `READY`/`WARMING` (INV-29), and an `r4` queued if the target has a closed record. A failure after A closed offers **Back to A**, which opens A by the same path.
At no moment do two workspaces accept requests or run jobs. **Reopen** is this switch with A as its own target. [INV-31; ADR-0063]

### 9.2 Boot

The app-instance lock (ADR-0062), then `recover_all`, then the open of the last workspace (or the one asked for). App-scoped requests wait until the boot has ended (§3). [INV-32; ADR-0062 decision 3]

### 9.3 Quit

1. The window's unsaved guard (failure-modes W7).
2. The same quiesce as §9.1 step 2, for the open workspace, with no target.
3. While saves finish: "Finishing your last change…" with **Quit anyway**. Its confirmation names what is lost: "Quit now? Your change that is being saved will be undone the next time Finzytrack starts. N more changes waiting to be saved will not be saved. Your ledger stays safe." Quit anyway removes queued saves (nothing written) and ends a running job, before or after its commit point (a save's journal or a create's staging record covers it; the next start recovers it). A quit during a switch's `recover_all` ends it the same way; the next start runs it again.
4. `close()` signals the engine's group once and the sidecar exits **without waiting for the reap**: an engine stuck on a dead disk cannot hold the quit. Its locks go when it finally ends; until then, the next start's recovery finds a live writer and leaves that journal blocking with Retry (§8.1). [ADR-0063 decision 1 step 4: every wait has an exit; review 5 M4]
An OS logout or shutdown that ends the engine past its commit point is a crash; the engine does not delay SIGTERM, and its parent-death watchdog ends its group even in the middle of a save (§5.2), since the journal makes every cut safe (this answers BL-0091's SIGTERM and parent-death questions). An installer never runs while a `.open` journal exists (ADR-0012). [failure-modes M7, S7; ADR-0063 decision 1 step 4]

## 10. What survives a crash at each step

"Engine" means only the engine died (the session goes on); "app" means the sidecar died too (the engine follows within 0.25 s on POSIX, at once on Windows) or the computer restarted; "power" is a power loss, after which only synced steps count.

| The save had reached | Ledger files | Record | Engine death in session | App crash or power loss, then the next start |
|---|---|---|---|---|
| before the commit point (planning, locks, gate, load, re-check) | unchanged | none | "Not saved. Your ledger is unchanged." (after re-hashing) | nothing to recover |
| backups written | unchanged | none | as above; backups are kept like any backup | nothing to recover |
| journal being written (`.part`), not yet `.open` | unchanged | `.part`, possibly torn by a power loss | as above | recover-all deletes the `.part`; nothing blocks |
| journal durable, no replace yet | unchanged | `.open` | "Could not finish your change", **Reopen**; recovery finds every file at pre | recover-all resolves it, notice |
| some files replaced | each file whole: pre or post | `.open` | as above; recovery restores pre | as above. After a power loss a replace whose directory was not yet synced is simply pre |
| all replaced and read back, close not yet durable | post | `.open` | as above: **not saved**, put back. The user was never told "saved" | as above |
| close durable (`committed`) | post | `.closed` | **saved**; a `rebuild` if the publish did not land; R4 owed | saved; R4 runs at the open, before any save |
| published | post | `.closed` | saved; R4 owed | as above |
| R4 ended | post | `.done` or `.mismatch` | as recorded | as recorded |

| a `create` or `copy` before its commit point | nothing written | none | "Not created." | nothing to recover |
| a `create` or `copy` filling its staging folder | the target name untouched | staging record | "Not created." | recover-all removes the staging folder and the record |
| renamed to the target, not yet registered | the complete folder at the target name | staging record | the registration finishes in the job; if the engine died first, as the next column | the record is removed, and the notice offers Open folder… |

A crash during a recovery or a rollback leaves the journal `.open` with each file at pre or post, so the next recover-all finishes it; a crash or Cancel of `recover_all` on one journal leaves that journal blocking and the others are still tried (§8.1). A crash with an outside save in flight leaves foreign bytes in the file or in its temp: the journal stops and the user sees both versions (§6.4). In every row the reported outcome matches the disk after the next start: a power loss never removes a save the user was told about, because "saved" is sent only after the close is durable.

## 11. Messages and codes

New codes join the registry of ARCHITECTURE §6.1; the existing ones keep their meaning. **"Your ledger is unchanged" is said only after the targets were hashed again and equal their pre bytes** (ADR-0008, ADR-0012 F-2), whatever the path: Cancel, refusal or engine death; otherwise the message says which file changed.

| Situation | Message (plain text the user reads) | Actions | Code or result |
|---|---|---|---|
| a job queued or running | its phase (§5.4), then "Still working…" | Cancel where §4 allows | — |
| save cancelled before its commit point | "Your change was not saved. Your ledger is unchanged." (by the rule above) | — | `JOB_CANCELLED` |
| rebuild cancelled | "Your views may be out of date." | Update now | `JOB_CANCELLED` |
| Cancel after the commit point | "Finishing your change…", then "Your change is saved." | — | result `saved` |
| save landed | (the edit shows as saved) | — | result `saved` |
| a read job's engine stopped | "The ledger engine stopped while working on this. Your ledger files are safe." | Try again, Copy diagnostics | `ENGINE_STOPPED {crashed}` |
| stopped by the system | failure-modes E4's text | Try again, Copy diagnostics | `ENGINE_STOPPED {killed_by_system}` |
| bound reached | failure-modes E1's text | Try again, Copy diagnostics | `ENGINE_UNAVAILABLE` |
| a save's engine stopped, outcome found | "Your change was saved." / "Your change was not saved. Your ledger is unchanged." / "…not saved, and a file was changed by another program." | — / — / Review | result |
| unfinished save in this session | "Finzytrack could not finish your change. Reopen the workspace so Finzytrack can put your files back." | Reopen | `WRITES_PAUSED {unfinished_save}` |
| a save waits for R4 | "Checking your last change first…" | Cancel | — |
| a save's R4 could not finish | "Finzytrack could not check your last change, so it did not save this one. Your ledger is unchanged." | Try again, Copy diagnostics | `WRITES_PAUSED {check_pending}` |
| R4 mismatch | "Finzytrack's check of your last change found a difference. Saving is paused. Your backup is here." | Show backup, Resume saving, Copy diagnostics | `WRITES_PAUSED {check_failed}` |
| R4 nondeterministic | "Your ledger's plugins give a different result each time they run. Finzytrack updated your views from a fresh load." | Learn more | notice |
| ledger moved since the preview, or during the save's load | "Your ledger changed after this preview, so Finzytrack did not save your change. Review it again." (it may be Finzytrack's own price update, so it does not blame another program) | Review | `LEDGER_EXTERNALLY_CHANGED` |
| outside save during the commit | "Your editor saved this file at the same moment. Finzytrack did not save your change. Review this file." | Review | `LEDGER_EXTERNALLY_CHANGED` |
| the ledger keeps changing | "Another program keeps changing your ledger. Finzytrack will update when it stops." | Try again | `LEDGER_CHANGING` |
| legacy app saving | "The old Finzytrack app is saving. Quit the old Finzytrack first, then try again." (ADR-0062) | Try again | `LEGACY_APP_SAVING` |
| queue full | "Finzytrack is busy with other ledger work. Please try again in a moment." | Try again | `BUSY_HEAVY_LOAD` |
| a process that cannot be ended | "Finzytrack is waiting for a disk and cannot stop this yet. It will continue when the disk answers." | Copy diagnostics | — |
| switching | "Just a moment… switching workspaces." / "Finishing your change before switching…" | Stay (until A has closed) | — |
| recovery done | "Finzytrack undid an interrupted save in *Personal*." | — | notice |
| blocking journal: could not be undone | ADR-0063's text | Retry, Stay or Back, Keep the files as they are now | `RECOVERY_BLOCKED {path, workspace, reason: stopped}` |
| blocking journal: recovery cancelled, failed, or not tried because the engine is not working | "accounts.beancount has an unfinished save from *Personal* that Finzytrack has not undone yet." | Retry, Stay or Back, Keep the files as they are now | `RECOVERY_BLOCKED {path, workspace, reason: cancelled \| failed \| not_tried}` |
| create or copy onto an existing folder | "A folder with this name already exists. Nothing was changed." | Choose another name | refused |
| created, not registered (after a crash) | "Your new workspace at <folder> was created. Open it with Open folder…" | Open folder… | notice |
| request for a workspace that is not open | ADR-0063's text | Switch back | `WORKSPACE_NOT_OPEN {open_workspace, switching}` |
| quitting during a save | "Finishing your last change…" | Quit anyway | — |

## 12. The decision log

The owner's requirement (2026-10-26: "Yes, add it to the scheduler spec as a requirement. Let's make sure this logging system design is documented and implemented"): every scheduling decision can be rebuilt from a user's log, which holds no ledger content.
- **Pure tables:** `queue` (dispatch, Cancel, the commit-point step, quiesce), `switch` (§9), the engine supervision (S0040, amended by §5.5) and `save_outcome` (§6.6) run in the sidecar on the one scheduler thread; each is `(state, event) -> (state, effects)` with no clock, random value, environment or global (times arrive inside events) and carries a `table_version`. The engine's decisions are pure functions too: the gate's branch, the recovery check per file, R4's outcome, the legacy-lock outcome. Each engine reply that carries one adds `decision {table, version, inputs, output}`. [the owner's requirement]
- **Content-free lines** (S0039's redaction applies): `sched.input` before each table step (the event: job id and kind, waited-for or background, the workspace's id (never its path), outcome names, counts, which check failed, hash *equalities* such as "file 2 holds post"; never a path, file name, amount or hash value), `sched.decision` after it (effects and a digest of the state), `sched.checkpoint` (the tables' state) at start, every 1,000 inputs and first in each rotated log file. Engine decisions are logged as `sched.input` lines with table `engine.<name>` before the sidecar acts on them. The sidecar's own disk reads (records listed, outcome hashes compared) are events too. Ordered collections only.
- **Replay test in CI:** every scenario test of §14 is also a recording, replayed through the same tables with no divergence and no unexplained input, under three hash seeds; a Hypothesis walk checks `replay(record(walk)) == walk`.
- **Flight recorder:** a ring of the last 4,096 events or 1 MiB, written to the log on an error event and on `ENGINE_STOPPED`.
- **Volume:** with no liveness ticks, a job is a few lines (about 2-4 KB); a heavy day of about 200 jobs is under 1 MB.

## 13. What the TLA+ model must check

Built only from this spec once it is golden, and run by the owner as a plain script (PROCESS.md §3). Start from the private `tla/v3/`, cut to one engine.
- **World (small):** two workspaces whose ledger sets share one file, and a third that shares none; a save that replaces one file and creates one, in a ledger set with one more include it does not write; two or three journals left by crashed saves; one `create` with its staging folder; per file the bytes on disk and, separately, what survives a power loss; backups, temps, journal and record states; the queue; one engine slot.
- **Actors:** the user (preview, save, BQL, Cancel, switch, Stay, Reopen, quit, Quit anyway, Keep the files as they are now, Resume saving); the app (queue, switch, outcome check); the engine (the job steps of §6-§8); the OS (engine crash, app crash, power loss); an outside editor (writes any file, durably, at any time: during a load, between a recovery's check and its swap, during a rollback); a path changer (replaces a folder by a link after a crash); an orphaned engine that outlives its app for a few steps.
- **Safety:** `AtMostOneEngine`; `AtMostOneJobRunning`; `AtMostOneOpenWorkspace`; `OnlyOpenWorkspaceRunsJobs`; `WaitedForFirst`; `NoOwnEndAfterCommitPoint` (except Quit anyway); `CancelBeforeCommitLeavesDiskUnchanged`; `OutcomeTruthful` (every reported outcome matches the disk, also after the next start); `SavedSurvivesPowerLoss`; `NoTornLedgerAfterRecovery`; `RecoveryNeverClobbersForeignBytes`; `RecoveryWritesOnlyRecordedPaths`; `NoOpenOverUnresolvedJournal` (INV-32); `BlockedOnlyForCause` (foreign bytes, a moved or unreachable path, a live writer, an unreadable journal, a recovery cancelled or failed or not tried); `EveryJournalRecoveredOrBlocking` before any open; `CommittedCombinationChecked` (no outside edit made during the load lands beside a commit); `NoPartialFolderAtTarget` and `NoStagingLeftAfterRecovery`; `PartJournalNeverBlocks`; `NoSaveWhileR4Owed`; `ExternalEditNeverSilentlyLost`; `BackupDurableBeforeReplace`; `JournalDurableBeforeFirstReplace`.
- **Liveness** (weak fairness on engine and app steps): every waited-for job ends (answered, refused or cancelled); every switch ends (target open, Back, or Stay); every quit ends; an owed R4 runs or is superseded before the next save lands. **`EveryWaitHasAnExit`:** in every state where the user waits, a user action that ends the wait is enabled.
- **Seeded bugs, each must fail its property:** the twenty-one mutants of the two executable models below, plus: dispatch by arrival only (`WaitedForFirst`); the switch drops a queued confirmed save (`OutcomeTruthful`); recovery while a workspace is open (`OnlyOpenWorkspaceRunsJobs`); a second engine spawned before the first is reaped (`AtMostOneEngine`).
- **Already checked by two small executable models** (session scratchpad, not committed). Model 1: the save of §6.1, the rollback of §6.4, recovery of one journal and the outcome check of §6.6; 2,533 crash, cancel, outside-edit, link and recovery-crash scenarios, 0 violations; its mutants, each caught: `report_before_close_sync`, `no_journal_sync`, `no_backup_sync`, `no_temp_fsync`, `gate_in_memory`, `no_path_guard`, `recover_target_only`, `cancel_after_commit`, `resolve_before_restores_synced`, `no_temp_check`, `plain_create`, `temps_after_close`. Model 2: recover-all over three journals with Cancel, crash, the stop bound and an outside write between check and swap on each; journal creation under a power loss that keeps directory entries before data; an outside edit at each point of the save's load; `create` with crash or power loss at every step, onto a free or a taken name; 218 scenarios, 0 violations; its mutants, each caught: `cancel_stops_all`, `crash_unmarked`, `untried_unmarked`, `no_displaced_check`, `direct_open`, `no_recheck`, `direct_write`, `no_record`, `plain_rename`. Model 2 treats a restore swap and a created file's removal as one checked step.
- **Not modelled:** Beancount semantics (the corpus and the Hypothesis model, ARCHITECTURE §8), digests beyond equal or not, OS rename atomicity beyond the stated swap and exclusive-create primitives.

## 14. Tests the slice must have

Unit tests run on macOS; the write path's OS parts on Linux and Windows from M1b (BL-0087).
- **Queue:** the exhaustive (state × event) test of `queue.transition` and `switch`; waited-for jobs start before older background jobs; a running job is never interrupted to start another; jobs of a workspace that is not open are refused with `WORKSPACE_NOT_OPEN`; `BUSY_HEAVY_LOAD` beyond `max_queued`; during a switch only its own jobs start, and a create requested meanwhile starts after the target's open began.
- **Cancel:** a queued job is removed; a running read ends its engine and is not counted as a stop (three Cancels in a row leave the engine `Ready`, not `Unavailable`); a save, a `create` and a `copy` cancelled before `gate` leave every file and the journal directory byte-identical; a Cancel arriving in the same step as `gate` wins, one arriving after `proceed` is "too late" and the save finishes; Cancel of a job queued behind a hung background job frees the engine; a client disconnect cancels a preview and never a save.
- **Engine:** at most one engine process at a time (a second job waits for the reap); an engine that ran user code is replaced, one that only parsed is reused; the spawn has `-s -S -B -P`, no `-I`, `PYTHONHASHSEED=0` and no `FT_*`/other `PYTHON*` variable, and two engines give the same `hash()` of a string; a plugin's `__pycache__` is never written; a plugin's child process holding a ledger lock is ended with the engine and the lock is free; the sidecar ended hard while a plugin's child holds a ledger lock: the watchdog ends the engine's whole group within its interval and the lock is free; "no live member" with a zombie leader and a live grandchild (the drain goes on) and with only the zombie leader (macOS `EPERM`, Linux `/proc` state `Z`: the drain ends); no group signal after the reap (a fake process port records every signal); `close()` and Quit anyway return while a fake port's reap never returns; each row of §5.3's table, with its stop count; a violation after the commit point is read and discarded until the exit.
- **Save, durable order:** fault injection at every step of §6.1 step 8 and a simulated power loss after every step, including one that keeps directory entries before their data (a torn `.part` is deleted and blocks nothing) (an in-memory file system with a separate durable layer), each followed by recover-all: the ledger is all pre or all post, and the outcome told matches; "saved" is sent only after the close's directory sync; an exclusive create refuses a file that appeared; a displaced outside save at the swap is kept and reported; a crash right after such a swap leaves the journal stopped with both versions; the path guard refuses a parent replaced by a link between the journal and the replace (macOS and Linux); an outside edit to an include the save does not write, made during the load, is refused at the re-check with nothing written; an outside write between a rollback's check and its swap is put back and the journal stops.
- **Records and gate:** the gate re-reads the directory (a closed record written by another process, or left by a crashed session, pauses the save); the save runs an owed R4 first; `.mismatch` pauses saving until Resume saving; history is trimmed to 16.
- **R4:** each outcome (`pass`, `superseded` by an outside edit, by a plugin source edit and by a new engine build, `nondeterministic`, `mismatch`), the confirm in a fresh engine that runs no user code, a file-creating save ends `pass` (its created file's `path_key` is the same before and after).
- **Outcome after an engine death:** each branch of §6.6 with the message, including "unchanged" only after the re-hash.
- **Paths:** a corpus per OS of case, NFC/NFD, a symlink, a symlinked directory and an absent path; the key of an absent path equals the key of the file after it is created under any spelling the volume treats as equal.
- **Recovery:** BL-0103's list (INV-32), plus: recovery cut at every step and run again; with three journals, Cancel on the first: it blocks only the workspaces and copies using its files, the other two are still recovered, and a workspace using none of them opens; the same with an engine crash on the first, and with a journal that crashes recovery every time (it blocks, the stop bound is reached, the untried ones block with `not_tried`, and "Keep the files as they are now" is offered for each); an outside write between a recovery's check and its restore swap, and during a created file's removal, is put back and the journal stops.
- **Create and copy:** crash and power loss at every step of §8.2: a partial folder never appears under the target name and is never registered; recover-all removes the staging folder; a complete folder renamed but not registered gets the notice; an existing folder at the target name is refused and left byte-identical.
- **Switch, quit, boot:** INV-28/INV-31's switch and quiesce tests (BL-0102); a running save finishes across a switch and Stay keeps A open; Quit anyway during a save past its commit point, then the next start recovers it; Quit anyway's confirmation counts the queued saves it drops; boot runs recover-all before the first open.
- **Legacy lock:** ADR-0062's detection test (held, free, absent, an error refuses; locks deduplicated by `path_key`; the Copy re-check).
- **Decision log:** the replay test of §12 over every scenario above; the flight recorder dumps on `ENGINE_STOPPED`.

## 15. Honest limits

- **Speed:** with R4 off the "saved" path, spike S4 measured 3.0 s p95 for a 54k ledger in 12 files; one 20 MB file took 8.7 s with R4 inline and is expected to stay above ADR-0008's 5 s until the bounded parse cache of ADR-0008's amendment is built and measured (BL-0082 measures first). A user action right after a save may wait for that save's R4 (about 2 s at 54k).
- **One job at a time:** a BQL query waits behind a running save or rebuild.
- **Lock-ignoring programs** (an editor, Fava, a sync client) are detected (ADR-0013), not excluded.
- **A process that cannot be ended** keeps the engine and its locks until the disk answers.
- **Files a plugin reads by itself** are not in the fingerprint or the ledger set: a change to one is not detected and shows only after the next load that publishes (a save or a rebuild), and a journal naming one does not block a workspace whose plugin reads it (ADR-0063).
- **An orphaned engine** of a crashed session, with its whole group, can hold a lock for up to its watchdog's 0.25 s after a relaunch (longer if it is stuck on a disk); a recovery that meets it leaves that journal blocking, for Retry.
- **An outside edit after the re-check of §6.1 step 6** to a file the save does not write is the same as an edit made right after the save: the publish check and the next load see it (§7).
- **A job's CPU** misses plugin children the engine never waited for (§5.2); it is a measurement only.
- **Windows:** the swap, the path guard, the Job Object and `durable_sync` are designed for it and tested from M1b (BL-0087).

## 16. Open questions

None. The decisions this draft takes beyond the owner's words are marked "(agent)" in ADR-0064 and listed there.
