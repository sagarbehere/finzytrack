# Engine scheduler: concurrency, admission, liveness and cancel

> **DRAFT 4 (2026-10-27), not yet adopted.** History:
> - **Draft 1** (d68ce933) was checked by design review 2 and the first TLA+ model.
> - **Draft 2** (2b770660) was checked by review 3 and the second model.
> - **Draft 3** (a83dd793) was checked by review 4 (review:m1a/scheduler-design/review-4, H1–H5, M1–M7, L1–L3, O1–O10) and modelled as written (review:m1a/scheduler-design/tla/v3).
>
> This draft folds in review 4 and the v3 model's notes, and applies the owner's decision of 2026-10-27: **one Finzytrack instance, one open workspace at a time, no file in two workspaces** (the one-workspace ADR, S0045; the single-instance ADR, ADR-0062). It is the version the long model run checks (§11, §12). A later slice commits it with the amendments of §14. Until then the accepted ADRs win.

## Orientation
- **What this is:** how the main sidecar runs ledger-engine work for the one open workspace, and how it switches workspaces. It covers:
  - which jobs exist, which run together, and in what order;
  - how many engine processes may run, and with how much memory;
  - how a slow job is told from a sick engine;
  - what Cancel does;
  - what the user sees;
  - how a rare failure is replayed from the log.

  It answers the area review's F1/F2 (2026-10-26) and applies the owner's decisions of 2026-10-26 and 2026-10-27 (`design/inbox/answered/20261026-engine-concurrency-and-cancel.md`; the answers to reviews 2 and 3; ADR-0062; the one-workspace ADR).
- **What exists:** none of this yet. Today there is one `EngineClient` per user container, one request in flight, and a caller's time limit that counts as a crash (S0035, S0040, S0042). Every part carries its phase:
  - **[A]**: M1a, the scheduler slice, before the read core;
  - **[B]**: M1a, with the mirror build;
  - **[C]**: M1b, with the write contract and BL-0091;
  - **[M2]**: with BL-0092, BL-0093, BL-0094, BL-0100;
  - **[M3]**: legacy adoption;
  - **[M11]**: Linux and Windows sessions;
  - **[Hosted]**: deferred.

  §1.2 says which [A] parts are stable enough to build now.
