# MirrorPublish.tla and the mirror publish spec, both ways

- **Spec:** `design/specs/mirror/publish.md` (S0085). `§n` below is a section of that file; the engine-jobs spec is cited as "engine-jobs n.n".
- **Model:** `MirrorPublish.tla`, one module, durability only (EP-19): BL-0113's two properties and nothing else. Its own module, not an extension of `formal/tla/engine-jobs/`: the two share no state but "one engine job at a time" (here `ctl.pc`) and the `.open` journal (here `jopen`); the save's durability is engine-jobs' model, and the publish's world (generations, a link, readers, a purge) would multiply that model's states for nothing. BL-0110's row already said so.
- **Configurations:** `gen_cfgs.py` (`cfg/*.cfg`, `configs.txt`); `run_all.sh` runs them (HOW_TO_RUN.md).
- **State:** the small tier ran to completion on the model of S0085's round 1 fixes, all as expected: 4 base worlds pass, 7 mutants each break their property, 11 witnesses are each reached. The medium and large tiers are the owner's (BL-0110).
- **Code:** nothing of the publish is built yet. "planned (S0086)" names the function S0086 builds (BL-0113); a row whose code exists names it. When S0086 lands, it marks its functions and updates this column.
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
| `StartPublish` | §4 (an `open` or `rebuild` job starts) | `ledger_engine.ledger.rebuild.run` (built; the open's: planned, BL-0152) calling `ledger_engine.mirror.publish.publish` (planned, S0086) |
| `P_CleanDb`, `P_CleanJl`, `P_CleanDone` | §6, §2 | `ledger_engine.mirror.publish.clean` (planned, S0086) |
| `P_Obs` | §4 (the load), engine-jobs 6.7 | `ledger_engine.ledger.fingerprint.observe` (built), in `ledger_engine.ledger.rebuild.run` (built) |
| `P_Read` | §4 (the load) | `ledger_engine.ledger.beancount_port.real_load` (built), in `ledger_engine.ledger.rebuild.run` |
| `P_Create` | §3, §4 step 3, §5 | `ledger_engine.mirror.publish.new_name` (planned, S0086), then `ledger_engine.mirror.build.build` (built: exclusive create; S0086 adds the `mode=rw` open of §2) |
| `P_Commit` | §4 step 3 | `ledger_engine.mirror.build.build` (built: one transaction) |
| `P_Check` | §4 step 4 | `ledger_engine.mirror.publish.publish` (planned, S0086: refuses `Built.complete` false) |
| `P_SyncFile` | §4 step 5 | `ledger_engine.mirror.publish.publish` (planned) through `ledger_engine.fs.port.DirHandle.sync_file` (built) |
| `P_DirSync` | §4 step 6 | `ledger_engine.mirror.publish.publish` (planned) through `ledger_engine.fs.port.DirHandle.sync` (built) |
| `P_ReObs` | §4 step 7 | `ledger_engine.ledger.fingerprint.observe` (built), called by `ledger_engine.mirror.publish.publish` (planned) |
| `P_JCheck` | §4 step 7 | `ledger_engine.ledger.save.records` (built), called by `ledger_engine.mirror.publish.publish` (planned) |
| `P_Swap` | §4 step 8 | `ledger_engine.mirror.publish.swap` (planned, S0086; the file-system port gains a link, `readlink` and `utime`) |
| `SaveCut` | §1, §4 step 7 | `ledger_engine.ledger.save.run` (built) ended by an engine death |
| `Recover` | §1 | `ledger_engine.ledger.restore.recover_journal` (built) |
| `R_Resolve` | §7 | `app.mirror.files.pin` (planned, S0086); the gate's `ledger_engine.mirror.publish.read_published` (planned, S0086) |
| `R_Read1`, `R_Read2` | §7 | `app.mirror.files.open_mirror` (built) on the pinned path |
| `R_End` | §7 | the request's end (`app.mirror.files.pin`'s scope, planned, S0086) |
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

- **The folder's own shape** (§2): a link at `mirror/` or inside it, the folder descriptor, the build's `mode=rw` open and identity check, unknown files and folders, the temporary link, `current.db-*` names. They protect files outside the folder, which is not a durability property of the mirror; readers resolve `current.db` and open generation names only. S0086's tests check them (must-not-pass: a link at `mirror/`, a link and a folder inside it, `current.db` a file or a folder, `mirror/` swapped for a link during the build).
- **Time and the cap** (§6): the cleanup may remove any file it does not keep at any moment of its step, which covers every timing and every cap. A removal under a reader is shown harmless by `W_ReadDeleted`, a purge under a reader by `W_ReadAcrossPurge`.
- **The floor** (§3) and the freshness check (§8): they keep `ledger_version` ordered and decide when to build; neither changes what a reader reads. The model orders names by their creation, which equals their order by `N` among the names present, since a purge removes every name at once.
- **The gate's comparison** (§7): the published generation fresh against the disk on its own set, beside the disk equal to the plan. It decides whether a save may go on; it changes nothing a reader reads, and the model's fingerprints are whole ledger states with no sets. S0086's tests in `engine/tests/ledger/test_save.py`, listed in §7, cover it.
- **Availability:** a read that finds no file, or a refused one, ends its request (`NoRd`); "try again", the open's rebuild, and §4 step 6's requirement are tests of the code.
- **The load:** two files with version numbers. The open's overlay load (stamped with the bytes it parsed) is the case `P_Obs` = `P_Read`; the rebuild's real load (stamped with its observation before the load) is the general case modelled.
- **SQLite** is reduced to: one transaction whose commit deletes the journal; a read-only open that refuses a file a build is still writing or a file with a journal beside it; a read of whatever file is at the opened name. No assumption about stale journals is needed: names are never reused.
- **The purge** is modelled as the whole folder going at once, durably; a job running then is ended, since its later writes go into a folder nobody can reach.
- **The `.open` journal and recovery** are reduced to one cut save of one file; their durability is engine-jobs' model.
- **Windows** (BL-0206).

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
