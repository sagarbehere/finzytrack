# MirrorPublish.tla and the mirror publish spec, both ways

- **Spec:** `design/specs/mirror/publish.md` (S0085). `§n` below is a section of that file; the engine-jobs spec is cited as "engine-jobs n.n".
- **Model:** `MirrorPublish.tla`, one module, durability only (EP-19): BL-0113's two properties and nothing else. Its own module, not an extension of `formal/tla/engine-jobs/`: the two share no state but "one engine job at a time" (here `ctl.pc`) and the `.open` journal (here `jopen`); the save's durability is engine-jobs' model, and the publish's world (generations, a link, readers, a purge) would multiply that model's states for nothing. BL-0110's row already said so.
- **Configurations:** `gen_cfgs.py` (`cfg/*.cfg`, `configs.txt`); `run_all.sh` runs them (HOW_TO_RUN.md).
- **State:** the small tier ran to completion on the model of S0085's round 1 fixes, all as expected: 4 base worlds pass, 7 mutants each break their property, 11 witnesses are each reached. The medium and large tiers are the owner's (BL-0110).
- **Code:** built in S0087 (BL-0113). Each function named in §2.1 carries `@tla_action("<Action>", model="mirror-publish")` (the engine's `ledger_engine.tla`, the sidecar's `app.mirror.model_map`); `backend/tests/architecture/test_tla_mapping_mirror_publish.py` checks the map both ways (every engine and reader action marked or codeless with its reason: `R_End`). Since S0091 (BL-0210) the marked engine functions are walked by a replay of this model's traces (section 5 below), as S0060's rule asks; since S0100 (BL-0225 F2) the open's `ledger_engine.ledger.opening.run` too (every second publish job), except `ledger_engine.mirror.store.SqliteStore.build` (`P_Create`, `P_Commit`; SQLite does not run on the in-memory fake: walked by `engine/tests/mirror/test_publish.py`), and the `.open` branch of `publish`'s re-check (`P_JCheck`), which no trace reaches in the code: the model's `SaveCut` runs only between jobs (one job at a time), so its job meets an `.open` journal only from its start, where the code's rebuild and open pause before their load (walked by `engine/tests/mirror/test_publish.py`, the re-check `paused`); the crash, power-loss and I/O-error sweeps at every effect (`engine/tests/mirror/test_publish_crash.py`) stay beside it.
- `scripts/tests/test_tla_mirror_publish.py` keeps this file honest: every action in `Next` has a row in §2.1 with a rule of the spec and a code function (or "environment"), every row names an action, every `§n` here exists in the spec, and the properties and mutants are the spec's §10, both ways.

## 1. Spec → model

### 1.1 Rules