- **Entry points (planned):**
  - `app.platform.engine_pool` (pool, budget, pressure, stop history; pure `engine_pool.table`);
  - `app.platform.engine_process` (one engine's statechart);
  - `app.platform.adapters.engine_client`;
  - `app.platform.job_queue` (pure `next_dispatch`);
  - `app.platform.liveness` (pure tick judge);
  - `app.platform.measurements` (pure learning rule);
  - `app.workspaces.switch` (pure switch table, §4.10);
  - the ports `memory_pressure`, `process_probe`, `clock` and `FileSystem` (adds `canonical_path`, `path_key`, `durable_sync`);
  - `app.platform.observability.decisions` (decision log and flight recorder);
  - on the engine side, `ledger_engine.protocol` (v2) and `ledger_engine.ledger.snapshot`;
  - the models `formal/tla/EngineScheduler.tla` and `formal/tla/Admission.tla`.
- **Decisions it applies:** ADR-0054 (d4 amended), ADR-0041, ADR-0008, ADR-0010, ADR-0011, ADR-0012, ADR-0013, ADR-0015, ADR-0017, ADR-0019, ADR-0061, ADR-0062 (and its amendment: a retired legacy install), the one-workspace ADR (S0045), and owner decision 2 as amended by answer 6.
- **Invariants:** INV-01, INV-10, INV-11, INV-22, INV-26 (as amended by ADR-0062), INV-27, INV-29, INV-30, INV-31. INV-30 and INV-31 are now **without** the cross-workspace exception draft 3 proposed (§13).
- **Traps:**
  - A heartbeat proves nothing (§8).
  - The lane does not keep the mirror correct; per-file locks on path keys, the content fingerprint and the journal check do (§5).
  - `realpath` and `F_GETPATH` are not one key per file (case, normalization, absent vs created), so only `path_key` is (§1).
  - On macOS `fsync` does not reach the disk; `F_FULLFSYNC` does (§5.4).
  - An engine that ran user code is never reused (H1).
  - The pool is the only reaper, and drains a group before the reap (§6.1).
  - `python -I` ignores `PYTHONHASHSEED` (§6.1).
  - "Usual" has one definition (§1.1).
  - A switch is a pure table, not a sequence of awaits (§4.10).
- **Main tests (planned):** §12; the models and their runner; the replay test (§9.3).

## In two minutes

Today Finzytrack sends the ledger engine one request at a time, and the only way to stop one is a time limit that counts as a crash. That does not work for what is coming: loading a 54,000-entry ledger takes about 2 seconds of full CPU, and several such jobs will want to run at once.

**What changes:**
- **One Finzytrack, one open workspace, no shared files.** Finzytrack runs as one instance (ADR-0062) and works on one workspace at a time. A ledger file can belong to only one workspace: registering or opening a workspace whose files overlap another's is refused.
- **Switching workspaces** finishes what must finish (your confirmed saves) and stops everything else. Then it closes the old workspace and opens the new one, showing "Just a moment… switching workspaces." If something must wait, it says what, and you can switch anyway. A switch cut off by a crash is completed at the next start. Nothing ever runs for a workspace that is not open.
- **Up to 2 engine processes** run jobs at once (a setting). Each job that runs your ledger's code gets a **fresh process** that ends with it: about 0.1 s and 34 MB on your Mac.
- **A queue in the main sidecar** decides what runs next. Jobs you are waiting for go first, and background checks last, but those never starve. A large job waiting for memory is not overtaken for ever, and never blocks work of a higher class that fits. Identical queries share one load.
- **Two jobs that change the workspace never run at once.** Reads run beside the rebuilding of your views. A read that overlaps a save waits for it, and is used only if the files did not change while it read. The check after a save never waits behind the next save.
- **Saves reach the disk:** each durable step uses the Mac's true flush, about 40 ms per one-file save (you approved, 2026-10-27).
- **Memory:** ledger work may use up to a quarter of your RAM (a setting). Finzytrack learns what each kind of job **usually** needs from the jobs themselves (§1.1). A job never measured runs alone. At memory-pressure "warning", one job runs at a time. At "critical", nothing new starts unless you press **Run anyway**, which also runs what that job waits for.
- **Cancel** stops a read at once. A save can be cancelled until it starts writing; after that, "Finishing your change…".
- **A slow job is not a crash.** The engine is watched from outside: CPU of the engine and its children, bytes sent, disk page-ins. No guessed time limit applies, not even at start. Background jobs at about 10× their usual CPU, or with no progress for much longer than usual, stop and are logged. A first run is stopped only by hand.
- **The check after a save (R4)** reloads with Beancount's own loader. Only a real loader difference pauses saving; a plugin that answers differently each time shows a notice.
- **The old Finzytrack:** if it is saving, a save or copy is refused with "Quit the old Finzytrack first", never waited for.
- **Crash loops are bounded.** Rare failures can be replayed from content-free decision logs.

**What it costs:**
- about 0.1 s per job;
- about 40 ms per save for true flushes;
- a little memory while two jobs run;
- about 15 KB of log per job (about 2 weeks kept);
- design, mostly built later.

**What stays imperfect:**
- Programs that save your ledger without our locks are detected, not excluded (ADR-0013).
- A process stuck on a dead network disk keeps its slot and locks until it ends.
- Files a plugin reads by itself are invisible (§5.8).

## 1. Terms

- **Job:** one unit of engine work, for the open workspace (or for none, for cheap jobs). It has a kind (§3), a priority, its requesters, and a state: `queued → admitted → running → ended(outcome)`.
- **Heavy job:** runs user code (plugins, `insert_pythonpath`) or loads the whole ledger; needs a fresh engine (§6.1).
- **Large job:** a job whose usual peak (§1.1), or whose estimate when unmeasured, exceeds the interpreter baseline by more than `cheap_margin` (32 MB).
  - Every heavy job is large.
  - The parser-only `plan` and R4's `confirm` are large but not heavy.
  - Large jobs go through admission (§7.3).
- **Cheap job:** neither heavy nor large.
- **Open workspace:** the one workspace whose `WorkspaceContainer` exists, phase `READY`, `WARMING` or `RECOVERING` (the one-workspace ADR). During a switch (§4.10) the old one is `CLOSING` and the new one `OPENING`. They are never both open: property `AtMostOneOpenWorkspace`.
- **Lane:** the open workspace's ordering.
  - **Exclusive** jobs (`rebuild`, `commit`, `create`, `recover`) run one at a time.
  - **Shared** jobs (reads, `r4`) may run beside each other.
- **Engine:** one engine process, the leader of its own process group. **Slot:** permission to have one engine; `max_engines` (2) bounds all engines.
- **Canonical path and path key** [B] (review 3 F2; review 4 H1). Spike K showed why both are needed: under draft 3's rule an absent `Prices.beancount` and the same file after a save created it had different keys, and so did NFC names.
  - `FileSystem.canonical_path(p)` is the stored spelling, used to open files and in messages.
  - `FileSystem.path_key(p)` is the one identity used by every lock file, fingerprint entry, the registry's deduplication and overlap check (ADR-0017, the one-workspace ADR), the demo check (INV-25) and the digests' file names (§5.5). **A key never depends on whether the file exists yet.**
  - `path_key(p)` is the canonical spelling of the deepest existing ancestor, joined with the remaining components as written, then every component folded by its volume's rule:
    - **macOS** (APFS, HFS+): NFD always, plus `str.casefold()` when `pathconf(_PC_CASE_SENSITIVE)` is 0.
    - **Windows:** ordinal upper-case folding unless the directory is flagged case-sensitive.
    - **Linux:** none, except in a case-folding directory (`FS_IOC_GETFLAGS`, `FS_CASEFOLD_FL`) or on a folding mount (vfat, exfat, cifs `nocase`, ntfs3). A folding mount is found once per mount, by `stat` of an existing name against its case-swapped spelling (review 4 O10).
  - The ancestor's spelling comes from:
    - macOS: `F_GETPATH` on a descriptor opened with `O_EVTONLY` (no read access, no download of a cloud placeholder);
    - Windows: `GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED)`;
    - Linux: `realpath`.
  - Where Python's fold differs from a volume's, two names can only fold into one key (APFS treats `straße`/`strasse` as one). That means more exclusion, never less.
  - A corpus test per OS covers: case, NFC/NFD, a symlink, a symlinked directory, an absent path, and that **the key of an absent path equals the key of the same file after it is created under any spelling**.
- **stat fingerprint:** ADR-0015's extended fingerprint, keyed by path key: the root; `(path, mtime, size)` of every glob-expanded include; each glob's match-list hash; expected-but-absent paths; documents-root listing hashes; plugin source files' `(path, mtime, size)`.
- **content fingerprint:** the same set with each file's SHA-256 in place of `(mtime, size)`, plus the **code inputs**: `(path key, sha256)` of every Python source the load imported from outside the engine's bundled site-packages (review 2 F3).
  - Engines never read a `__pycache__` in the user's folder (§6.1).
  - About 10 ms at 54k.
  - The mirror's meta stores both fingerprints; `ledger_version` = (generation id, content-fingerprint hash).
- **Workspace file set:** the path keys of a workspace's last verified set, plus those of any of its journals. The registry keeps it for the overlap check (§4.10).
- **Commit point:** after it, the sidecar ends a `commit` or `create` engine only for a confirmed hang or a stall after a protocol violation (§5.4).
- **Closed record:** the file a save's journal becomes at journal close. It proves the save landed, holds the R4 still owed, and gates the next write (§5.4).
- **Head:** the queued large job whose only obstacle is admission (§4.7).
- **On-time tick:** a watcher tick that ran on schedule, with the machine awake and the sidecar keeping up with the engine's output (§8.2).
- **Healthy run:** outcome `answered` (and verified, for a read); not cancelled, preempted, stopped or ended.
- **Settings:** configuration values in the INV-10 sense, each with a range (§8.7).

### 1.1 What "usual" means [A]

The owner asked: "What is 'usual' and how is that measured and known?" This is the only definition. Every rule and message saying "usual", "much longer than usual" or "far over its usual CPU time" means this.

**Usual is what healthy runs of the same kind of job, for the same workspace, on this computer, have recently needed.** It is kept per (workspace, job kind) as four numbers, each stored with the ledger size it was measured at.

| Number | Measured how | Used for |
|---|---|---|
| **usual CPU time** | the group-CPU accumulator of §8.2 at the job's end. This is the same quantity the running rules see. It may exceed the true CPU by at most 2× when children are reaped by their own parents; it never misses CPU (review 4 M1). | runaway stop (§8.5); "much longer than usual" (§9.1) |
| **usual peak memory** | the finished process's peak: macOS lifetime physical footprint, Linux `ru_maxrss`, Windows peak job commit (§6.1) | admission (§7.3) |
| **usual reply size** | bytes of the final reply | the estimate's decoding share (§7.2) |
| **usual longest quiet stretch** | the longest run of on-time ticks in a healthy run with no CPU, no bytes and no page-ins; and separately, the longest with bytes but no progress | the minimum tick counts for "hung" and "stalled" (§8.3, §8.4) |

**How it is learned** (`measurements.update`, a pure, logged table, §9.3):
- **Only healthy runs** teach time. A memory death raises the usual peak.
- **Decaying high-water mark, per run.** Both values are scaled to the current size, then `usual ← max(this run, 0.9 × usual)`. Growth is followed at once; an outlier fades over about 20 runs and is lenient meanwhile.
- **Size scaling.** Values scale up in proportion to the ledger's growth, never down. Nothing is measured at startup.
- **R4 borrows** its values from the step-3 load of the save it checks.

**Unmeasured:**
- a first run;
- after the plugin code changed;
- a missing, unreadable or other-schema file.

An unmeasured job runs alone, and is never stopped automatically or flagged (owner decision 4).

**Provisional** (review 3 F9): after an app update, a record from the previous engine build counts for admission at × `provisional_margin` (1.5). Runaway, stall and "much longer" treat the job as unmeasured. `hang_ticks` uses the provisional quiet stretch × `provisional_margin` (review 4 O7). The first healthy run replaces the record.

**Honest limits:**
- It is per computer.
- Scaling is proportional, which is right for Beancount's load but not always for a plugin.
- Plugins that read their own files change cost unseen.
- 10× catches a loop, not a 3× slowdown.
- CPU time is not wall time: a network wait is caught by the stall rule, not by the runaway rule.

`Normal`, `Warn`, `Critical` (in code font) are only the OS pressure levels (§7.4).

### 1.2 Phase [A]: what can be built now, and how stable it is

The owner lets [A] be built before the read core only if no open finding or model change can alter it. Each [A] part, with its status after review 4 and the v3 model notes:

| [A] part | Sections | Open findings or model changes that could still alter its interface or behaviour |
|---|---|---|
| Engine pool: slots, the pool table, stop history, backoff, `Unavailable`, `single_engine`, at most one stop per (workspace, fingerprint) | §2, §8.7 | **None open.** Review 4 has no pool finding. The pool properties (`PoolNoHeavyWhileUnavailable`, `OneLedgerCannotTripPool`, `OwnKillNotAStop`) passed in the v2 and v3 runs. Residual risk: only a failure in the long run, which would show as a named property. |
| Fresh engines: spawn flags, environment, bytecode directory, start cost | §6.1–§6.2 | **None open.** Review 4 M3 (bytecode directory) and O4 (environment) are folded in here. |
| Reaper and drain; process groups; Job Objects; end classification | §4.6, §6.1, §6.3, §6.4 | **None open.** Review 4 M5 (drain before the reap) is folded in. The kill-on-close Windows details are [C] for the write path, and their interface (`EngineGone`) does not change. |
| Per-engine statechart | §8.7 | **Interface stable; the commit-point rows are provisional until [C].** The states `Running(past_commit_point)` and `Draining(discard)` and the events `Gate`/`Proceed` are defined now and unused in [A]. A [C] finding about the commit point could change those rows only, never the [A] rows (start, run, reply, drain, end). Build [A] with the [C] rows present and table-tested, but not reachable. |
| Protocol v2 envelope: `progress` (with `children_cpu_ms`), `gate`/`proceed`, one final reply, `decision` objects, domain outcomes as results | §8.1, §9.3 | **Envelope stable.** Per-operation `result` and `decision.inputs` fields are added by the slices that add those operations. The rule: an added optional field does not bump the version; a changed meaning does. |
| `JobQueue` and `next_dispatch`: classes, inheritance, count-based aging, preemption, merging, bounds, busy, head-of-line reservation, the Run-anyway chain | §2, §4.2–§4.5, §4.7 | **Stable**, with one stated hook: lane rules ([B]) and the R4 gate ([C]) enter only through a `dispatchable(job, lane_view)` predicate that [A] defines and leaves permissive. Simplified by the one-workspace policy (no cross-workspace fairness, §13). `Admission.tla` checked the reservation (v2). |
| Admission, the pressure port, measurements ("usual") | §1.1, §7 | **None open.** Review 4 M1 changed what the CPU number measures; that is folded in. |
| Liveness judge: on-time ticks, hung, stalled, runaway, page-ins, the sidecar-behind rule, the group-CPU accumulator, stall after a violation regardless of CPU | §8.2–§8.5 | **None open.** M1 and M4 are folded in. The judge's numbers are covered by tests, not by the model (§11.7). Residual risk: a calibration change to defaults (settings), not to the interface. |
| Cancel for reads, `RequesterLeft`, quiesce for reads | §4.4, §4.6, §4.8 | **None open.** |
| Replay of decisions: decision log, checkpoints, flight recorder, replay test | §9.3 | **Stable.** Review 4 M7 is folded in. Engine-side decision objects for later operations are additive. |
| The switch table (§4.10) | §4.10 | **Not [A].** It is [B] (engine jobs and lanes) and [M2] (UI). |

## 2. Components and where they live

**[A] stability:** see §1.2. Nothing here depends on an open finding.

| Part | Lives in | Holds | Bound |
|---|---|---|---|
| `EnginePool` [A] | process-wide: the one `process` entry of `platform/state_registry.py` (ADR-0041, ADR-0054 d4) | engines, budget, pressure, stop history, pool table state | `max_engines`; `max_crashes` stop times |
| `JobQueue` [A]; lanes [B] | `UserContainer` (INV-11); holds the **open workspace's** queue and lane | queued jobs, running set, lane state, the R4 gate (re-read from the closed records before each wait), poison counters, the last `max_crashes` stop fingerprints, recent outcomes | §3.1 bounds; outcomes: last 16 |
| `WorkspaceSwitch` [B] | `UserContainer` | the switch table's state (§4.10) | one switch in progress, one queued target |
| `Measurements` [A] | `UserContainer`; persisted in `app_local_data/engine/measurements.json` | §1.1 per (workspace, kind) | one record per (registered workspace, kind); removed when the workspace is forgotten |
| `DecisionLog`, `FlightRecorder` [A] | `UserContainer` | §9.3 | recorder: 4,096 events or 1 MiB |
| `MemoryPressurePort`, `ProcessProbe`, `Clock` [A] | platform ports (EP-07). `ProcessProbe`: `psutil` (BSD-3, pinned) plus `proc_listpids(PROC_PGRP_ONLY)` on macOS. | — | — |
| `FileSystem` additions [B, C] | platform port, both processes | `canonical_path`, `path_key`, `durable_sync` | — |

The pool pulls work. With a free slot and memory, it asks the user's `JobQueue` for its best dispatchable job (§4.7). Across users, round-robin applies among equal classes; this is the [Hosted] fairness seam. With one open workspace per user there is no fairness across workspaces.

Each running engine has its `EngineClient`, owned by the pool.

**Superseded:**
- ADR-0011's per-workspace limiter becomes the open workspace's exclusive lane [B], keeping FIFO.
- §2.5's "optional BQL entries cache" row is dropped.

**Moves into the engine:** lock, fingerprint and journal code. `portalocker` 3.2.0 joins the engine's environment for the ledger-set locks and ADR-0062's legacy-lock try.

## 3. Job kinds

**[A] stability:** `query`, `snapshot` and `cheap` are [A]; their rows are stable. The other kinds are [B]/[C].

**Reconciliation** here means R4 (ADR-0010 guardrail 3).

### 3.1 Scheduling attributes

All jobs belong to the open workspace (or to none); no job runs for a workspace that is not open (property `NoJobOfClosedWorkspace`).

| Kind | Phase | Heavy / large | Lane | Priority (§4.2) | Preemptible | Cancellable: when, by whom | Merge | Queue bound |
|---|---|---|---|---|---|---|---|---|
| `rebuild`: WARMING, stale, COLD first build, after an outside edit; local date differs from the build date (either direction); a save that did not publish; a re-plan's stale mirror (§5.4 2a) | B | heavy | exclusive | P1; inherits a waiting save's class | no | any time, by the system: switch, quit | newest only: 1 queued + 1 running | 1 (never busy) |
| `commit`: the legacy try, locks, gate, step-3 load, journal, swaps, read-back, journal close, mirror build, publish | C | heavy | exclusive | P1 for a user save, import or AI confirm; P3 for a background writer of the open workspace (price fetch) | no | **before the commit point**: the user, a switch (§4.10), quit (owner decision 3) | never | 4 |
| `create`: new workspace, demo seeding, Copy adoption, a lifecycle migration writing ledger files. Runs as part of opening the new workspace (§4.10). | C | heavy | exclusive | P1 | no | before its commit point | never | 1 |
| `r4` | C | heavy (its step 2), then a clean `confirm` engine when needed (§5.5) | shared; ordered right after its save; waits only for a *running* exclusive job | P3; a waiting save lends it its class | only while no save waits on it, at most `preempt_max` times | any time: the user ("Checking your last change…", M2), a switch, quit, the system (superseded). Its closed record stays; saving is paused; **Run check now**. | one per save | 1 |
| `plan` (contract step 1, parser only) | C | large, not heavy | shared; after earlier exclusive jobs | as its preview | no | as its preview | the first part of its preview | as preview |
| `preview` (steps 2–3) | C | heavy | shared; after earlier exclusive jobs | P1 | no | any time: user, requester left, a newer preview of the same session, switch, quit | newest only per edit session | 4 |
| `query`: user or AI BQL; recipe `bql` steps | A | heavy | shared | P1 if a requester waits on screen; P2 otherwise | P2 only, by P0/P1 heavy jobs | any time: each requester, switch, quit | queued queries of the same (`ledger_version`, `LoadPolicy`) merge into one batch of ≤ 16 | 4 batches |
| `snapshot`: the copy's "loads the same" check, export-style reads, vector runs | A | heavy | shared | P1 or P3 | if P3 | any time | as `query` when the job is a pure function of the load | 4 |
| `cheap` | A | neither | none | P1; never preempts | no | any time | identical requests | 16 |
| `recover`: journal recovery of the workspace being opened (§4.10), or in session after an engine died past its commit point | C | neither | exclusive | P0 | no | never, except "Switch anyway" or Quit anyway; its journal stays for the next open | one per journal | 1 |

**Notes:**
- **Bounds:** at most 64 queued jobs per user.
- **Frequent facts** come from the handshake or the mirror's meta.
- **Timer-refreshed `bql`** (review 4 O9): no surface refreshes a `bql` step on a timer. Recipes re-run when `ledger_version` changes (ADR-0037's invalidation). A timer refresh, if ever added, needs an INV-10-bounded result cache first; draft 3's memo claim is withdrawn.
- **A `create`** leaves a closed record, so R4 checks adoption and migration writes. A lifecycle run's record is recovered after the write journal (ADR-0012).

### 3.2 Failure outcomes per kind

- **"Stop"**: counted in the pool's stop history (§8.7's one-per-(workspace, fingerprint) rule).
- **"Poison"**: counted per (kind, fingerprint).
- Every row is logged (§9.2) and is a decision (§9.3).

| Kind | Cancel | Engine crash | OS memory kill | `MemoryError` | Hang | Stalled / runaway (background only) |
|---|---|---|---|---|---|---|
| `rebuild` | ended; build file left for GC | stop + poison; retried once; then stays WARMING with the message | poison; one engine; retried once, alone | as memory kill | ended; stop + poison; retried once | n/a |
| `commit`/`create` before the commit point | ended; disk byte-identical, no journal | stop + poison; disk unchanged; not retried | poison; one engine; not retried | as memory kill | ended; stop + poison | P3 writers: stopped, "not saved, unchanged" |
| `commit`/`create` after the commit point | not ended: "Finishing your change…" | stop; resolved from disk (§5.7) | as crash; then one engine | `engine.out_of_memory`, exit; resolved | ended only for a confirmed hang | never, except the stall after a violation (§5.4) |
| `r4` | ended; record stays; saving paused; queued saves refused | stop + poison; retried once; then saving paused (INV-22), Try again; queued saves refused | poison; one engine; retried once, alone | as memory kill | ended; as crash | stopped; saving paused until **Run check now** (no automatic stop; also clears the `r4` poison of that fingerprint, review 4 O6) |
| `plan`, `preview` | ended | stop + poison; reported | poison; one engine; reported | as memory kill | ended; reported | n/a |
| `query`, `snapshot` | ended when no requester remains | stop + poison; retried once; then reported | poison; one engine; retried once, alone | as memory kill | ended; retried once | P3 `snapshot`: stopped |
| `cheap` | ended | stop; retried once | as crash | as crash | ended; stop | n/a |
| `recover` | n/a | stop; retried once (idempotent); then `RECOVERING`, writes blocked: "Your last change may or may not be saved. Reopen the workspace." | as crash | as crash | ended; as crash | n/a |

"Retried once" means once per (job, fingerprint), outside `Unavailable` or poison, after the backoff.

## 4. The scheduler

### 4.1 Lanes [B]

For the open workspace:
1. **At most one exclusive job runs** (`OneExclusivePerWorkspace`, per session).
2. **Shared jobs and saves.**
   - A shared job is not dispatched while a `commit`, `create` or `recover` is running or queued ahead of it.
   - **Except `r4`**, which waits only for a *running* exclusive job and is ordered right after the save it checks (review 3 F1; TLA v2 H1; `R4NeverBehindItsGatedWrite`).
   - Shared jobs may run beside a `rebuild`.
   - A running shared job is not stopped when an exclusive job starts.
3. **Writers wait for R4.** An exclusive writer waits until the previous save's R4 has cleared its closed record (§5.5).
4. **Previews wait for a current mirror.** A `preview` is dispatched only when the published mirror's content fingerprint is the one it plans on; it waits for a running or queued `rebuild`.
5. **No writes without a baseline.**
   - While `WARMING` or `COLD`, a `commit` is refused at once (`WORKSPACE_NOT_READY {phase}`).
   - A `create` is the only writer in `COLD`. Its gate compares only the disk. Its baseline errors are empty for a new ledger, and are the pre-image's load for adoption and migration. It publishes the first generation (`NoCommitWithoutBaseline`).
6. **Recovery first.** While `RECOVERING`, or with an unresolved engine death past a commit point, only `recover` is dispatched. Reads wait too (to avoid wasted loads, not for safety). No rebuild is queued, and the block is shown (`NoWriteAfterUnresolvedDeath`).
7. **Plans after earlier saves.** A `plan` or `preview` is ordered after every exclusive job enqueued before it (`PreviewAfterEarlierSaves`).

The lane is an ordering rule, not a correctness rule. Before [B], the scheduler refuses every exclusive kind.

### 4.2 Priority, inheritance and aging [A]

**[A] stability:** stable (§1.2).

**Classes:**
- **P0** recovery;
- **P1** what the user waits for on screen;
- **P2** interactive work nobody watches;
- **P3** background (R4, background writers, prefetch).

**Order:** by effective class, then FIFO. Exclusive jobs keep FIFO among themselves whatever their class, with one exception: a re-planned save sits directly behind the rebuild its re-plan waits for (§5.4 2a; review 4 H4). An `r4` sits right after its save.

**Inheritance:** a queued job that another waits for (lane rules 3, 4 and 7, the reservation, Run anyway, a re-plan's rebuild) is ordered at the higher class, transitively.
- It reorders queued jobs only: a running job's OS priority is fixed at start (§10; spike s5: EACCES).
- A head does not inherit from a job that may pass it.

**Aging by count:** each pass by a later job raises the job's `passed` counter. At `aging_passes` (8) the job moves up one class. Test obligation: a queued job is dispatched within `aging_passes × 4` passes once admission allows it.

### 4.3 Preemption [A]

A queued P0 or P1 **heavy** job that cannot be dispatched may preempt one preemptible running job: `query` at P2, `snapshot` at P3, or `r4` while no save waits on it, each preempted fewer than `preempt_max` (2) times.
- Cheap jobs never preempt.
- Preemption is our end (`preempt`), not a stop; the job is requeued at the front of its class.
- Under `Critical`, preemptible P3 jobs are preempted even when nothing waits.

### 4.4 Merging and newest-only [A] queries, [B] rebuilds

- **Merging only among queued jobs:** a batch is fixed at dispatch, so no ledger is resident (INV-10).
- **Requesters:** a job ends when its requester set is empty.
- **Per-statement results or refusals** (`engine.result_too_large`).
- **Rebuild: newest only.** A stat change with unchanged content re-stamps the mirror's stat fingerprint instead of rebuilding.
- **Preview: newest per edit session.**

### 4.5 Queue bounds and "busy" [A]

Over a bound: `BUSY_HEAVY_LOAD` (`Unavailable`, no `retry_after_ms`), and nothing is queued.
- Merged kinds are never busy.
- The frontend never retries `BUSY_HEAVY_LOAD` or `LEDGER_CHANGING` by itself.
- A busy refusal is logged as a count (§9.3).

### 4.6 What "running" means [A]

**[A] stability:** stable (§1.2).

- **The job and its lane** end at the final reply, or at confirmed death.
- **The slot and memory reservation** end only when the engine and its process group are gone (TLA F3; `EnginesBounded`):
  - POSIX: the leader is reaped and `killpg(pgid, 0)` fails with `ESRCH`;
  - Windows: the Job Object's active count is 0.
- **Draining** (review 3 F8; review 4 M5):
  - After its final reply, the engine ends with `os._exit(code)`.
  - **Before the leader's reap**, while the leader is still a zombie that pins the group number: if `killpg(pgid, 0)` succeeds (a member lives), the pool records `drain` and sends `killpg(pgid, SIGKILL)`, then waits until `killpg(pgid, 0)` gives `EPERM` (only zombies left).
  - Then the pool reaps. **No signal is ever sent to that group number after the reap** (spike M), so a reused number can never reach another program.
  - `drain` is our end, never a stop; the job stays `answered` (`AnsweredJobNotAStop`).
  - A leader that replied but does not exit is judged by the hang rule; ended with `drain`; not a stop. Property: `SlotEventuallyFreed`.
- **A kill sent is never an end** (`RunningUntilAnsweredOrDead`).
- **The HTTP handler is not the job.** `RequesterLeft` cancels a read with no requester left, never a save.
- **Outcomes outlive a crash.** A save's outcome is in recent outcomes, and durably in its closed record, `.done`, `.mismatch` or `.resolved` journal (§5.5, §5.7; review 4 O8). A reloaded window learns it even after a sidecar crash.
- **Cost:** an engine that cannot be ended keeps its slot, lane and locks. The workspace's verifies and saves wait, and "cannot be stopped" is shown. A `setsid` descendant keeps inherited locks while it lives.

### 4.7 Dispatch, as a pure function, with head-of-line reservation [A]

**[A] stability:** stable; lane and R4-gate rules enter through `dispatchable(job, lane_view)` (§1.2).

`next_dispatch(pool_view, queue) -> Dispatch | Preempt | Wait(reason)`.

A candidate is **dispatchable** when:
- `dispatchable(job, lane_view)` holds (lane rules, §4.1; the switch state, §4.10);
- the pool is not `Unavailable` (for heavy and large jobs) and not `Incompatible`;
- the key is not poisoned;
- the backoff has passed;
- admission allows it (§7.3).

**Reservation** (review 2 F1; review 3 F9; TLA v2 M7):
- **The head** is the queued large job of the highest class, then FIFO, whose only obstacle is admission.
- **While a head exists:**
  - no other large job of the same or a lower class is dispatched, except one that preempts on the head's behalf;
  - a higher-class large job may be dispatched when admitted, and that counts as a pass for the head's aging;
  - a cheap job may run at `Normal`, not at `Warn`.
- **When the running large jobs end**, the head runs alone.
- **The head is re-chosen at each event.** A Run-anyway job is head even over a same-class head.
- **Evidence:** spike s6 (without the reservation, starvation; with it, the job ran at step 22); `Admission.tla` (`NoStarvationUnderAdmission`, `NoClassInversion`).

`Wait(reason)` is one of: `queued`, `waiting_for_memory`, `waiting_to_run_alone`, `backoff`, `switching`.

It re-runs on every event, as an EP-17 pure table, and is logged (§9.3).

### 4.8 Quit and quiesce [A] reads; [C] saves

`POST /api/app/quiesce`:
1. Stop dispatching.
2. End queued jobs (§4.9). A queued `recover` is left for the next open.
3. End running reads, plans, previews, rebuilds and R4s (records stay).
4. End saves before their commit point ("not saved, unchanged"; W7's guard asked first).
5. Wait for saves past their commit point, and for a running `recover`: "Finishing your last change…".
   - After `quit_patience` on-time ticks (10) with no phase change: **Quit anyway**, with "Finzytrack will finish or undo this change the next time it opens. Your ledger stays safe."
   - Properties: `QuiesceTerminates`; `OutcomeTruthful`.

**Before an app update** (ADR-0007; review 4 L2), quiesce runs in two parts:
1. If the open workspace has an owed R4, that R4 runs at P1 under the Run-anyway exemption (§7.3), with Cancel; nothing else is dispatched.
2. Steps 1–5 above.

An R4 the user cancels keeps its record, and the update proceeds; at the next start that R4 is superseded (`engine_changed`, logged). Owed R4s of workspaces that are not open stay for their next open; nothing runs for them now.

### 4.9 Queued and running jobs on each event [A]; save rows [C]

| Event | Queued jobs | Running jobs |
|---|---|---|
| pool `Unavailable` | P1 and P2 heavy and large jobs end with E1's message. P3 jobs stay queued. A `rebuild` stays (WARMING plus E1). Closed records stay. `recover` stays and is admitted. | not affected |
| pool `Incompatible` | all end with E6's message | none can exist |
| a key is poisoned | its jobs end "poisoned" | — |
| writes blocked: foreign bytes at recovery, recovery failed twice, an R4 that cannot run or was cancelled, a mismatch or undecided R4 | queued saves refused at once with `WRITES_BLOCKED` and the block's message. A save never re-arms a cancelled R4; **Run check now** does. | — |
| a legacy lock (ADR-0062) held, or any try outcome other than free or absent | — | the save or copy refuses at step 0: "The old Finzytrack app is saving. Quit the old Finzytrack first, then try again." Nothing is written; not a stop, not poison. |
| a switch starts (§4.10) | §4.10 step 2 | §4.10 step 2 |
| a workspace is forgotten (ADR-0019) | only a workspace that is not open can be forgotten. It is refused while that workspace has an open journal or a closed record ("Open it once to finish its last change"); otherwise its measurements, `.done` records and caches are removed. | — |
| sidecar exit or crash | lost; journals, records and high-water marks are on disk | engines end with their group; a save past its commit point is resolved at the next open, and a switch cut off midway is completed (§4.10) |
| quiesce | §4.8 | §4.8 |
| `max_engines` or `memory_share` changed | re-evaluated | never ended |
| pressure rises to `Critical` / falls | stay queued, `waiting_for_memory` / re-evaluated | preemptible P3 preempted / — |
| machine sleep and wake (review 4 O1) | — | the ticks spanning the sleep are late and judge nothing |
| disk full during a save (review 4 O1) | — | before the commit point: refused, unchanged. After it: ADR-0012's in-process rollback; if that cannot complete, as an engine death (§5.7). |

### 4.10 The workspace switch [B] engine side; [C] save rows; [M2] UI

The owner, 2026-10-27: "Switching between workspaces should work well, without issues. The switch should do whatever book-keeping that is needed. Fine to tell the user something like, 'Just a moment.. switching workspaces.' or similar, if the switch takes time."

A switch is a pure table, `workspace_switch.transition(state, event) -> (state, effects)` (EP-17). Its inputs and outputs are logged (§9.3), and the frontend's switch machine (ARCHITECTURE §7) models only the UI over it. Opening at start (no old workspace) is the same table, entered at `Opening`. It produces the same effects as before: INV-29 records the open only at `READY`/`WARMING`, and INV-30 holds (opening never writes the folder except recovery on open).

**States:**
- `Open(A)`;
- `Draining(A → B, waiting_for)`;
- `Closing(A → B)`;
- `Opening(B, step)`, where step ∈ {`assess`, `overlap`, `recover`, `r4`, `warm`};
- `Open(B)`;
- `OpenFailed(B, phase)`;
- `Opening(B)` after a crash (from the persisted switch marker).

**Events:**
- `SwitchRequested(B)`, `Cancel`, `SwitchAnyway`;
- `JobEnded(job, outcome)`, `PastPointEnded`, `AllEnded`, `Closed`;
- `Assessed(phase)`, `OverlapChecked(ok | overlap{ws})`, `RecoveryEnded(outcome)`, `R4Ended(outcome)`, `RebuildPublished`, `RebuildFailed`;
- `Tick(no_progress_ticks)`;
- `Restarted(marker)`.

**Sequence:**
1. **`SwitchRequested(B)`** in `Open(A)`. The frontend's leave guard has run (unsaved edits; W7).
   - The sidecar writes a **switch marker** `{from: A, to: B, step}` to `app_local_data` (atomic replace, `durable_sync`) and enters `Draining`.
   - A's `JobQueue` stops accepting jobs. New requests for A get `WORKSPACE_NOT_READY {phase: CLOSING}`; new requests for B wait for B.
   - The user sees "Just a moment… switching workspaces."
2. **Draining A**, in this order:
   - **Ended at once (our end; not stops):**
     - queued and running reads, plans, previews and `snapshot`s, with requesters told `cancelled {switch}`;
     - queued and running `rebuild`s (the build file is left for GC);
     - a queued or running `r4`, whose closed record **stays**: A's next open runs it before A's writes.
   - **Allowed to finish:** saves the user confirmed. Queued saves of A are dispatched in FIFO, together with the R4 each depends on, because a confirmed change is the user's work. The user sees "Finishing your changes before switching… (n left)".
   - **A running `recover`** finishes.
   - **Nothing new** is dispatched for A except these saves, their R4s and that recovery.
   - **Exits for the user:** after `quit_patience` on-time ticks with no phase change of what is being waited for, **Switch anyway** appears (Quit-anyway style), with the text "Finzytrack will finish or undo the unfinished change the next time you open *A*. Your ledger stays safe."
     - Pressing it ends saves before their commit point ("not saved, unchanged", listed) and ends engines past the commit point, or a `recover` (reason `switch`; the journal covers them; A's next open resolves them, §5.7).
     - **Cancel** (only while nothing has been ended by Switch anyway) returns to `Open(A)`, with the queue accepting again.
   - **`AllEnded`:** no engine of A remains (each group confirmed gone, §4.6), so no lock of A is held. The switch moves to `Closing`.
   - Property `NoJobOfClosedWorkspace`: after `Closing`, no engine runs a job of A.
3. **Closing A:**
   - drop A's `WorkspaceContainer` (caches, handles, mirror reader pins; INV-31);
   - write `measurements.json`;
   - log `workspace.closed`;
   - keep A's journals, closed records and generation files on disk;
   - the marker's step becomes `opening` (synced).

   No job of A remains, and none will run until A is opened again.
4. **Opening B:**
   1. `assess` (ADR-0019 `assess_workspace`): `WS_CONFIG_INVALID`, `LEDGER_UNAVAILABLE`, `NEWER_WORKSPACE`, `UPGRADE_PENDING` end in `OpenFailed(B, phase)`, with its dialog.
   2. `overlap` (the one-workspace ADR): B's file set, its last verified set plus any journal of B, must not share a path key with any other registered workspace's file set (the registry's records plus a listing of each workspace's journal directory, usually empty).
      - An overlap ends `OpenFailed(B, overlap{ws})`: "This ledger uses a file that belongs to workspace *X*. A file can belong to only one workspace." The actions are **Go back to A** and **Choose another workspace**.
      - Registration does the same check.
      - When B's set grows during the session (a glob matches a new file, an include is added), the verify that sees it repeats the check for the new keys. On overlap, saves are refused (`WORKSPACE_OVERLAP`), and reads go on from the last mirror.
   3. `recover`: B's journals (ADR-0012 recovery on open; §5.7). It waits for the locks: an orphaned engine of a crashed session may still hold them for 0.25 s.
   4. `r4`: an owed R4 from B's closed record. It is re-armed, or superseded if B's disk moved since or the engine build changed (§5.5). Writes wait for it; reads do not.
   5. `warm`: the stat check. If the mirror is stale or absent, the workspace becomes `WARMING`/`COLD` and its rebuild is queued.
   6. Then `Open(B)`, with INV-29 recording the open. The marker is removed (synced).

   The user sees "Just a moment… switching workspaces." until `assess` and `overlap` pass, then B's own phase: "Checking your last change…" while recovering, or the WARMING banner.
5. **`OpenFailed(B, phase)`:** no workspace is open.
   - **Go back to A** is a new switch to A, which opens quickly: A's mirror is on disk.
   - **Retry**, **Locate**, **Upgrade** follow ADR-0019's dialogs.
6. **A new `SwitchRequested(C)` during a switch:**
   - in `Draining`, it retargets (newest only);
   - in `Closing` or `Opening`, it is queued and runs after `Open(B)` or `OpenFailed`.
7. **A sidecar crash midway** (`Restarted(marker)`): at the next start the marker names the step.
   - **`draining` or `closing`:** A was never closed cleanly, but no engine of A survives the sidecar (§6.3). The start opens **B** (the user's last request): A's journals and records wait for A's next open, which is safe because no file is shared and nothing runs for A.
   - **`opening`:** the open of B restarts from `assess`.
   - The marker is removed at `Open` or `OpenFailed`.
   - The workspace list marks every workspace with an open journal or closed record: "Has an unfinished change. Open it to finish."

**Properties:**
- `AtMostOneOpenWorkspace`;
- `NoJobOfClosedWorkspace`;
- `SwitchEnds` (liveness: a switch request ends in `Open(·)` or `OpenFailed(·)`);
- `OutcomeTruthful` across switches (a save ended by Switch anyway is resolved at A's next open);
- `NoFileInTwoWorkspaces` (an open passes `overlap`).

### Why the per-workspace journal check now suffices (the app-wide journal index is removed)

Draft 3 needed an app-wide index because two workspaces could share a file. A journal in workspace A's directory could then cover a file of B, and B's verify had to see it. Under the one-workspace ADR this cannot happen:
- Every journal is written by a save of the workspace that owns its directory, over that workspace's set and the files the save creates (§5.4 step 1).
- No file is in two workspaces' sets; registration, open and a growing set are all checked.
- Only the open workspace runs jobs, and an orphan of a crashed session is a job of the workspace it was opened for.

So "a journal is open over a file of this set" equals "this workspace's journal directory has an `.open` journal". The overlap check's listing of other workspaces' journal directories covers the one remaining case: a save of another workspace that created a file which this workspace's glob later matches. That check is made at open, not at every verify. A file created after the open by an outside program is an outside edit (ADR-0013).

## 5. Consistency [B] unless marked

### 5.1 The two fingerprints

- **The stat fingerprint** decides freshness.
- **The content fingerprint** decides correctness: verify, publish condition, gate.
- Plugin code is in both.
- All paths are path keys.

### 5.2 Verified snapshot reads (H3; ADR-0012 F-6)

A job that loads the ledger returns a result only if the bytes it parsed were, at one instant, the whole set with no write in progress.

1. **Read without locks.** Each file is read once as bytes, recording `(path key, size, sha256)`. Glob matches, absent includes, documents listings and plugin files are recorded too. For a cloud placeholder, `reading_files` reports `done` as bytes read (§8.4).
2. **Verify under the locks** (phase `waiting_for_lock` while waiting):
   - **Ledger-set locks:** one lock file per path key at `app_local_data/locks/<sha256(path key)>`, each taken once, sorted by path key. Lock files are never deleted while a Finzytrack process may run.
   - **The set may grow under the locks:** globs are re-expanded and paths re-keyed.
     - New locks are tried with `LOCK_NB`.
     - If any is held, all locks are released and step 2 restarts in order (`snapshot_moved {set_changed}`, bounded by `snapshot_retries`).
     - A blocking wait is only for a lock that sorts after every lock already held.
     - A new member is overlap-checked (§4.10 step 4.2).
   - **No `.open` journal** in this workspace's journal directory. The directory is `durable_sync`ed before a check relies on "no journal" (F-8; a clean directory costs nothing, spike I).
   - **Content fingerprint recomputed** and compared. Re-hashing reads only files the unlocked read brought local; an evicted placeholder ends `snapshot_moved {evicted}`.
   - Result: **verified at instant T**, or `snapshot_moved {cause}`, or `orphan_journal {session, job}`.
3. **Release the locks.** A publishing job publishes first.

The locks are held for milliseconds, never across user code.

**Outcomes:**
- **Moved by our own exclusive job:** requeued behind it; the retry counter does not move.
- **Moved by an outside writer:** up to `snapshot_retries` (3), then `LEDGER_CHANGING`.
- **Orphan journal:** `RECOVERING` (§4.1 rule 6; §5.7).

**Guarantee:**
- against our saves and an orphaned engine of a crashed session (the only lock-respecting writers left): a committed state (`VerifiedReadsAreSnapshots`);
- against lock-ignoring programs: only "the disk equalled what was parsed at instant T".

### 5.3 Conditional publish and generation ids

A generation is published by the engine that built it, under its lock hold, only when:
- the disk's content fingerprint equals the build's;
- no journal is open;
- its id is greater than the current one.

**Ids at publish:**
- `id = 1 + max(current, high-water in app_local_data/engine/generations/<ws key>, highest file present)`.
- The high-water mark is `durable_sync`ed before the pointer swap.
- Properties: `PublishOnlyCurrent`, `GenerationsMonotonic`, `PublishedNotOlder`; seeds `hw_in_cache` and `id_at_dispatch`.

**Mechanics:** `build-<job>.db` is built, then renamed `gen-<id>.db`, then the pointer is swapped. A stale build is dropped and one rebuild is queued. GC removes the `build-*` files of ended jobs.

### 5.4 The write path [C]

A `commit` runs in one fresh engine.

0. **Legacy-lock detection** (ADR-0062; INV-26 as amended; review 4 H3).
   - **Which locks:** when the config of any known, not-retired legacy install names a file of this workspace's set (or, for a Copy, of the set being copied), the engine collects the `data/.write.lock` of **every** such install and removes duplicates by path key (two installs may share one data folder; spike J: trying one file twice in one engine gives `free`, then `held`).
   - **How:** each is tried **once, without waiting** (`LOCK_EX | LOCK_NB`, portalocker 3.2.0's primitive and range), in path-key order, before any ledger-set lock. A lock file is opened read-only, only if it exists, and never created.
   - **Free:** held until the save, or the copy's reads, end.
   - **Absent:** go on. For a Copy, after the reads the file must still be absent and the sources' content fingerprint unchanged; otherwise the copy is discarded and refused.
   - **Held, or any other outcome** (cannot open, an error other than "already locked", a volume that cannot lock): refused at once, nothing written; not a stop, not poison (INV-22).
   - **Never contended by ourselves:** only the open workspace's one exclusive job runs step 0 at a time (lane rule 1). An orphaned engine of a crashed session may hold the lock for up to 0.25 s after a relaunch; one refusal then, and Try again works.
   - **Retired installs** (ADR-0062 amendment, M3) are not tried.
   - This is the only way v0.4 touches a legacy lock.
1. **Locks** over the union of the plan's pre set, its post set (files it will create) and the absent paths, in path-key order with §5.2's non-blocking relock. A wait shows `waiting_for_lock` with **Cancel**. It waits only for an orphan of a crashed session.
2. **Gate, under the locks:**
   - the disk's content fingerprint equals the plan's;
   - the mirror's equals the plan's;
   - no `.open` journal of this workspace;
   - no closed and no `.mismatch` record of this workspace;
   - no overlap flag;
   - for a `create`, the disk half only.

   On failure:
   - a journal → `RECOVERING`;
   - a closed record → wait for its R4;
   - `.mismatch` → `WRITES_BLOCKED`;
   - a stale mirror → `WORKSPACE_NOT_READY {WARMING}`;
   - a moved disk → 2a.

   **2a. Our own earlier saves are not outside changes** (review 2 F9; review 3 F14; TLA v2 M5; review 4 H4, O3):
   - **Ours:** every file that differs between the plan's fingerprint and the disk's has as its current hash the post hash of a closed, `.done` or `.resolved`-saved record of **this workspace** that is newer than the plan's baseline generation. So an outside revert to an earlier v0.4 state is not taken for ours.
   - **End and requeue:** the job ends its engine before the commit point (`replan`, not a stop) and releases the locks.
   - **Re-plan against the published mirror**, re-resolving `TransactionId`s (ADR-0026).
     - When that mirror's content fingerprint is not the disk's (the earlier save was `saved_nopub`, or an outside edit happened too), the sidecar enqueues a `rebuild` (absorbed into a queued one) and places the re-planned save **directly behind it**. That rebuild inherits the save's class. This is the one exception to FIFO among exclusive jobs.
     - The re-plan runs after that rebuild publishes. A rebuild that ends without publishing ends the save with its outcome (`LEDGER_CHANGING`, poisoned, `WORKSPACE_NOT_READY`), never a silent wait.
   - **Same intended change** (same targeted entries and canonical texts) → proceed; otherwise `PLAN_OUTDATED`.
   - **At most `snapshot_retries` re-plans**, then `LEDGER_CHANGING`.
   - Properties: `OwnCommitsNeverExternal`, `ReplanProgresses`; seeds `no_replan`, `replan_ahead_of_rebuild`.
3. **Load** (step 3): its CPU and peak become the R4 baseline.
4. **The commit point.** The engine sends `gate`. The sidecar, in one table step, either ends the engine (a cancel is pending) or marks the job past the commit point and then sends `proceed` (`NoKillAfterCommitPoint`, `CancelBeforeCommitLeavesDiskUnchanged`). From the commit point to its final reply, the engine ignores `SIGTERM`.
5. **Write, in one durable order** (every sync is `FileSystem.durable_sync`; review 3 F6, owner answer (a); review 4 H2's ordering, L3):
   1. backups, and their directory;
   2. the journal `journal-<session>-<job>.open`, which holds every field of the future closed record (post content fingerprint, adopted digest, parse digest, engine build, R4 baseline), and its directory;
   3. the replaces (temp file, swap with the displaced-hash check, directory), children first and the root last;
   4. read-back;
   5. **journal close:** a pure rename into `journal-<session>-<job>.closed`, the handle closed first (Windows), then the directory.

   The save has landed exactly when the closed record exists.
   - A full disk before the journal is durable is an in-process abort: "not saved, unchanged".
   - **`durable_sync`:** macOS `F_FULLFSYNC` (falls back to `fsync` only where refused, logged once per volume); Linux `fsync`; Windows `FlushFileBuffers` plus `MOVEFILE_WRITE_THROUGH`.
   - **Cost:** about 9 syncs for a one-file save (backup and directory, journal and directory, temp file, replace directory, close directory, high-water file and directory at publish), about 40 ms on the owner's Mac (spike I: about 4.5 ms per sync), plus about 3 per further file. A directory sync with nothing to flush costs nothing.
   - **No user code after the commit point.**
   - **A protocol violation after the commit point** (review 3 F5; review 4 M4):
     - The sidecar keeps reading and **discarding** the engine's output, and waits for it to exit.
     - Discarded bytes are not liveness.
     - **`stall_ticks` on-time ticks with `phase`, `done` and `total` frozen at the violation end the engine (`stalled_after_violation`), whatever its CPU.** A protocol-broken engine that keeps computing is not finishing its save, and the journal makes the end safe. The outcome is resolved from disk.
6. **Publish under the same locks**, release, reply. `saved_nopub` queues a rebuild.

Write serialization is the lane plus the per-file locks (Q29).

### 5.5 The R4 check [C]

**The gate is the closed record on disk.**
- Recovery that resolves "saved" re-arms R4.
- The in-memory gate is a cache, re-read from disk before every wait and at every open (TLA v2 M1).
- R4 renames the record `.done` on `pass`, `superseded` or `nondeterministic`; the last 16 are kept.
- On `mismatch` or `undecided` it renames it `.mismatch {kind}`, which blocks saving until the user acknowledges (then `.done`).

**Digests** (review 3 F7; review 4 M2):
- The **adopted digest** is the SHA-256 of the canonical encoding: entries in Beancount's order with ties broken by canonical text, the error keys, and the options plugins can read.
- The **parse digest** is the same encoding over `_parse_recursive`'s output.
- File names in entries' `meta`, in errors and in options (`filename`, `include`) are encoded as **path keys**, so two spellings never differ.
- Engines run with `PYTHONHASHSEED=0`.

**Steps:**
1. **Under the locks:** the content fingerprint equals the record's post fingerprint, and there is no `.open` journal (an open one gives `orphan_journal`, then `RECOVERING`, and the record stays; review 4 O2). Release. A differing fingerprint gives `superseded`.
2. **Without locks:** `beancount.loader._uncached_load_file(abs_path, log_timings, extra_validations, encoding)`. An audit hook on `open` counts the bytes read, so a cloud placeholder stalls rather than waiting silently (review 4 O5).
3. **Digest**, compared with the adopted digest.
4. **Under the locks:** re-check the whole content fingerprint and the journal directory. Then:
   - **equal digest, unchanged fingerprint:** `pass`;
   - **fingerprint moved:** `superseded` (`R4NoFalseMismatch`);
   - **digest differs, fingerprint unchanged:** **confirm in a fresh, clean engine** (review 4 M2). The R4 engine ends (our end, `confirm`, not a stop). A clean engine (large, not heavy; no user code) takes the slot as part of the same R4 job, takes the locks, re-checks the fingerprint and the journal directory, runs Beancount's `_parse_recursive` on the disk and the overlay replica on the same bytes, and compares the parse digests. No plugin thread or patch can run under the locks or reach the parser (`CleanEngineForUserCode`, `OnlySavesLockAcrossUserCode`). Cost: one engine start (about 0.05 s), on a rare path.
     - **overlay ≠ Beancount:** `mismatch`. Saving is blocked, the backup shown, `engine.r4.mismatch` logged at error, and the `load_file` result published (ADR-0010).
     - **both equal each other and the record:** `nondeterministic`. A notice is shown, the `load_file` result published, and saving is **not** blocked (owner answer 4).
     - **both equal each other but not the record:** the fingerprint missed a change. R4 is **re-queued once as a new R4 job** (fresh engines). If the same result recurs, the outcome is **`undecided`** (INV-22): `.mismatch {undecided}`, saving blocked, "Finzytrack could not double-check your last change. Saving is paused. Your backup is here.", with **Show backup**, **Run check now** and **Copy diagnostics**. Acknowledging renames it `.done`. That is the exit; it cannot recur without a new save.
     - `R4OutcomeDefined`.

**Rules:**
- **Superseded:** the disk moved after the save (an outside edit), or the engine build changed (at open). One guardrail-3 sample is lost; the outside change sends the workspace to WARMING.
- **At open** (§4.10 step 4.4): superseded or re-armed.
- **Priority:** P3 at normal OS priority, ordered right after its save.
- **Never unmeasured.**
- **Failure** (two crashes, runaway, Cancel): the record stays, saving is paused (INV-22), queued saves are refused. **Run check now** runs R4 at P1 with no automatic stop and clears its poison.
- **A file-creating save ends R4 `pass`**, because the created file's path key is the same before and after (review 4 H1; seed `created_file_alias` must fail `R4NeverSupersededByItsOwnSave`).
- **Synchronous R4 during rollout** runs inside the save's job.
- **No job holds the locks across user code except a save** (`OnlySavesLockAcrossUserCode`).

### 5.6 Outside writers, the legacy app, other instances

- **Outside editors and sync clients:** detected by the focus check, the verify, the gate and the swap hash (ADR-0013); never excluded.
- **The legacy app** (ADR-0062): Copy only. The lock is detected once at step 0 and the save or copy refused; v0.4 never waits. An old app pointed at a v0.4 folder is unsupported and caught as an outside edit. A retired install (ADR-0062 amendment) is not tried.
- **A second v0.4 instance** on the same app root is refused at start (BL-0100, M2). Other installations, app roots and OS accounts are unsupported; each is an outside editor to the other. ADR-0012 Q30 stays as a defence: a journal in this workspace's directory with a session that is neither this one nor an earlier one of this installation refuses writes and asks the user to reopen.
- **Orphaned engines of a crashed sidecar** are the only other lock-respecting writer. They hold locks until their watchdog ends their group (0.25 s POSIX; at once on Windows).
- **Two workspaces** never share a file (the one-workspace ADR, §4.10).

### 5.7 Recovery and the outcome of a save whose engine died [C]

When a save's engine ends past its commit point without a final reply, the outcome is **resolved** (BL-0091; `OutcomeTruthful`) by a `recover` of the open workspace. Such a recover runs in two cases: in session, or as step 4.3 of the next open (after a crash, Quit anyway or Switch anyway). It is P0 and admitted at any pressure (owner answer 3).

- **Locks first, then read** (TLA v2 L1). It waits for the locks, which on Windows may lag after the engine's end. Only after taking them does it read the journal directory and records (`RecoveryNeverClobbersForeignBytes`). On-open recovery waits too; ADR-0012's skip is amended.
- **`resolve(journal state, records, disk hashes, plan pre/post hashes)`**, a pure function that runs in the engine and returns its inputs and output (§9.3):
  - **`.open` journal:** every file pre or post → restore pre, root first: **not saved, put back**. Other bytes → **blocked**: writes off, both versions shown (`BlockedOnlyOnForeignBytes`).
  - **This job's closed record:** **saved**; R4 re-armed.
  - **Neither:** **not saved**, plus "changed by another program" if the disk differs from the pre-image.
- **Recovery's durable order** mirrors the save's (review 4 H2, ordering part):
  1. restored files and their directories synced;
  2. the journal renamed `journal-<session>-<job>.resolved {outcome}`, and its directory synced.

  In-process rollback uses the same order. A recovery cut before step 2 restores again (idempotent). The `.resolved` file keeps a "not saved" outcome durable for a reloaded window (review 4 O8); the last 16 are kept.
- **Whose journal:** this workspace's directory only.
  - This session → recover now.
  - An earlier session of this installation → recover at open, or now.
  - Anything else → Q30 (§5.6).
- **A recovery that dies** is retried once, then the workspace is visibly blocked, with Reopen (`RunningWriteEndsOrBlocks`).
- **Then the mirror:** saved → publish if not yet published; not saved → the mirror is still the pre state.
- **Order with lifecycle runs:** the write journal first, then the lifecycle record (ADR-0012).

Nothing is recovered for a workspace that is not open. Its journal waits for its next open, which is safe because no file is shared and nothing runs for it. The workspace list shows "Has an unfinished change" (§4.10 step 7).

### 5.8 What is not watertight, and what it costs

- **Lock-ignoring writers** are detected, not excluded.
- **Other instances, installations and OS accounts:** unsupported. The instance lock exists only from M2.
- **An unkillable process** keeps its slot, lane and locks.
- **A first background run** has no bound; before M2 only quitting stops it.
- **On a saturated machine** a frozen engine is not judged hung until load drops (`stopped` is judged even then).
- **Long `Critical` pressure:** only recovery and Run anyway start.
- **Files a plugin reads itself** are invisible; **Rebuild now** exists for them.
- **Two loads of one ledger** may run its plugins at once (user docs).
- **Orphans** are invisible to the next session's count for up to 0.25 s.
- **A plugin input that drifts** between R4's load and the save's gives `nondeterministic`, never a false `mismatch`.
- **A workspace whose save was cut off and that is not reopened** keeps its journal until it is opened. Its folder stays as the crash left it, with each file whole (atomic swap): pre or post bytes, mixed only across files. Outside tools see that mixed state until then. The list marks it.

## 6. Engines [A]

**[A] stability:** stable (§1.2); review 4 M3, M5 and O4 are folded in.

### 6.1 A fresh process per heavy job; the pool as the only reaper

- **At most one heavy job per engine.** Cheap jobs, a `plan` or a `confirm` may come first, never after (`CleanEngineForUserCode`). A clean engine that ran only cheap work ends after `idle_ticks` (5).
- **Spawn:**
  - **Flags:** `-s -S -B -P -X pycache_prefix=<the engine's bytecode directory>`. That directory is private to the engine build (`app_local_data/engine/bytecode/<build>/`, or inside the packaged install), filled once per build with the bytecode of the engine's own standard library, site-packages and code, and read-only afterwards. User plugin modules are always compiled from the hashed source; a `__pycache__` in the user's folder is never read or written. Spike N (review 4): an empty prefix cost 0.24 s per start against 0.05 s; a filled one costs nothing.
  - **Environment:** the user's environment minus `FT_*` and `PYTHON*`, plus `PYTHONHASHSEED=0` (review 4 O4). Plugins keep `PATH`, `HOME`, `LANG` and `TZ`.
- **The pool is the only reaper:**
  1. `os.waitid(P_PID, pid, WEXITED | WNOWAIT)`, leaving a readable zombie. On macOS, `proc_pid_rusage(pid, RUSAGE_INFO_V4)` for the lifetime footprint.
  2. **Drain while the leader is a zombie** (§4.6): if `killpg(pgid, 0)` succeeds, record `drain`, send `killpg(pgid, SIGKILL)`, and wait for `EPERM`.
  3. `os.wait4(pid, 0)` for the status, CPU and `ru_maxrss`. The slot is freed at `ESRCH`. **No group signal after the reap.**

  `Popen.wait`/`poll` are never used. On Windows, the Job Object's accounting.
- **Spare engine:** off by default on macOS and Linux; Windows decided at [M1b]/[M11].

### 6.2 Start cost

With the spawn flags and a filled bytecode directory: 0.05–0.10 s and 34 MB warm, 0.42 s cold, on the owner's Mac. That is about 5 % of a 2 s load at 54k. Windows: [M1b], [M11].

### 6.3 Process groups and Job Objects

- **POSIX:** each engine leads its own group. Our end is `killpg(pgid, SIGKILL)` (before the reap only). The watchdog (0.25 s) ends its whole group. There is no `PR_SET_PDEATHSIG`. Linux engines start at `oom_score_adj` +500.
- **Windows** ([C] for the write path): a Job Object nested in the shell's, with kill-on-close and no breakaway; `TerminateJobObject`.
- **A save past its commit point when the sidecar dies:** the engine ends with its group on every OS, and the next open recovers it (failure-modes E5).

### 6.4 How an engine ended

The reason is recorded **before** the signal, and **a recorded reason wins** over any exit status. Reasons: `cancel`, `preempt`, `superseded`, `replan`, `confirm`, `switch`, `hang`, `stalled`, `stalled_after_violation`, `runaway`, `quit`, `idle`, `drain`.

| Observation | Classified as | Stop? |
|---|---|---|
| final reply, then exit 0, or ended with `drain` | `answered` | no |
| ended by us, reason recorded | that reason | only `hang`, `stalled_after_violation` |
| `SIGKILL`, no reason of ours | `killed_by_system` | no; poison, one engine |
| `EXIT_OUT_OF_MEMORY`, or Windows `0xC0000017` | `out_of_memory` | no; poison, one engine |
| protocol violation before the commit point | `violation` | yes |
| protocol violation after the commit point | read and discard, wait (§5.4) | as `crashed` if the exit is abnormal |
| any other exit or signal | `crashed` | yes |
| handshake refused, or the starting engine judged hung (no handshake time limit) | `start_failed` | yes |
| another protocol version | `incompatible` | no |

### 6.5 Out-of-memory errors inside the engine

The engine catches `MemoryError` at the job's top level, writes a pre-encoded `engine.out_of_memory` reply and exits `EXIT_OUT_OF_MEMORY`. After the commit point the outcome is resolved (§5.7).

## 7. Admission and memory [A]

**[A] stability:** stable (§1.2).

### 7.1 Budget

`budget = memory_share (0.25) × total`:
- `total` is physical RAM, or the container limit if lower;
- the budget covers engines and reply decoding;
- there is no CPU formula.

### 7.2 Estimates

`E = usual_peak × max(1, size_now / size_at) × (provisional_margin if provisional) + usual_reply × decode_factor (8 until BL-0082 measures it)`.
- An unmeasured large job runs alone.
- A cheap job's estimate is the interpreter baseline.

### 7.3 Admission rule (owner decision 2 as amended by answer 6; answers 1 and 3)

| Pressure | Large job | Cheap job | `recover` |
|---|---|---|---|
| `Normal` / `Unknown` | alone, or all measured or provisional and `Σ E ≤ budget` (on Windows also `E ≤ ullAvailPageFile`) | admitted (not while a head exists at `Warn`) | admitted |
| `Warn` | only if no other job of any kind runs | as large | admitted |
| `Critical` | only the Run-anyway chain | no | admitted |

**Rules:**
- A job alone is admitted at `Normal` or `Warn`.
- Running jobs are never stopped by admission (`MemoryAdmission`).
- **Run anyway** (review 3 F10; TLA v2 M6):
  - It applies to a P1 job the user started and to **everything it waits for**: saves ahead in the lane, the previous save's R4, a preview's rebuild, a re-plan's rebuild.
  - These run one at a time, at any pressure, as the one chain.
  - The chain ends when the chosen job ends or is cancelled.
  - It is logged; property `RunAnywayProgresses`.
- The amendment to decision 2 is the owner's (answer 6).

### 7.4 The pressure signal per OS

| OS | Source | Warn | Critical |
|---|---|---|---|
| macOS [A] | `kern.memorystatus_vm_pressure_level` | 2 | 4 |
| Linux [M11] | PSI (cgroup, then system); fallback `MemAvailable` | `some avg10` ≥ 10 % | `full avg10` ≥ 5 % |
| Windows [M11] | `CreateMemoryResourceNotification`, `GlobalMemoryStatusEx` | load ≥ 90 % | notification, or commit below the smallest estimate |

`Unknown`: budget only, logged once.

### 7.5 After a memory death

`single_engine` holds until 20 consecutive healthy large jobs end at `Normal`, or until the next start. The usual peak is raised, and the poison counter moves.

### 7.6 Containers and hosting

- **Containers:** the budget and PSI come from the cgroup.
- **[Hosted]:** the single pool stays; jobs carry their user; per-user fairness is deferred; files live under each user's root.

## 8. Liveness and progress [A]

**[A] stability:** stable (§1.2); review 4 M1 and M4 are folded in.

### 8.1 Protocol v2

`PROTOCOL_VERSION` = 2. Per request: zero or more `progress` messages, at most one `gate` (saves, creates), then exactly one final reply.

- **`progress`:** `{seq, phase, cpu_ms, children_cpu_ms, peak_rss_bytes, done, total}`.
  - `children_cpu_ms` is the leader's `getrusage(RUSAGE_CHILDREN)` (review 4 M1).
  - Phases: `starting`, `reading_files`, `parsing`, `booking`, `plugins`, `validating`, `querying`, `waiting_for_lock`, `verifying`, `building_mirror`, `publishing`, `committing`, `serializing`, `recovering`, `confirming`.
  - `waiting_for_lock` is the only declared wait.
  - For `reading_files`, `done` counts bytes.
- **`gate`/`proceed`** (§5.4).
- **Final reply:** `usage`, and a `decision` object (§9.3).
- **Domain outcomes are results:** `snapshot_moved {cause}`, `orphan_journal {session, job}`, `superseded`, `nondeterministic`, `mismatch`, `undecided`, `plan_outdated`, `legacy_lock_refused {outcome}`, `overlap {ws}`, `result_too_large`.
- **The output channel is locked per line.**
- **The request** carries `job {id, kind, ws_key, attempt}` and no time limit; `EnginePort.submit(job) -> JobHandle`.
- **Versioning:** an added optional field does not bump the version.

### 8.2 The watcher and on-time ticks

Ticks run on the scheduler thread's queue, every `tick` (1 s); the next schedule is the previous wake plus `tick`.

**A tick is on time when:**
- it woke within `tick/2` of its schedule (`Clock.monotonic`);
- `Clock.monotonic_with_sleep` advanced at most `1.5 × tick`;
- **and the sidecar kept up**: no unread bytes in the pipe (`FIONREAD` or `PeekNamedPipe`) while the reader read nothing (spike C).

Clocks: macOS `CLOCK_MONOTONIC` (verified); Linux `CLOCK_BOOTTIME`; Windows `QueryInterruptTime` (verify). App Nap: the shell holds an activity, and [A] verifies that the sidecar's ticks stay on time with the window hidden.

**Samples, taken from outside:**
- **Group CPU, a monotone accumulator with two parts that only grow** (review 4 M1):
  - live members, keyed by (pid, start time): the increase of their own `user + system` (clamped at 0); a new member adds its whole total;
  - reaped descendants: the increase of the leader's `children_cpu_ms`, plus, on Linux, the increase of each non-leader member's `cutime + cstime`.
  - A child sampled alive and later reaped is counted twice, so the measure may exceed true CPU by at most 2×; it never misses a reaped child and never falls. Spike L (review 4): outside sampling alone saw 0.31 s of 7.85 s spent in 40 ms subprocesses.
  - Windows: the Job Object's total user and kernel time.
- RSS and page-ins (per member: macOS `pageins`, Linux `majflt`, Windows the job's page faults);
- process status;
- system idle CPU;
- bytes the reader read (discarded bytes after a violation do not count).

These feed `liveness.judge`, a pure table that is logged.

### 8.3 Hung

Hung means `hang_ticks` consecutive on-time ticks, each with:
- no bytes;
- group CPU under 1 % of the tick;
- at least half a core idle;
- no page-ins.

A `stopped` process counts even on a busy machine; Linux `D` never counts. The same rule applies to a starting engine and to a draining leader. The verdict is the end `hang`, a stop (`drain` while draining).

`hang_ticks = max(10, 3 × usual quiet stretch)` (provisional: × `provisional_margin`). This counts observations of a condition healthy runs never show; it is not a time budget (spike s4).

### 8.4 Stalled

Stalled means progress bytes arrive but, for `stall_ticks` consecutive on-time ticks, group CPU stays under 1 % and `phase`, `done` and `total` do not change, in a phase other than `waiting_for_lock`. Examples: a plugin blocked on the network; a cloud placeholder that never downloads.

`stall_ticks = max(30, 3 × usual stalled stretch)`.
- **Interactive:** a status with Cancel.
- **Background:** stopped (`stalled`, poison), never when unmeasured, never past the commit point (owner answer 2).
- **After a violation past the commit point:** `stall_ticks` with the phase frozen, **whatever the CPU**, end the engine (`stalled_after_violation`, §5.4).

### 8.5 Runaway (owner decision 4)

Background job stopped when group CPU > `max(10 × B, B + 10 s)`, where B is the usual CPU (§1.1). Unmeasured and provisional jobs: no bound. Interactive jobs past it are flagged "much longer than usual".

### 8.6 Learning

`measurements.update` runs after each run; the file is written atomically; an unreadable file reads as unmeasured.

### 8.7 Supervision: per engine, pool, poison

**Per engine** (`engine_process`):
- **States:** `Spawning → Handshaking → Clean → Running(job, phase, past_commit_point) → Draining(normal | discard) → Gone`, plus `Ending(reason)`.
- **Events:** `Spawned`, `SpawnFailed`, `HandshakeOk`, `HandshakeForeign`, `Dispatch`, `Progress`, `Gate`, `Proceed`, `Reply`, `Verdict`, `Cancel`, `End(reason)`, `OutputEnded`, `ZombieSeen`, `GroupDrained`, `Reaped`, `GroupGone`, `Violated`.
- Past the commit point, `End` is emitted only for `hang` and `stalled_after_violation`.
- The [C] rows are provisional until [C] (§1.2).

**Pool:**
- backoff (0.5, 2, 8 s);
- `max_crashes` (3) stops in `window` (300 s) make it `Unavailable` until a stop leaves the window, or Try again;
- the window runs on `monotonic_with_sleep`.

**One ledger cannot trip the pool:**
- a fault counts only if it is a start failure, a cheap job's fault, or the first at that workspace's content fingerprint;
- the last `max_crashes` fingerprints are kept;
- a pre-fingerprint fault is keyed by the stat fingerprint.

Properties: `OneLedgerCannotTripPool`, `BoundedFaultLoop`, `PoolNoHeavyWhileUnavailable`, `OwnKillNotAStop`.

**Poison:** per (workspace, kind, fingerprint); at 2, the key is refused until the fingerprint changes or Try again.

**Settings** (app-level, logged as inputs; refused outside their range):

| Setting | Default | Range |
|---|---|---|
| `max_engines` | 2 | 1–8 |
| `memory_share` | 0.25 | 0.05–0.9 |
| `decay` | 0.9 | 0.5–0.99 |
| `provisional_margin` | 1.5 | 1–4 |
| `runaway_factor`, `runaway_floor` | 10; 10 s | 2–100; 1–600 s |
| `beat`, `tick` | 1 s, 1 s | 0.25–5 s, `beat ≤ tick` |
| `hang_ticks`, `stall_ticks` floors | 10, 30 | 3–600, 10–3600 |
| `quit_patience` (also the switch's) | 10 | 3–600 |
| `idle_ticks` | 5 | 1–600 |
| `checkpoint_ticks` | 60 | 10–3600 |
| `max_crashes`, `window`, `delays` | 3; 300 s; (0.5, 2, 8) s | 2–10; 60–3600 s; 0–60 s |
| `poison_limit` | 2 | 1–5 |
| `single_engine_recover` | 20 | 1–1000 |
| `snapshot_retries` | 3 | 1–10 |
| `aging_passes`, `preempt_max` | 8, 2 | 1–100, 0–10 |
| `cheap_margin` | 32 MB | 0–1024 MB |
| queue bounds | §3.1; 64 | 1–1024 |
| pressure thresholds | §7.4 | 1–99 % |

## 9. What the user sees, the log, and replay

### 9.1 Messages [M2 UI; outcomes and codes from A/B/C]

| Situation | Message | Actions |
|---|---|---|
| switching | "Just a moment… switching workspaces." | — |
| switching, finishing saves | "Finishing your changes before switching… (n left)"; after `quit_patience`, **Switch anyway** with "Finzytrack will finish or undo the unfinished change the next time you open *A*. Your ledger stays safe." | Cancel (before Switch anyway), Switch anyway |
| open refused: overlap | "This ledger uses a file that belongs to workspace *X*. A file can belong to only one workspace." | Go back, Choose another workspace |
| set grew into another workspace's file | "This ledger now uses a file that belongs to workspace *X*. Saving is off until that changes." | Copy diagnostics |
| workspace with an unfinished change (list) | "Has an unfinished change. Open it to finish." | Open |
| queued | "Waiting for other ledger work to finish…" | Cancel |
| waiting to run alone | "Waiting for other ledger work to finish, so this can run on its own…" | Cancel |
| waiting for memory | "Waiting for memory. Your computer is low on memory. Closing other apps helps." | Cancel; **Run anyway** |
| running, per phase | "Reading your ledger files…", "Checking your ledger…", "Running your query…", "Saving…", "Updating your views…", "Checking your last change…" | Cancel where allowed |
| `waiting_for_lock` | "Waiting for other ledger work on these files to finish…" | Cancel |
| downloading | "Waiting for a ledger file to download from your cloud storage…" | Cancel |
| legacy lock refused | "The old Finzytrack app is saving. Quit the old Finzytrack first, then try again." | Try again |
| much longer than usual | "This is taking much longer than usual for this ledger." | Cancel |
| no progress | "No progress for a while. It may be waiting for something." | Cancel |
| cannot be ended | "Finzytrack is waiting for a disk and cannot stop this yet. It will continue when the disk answers." | Copy diagnostics |
| Cancel past the commit point | "Finishing your change…", then "Your change is saved." | Undo where available |
| save cancelled | "Your change was not saved. Your ledger is unchanged." | — |
| plan outdated | "Your earlier change moved this entry. Review it again." | Review |
| busy | "Finzytrack is busy with other ledger work. Please try again in a moment." | Try again |
| crash on a read | "The ledger engine stopped while working on this. Your ledger files are safe." | Try again, Copy diagnostics |
| memory death | "Your computer ran out of memory while Finzytrack worked on this. Your ledger files are safe. Close other apps and try again." | Try again |
| hang | "The ledger engine stopped responding and was restarted. Your ledger files are safe." | Try again |
| save resolved after an engine death | "Your change was saved." / "…not saved. Your ledger is unchanged." / "…not saved, and your ledger was changed by another program." / "Finzytrack could not finish your change and put your files back as they were." / "A ledger file was changed by another program while Finzytrack was saving. Saving is off until you review it." | — / — / Review / — / Review |
| recovery failed twice | "Your last change may or may not be saved. Reopen the workspace to check." | Reopen |
| poisoned | "Finzytrack stopped trying: the ledger engine failed twice on this ledger. Your files are safe. Change the ledger or try again." | Try again, Copy diagnostics |
| pool unavailable | failure-modes E1's message | Try again, Copy diagnostics |
| ledger keeps changing | "Another program is changing your ledger. Finzytrack will update when it stops." | Try again |
| R4 stopped, cancelled or failed | "Finzytrack could not double-check your last change. Saving is paused until the check runs." | Run check now |
| R4 mismatch | "Finzytrack's check of your last change found a difference. Saving is paused. Your backup is here." | Show backup, Copy diagnostics |
| R4 undecided | "Finzytrack could not double-check your last change. Saving is paused. Your backup is here." | Show backup, Run check now, Copy diagnostics |
| R4 nondeterministic | "Your ledger's plugins give a different result each time they run. Finzytrack updated your views from a fresh load." | Learn more |
| quit during a save | "Finishing your last change…"; then **Quit anyway** | Wait, Quit anyway |

### 9.2 Log events (safe kinds only)

Grouped by area; every field is a safe kind (§6A).

**Jobs**
- `engine.job.ended` (info: kind, outcome, `duration_ms`, `queued_ms`, `admission_wait_ms`, `cpu_ms`, `peak_bytes`, attempt, `ws`);
- `engine.job.cancelled` (info: reason), `engine.job.preempted` (info), `engine.job.flagged` (info);
- `engine.job.runaway_stopped`, `engine.job.stalled_stopped` (warning).

**Engines and the pool**
- `engine.stopped` (warning: `crashed`, `hang`, `protocol_violation`, `stalled_after_violation`, `start_failed`);
- `engine.drained` (debug);
- `engine.confirm.started` (debug);
- `engine.killed_by_system`, `engine.out_of_memory` (warning);
- `engine.pool.single_engine` (warning) and `…_ended` (info);
- `engine.unavailable`, `engine.unkillable`, `engine.workspace.poisoned` (error).

**Admission and queue**
- `engine.admission.waiting` and `engine.admission.run_anyway` (info);
- `engine.pressure.changed` (info) and `engine.pressure.unknown` (info, once);
- `engine.queue.busy` (info, once per drain).

**Locks, reads and the mirror**
- `engine.lock.waiting` (info: job kind, lock count), `engine.lock.acquired` (debug: `waited_ms`), `engine.lock.relocked` (debug);
- `engine.legacy_lock.refused` (info: outcome);
- `engine.snapshot.moved` (debug; info when retries run out);
- `mirror.publish.refused_stale`, `mirror.generation.published` (info).

**R4**
- `engine.r4.passed` (debug);
- `engine.r4.superseded` (info: `disk_moved` | `engine_changed`);
- `engine.r4.mismatch` and `engine.r4.undecided` (error);
- `engine.r4.nondeterministic` (warning).

**Saves, quiesce and the switch**
- `engine.commit.finishing` (info);
- `engine.commit.resolved` (warning: resolution);
- `engine.quiesce.quit_anyway` (warning);
- `workspace.switch.started` (info: from, to), `workspace.switch.waiting` (info: what, count), `workspace.switch.anyway` (warning: ended counts), `workspace.switch.ended` (info: outcome, `duration_ms`);
- `workspace.closed`, `workspace.opened` (info: phase);
- `workspace.overlap_refused` (warning: other `ws`).

**Durability and settings**
- `fs.durable_sync.fallback` (warning, once per volume);
- `settings.refused` (warning).

**Removed from draft 3** (cross-workspace): `engine.commit.resolved`'s `owner_ws` field and the shared-file repair notice.

### 9.3 Replayable decisions (requirement; owner, 2026-10-26)

**Goal:** rebuild every decision from a user's log, with no ledger content in it.

**R1. Pure tables, one thread** [A]:
- **Sidecar tables:** `next_dispatch` (with admission), the pool table, the per-engine statechart, `liveness.judge`, `measurements.update` and the estimate, and `workspace_switch` [B].
- Each is `(state, event) -> (state, effects)`, with no clock, random value, environment or global; clocks arrive as values.
- They run on **one scheduler thread** from one queue. `seq` is assigned when an event is taken, and its `sched.input` line is written before the table runs.
- Only ordered collections are used; replay is tested under three hash seeds.
- Each table has a `table_version`.
- **Engine-side decisions are inputs** (review 4 M7). `resolve`, the gate's branch, 2a's per-file verdicts, the verify outcome and relock count, R4's three digests and outcome, the overlap result and the legacy-lock outcome are pure functions **in the engine**. Every `gate` message and final reply carries `decision {table, inputs, output}`, with content-free inputs (hashes, counts, which check failed), and the sidecar logs it as a `sched.input` (table `engine.<name>`, with the engine module's `table_version`) before acting. The replay runs the same functions.
- **The sidecar's own disk reads are events:** `RecordsRead {closed, mismatch, resolved}`, `FocusChecked {stat_fingerprint}`, `MarkerRead {switch marker}`, `OverlapRead {sets}`, `ForgetChecked {journal, record}`.

**R2. Logged lines, content-free** (S0039 redaction unchanged):
- `sched.input`: the draft-3 fields, plus `children_cpu_ms` and the decision object's fields.
- `sched.settings`: every §8.7 value, at start and on each change.
- `sched.decision`: effects and `state_digest`.
- `sched.checkpoint`: tables, measurements and settings; at start, every 1,000 inputs, and first in each rotated file.
- **Ticks are run-length encoded.** A run is consecutive ticks of one engine with the same class and no verdict or effect. Its line (class, count, CPU at the end) is written when the run ends, and as a partial line every `checkpoint_ticks`. A tick that changes a verdict or causes an effect ends the run and is logged on its own line before its effects (review 4 M7). The classifier is part of `liveness.judge`; raw samples go to the flight recorder only.
- A state-preserving refusal is logged as a count; a `seq` gap is reported.

**R3. Replay test** [A]: every scenario test is a recording and replays with no divergence and no unexplained input, under three hash seeds. This includes a write-path scenario (gate refusal, 2a, recovery) [C] and a switch scenario [B]. A Hypothesis walk checks `replay(record(walk)) == walk`.

**R4. Flight recorder** [A]: a ring of 4,096 events or 1 MiB, dumped on error events and on `hang`, `stalled`, `runaway` and `unkillable`.

**R5. User-log replay tool** [M2, BL-0093].

**R6. Data-dependent reproduction** [M2+, BL-0060, BL-0093]: by a consented, anonymized copy, compared by its fingerprints when byte-identical, by structure otherwise.

**R7. Limits:** decisions are replayed given inputs as observed. Real thread and signal timing is covered by TLA+ and property tests; OS behaviour is not replayed; non-deterministic plugins are not reproduced.

**Volume** (review 4 M7, measured on a sample line):
- an input line is about 870 bytes and a decision line about 320;
- a heavy job is about 25 lines, about 15 KB;
- a heavy day (about 200 jobs) is about 3 MB;
- 10 MB × 5 rotation keeps about 2 weeks. BL-0093 decides whether `sched.*` gets its own rotation.

**ARCHITECTURE §6A changes** ([A] slice): §6A.1, a new §6A.7 (R1–R7, events, run rule, checkpoints, recorder, volume), §6A.5, §6A.6, §6A.4 [M2], §2.5. ADR-0039 is amended.

## 10. Cross-platform behaviour (ADR-0061)

| Concern | macOS (first) | Linux | Windows |
|---|---|---|---|
| Our end of an engine | group signal `SIGKILL`, only before the reap | same | `TerminateJobObject` |
| End with the sidecar | watchdog, then a group signal to its own group | same | Job Object kill-on-close |
| Confirmed gone | reaped, and the group probe gives `ESRCH` (`EPERM` = only zombies or foreign members) | same | job active count 0 |
| Durable sync | `F_FULLFSYNC` (about 4.5 ms; spike I) | `fsync` | `FlushFileBuffers`, `MOVEFILE_WRITE_THROUGH` |
| Canonical path and path key | `F_GETPATH` via `O_EVTONLY`; NFD plus casefold per volume | `realpath`; folding directories and mounts | `GetFinalPathNameByHandleW`; ordinal folding |
| OS memory response | jetsam `SIGKILL`, after compression and swap in most cases | OOM killer; `oom_score_adj` +500 | allocation fails |
| Pressure | memorystatus sysctl | PSI, or `MemAvailable` | low-memory notification, commit |
| Per-job peak and CPU | `waitid(WNOWAIT)` and footprint, then `wait4` | `wait4` (kB) | job accounting |
| Group CPU | live members (psutil, `proc_listpids`) plus the leader's `RUSAGE_CHILDREN` | live members plus `cutime/cstime` | Job Object total |
| Page-ins | `pageins` | `majflt` | job page faults |
| Sidecar behind on the pipe | `FIONREAD` (verified) | `FIONREAD` | `PeekNamedPipe` |
| Sleep-counting clock | `CLOCK_MONOTONIC` (verified) | `CLOCK_BOOTTIME` | `QueryInterruptTime` (verify) |
| Ticks delayed by the OS | App Nap; the shell (or the sidecar) holds an activity | — | — |
| Bytecode | an engine-private prefix, filled once per build | same | same |
| Lock released after our end | at the end of the group | same | may lag; waited for |
| Legacy-lock try | portalocker 3.2.0 `flock` | same | portalocker 3.2.0 `msvcrt.locking`, first 64 KiB |
| Start cost | 0.05–0.10 s, 34 MB | expected similar | BL-0087 [M1b], [M11] |
| Background OS priority | `nice +10` only for P3 jobs nothing can wait on; R4 and background writers at normal priority | same | `BELOW_NORMAL_PRIORITY_CLASS`, same jobs |

The write-path parts are designed for Windows now and tested from M1b (BL-0087): `durable_sync`, `path_key`, the Job Object, and the lock lag (via a fake port).

## 11. The TLA+ models for draft 4

### 11.1 The models and the deltas to apply

The models are:
- `formal/tla/EngineScheduler.tla`, which is tla/v3's `EngineScheduler.tla` with the deltas below;
- `formal/tla/Admission.tla`, as in v2/v3, plus delta D10.

**Draft 4 and the models agree** once these deltas are applied (the slice, or the agent preparing the long run, applies them):
- **D1. One open workspace and the switch** (§4.10).
  - Two workspaces with **disjoint** files: ws1 = {2, 3} plus a glob that may match file 1; ws2 = {4}.
  - An `open` variable (one workspace, or none during a switch) and the switch actions:
    - `SwitchRequest(w)`, `SwitchDrain` (ends reads, rebuilds and R4s of the old workspace; lets its queued saves and their R4s run);
    - `SwitchAnyway` (ends saves past their commit point and recovers, reason `switch`);
    - `SwitchClose` (enabled when no engine of the old workspace remains);
    - `SwitchOpen` (overlap check, on-open recovery, R4 from the record, WARMING).
  - The switch marker is a persisted variable, and `Restart` resumes from it.
  - Dispatch requires `job.w = open`.
  - Properties: `AtMostOneOpenWorkspace`, `NoJobOfClosedWorkspace`, `SwitchEnds` (liveness).
  - Seeds: `switch_open_before_close`, `switch_close_while_saving`, `switch_skips_recovery`, `switch_accepts_new_jobs`.
- **D2. No journal index.** v3's index semantics and `ForeignRecovering` are removed; the journal check is `JOpen(w)` over the workspace's own journals. The **overlap** config gives ws2 = {2, 4}; `SwitchOpen` must refuse it (`OpenFailed`). Seed `overlap_allowed` (the open admits it) must fail `PublishedIsCommittedState` or `VerifiedReadsAreSnapshots` with a crash past the commit point in ws1. It replaces v2/v3's `journal_check_per_ws`. Property `NoFileInTwoWorkspaces`.
- **D3. Rebuild FIFO and the re-plan's rebuild** (review 4 delta 6, single workspace).
  - `cq` holds rebuilds, and R4 tokens placed right after their save.
  - A re-plan with a stale mirror enqueues a rebuild directly ahead of the save and waits for its publish; a failed rebuild ends the save.
  - Property `ReplanProgresses`; seed `replan_ahead_of_rebuild` (`DEADLOCK` or `TEMPORAL`).
  - "Ours" requires a record newer than the plan's base (review 4 O3).
- **D4. Stall after a violation regardless of CPU** (delta 8): `Kill(stallv)` stays enabled after `Violate` past the commit point. Seed `stall_needs_low_cpu` (stall only when an environment flag `cpuIdle` holds) must fail `RunningWriteEndsOrBlocks` (`TEMPORAL`).
- **D5. Confirm in a fresh engine; re-queued R4; undecided** (delta 9; review 4 M2).
  - The confirm takes the R4 job's slot in a new, clean process record.
  - A `FINGERPRINT_GAP` environment flag makes the "equal but not the record" branch reachable: a re-queued R4, then `undecided` → `.mismatch`, with blocking.
  - An `Acknowledge` user action clears it.
  - Seed `confirm_in_used_engine` must fail `CleanEngineForUserCode`.
- **D6. Path keys.** One key per file whether present or not. Seed `created_file_alias` (file 1's key before `CreateFile` differs from after) must fail `R4NeverSupersededByItsOwnSave` or `OwnCommitsNeverExternal`. Seed `lock_key_alias` keeps target `NoTwoWritersMidCommit` in an orphan config.
- **D7. The legacy lock** (review 4 H3).
  - One `LegacyHolder` flag per lock file, with `OldAppHold`/`OldAppRelease` environment actions on Copy sources only; `OldAppSave` writes a Copy source while it holds the lock.
  - A save or create tries each distinct lock file once.
  - Properties: `NoFalseLegacyRefusal` (a refusal only when an outside holder exists, or an error flag is set) and `CopyIsSnapshotOfSource` (a completed Copy equals a source state).
  - Seeds: `legacy_tried_twice` (the same file twice in one engine) must fail `NoFalseLegacyRefusal`; `copy_no_recheck` must fail `CopyIsSnapshotOfSource`.
- **D8. Recovery's durable order.** `.resolved` is a journal state: recovery restores, then renames to `.resolved`, and a crash between the two re-runs the recovery. With no power loss in the model, the order matters only for crashes, which the model checks. Power loss is §11.7's.
- **D9. `B_verify_no_locks` reachable.** v3 notes it cannot fail under ADR-0062 (its counterexample needed the legacy two-file write). Draft 4 adds an environment with an orphaned engine of a crashed session mid-commit over two files, beside a verify of the new session. If TLC still cannot violate it, the seed is dropped, and the spec states that the verify's locks are needed only against orphans, so its lane rule 2 makes the locks belt-and-braces in one session.
- **D10. `Admission.tla`** loses multi-workspace fairness: one queue per user. `A_M_warn_no_cheap` (v3: cannot fail) is dropped; the Warn rule for cheap jobs is a pure-table test.
- **D11. Kept as in v3:** the parse-only confirm outcomes, R4Refresh, the `.mismatch` refusal through the dispatch block (equivalent to the gate's refusal for every property), `single_engine` lasting the session (it only restricts concurrency), the draft-1 seeds, and the policy cuts of ADR-0062.

### 11.2 Scope

| | In the model |
|---|---|
| **Actors** | one sidecar (crash, restart from the switch marker, quiesce, Quit anyway); up to 3 engine records over 2 slots; orphaned engines and their watchdog; the user (cancel save, cancel R4, Run check now, Run anyway, Quit anyway, `SwitchRequest`, `SwitchAnyway`, switch `Cancel`, `Acknowledge`); a lock-ignoring editor; a sync client creating a file a glob matches; `PluginDrift`; `FINGERPRINT_GAP`; pressure N/W/C; cache purge; the old app as a lock holder that may write Copy sources |
| **World** | files 1–4 as version tags (`ABS` = absent); ws1 = {2, 3} plus glob {1}; ws2 = {4} (overlap config: {2, 4}); lock keys 1–5 (5 = seeded alias); Copy source files S1, S2 (D7 configs only) |
| **Jobs** | `commit`, `create` (with Copy), `rebuild`, `query`, `preview`, `r4` (and its confirm), `recover` |
| **Mechanisms** | one open workspace and the switch; overlap refusal at open; per-file path-key locks in order; non-blocking relock; per-workspace journal check; verified reads; conditional publish; ids at publish; high-water mark outside the cache; the gate's checks; the agreed commit point; the closed record; journal states `.open`, `.closed`, `.done`, `.mismatch`, `.resolved`; R4 split, exempt and refreshed; fresh-engine confirm; undecided; reads beside a rebuild; previews after saves; `create` in COLD; re-plan by end-and-requeue behind its rebuild; violation read-and-discard; stall end regardless of CPU; drain; recovery retried once, locks first; quiesce; the Run-anyway chain; poison per kind; pool stops capped per (ws, fp); the legacy try once per lock file, with the Copy re-check |
| **Admission.tla** | `next_dispatch` over sizes and classes: reservation, higher-class passing, aging, Run anyway as head |
| **Abstracted** | bytes as tags; one commit writes all its targets; hang/stall/runaway as budgeted nondeterministic ends; priorities and memory arithmetic only in `Admission.tla`; no power loss; no cheap jobs, spare engine or separate `plan`; path keys as one key per file; R4's queue position only as "after its save" (the gate, not the position, enforces `R4BeforeNextCommit`; the exemption enforces its progress) |

### 11.3 Safety properties (invariants, plus TLC's deadlock check)

**Workspaces:** `AtMostOneOpenWorkspace`, `NoJobOfClosedWorkspace`, `NoFileInTwoWorkspaces`.

**Lanes and resources:** `OneExclusivePerWorkspace`, `RunningUntilAnsweredOrDead`, `EnginesBounded`, `MemoryAdmission`, `PoolNoHeavyWhileUnavailable`, `OneLedgerCannotTripPool`, `OwnKillNotAStop`, `AnsweredJobNotAStop`.

**The mirror and reads:** `PublishOnlyCurrent`, `GenerationsMonotonic`, `PublishedIsCommittedState`, `PublishedNotOlder`, `VerifiedReadsAreSnapshots`.

**Saves:** `GateOnCurrentMirror`, `NoCommitWithoutBaseline`, `PreviewAfterEarlierSaves`, `OwnCommitsNeverExternal`, `NoKillAfterCommitPoint`, `CancelBeforeCommitLeavesDiskUnchanged`, `NoWriteAfterUnresolvedDeath`, `NoTwoWritersMidCommit` (orphans), `CleanEngineForUserCode`, `OnlySavesLockAcrossUserCode`.

**R4:** `R4BeforeNextCommit`, `R4NeverBehindItsGatedWrite`, `R4NoFalseMismatch`, `R4OutcomeDefined`, `R4NeverSupersededByItsOwnSave`.

**Outcomes and recovery:** `OutcomeTruthful` (across switches and restarts), `AckedCommitNeverRolledBack`, `RecoveryNeverClobbersForeignBytes`, `BlockedOnlyOnForeignBytes`, `NoSpuriousBlock` (review 4 H5, single-workspace part: `blk` only after a mismatch or undecided R4, an R4 that cannot run, a recovery that died twice, or foreign bytes).

**Legacy:** `NoFalseLegacyRefusal`, `CopyIsSnapshotOfSource`.

**`Admission.tla`:** `NoClassInversion`.

### 11.4 Liveness properties

`QueuedWriteLeavesQueue`, `RunningWriteEndsOrBlocks`, `QueriesEnd`, `PreviewsEnd`, `RecoveryResolves`, `MirrorEventuallyCurrent`, `R4EventuallyRuns`, `QuiesceTerminates`, `SlotEventuallyFreed`, `RunAnywayProgresses`, `ReplanProgresses`, `SwitchEnds`, `EveryWaitHasAnExit` (approximated: deadlock check plus this set; also a pure-table test). In `Admission.tla`: `NoStarvationUnderAdmission`.

### 11.5 Seeded bugs (each must fail its target)

**Carried from v3:**

| Seed | Must fail |
|---|---|
| `r4_behind_queued_write` | `R4NeverBehindItsGatedWrite` / TEMPORAL |
| `r4_load_locked` | `OnlySavesLockAcrossUserCode` |
| `r4_no_recheck` | `R4NoFalseMismatch` |
| `relock_out_of_order`, `lock_unsorted` | DEADLOCK |
| `stop_reading_after_violation` | DEADLOCK |
| `kill_on_violation_past` | `NoKillAfterCommitPoint` |
| `preview_before_saves` | `PreviewAfterEarlierSaves` |
| `admit_at_warn` | `MemoryAdmission` |
| `commit_in_cold` | `NoCommitWithoutBaseline` |
| `no_replan` | `OwnCommitsNeverExternal` |
| `quitanyway_says_saved` | `OutcomeTruthful` |
| `recover_journal_early` | `RecoveryNeverClobbersForeignBytes` |
| `lock_key_alias` | `NoTwoWritersMidCommit`, orphan config |
| `pass_head`, `s6_noreserve` | TEMPORAL (`Admission.tla`) |
| draft 1's `lane_on_kill_sent` | `RunningUntilAnsweredOrDead` |
| draft 1's `unconditional_publish` | `PublishOnlyCurrent` |
| draft 1's `id_at_dispatch` | `GenerationsMonotonic` |
| draft 1's `one_sided_gate` | `CancelBeforeCommitLeavesDiskUnchanged` |
| draft 1's `stops_per_engine` | `PoolNoHeavyWhileUnavailable` |
| draft 1's `verify_no_locks` | `VerifiedReadsAreSnapshots` (D9 environment, or dropped) |
| draft 1's `gate_no_mirror` | `GateOnCurrentMirror` |
| draft 1's `r4_not_persisted` | `R4BeforeNextCommit` |
| draft 1's `hw_in_cache` | `GenerationsMonotonic` |
| draft 1's `cancel_after_point` | `NoKillAfterCommitPoint` |
| draft 1's `lane_on_requester_left` | `OneExclusivePerWorkspace` |
| draft 1's `verify_no_journal_check` | `VerifiedReadsAreSnapshots` |
| draft 1's `dispatch_while_unresolved` | `NoWriteAfterUnresolvedDeath` |
| draft 1's `reason_after_kill` | `OwnKillNotAStop` |
| draft 1's `admit_under_pressure` | `MemoryAdmission` |

**New in draft 4:**

| Seed | Must fail |
|---|---|
| `overlap_allowed` | `PublishedIsCommittedState` / `VerifiedReadsAreSnapshots` |
| `replan_ahead_of_rebuild` | DEADLOCK / TEMPORAL |
| `stall_needs_low_cpu` | TEMPORAL |
| `confirm_in_used_engine` | `CleanEngineForUserCode` |
| `created_file_alias` | `R4NeverSupersededByItsOwnSave` |
| `legacy_tried_twice` | `NoFalseLegacyRefusal` |
| `copy_no_recheck` | `CopyIsSnapshotOfSource` |
| `switch_open_before_close` | `AtMostOneOpenWorkspace` |
| `switch_close_while_saving` | `NoJobOfClosedWorkspace` or `NoTwoWritersMidCommit` |
| `switch_skips_recovery` | `NoWriteAfterUnresolvedDeath` or `PublishedIsCommittedState` |
| `switch_accepts_new_jobs` | `NoJobOfClosedWorkspace` |

**Dropped:**
- `journal_check_per_ws`, replaced by `overlap_allowed`;
- review 4's `index_after_journal`, `index_never_cleared` and `xws_block_forever` (moot: no index, no cross-workspace wait);
- `query_during_recovery`, `recover_no_retry` and `A_M_warn_no_cheap` (they cannot fail);
- `twows*` configs, replaced by `F_switch` and `F_overlap`.

The leave-one-out `M_*` configs remove each fix in turn.

### 11.6 Abstractions, and why none bears on safety or liveness

- **Priorities and memory arithmetic** live only in `Admission.tla`, which checks them deterministically. The main model's safety holds for every dispatch order, and its strong fairness per kind is what `NoStarvationUnderAdmission` establishes.
- **One commit writes all its targets.** The per-file order is S5's.
- **Hang, stall and runaway** are nondeterministic ends that may fire at any step. Safety must hold whenever they fire, and when they fire is the judge's tested behaviour.
- **Path keys** are "one key per file" in the model; the computation is the corpus test, and the seeded aliases show what breaks without it.
- **R4's queue position** is "after its save". The gate enforces `R4BeforeNextCommit`, and the exemption enforces `R4EventuallyRuns`. Its exact position among P3 jobs is a pure-table test.
- **`.mismatch` refusal through the dispatch block** is equivalent to the gate's refusal for every property.
- **No power loss** in this model (below).

### 11.7 What the models cannot show, and where it is covered

| Not shown | Covered by |
|---|---|
| power loss across the journal, backups, replaces, the closed record, the `.resolved` rename and the high-water mark. S5 predates the closed record and `.resolved`, so this draft does not claim S5 covers them. | §12 [C] fault injection and simulated power loss at every durable step; S5 extended with the closed record and `.resolved` in [C] (BL-0091) |
| aging's bound; merging; preemption limits; cheap jobs; spare engine; `plan` | pure-table tests |
| the liveness judge's numbers; the accumulator's 2× bound | `liveness.judge` tests (spikes s4, C, D, L) |
| the drain's order relative to the reap; no group signal after the reap | §12 [A] fake process port |
| replay | the replay test |
| Beancount semantics; the parse-stage claim | the corpus; ADR-0010's suite |
| `path_key` computation | the corpus test per OS |
| globs, documents roots, same-size saves | snapshot tests |
| Windows lock lag | fake port |
| unkillable processes (assumed to end) | stated cost |
| larger worlds | `XL` weekly |
| unsupported setups | removed by policy |

### 11.8 Removed by policy (ADR-0062; the one-workspace ADR)

| Removed | Was | By |
|---|---|---|
| legacy actor, `NeedsL`, `NoSilentLostUpdateLocked`, `LegacyWriterProgress`, `_twowslegacy`, TLA v2 H4, TLA F14 | a running legacy app writing a shared ledger | ADR-0062 (draft 3) |
| second-instance actor, `Fixed_second`, `Fixed_legacy` | a second v0.4 instance | ADR-0062 (draft 3) |
| the journal index (`jidx`), `ForeignRecovering`, cross-workspace recovery, `RECOVERING {owner_ws}`, `F_twows`/`LF_twows` with two workspaces sharing file 2, `journal_check_per_ws` | two workspaces sharing a file, several open | the one-workspace ADR (draft 4) |

What stays: orphaned engines after a sidecar crash, lock-ignoring editors and sync clients, the glob matching a newly created file within one workspace, crash recovery, and the legacy lock as an outside holder that is detected only.

## 12. Test obligations per phase

**[A]** amends ADR-0054 d4, ADR-0041 and ADR-0039, ARCHITECTURE §2.0, §2.5, §6A and §7, and failure-modes E1–E7, M1 and M7. Its tests:
- **The models and their runner.** Commit under `formal/tla/`:
  - `EngineScheduler.tla` and `Admission.tla` with the §11.1 deltas;
  - `gen_cfgs.py` and `gen_admission.py`;
  - `configs.txt`, with each config's expected outcome and size;
  - `run_all.sh`/`run_all.py`: parallel, resumable, signal-safe, and writing `results/RESULTS.md`, which ends with `ALL AS EXPECTED` or a list of surprises;
  - `HOW_TO_RUN.md`, `MAPPING.md` and `fetch_tools.sh`;
  - `tools.lock`: tla2tools 1.7.4, SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`, Java 21.
- **Runs**, using v3's measured sizes (configs named as in v3; new configs are sized by the runner's first run and placed by measured time):
  - **T1** (each under about 3 min; about 10 min in total): `A_*` except `A_large_*`; every `B_*` and safety `M_*`; `F_glob`, `F_linger`, `F_r4nondet`, `F_cold`, `F_r4`, `F_violation`, `F_recover`, `F_replan`, `LF_r4nondet`; the new `F_overlap` and `F_legacy` (expected S).
  - **T2** (up to about 40 min): `F_quit`, `F_pressure`, `F_beside`, `A_large_*`, M-sized liveness; the new `F_switch` and `LF_switch` (expected M).
  - **Weekly and at each milestone exit:** `F_large_one`/`two`, and the L/XL liveness configs.
  - **Before draft 4 becomes the design of record:** the T1 and T2 sets green, and `F_switch` finished at least once.
- **Pure-table tests:** the aging bound, merging, preemption limits, cheap jobs, the Warn rule for cheap jobs, `EveryWaitHasAnExit`, R4's queue position.
- **Tables:** exhaustive (state × event) tests and random walks for the per-engine table (its [C] rows included and unreachable in [A]) and the pool table.
- **`next_dispatch`:** aging; preemption; merging with requesters; spike s6's workload; a P1 job passes a P3 head; no cheap job at Warn while a head exists; the Run-anyway chain.
- **Protocol v2:** schema; one final reply; `children_cpu_ms`; `decision` objects; an added optional field needs no version bump.
- **Engines:**
  - H1's `plug.py`;
  - the same digest from two engines for a set-iterating plugin;
  - no `__pycache__` in the ledger folder;
  - **start cost with the spawn flags within 20 % of a start without them**, and the bytecode directory filled once per build (review 4 M3).
- **Reaping and draining:**
  - the pool alone reaps;
  - a `SIGKILL`ed child is never `answered`;
  - the peak minus the baseline is within 5 % of a ≥ 200 MB allocation;
  - the macOS footprint is read from the zombie;
  - a non-daemon thread plus a detached subprocess gives `answered`, no stop, and the slot freed;
  - **a fake process port records that no group signal is sent after the reap** (review 4 M5);
  - `EPERM` is not "gone".
- **End classification:** a recorded reason wins; the stop rules; stop fingerprints.
- **Liveness judge:**
  - a SIGSTOPped engine is hung;
  - a GIL-holding regex is not;
  - a sleeping plugin stalls;
  - an engine waiting on a CPU-bound child is not hung, and the accumulator never falls;
  - **a plugin running 200 subprocesses of 40 ms each: the accumulator is within 1× to 2× of the reaped total, and a background loop in it is stopped by the runaway rule** (review 4 M1);
  - a hang at Warn without page-ins is judged;
  - a full pipe makes ticks late;
  - the handshake is judged by the hang rule;
  - sleep is not counted;
  - a placeholder that never downloads stalls;
  - **a violation past the commit point followed by a busy loop ends after `stall_ticks`** (unit; [C] end to end).
- **Admission:** the three levels; recovery at Critical; `single_engine` recovery; `Unknown`; provisional estimates.
- **Usual:** the decay, invalidation, the provisional rule, the provisional `hang_ticks`, an unreadable file.
- **Pressure port:** the macOS sysctl; Linux and Windows from fixtures.
- **Process groups:** a cancel ends children; the sidecar's death ends the group; `oom_score_adj`.
- **Settings:** values out of range are refused.
- **Replay:** every scenario test replays under three hash seeds; the Hypothesis walk; a version change and a gap are reported; the partial run line; tick runs split at verdicts; `sched.*` in the redaction corpus.
- **Flight recorder:** bounded, dumped on its triggers, redacted.
- **App Nap:** the sidecar's ticks stay on time with the window hidden.
- **failure-modes.md:** rows E1–E7, M1, M7, and the known gaps.

**[B]:**
- **`path_key` corpus** per OS, including absent-equals-created, folding mounts, and `O_EVTONLY`.
- **Overlap refused** at register and at open; a set that grows into another workspace's file during the session refuses saves.
- **The switch** (§4.10):
  - every state × event of `workspace_switch`;
  - a switch with reads, a rebuild and an R4 running ends them, and the R4's record stays;
  - queued confirmed saves finish first;
  - Switch anyway after `quit_patience`;
  - Cancel returns to A;
  - retargeting;
  - B's overlap refusal with Go back to A;
  - a crash at each marker step completes at the next start;
  - A's closed record runs its R4 at A's next open;
  - no engine of A runs after Close;
  - the replay of a switch scenario.
- **Lanes:** rule 2's exemption (a save confirmed while another runs commits after the first save's R4); reads beside a rebuild; previews after saves; COLD and WARMING refusals; RECOVERING.
- **Verified snapshot:** outside saves; `LEDGER_CHANGING`; same-size saves; plugin changes; an orphan journal; a newly matched glob file sorting before a held lock (release and restart); an evicted placeholder; lock files never deleted.
- **Conditional publish, GC, and ADR-0015's 38,800-reads test with two engines building.**

**[C]:**
- **Legacy-lock detection** (review 4 H3):
  - a held lock refuses without waiting and writes nothing;
  - a free one is held and released;
  - an open error or a lock error refuses;
  - a lock file that appears, or a source that changes, during a Copy read refuses;
  - two installs sharing one data folder are tried once (free gives `free`);
  - a retired install is not tried;
  - the lock file is never created.
- **The gate:** the commit point (fault injection, the property race, `SIGTERM` ignored); each check refuses on its own.
- **Re-plan:**
  - proceeds on the same intent;
  - gives `PLAN_OUTDATED` on a moved entry;
  - a `saved_nopub` earlier save queues the rebuild, the save runs directly behind it, and it proceeds;
  - a failing rebuild ends the save with its outcome;
  - an outside revert to an earlier v0.4 state is not taken for ours;
  - retries out give `LEDGER_CHANGING`.
- **`create`** in COLD, with its record; Copy with the legacy rules.
- **The closed record:** close is a rename; saved iff the record exists; R4 re-armed after recovery, a restart and a switch; the in-memory gate re-read.
- **Durable order and power loss** (review 4 H2, ordering part): fault injection at every durable step of a save (backups, journal, each replace, close) and of a recovery (restores, the `.resolved` rename), plus a simulated power loss that keeps only synced state at each step. Every case ends with §5.7's outcome and no workspace left in `RECOVERING`; a recovery cut before its rename restores again.
- **`durable_sync`:** the macOS adapter issues `F_FULLFSYNC` (T2); the fallback is logged; Windows write-through.
- **R4:**
  - pass without locks;
  - superseded;
  - **a file-creating save (also an NFC-named file) ends `pass` and publishes** (review 4 H1);
  - the confirm runs in a fresh engine: a plugin thread still running, or a plugin that patches `beancount.parser`, changes neither the verdict nor runs under the locks;
  - mismatch, nondeterministic, undecided and its acknowledgement;
  - Cancel R4; Run check now clears poison;
  - `.picklecache` untouched;
  - `engine_changed`;
  - a cloud placeholder stalls R4's load;
  - the update's quiesce runs the owed R4.
- **Fault injection** (BL-0091): ends and SIGSTOPs after `proceed`; an editor save between the gate and the journal; recovery locks first; Q30.
- **Edge cases:** a recovery that dies is retried once then blocks; queued saves are refused on a block; violation read-and-discard; Quit anyway; Switch anyway with a save past its commit point, resolved at A's next open.
- **Windows runner** (BL-0087): Job Objects, the lock lag, `STATUS_NO_MEMORY`, `path_key`, `durable_sync`, start cost.

**[M2]:** BL-0092 (the UI states, including the switch's "Just a moment…" and Switch anyway); the job list; BL-0094; BL-0093; BL-0100.

**[M3]:** Copy adoption and retired installs (BL-0101).

**[M11]:** the Linux and Windows adapters; the Windows clock; the spare engine.

## 13. Where this departs from the reviews, and why

**Kept from drafts 1–3:**
- safety rests on locks and content;
- verify under the locks with content hashes, holding them only for the verify;
- ids at publish;
- the agreed commit point;
- liveness judged from outside;
- exact peaks from the reaped process;
- no cheap lane;
- poison per kind with the pool capped per (workspace, fingerprint) (review 2 F6 not adopted; evidence: the first TLA check's counterexample, confirmed by v2 and v3);
- the parse-only confirm's `nondeterministic` instead of `pass` (TLA v2 M4).

**The one-workspace policy** (the one-workspace ADR, S0045; owner, 2026-10-27: "No sharing anywhere, no multiple instances of Finzytrack anywhere. Period. Finzytrack is just about one instance, working with one workspace at a time.") removed:
- **The app-wide journal index** and its durable order, stale-entry cleaning, Windows write-through test and directory-creation syncs. The per-workspace journal check suffices because no journal can cover a file of another workspace's set (§4.10, end).
- **Cross-workspace recovery**, `RECOVERING {owner_ws}`, the shared-file repair notice, and **the INV-31 and INV-30 exceptions**. The owner's approval of the INV-31 exception (answer (b), 2026-10-27) is **moot**: nothing ever runs for a workspace that is not open, so no exception is needed and none is requested.
- **Re-plan triggers from another workspace's saves** (2a's "of any workspace").
- **Lanes across workspaces and multi-workspace fairness:** one queue per user, for the open workspace only.
- **Background jobs for workspaces that are not open:** owed R4s and recoveries wait for the workspace's next open; an update's quiesce runs only the open workspace's R4.
- **The model's two-workspaces-sharing-a-file environment** (`F_twows`, `LF_twows`, `journal_check_per_ws`), replaced by the switch and the overlap refusal.

**Review 4 findings made moot, with the reason:**
- **H1 (cross-workspace part):** another workspace's glob missing a save's lock or index entry cannot happen without shared files. **Not moot, and adopted:** the `path_key` split for a created file within one workspace (R4 superseded, 2a's lookup).
- **H2 (index part):** stale index entries and the index's order have no index to apply to. **Adopted:** the durable order of backups, journal, replaces and close, and recovery's restore-then-`.resolved` order.
- **H3 (two of our own engines contending):** only one exclusive job of the one open workspace runs step 0 at a time. **Adopted:** ADR-0062's every-install try with deduplication by path key, its fail-closed outcomes, the Copy re-check, the message, and retired installs.
- **H4 (cross-workspace trigger):** moot. **Adopted:** the single-workspace part, where the rebuild is enqueued ahead of the re-planned save (an earlier `saved_nopub`, or an outside edit beside our own save).
- **H5:** entirely moot. Nothing waits on another workspace's repair. `NoSpuriousBlock` is kept for the single workspace.
- **M6 (deltas 5 and 7, `F_twows`):** moot. **Adopted:** deltas 6, 8 and 9 (D3–D5), the power-loss note, and the cannot-show rows.
- **L1:** moot: no INV-30 or INV-31 exception.
- **L2 (closed workspaces):** moot. **Adopted:** the two-part update quiesce for the open workspace.
- **L3 (index syncs):** moot. The remaining count was recomputed: about 9 syncs, about 40 ms.
- **Review 4's two owner questions:** moot (both concerned cross-workspace repair).

**Review 4: adopted as proposed** M1, M2, M3, M4, M5 and M7, and O1–O8 and O10.

**Review 4: changed:**
- **O9:** the timer-refresh claim is withdrawn; there is no timer refresh, and a future one needs a bounded cache first.
- **O1 ("workspace opened" row):** became §4.10.

**The v3 model's notes:**
- **Unreachable undecided branch:** made reachable by `FINGERPRINT_GAP` (D5).
- **`B_verify_no_locks`:** given an orphan environment, or dropped with a stated reason (D9).
- **R4's queue position:** stated as an abstraction (§11.6).
- **"Even when not open":** gone by policy.

**Departures from the owner's numbers:**
- the runaway floor (for jobs under about 1.1 s of CPU);
- decision 2, amended by the owner's answer 6.

## 14. What changes elsewhere (in the slices that build each phase)

- **The one-workspace ADR (S0045)** decides the policy itself: one open workspace, no file in two workspaces, the overlap refusal at register and open. This spec relies on it and does not decide it.
- **ADR-0054 d4** [A]: the pool owns the engines and admission; the `JobQueue` holds the open workspace's lane; 34 MB per engine; protocol v2.
- **ADR-0041** [A]: the only process-wide entry is the pool.
- **ADR-0039** [A]: the decision log, checkpoints and flight recorder.
- **ADR-0011** [B]: the limiter becomes the open workspace's lane; the BQL cache is withdrawn.
- **ADR-0015** [B]: ids at publish, with the high-water mark outside the cache; both fingerprints and plugin code in meta; re-stamp; date triggers.
- **ADR-0017** [B]: the registry deduplicates by `path_key`, and keeps each workspace's file set for the overlap check.
- **ADR-0019** [B, C]:
  - the open table becomes `workspace_switch` (§4.10), with `CLOSING`/`OPENING`, the switch marker, the overlap phase and COLD;
  - `create`;
  - reads blocked in `RECOVERING`;
  - forgetting a workspace is refused while it has an open journal or a closed record;
  - the workspace list flags unfinished changes.
- **ADR-0008** [C]: R4 completes or is superseded; the gate's checks; the fresh-engine parse-only confirm; `nondeterministic`; `undecided` (`.mismatch {undecided}`, with its exit); the create baseline.
- **ADR-0010** [C]: a mismatch publishes `load_file`; a nondeterministic plugin does not block; the parse-stage claim.
- **ADR-0012** [B, C]:
  - F-6 restated;
  - `durable_sync`;
  - the durable order of a save and of a recovery (`.resolved`);
  - journal close as a rename into the closed record;
  - `(ws, session, job)` naming;
  - on-open recovery waits for the locks and reads after taking them;
  - in-session recovery, retried once;
  - Q29 as per-file locks on path keys with the non-blocking relock;
  - Q30 as a defence.

  Draft 3's journal index is **not** added.
- **ADR-0007** [C]: the update's quiesce runs the open workspace's owed R4 first.
- **ADR-0062** (already accepted, with its amendment): §5.4 step 0 is its runtime check; retired installs are not tried. No amendment by this spec.
- **INV-26:** as amended by ADR-0062.
- **INV-30 and INV-31:** **no amendment.** Draft 3's proposed INV-31 exception is withdrawn, with the owner's approval moot (§13).
- **ARCHITECTURE:**
  - §2.0 (protocol v2, supervision, spawn flags, bytecode directory);
  - §2.5 (the pool; `JobQueue` for the open workspace; `WorkspaceSwitch`, `Measurements`, `DecisionLog`, `FlightRecorder`; `EngineClient` per engine; the BQL cache row removed);
  - the `FileSystem` port (`canonical_path`, `path_key`, `durable_sync`);
  - §5 (engine groups and Job Objects);
  - §6.1 (codes: `WRITES_BLOCKED`, `PLAN_OUTDATED`, `LEDGER_CHANGING`, `LEGACY_LOCK_REFUSED`, `WORKSPACE_OVERLAP`, `WORKSPACE_NOT_READY {CLOSING|OPENING}`; `BUSY_HEAVY_LOAD` without `retry_after_ms`);
  - §6A (§9.3);
  - §7 (the per-engine and pool statecharts, the liveness judge, and the backend `workspace_switch` table under the FE switch machine);
  - §8.3 (the models; no legacy actor, second instance or shared files);
  - §9.1, §9.2 (the commit point, the gate, the closed record, the R4 gate, the legacy try);
  - §9.6 (the switch sequence of §4.10).
- **failure-modes.md** [A]: E1–E7 (E5, E7), M1, M7, and the known gaps. A switch cut off by a crash is resolved by the marker.
- **glossary.md:** heavy, large, cheap, lane, slot, head, open workspace, switch, path key, canonical path, closed record, commit point, usual, on-time tick.
- **capabilities.md/.toml:** cancel, job status, Run anyway, Run check now, switch.
- **state_registry** [A]: the `process` entry; `EngineBoot` removed; the flight recorder; `WorkspaceSwitch` [B].
- **Licence lists** [A]: `portalocker` 3.2.0 in the engine's, `psutil` in the main sidecar's.
- **User docs** [M2]:
  - one Finzytrack, one workspace at a time, no file in two workspaces;
  - quit the old app;
  - files a plugin reads itself;
  - plugins may run twice at once;
  - Rebuild now;
  - cloud placeholders;
  - a workspace with an unfinished change.
- **Backlog:**
  - BL-0091: §5.7, the durable orders, S5 extended with the closed record and `.resolved`; no index;
  - BL-0082: `decode_factor`;
  - BL-0087: Windows start cost, `durable_sync`, `path_key`;
  - BL-0093: §9.3 R5–R6, and whether `sched.*` gets its own rotation;
  - BL-0100: the app-instance lock;
  - BL-0101: retired installs.

## 15. Owner questions

None open:
- Review 4's two questions (repairing another workspace's interrupted save) are moot under the one-workspace decision.
- The owner's answers of 2026-10-27 stand: (a) true flush; (c) no legacy-lock wait, now ADR-0062.
- Answer (b), the INV-31 exception, is moot and withdrawn (§13).

"What is usual?" is answered in §1.1, and replayable decisions are in §9.3.