| Spec rule | In the model |
|---|---|
| §2: the folder is ours; the folder itself; one descriptor, never through a link; the build's `mode=rw` open and identity check | not modelled (§3 below): the folder always holds only names of the model |
| §2: only the engine writes it, one job at a time | `ctl.pc`: a publish (`StartPublish` … `P_Swap`) or a save (`SaveCut`), `Recover` only when no job runs |
| §3: `gen-<N>-<tag>.db`, its journal; a name never used twice | a name is an element of `Gens` (`gens[g].c` the database file, `gens[g].jl` its journal); `NextGen` takes a name never in `used`, ordered after every name there is |
| §3: `N` orders generations; the floor | the order of `Gens`; the floor is not modelled |
| §4: every try is its own job, starting at step 1 | a moved or refused build ends the job (`ctl' = NoCtl`); the next `StartPublish` begins with the cleanup |
| §4 step 1: the cleanup first in the job | `StartPublish` → `P_CleanDb`, `P_CleanJl`, `P_CleanDone` |
| §4 (the load) | `P_Obs` (the observation the build is stamped with), `P_Read` (what the rows come from) |
| §4 step 3: the build, exclusive create, one transaction | `P_Create` (the file and its journal, or the job ends when a file is at the name), `P_Commit` (whole, journal deleted, nothing synced) |
| §4 step 4: never incomplete | `P_Check` |
| §4 step 5: the file synced | `P_SyncFile` |
| §4 step 6: the folder synced (availability) | `P_DirSync`; no property depends on it (no mutant: MAPPING §4.1) |
| §4 step 7: checked again, no `.open` journal | `P_ReObs`, `P_JCheck` |
| §4 step 8: the temporary link renamed over `current.db`; "published" | `P_Swap` (the modification time and the two operations as one step: neither the time nor the temporary link is read by a reader); the rename stays pending in `lkpl` until a later `P_DirSync` |
| §5: the stale-journal rule | names are never reused (`used`), so no journal of another file ever stands at a new name; SQLite is not asked to discard one |
| §6: kept: current and previous (whole, complete, no journal); everything else may go | `Keep`, `Prev`; `P_CleanDb` may remove any other file, `P_CleanJl` any other journal, in any order; any subset (the grace period, the cap, a failed removal) |
| §6: a journal beside a kept generation is left | `P_CleanJl` only for `g \notin Keep` |
| §7: resolve once, pin the name, open it by name | `R_Resolve` (one `readlink`), `R_Read1`, `R_Read2` (each opens whatever file is at the pinned name now: `Opens`) |
| §7: a reader refuses a file with a hot journal | `Opens`: `~gens[g].jl` and not `PART` |
| §7: an open connection keeps its file after removal or a purge | the snapshot taken at `R_Read1` (`rd[r].s1`) never changes |
| §7: an outside tool outlives the app's crash | `OutReaders`; `AppCrash` keeps their requests |
| §7: the save's gate | a reader (resolve once, read `meta`); its freshness comparison is not modelled (§3 below) |
| §8: `meta` names the state the rows came from | the content's `lab` (from `P_Obs`) beside its `rows` (from `P_Read`) |
| §9: deaths, power loss, the OS purge | `EngineCrash`, `AppCrash`, `PowerLoss` with the `pl` and `lkpl` sets, `Purge` |
| §1, engine-jobs 6.5: committed ledger state | `hist`: every disk state while no `.open` journal exists; `SaveCut`, `Recover`, `Editor` |

### 1.2 Properties (§10)

| Property | Formula | What it rules out |
|---|---|---|
| `ReadersSeeCompleteGeneration` | INV: every accepted read's snapshot has kind `done` | a reader of a file being built, of an incomplete build, of torn bytes |
| `SnapshotIsSomeCommittedState` | INV: an accepted `done` snapshot's `rows` is in `hist` and equals its `lab`; a request's second snapshot equals its first | a mirror of a half-done save, of files read at different moments, a `meta` naming another state, a request reading two generations, a pinned name that leads to another build |

## 2. Model → spec

### 2.1 Actions

| Action | Rule | Code |
|---|---|---|
| `StartPublish` | §4 (an `open` or `rebuild` job starts) | `ledger_engine.ledger.rebuild.run` and the open's `ledger_engine.ledger.opening.run` (S0088), each of which opens the folder, cleans it, loads and calls `ledger_engine.mirror.publish.publish` |
| `P_CleanDb`, `P_CleanJl`, `P_CleanDone` | §6, §2 | `ledger_engine.mirror.publish.clean` |
| `P_Obs` | §4 (the load), engine-jobs 6.7 | `ledger_engine.ledger.fingerprint.observe`, in `ledger_engine.ledger.rebuild.run`; in `ledger_engine.ledger.opening.run` the overlay load's own fingerprint (`P_Obs` = `P_Read`) |
| `P_Read` | §4 (the load) | `ledger_engine.ledger.beancount_port.real_load`, in `ledger_engine.ledger.rebuild.run`; `ledger_engine.ledger.overlay.load`, in `ledger_engine.ledger.opening.run` |
| `P_Create` | §3, §4 step 3, §5 | `ledger_engine.mirror.publish.new_name`, then `ledger_engine.mirror.store.SqliteStore.build` (the exclusive create through the folder's handle, SQLite's `mode=rw` open, the identity check) |
| `P_Commit` | §4 step 3 | `ledger_engine.mirror.store.SqliteStore.build` (`ledger_engine.mirror.build.write`: one transaction) |
| `P_Check` | §4 step 4 | `ledger_engine.mirror.publish.publish` (refuses `Built.complete` false) |
| `P_SyncFile` | §4 step 5 | `ledger_engine.mirror.publish.publish` through `ledger_engine.fs.port.DirHandle.sync_file` |
| `P_DirSync` | §4 step 6 | `ledger_engine.mirror.publish.publish` through `ledger_engine.fs.port.DirHandle.sync` |
| `P_ReObs` | §4 step 7 | `ledger_engine.ledger.fingerprint.observe`, called by `ledger_engine.mirror.publish.publish` (the rebuild's re-check) |
| `P_JCheck` | §4 step 7 | `ledger_engine.ledger.save.records`, called by `ledger_engine.mirror.publish.publish` (the rebuild's re-check) |
| `P_Swap` | §4 step 8 | `ledger_engine.mirror.publish.swap` (the port's `touch`, `symlink`, `rename`) |
| `SaveCut` | §1, §4 step 7 | `ledger_engine.ledger.save.run` ended by an engine death |
| `Recover` | §1 | `ledger_engine.ledger.restore.recover_journal` |
| `R_Resolve` | §7 | `app.mirror.files.pin`; the gate's `ledger_engine.mirror.publish.read_published` |
| `R_Read1`, `R_Read2` | §7 | `app.mirror.files.open_mirror` on the pinned path |
| `R_End` | §7 | the request's end (`app.mirror.files.pin`'s scope; no code: the map's `CODELESS`) |
| `Editor` | §11 (A-B-A), §1 | environment: another program writes a ledger file |
| `EngineCrash`, `AppCrash`, `PowerLoss`, `Purge` | §9 | environment: the OS |

### 2.2 Variables

| Variable | Meaning |
|---|---|
| `disk`, `nv` | the ledger: one version per file; the next fresh version (an outside program never writes back earlier bytes, §11) |
| `jopen`, `jpre`, `jpost` | a cut save's `.open` journal, its pre versions, the version it wrote into file 1 |
| `hist` | ghost: the committed ledger states (§1) |
| `gens` | per generation name: content `c` (`abs`, `part`, `inc`, `torn`, `done`, with the `rows` and `lab` states), journal `jl`, power-loss outcomes `pl` |
| `used` | the names ever created: the tag of §3 makes each new name one not in it |
| `lk`, `lkpl` | the generation `current.db` names (0: none) and what a power loss may leave |
| `ctl` | the publish job: its step, its name, its stamp (`lab`), its rows |
| `rd` | per reader: idle, resolved, one read, two reads; the pinned name; the snapshots |
| `n`, `gh` | bounds; ghost flags for the witnesses |

## 3. What the model abstracts away

- **The folder's own shape** (§2): a link at `mirror/` or inside it, the folder descriptor, the build's `mode=rw` open and identity check, unknown files and folders, the temporary link, `current.db-*` names. They protect files outside the folder, which is not a durability property of the mirror; readers resolve `current.db` and open generation names only. S0087's tests check them (`engine/tests/mirror/test_publish.py`, must-not-pass: a link at `mirror/`, a link and a folder inside it, `current.db` a file or a folder, `mirror/` swapped for a link during the job and just before SQLite's open).
- **Time and the cap** (§6): the cleanup may remove any file it does not keep at any moment of its step, which covers every timing and every cap. A removal under a reader is shown harmless by `W_ReadDeleted`, a purge under a reader by `W_ReadAcrossPurge`.
- **The floor** (§3) and the freshness check (§8): they keep `ledger_version` ordered and decide when to build; neither changes what a reader reads. The model orders names by their creation, which equals their order by `N` among the names present, since a purge removes every name at once.
- **The gate's comparison** (§7): the published generation fresh against the disk on its own set, beside the disk equal to the plan. It decides whether a save may go on; it changes nothing a reader reads, and the model's fingerprints are whole ledger states with no sets. S0087's tests in `engine/tests/ledger/test_save.py`, listed in §7, cover it.
- **Availability:** a read that finds no file, or a refused one, ends its request (`NoRd`); "try again", the open's rebuild, and §4 step 6's requirement are tests of the code.
- **The load:** two files with version numbers. The open's overlay load (stamped with the bytes it parsed) is the case `P_Obs` = `P_Read`; the rebuild's real load (stamped with its observation before the load) is the general case modelled.
- **SQLite** is reduced to: one transaction whose commit deletes the journal; a read-only open that refuses a file a build is still writing or a file with a journal beside it; a read of whatever file is at the opened name. No assumption about stale journals is needed: names are never reused.
- **The purge** is modelled as the whole folder going at once, durably; a job running then is ended, since its later writes go into a folder nobody can reach.
- **The `.open` journal and recovery** are reduced to one cut save of one file; their durability is engine-jobs' model.
- **Windows** (BL-0206).
- **The rebuild's own steps around the publish** (S0091, met by the replay of section 5): the rebuild looks for this workspace's `.open` journal right after the cleanup and ends `paused` before its load (engine-jobs 7, S0088), where the model's job builds a generation and refuses it at `P_JCheck`; it skips the build when the published generation is fresh (§8); it observes the whole ledger set before its load and again after it, and ends `moved` there when the disk changed, where the model observes file by file, reads, and finds the change at `P_ReObs` after its build. Each refuses earlier and builds less than the model, never more: what a reader reads is the same. The replay runs each job on another day (no generation is fresh) and its worlds make no outside edit inside the load (`gen_traces.py` `edit_outside_the_load`).
- **The model's cleanup may stop anywhere; the code's removes everything it does not keep** once the grace period is over (§6). The replay runs every job long after the last one and walks only cleanups that remove all they may (`whole_cleanup`); a cleanup cut by a death or an I/O error is `test_publish_crash.py`'s.
- **A world runs out of names** (`P_Create` ends a job when `Gens` is used up); the code's names have no such bound. The replay's worlds start a job only while a name is left (`names_left`).

## 4. Mutants and witnesses

### 4.1 Mutants (each on a small world, checking only the property it must break)

| Mutant | World | What it changes | Breaks |
|---|---|---|---|
| `no_file_sync` | `S_power` | §4 step 5 skipped: a power loss after the swap leaves `current.db` on torn bytes | `ReadersSeeCompleteGeneration` |
| `publish_incomplete` | `S_publish` | §4 step 4 skipped | `ReadersSeeCompleteGeneration` |
| `build_in_place` | `S_publish` | ADR-0015's rejected alternative: the live file rebuilt in place | `ReadersSeeCompleteGeneration` |
| `no_tag` | `S_purge` | §3's names without the tag: after the OS purges the folder, the next build reuses a number, and a request that pinned the old name opens the new build before it was checked (its rows from an edit, its `meta` from before it) | `SnapshotIsSomeCommittedState` |
| `no_recheck` | `S_ledger` | §4 step 7's observation skipped: the rows and the stamp come from different states | `SnapshotIsSomeCommittedState` |
| `no_open_check` | `S_ledger` | §4 step 7's `.open` check skipped: a half-done save is published | `SnapshotIsSomeCommittedState` |
| `reresolve_per_read` | `S_ledger` | §7: the second read of a request resolves `current.db` again | `SnapshotIsSomeCommittedState` |

§4 step 6 (the folder sync) has no mutant: it serves availability only (§4, §12), which no property counts.

### 4.2 Vacuity witnesses (each base world must reach the branch: TLC must report the witness violated)

| Witness | World | Branch reached |
|---|---|---|
| `W_ReadTwice` | `S_publish` | a request read twice |
| `W_ReadOldAfterNew` | `S_publish` | a request holds an older generation while a newer one is current |
| `W_ReadDeleted` | `S_publish` | a request reads a generation whose file the cleanup removed |
| `W_IncompleteRefused` | `S_publish` | §4 step 4 refused a build |
| `W_ReadAfterPower` | `S_power` | a read after a power loss |
| `W_TornLeft` | `S_power` | a power loss left torn bytes in some generation file (never read) |
| `W_LinkReverted` | `S_power` | a power loss put `current.db` back on an older generation while a newer whole file exists |
| `W_ReadAcrossPurge` | `S_purge` | a request reads a generation whose name the OS purge removed |
| `W_OutsideAfterCrash` | `S_purge` | an outside tool reads after the app crashed |
| `W_MovedRefused` | `S_ledger` | §4 step 7 found the ledger moved |
| `W_OpenRefused` | `S_ledger` | §4 step 7 found an `.open` journal |

## 5. The code's conformance (S0091, BL-0210)

`traces/corpus.json` holds TLC-simulated traces of three small worlds (`gen_traces.py`: `S_publish`, `S_ledger`, `S_crash`; the seed, the depth, the actions left out, a power loss and the OS's purge, and the branches each world leaves out, section 3). `engine/tests/mirror/test_publish_conformance.py` replays every trace over the in-memory fake: each publish job is `ledger_engine.ledger.rebuild.run`, and every second one the open's `ledger_engine.ledger.opening.run` (S0100, BL-0225 F2: its overlay load's own fingerprint as the stamp, the model's `P_Obs` = `P_Read`), with the fake as its file system, a store that writes what the fake store writes and the rows the load read, and an observation and a load of the model's two files; a cut save is its `.open` record and its file as the save leaves them, its recovery `ledger_engine.ledger.restore.recover_journal`; a reader's resolve is the gate's `ledger_engine.mirror.publish.read_published`, its reads open the pinned name by its name with the store's `meta`. At each hook of the code the model is walked up to the code's step and the fake compared with it: each generation's content (absent, being written, whole and complete or not, its rows and the state its stamp names), its journal, the link, the ledger. A step passed without a hook must change nothing compared; the file's and the folder's syncs are never passed without the code's. Each job's answer must be the model's (published the generation the model swapped in, refused incomplete, moved, paused, or ended by a death); each reader's resolve must pin the model's generation and each read read the model's snapshot.

- `StartPublish`: each call of `rebuild.run`; `P_CleanDb`, `P_CleanJl`, `P_CleanDone`: the folder made and the cleanup's removals, as one block compared at its end; `P_Obs`: the observation before the load; `P_Read`: the load; `P_Create`: the store's exclusive create of the file and its journal; `P_Commit`: its write and the journal's removal; `P_Check`: the answer `incomplete`; `P_SyncFile`, `P_DirSync`: the file's and the folder's syncs; `P_ReObs`: the re-check's observation; `P_JCheck`: the answer `paused` (in the code, before the load: section 3); `P_Swap`: the replaced generation's time, the temporary link and its rename; `Recover`: `recover_journal`; `R_Resolve`: `read_published`.
- Not walked by the replay: `SaveCut` (a cut save's effects are the engine-jobs save replay's: `engine/tests/ledger/test_save_conformance.py`); `R_Read1`, `R_Read2` (the sidecar's `app.mirror.files.open_mirror`, which an engine test cannot run: the replay reads as it does, and the sidecar's tests are `backend/tests/mirror/`'s); `R_End` (no code); `P_CleanJl` alone (a journal without its file is left only by a death or a power loss inside a build; the block counts it with `P_CleanDb`).

