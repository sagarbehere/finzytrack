# Engine scheduler: concurrency, admission, liveness and cancel

> **DRAFT 3 (2026-10-27), not yet adopted.** History:
> - **Draft 1** (d68ce933) was checked by design review 2 and a first TLA+ model.
> - **Draft 2** (2b770660) was checked by design review 3 (review:m1a/scheduler-design/review-3, F1–F15, O1–O14) and by a second TLA+ model (review:m1a/scheduler-design/tla/v2, H1–H5, M1–M7, L1–L2, overflow).
>
> This draft folds in both checks, the owner's three answers of 2026-10-27, and the owner's single-instance policy of the same day (the single-instance ADR, S0044, `design/decisions/ADR-0062-single-instance-no-legacy-coexistence.md` in the working tree). It is the version that the long model run checks (§11, §12). A later slice commits it with the amendments of §14. Until then, the accepted ADRs win where they differ.

## Orientation
- **What this is:** how the main sidecar runs ledger-engine work:
  - which jobs exist, which may run together, and in which order;
  - how many engine processes run, and how much memory they may use;
  - how a slow job is told from a sick engine;
  - what Cancel does and what the user sees;
  - how a rare failure is replayed from the log.

  It answers the area review's F1 and F2 (2026-10-26) and applies the owner's decisions of 2026-10-26 (`design/inbox/20261026-engine-concurrency-and-cancel.md`), the owner's six answers to review 2, the owner's three answers to review 3, and the single-instance policy.
- **What exists:** none of this yet. Today there is one `EngineClient` per user container, one request in flight, and a caller's time limit that counts as a crash (S0035, S0040, S0042). Every part below carries the phase that builds it:
  - **[A]** M1a, the scheduler slice, before the read core;
  - **[B]** M1a, with the mirror build;
  - **[C]** M1b, with the write contract and BL-0091;
  - **[M2]** with BL-0092, BL-0093, BL-0094 and BL-0100 (the app-instance lock);
  - **[M11]** the Linux and Windows sessions (ADR-0061);
  - **[Hosted]** deferred (ADR-0041).
- **Entry points (planned):**
  - `app.platform.engine_pool`: process-wide pool, budget, pressure, stop history; pure table `engine_pool.table`.
  - `app.platform.engine_process`: one engine's statechart.
  - `app.platform.adapters.engine_client`: one process's shell.
  - `app.platform.job_queue`: per-user lanes and queues; pure `next_dispatch`.
  - `app.platform.liveness`: the pure tick judge.
  - `app.platform.measurements`: the pure learning rule of §1.1.
  - Ports: `app.platform.ports.memory_pressure`, `process_probe`, `clock`, and `FileSystem` (gains `canonical_path` and `durable_sync`).
  - `app.platform.observability.decisions`: the decision log and the flight recorder.
  - Engine side: `ledger_engine.protocol` (protocol v2) and `ledger_engine.ledger.snapshot` (verified reads, conditional publish, the closed record, the journal index).
  - Models: `formal/tla/EngineScheduler.tla` and `formal/tla/Admission.tla` (§11).
- **Decisions it applies:**
  - ADR-0054: two processes; decision 4 amended here.
  - ADR-0041: one process-wide entry.
  - ADR-0008: the contract and its R4 amendment.
  - ADR-0010: the overlay load; guardrail 3.
  - ADR-0011: no resident ledger; write serialization.
  - ADR-0012: journal and recovery; Q29, Q30, F-1..F-8.
  - ADR-0013: atomic swap.
  - ADR-0015: atomic publish; WARMING.
  - ADR-0017: the registry.
  - ADR-0019: phases.
  - ADR-0061: platforms.
  - The single-instance ADR (S0044): no ledger shared with a running legacy app; legacy lock detected, never waited for; one v0.4 instance per app root.
  - The owner's decision 2, as amended by the owner's answer 6.
- **Invariants:** INV-01, INV-10, INV-11, INV-22, INV-26 (as amended by the single-instance ADR), INV-27, INV-30, INV-31 (exception in §5.7, owner-approved 2026-10-27).
- **Traps:**
  - A heartbeat proves nothing: liveness is judged from outside the engine (§8).
  - The lane does not keep the mirror correct; per-file locks on canonical paths, the content fingerprint and the app-wide journal index do (§5).
  - `realpath` is not one spelling per file on macOS (case, Unicode normalization); only `canonical_path` is (§1).
  - On macOS `fsync` does not reach the disk; only `F_FULLFSYNC` does (§5.4).
  - An engine that ran user code is never reused (H1).
  - `Popen.poll()` reports a killed child as exit 0 after our own reap, so the pool is the only reaper (§6.1).
  - `python -I` ignores `PYTHONHASHSEED` (§6.1).
  - "Usual" has one definition (§1.1).
- **Main tests (planned):** per phase in §12; the TLA+ models and their runner (§11, §12 [A]); the replay test (§9.3).

## In two minutes

Today Finzytrack sends the ledger engine one request at a time, and the only way to stop one is a time limit that counts as a crash. That does not work for what is coming: loading a 54,000-entry ledger takes about 2 seconds of full CPU, and several such jobs will want to run at once.

**What changes:**
- **Up to 2 engine processes** run ledger jobs at once (a setting). Each job that runs your ledger's code gets a **fresh engine process** that ends with the job: about 0.1 s and 34 MB on your Mac. One ledger's plugin can never leak into another ledger's job.
- **A queue in the main sidecar** decides what runs next:
  - jobs you are waiting for go first, and background checks last, but never starve;
  - a large job waiting for memory is not overtaken for ever by smaller ones, and it never blocks work of a higher class that fits;
  - jobs that would load the same ledger for the same question share one load.
- **Two jobs that change the same workspace never run at the same time.** Reads run beside the rebuilding of your views. A read that overlaps a save waits for it, and is used only if the files did not change while it read; otherwise it is quietly redone. The check after a save never waits behind the next save.
- **Shared files are safe:** two workspaces that include the same file lock it as one file, however its name is spelled. If a save in one workspace is cut off in a shared file, the other workspace waits and the cut-off save is repaired, even when only the other workspace is open (you approved this exception, 2026-10-27).
- **Saves reach the disk:** each durable step uses the Mac's true flush (`F_FULLFSYNC`), about 5 ms each, about 40 ms per save (you approved, 2026-10-27).
- **Memory:** ledger work may use up to a quarter of your RAM (a setting). Finzytrack learns what each kind of job **usually** needs for each ledger from the jobs themselves ("usual" is defined once, §1.1), so startup is not slower. A job never measured runs alone. At memory-pressure "warning", one job at a time; at "critical", nothing new unless you press **Run anyway** for a job you started, and Run anyway also runs whatever that job waits for.
- **Cancel** stops a read at once. A save can be cancelled until it starts writing files; after that, Cancel shows "Finishing your change…" and the save completes.
- **A slow job is not a crash.** The sidecar watches the engine from outside: CPU of the engine and its children, bytes sent, disk page-ins. Only an engine that shows none of these, while the computer is awake and has CPU to spare, counts as hung. Nothing is cut off by a guessed time limit, not even the engine's start.
  - Background jobs at about 10× their usual CPU, or making no progress for much longer than usual, stop automatically and are logged. A first run is stopped only by hand.
- **The check after a save (R4)** loads your ledger with Beancount's own loader and compares. Only a real difference between the two loaders pauses saving. A plugin that gives different answers each time shows a notice. Engines run with a fixed hash seed, so plugins that loop over sets give the same answer every time.
- **One Finzytrack at a time.** v0.4 runs as a single instance (the app-instance lock, M2) and never shares a ledger with a running old Finzytrack (Copy adoption only). If the old app is saving a ledger, v0.4 refuses the save with "Quit the old Finzytrack first" and never waits for it.
- **Crash loops are bounded:** one bad ledger adds at most one stop to the engine's crash count until it changes, and is not retried after failing twice.
- **Rare failures can be replayed:** every scheduling decision and what it was based on is logged without ledger content.

**What it costs:**
- about 0.1 s per job for the fresh process;
- about 40 ms per save for true flushes;
- a little more memory while two jobs run;
- about 6 KB of log per job;
- a fair amount of design, mostly built later (each part is marked).

**What stays imperfect:**
- Programs that save your ledger without our locks (an editor, a sync client, an old Finzytrack pointed at a v0.4 folder) are detected, not excluded (ADR-0013).
- Two Finzytrack instances, installations or OS accounts writing one ledger are unsupported (the single-instance ADR).
- A process stuck on a dead network disk keeps its slot and locks until it ends.
- Files a plugin reads by itself are invisible (§5.8).

## 1. Terms

- **Job:** one unit of engine work for one workspace (none for cheap jobs), with:
  - a kind (§3), a priority and its requesters;
  - a state: `queued → admitted → running → ended(outcome)`.
- **Heavy job:** runs user code (plugins, `insert_pythonpath`) or loads the whole ledger; needs a fresh engine (§6.1).
- **Large job:** a job whose usual peak (§1.1), or whose estimate when unmeasured, exceeds the interpreter baseline by more than `cheap_margin` (32 MB).
  - Every heavy job is large.
  - The parser-only `plan` is large but not heavy (spike S4: 306 MB for a 20 MB file).
  - Large jobs go through memory admission (§7.3).
- **Cheap job:** neither heavy nor large: static facts, or parsing a small text given in the request.
- **Lane:** per workspace. **Exclusive** jobs (`rebuild`, `commit`, `create`, `recover`) run one at a time per workspace; **shared** jobs (reads, `r4`) beside each other.
- **Engine:** one engine process, the leader of its own process group (§6.3).
- **Slot:** permission to have one engine. `max_engines` (2) bounds all engines.
- **Canonical path** [B] (review 3 F2): the one spelling of a file that every lock key, fingerprint entry, journal-index entry, the registry's deduplication (ADR-0017) and the demo check (INV-25) use. `FileSystem.canonical_path(p)`:
  - **macOS:** `fcntl(fd, F_GETPATH)` on the file opened read-only with symlinks resolved, which returns the volume's stored case and Unicode normalization.
    - Spike A: `realpath` gave three different keys for one inode (case variants, NFC vs NFD), and a second spelling's lock was acquired while the first was held.
    - For an absent path: `F_GETPATH` of the nearest existing ancestor, then the remaining components as written, normalized to NFD, and case-folded when `pathconf(ancestor, _PC_CASE_SENSITIVE)` is 0.
  - **Windows:** `GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED)`, with the same rule for absent paths, case-folded unless the directory is flagged case-sensitive.
  - **Linux:** `os.path.realpath`; a case-folding ext4 directory (`chattr +F`, read via `FS_IOC_GETFLAGS`) is treated like macOS.
  - A corpus test covers case, NFC/NFD, a symlink, a symlinked directory and an absent path on each OS.
- **stat fingerprint:** ADR-0015's extended fingerprint over canonical paths:
  - the root;
  - `(path, mtime, size)` of every glob-expanded include;
  - each glob's match-list hash;
  - expected-but-absent paths;
  - documents-root listing hashes;
  - `(path, mtime, size)` of every plugin source file recorded by the last load.
- **content fingerprint:** the same set with each file's SHA-256 in place of `(mtime, size)`, plus the **code inputs**: `(canonical path, sha256)` of every Python source the load imported from outside the engine's bundled site-packages (in practice the plugin modules reached through `pythonpath`/`insert_pythonpath`), recorded from `sys.modules` after the load (review 2 F3).
  - Engines never read a `__pycache__` in the user's folder (§6.1, review 3 O10), so the hashed source is what ran.
  - About 10 ms at 54k (spike S4) plus the plugin files.
  - The mirror's meta stores both fingerprints. `ledger_version` = (generation id, hash of the content fingerprint).
- **Journal index** [B] (review 3 F4; TLA v2 H2): an app-wide directory, `app_local_data/engine/journals/<sha256(canonical path)>/<ws key>-<session>-<job>`, one empty entry per file a journal covers. It answers "is a journal open over this file, from any workspace?" with one directory read per file (§5.4 step 5).
- **Commit point:** the moment in a `commit` or `create` after which the sidecar never ends the engine by its own choice, except for a confirmed hang or a stall after a protocol violation (§5.4).
- **Closed record:** the file a save's journal becomes at journal close (§5.4). It proves the save landed, holds the R4 still owed, and gates the next write.
- **Head:** the queued large job whose only obstacle is admission (§4.7).
- **On-time tick:** a watcher tick that ran on schedule, with the machine awake and the sidecar keeping up with the engine's output (§8.2).
- **Healthy run:** outcome `answered`, and verified for a read (§5.2); not cancelled, preempted, stopped or ended.
- **Settings:** configuration in the INV-10 style, each with an allowed range (§8.7).

### 1.1 What "usual" means

The owner asked: "What is 'usual' and how is that measured and known?" This is the only definition. Every rule and message saying "usual", "much longer than usual" or "far over its usual CPU time" means this.

**Usual is what healthy runs of the same kind of job, for the same workspace, on this computer, have recently needed.** It is kept per (workspace, job kind) as four numbers. Each number is stored with the ledger size it was measured at (review 3 O11).

| Number | Measured how | Used for |
|---|---|---|
| **usual CPU time** | the CPU seconds of the engine's whole process group: the monotone accumulator of §8.2 at the end, plus the leader's final `wait4` increase. Exact, and the same quantity the running rules see (review 3 F11). | the runaway stop for background jobs (§8.5); the "much longer than usual" flag (§9.1) |
| **usual peak memory** | the finished process's peak: macOS lifetime physical footprint, Linux `ru_maxrss`, Windows peak job commit (§6.1) | memory admission (§7.3) |
| **usual reply size** | bytes of the final reply | the decoding share of the estimate (§7.2) |
| **usual longest quiet stretch** | the longest run of on-time ticks in a healthy run with no CPU, no bytes and no page-ins; and, separately, with bytes but no progress | the minimum tick counts for "hung" and "stalled" (§8.3, §8.4) |

**How it is learned** (pure function `measurements.update`, a logged decision table, §9.3):
- **Only from healthy runs.** A cancelled, stopped or ended run teaches nothing about time. A memory death raises the usual peak (never lowers the estimate below what killed it).
- **Decaying high-water mark, per run, not per day.** After a healthy run, both values are scaled to the current ledger size, then `usual ← max(this run, 0.9 × usual)`. Growth is followed at once; one heavy outlier fades over about 20 runs, making rules more lenient meanwhile, never stricter.
- **Scaled by ledger size.** When the ledger grew since a value was measured, it is scaled up in proportion, never down. This answers the owner's "6 months later" question without measuring at startup.
- **R4 borrows its usual values** from the step-3 load of the save it checks: the same bytes and plugins (review 2 F2).

**Unmeasured** (no usual):
- a first run;
- after the plugin code changed (another plugin-set hash);
- a missing, unreadable or other-schema measurements file (review 2 O17).

An unmeasured job runs alone, is never stopped automatically, and is never flagged (owner decision 4).

**Provisional** (review 3 F9): after an app update, a record measured with the previous engine build counts as follows:
- memory admission uses its peak × `provisional_margin` (1.5);
- runaway, stall and "much longer" treat the job as unmeasured;
- the first healthy run replaces it.

So an update does not make every first job run alone.

**Honest limits:**
- **Per computer:** a new laptop starts unmeasured.
- **Size scaling is proportional:** right for Beancount's load (spike S4); a plugin's cost may grow faster.
- **Plugins that read their own files** change cost unseen; the next healthy run corrects it.
- **About 10× is coarse on purpose:** it catches a loop, not a 3× slowdown.
- **CPU time is not wall time:** a busy or sleeping machine is not "slow". A network wait uses no CPU, so the stall rule, not the runaway rule, catches it.

`Normal`, `Warn` and `Critical` (code font) mean only the OS pressure levels (§7.4). "Normal OS priority" means an unchanged `nice` value.

## 2. Components and where they live

| Part | Lives in | Holds | Bound |
|---|---|---|---|
| `EnginePool` [A] | process-wide: the **one** `process` entry of `platform/state_registry.py`, replacing the heavy-load semaphore (ADR-0041, ADR-0054 d4, amended §14) | engine processes, memory budget, pressure, stop history, pool table state | `max_engines` engines; `max_crashes` stop times; one pressure value |
| `JobQueue` [A]; lanes [B] | `UserContainer` (INV-11) | per workspace: queued jobs, running set, lane state, the R4 gate (re-read from the closed records before every wait, §5.5), poison counters, the fingerprints of its last `max_crashes` counted stops (§8.7), recent outcomes | §3.1 bounds; outcomes: last 16 per workspace |
| `Measurements` [A] | `UserContainer`; `app_local_data/engine/measurements.json` (never the workspace folder: INV-27, INV-30; under the user's root in hosted mode) | §1.1's record per (workspace, kind), with engine build, plugin-set hash and sizes | one record per (registered workspace, kind); removed when the workspace is forgotten |
| `DecisionLog`, `FlightRecorder` [A] | `UserContainer` | §9.3 | recorder: last 4,096 events or 1 MiB |
| `MemoryPressurePort` [A] | platform port (EP-07) | nothing | — |
| `ProcessProbe` [A] | platform port; adapter on `psutil` (BSD-3-Clause, pinned EP-24, main sidecar's licence list) plus `proc_listpids(PROC_PGRP_ONLY)` on macOS (0.09 ms per call, review 3 spike D) | nothing | — |
| `Clock` port [A] | platform port | `monotonic()`, `monotonic_with_sleep()` (§8.2) | — |
| `FileSystem` port additions [B, C] | platform port, both processes | `canonical_path` (§1), `durable_sync` (§5.4) | — |

The pool pulls work. When a slot and memory are free, it asks each user's `JobQueue` for its best dispatchable job (§4.7) and takes the best across users, round-robin among equal classes ([Hosted] fairness seam). The pool never sees ledger semantics; the queue never sees processes. Each running engine has its `EngineClient`, owned by the pool for that engine's life.

**Superseded:**
- ADR-0011's per-workspace `anyio.CapacityLimiter(1)` becomes the exclusive lane [B], keeping its FIFO guarantee.
- ARCHITECTURE §2.5's "optional BQL entries cache" row is dropped (area review F8).

**Moves into the engine:** lock, fingerprint and journal code. `portalocker` 3.2.0 joins the engine's environment and licence list for the ledger-set locks and the legacy-lock detection of the single-instance ADR.

## 3. Job kinds

**Reconciliation** here means R4 (ADR-0010 guardrail 3). Statement reconciliation (ADR-0028) reads the mirror; it is no engine job.

### 3.1 Scheduling attributes

| Kind | Phase | Heavy / large | Lane | Priority (§4.2) | Preemptible | Cancellable: when, by whom | Merge (§4.4) | Queue bound per workspace |
|---|---|---|---|---|---|---|---|---|
| `rebuild` (WARMING or stale mirror; COLD first build; after an outside edit; local date differs from the generation's build date, either direction) | B | heavy | exclusive | P1 if viewed; not run otherwise (ADR-0015) | no | any time, by the system: workspace closed or switched away, quit | newest only: 1 queued + 1 running | 1 (never busy) |
| `commit` (confirm: the legacy-lock try, locks, gate, step-3 load, journal, swaps, read-back, journal close into the closed record, mirror build, publish) | C | heavy | exclusive | P1 user save, import, AI confirm; P3 background writers (price fetch) | no | **before the commit point** only: the user, quit (owner decision 3) | never | 4 queued |
| `create` (new workspace, demo seeding, Copy adoption, a lifecycle migration writing ledger files) | C | heavy | exclusive; the only writer in `COLD` (§4.1 rule 5) | P1 | no | before its commit point, as `commit` | never | 1 |
| `r4` (guardrail 3 after a save or create) | C | heavy | shared; ordered right after the save it checks; waits only for a *running* exclusive job of its workspace (§4.1 rule 2) | P3; a waiting save lends it its class while queued | only while no save waits on it, at most `preempt_max` times | any time: the user (Cancel on "Checking your last change…", M2), quit, the system (superseded). Cancel leaves saving paused, with **Run check now**; queued saves are refused (§4.9) | one per save | 1 |
| `plan` (contract step 1: read and parser-parse the edited files; no plugins) | C | large, not heavy: may use a clean engine and leaves it clean | shared; after earlier exclusive jobs (rule 7) | as its preview | no | as its preview | runs as the first part of its preview, same engine | as preview |
| `preview` (steps 2–3: semantic diff, overlay load of the candidate) | C | heavy | shared; after earlier exclusive jobs (rule 7) | P1 | no | any time: the user, requester leaving, a newer preview of the same edit session, quit | newest only per edit session | 4 |
| `query` (user or AI BQL; recipe `bql` steps) | A | heavy | shared | P1 if a requester waits on screen; P2 otherwise | P2 only, by P0/P1 heavy jobs | any time: each requester (ends when none remains), workspace closed, quit | queued `query` jobs of the same (workspace, `ledger_version`, `LoadPolicy`) into one batch of ≤ 16 statements | 4 batches |
| `snapshot` (other full loads: the copy's "loads the same" check, export-style reads, vector runs) | A | heavy | shared | P1 or P3, as the caller says | if P3 | any time: requester, quit | as `query` when a pure function of the load | 4 |
| `cheap` (static facts; parse a small text) | A | neither | none | P1; never preempts | no | any time: requester | identical requests merge | 16 |
| `recover` (journal recovery: on open; in session after an engine died past its commit point; for another workspace's journal over a shared file) | on open: C; in session: C (BL-0091) | neither | exclusive (of the journal's workspace) | P0 | no | never, except quit (journal left for the next open) | one per journal | 1 |

- **Global bound:** 64 queued jobs per user.
- **Frequent facts** come in the handshake (static) or from the mirror's meta (ledger-derived), not as cheap jobs (EP-30).
- **A timer-refreshed `bql` step** with an unchanged `ledger_version` is answered from the recipe memo (ADR-0029 keys on `ledger_version`) and starts no job (review 3 O9).
- **A `create`** leaves a closed record like a save, so R4 checks adoption and migration writes (review 3 O13). A lifecycle run's own record is recovered after the write journal (ADR-0012's order).

### 3.2 Failure outcomes per kind

"Stop" = counted in the pool's stop history, subject to §8.7's one-per-(workspace, fingerprint) rule. "Poison" = counted in the workspace's poison counter for (kind, fingerprint). Every row is logged (§9.2) and is a decision (§9.3). §4.9 covers queued jobs.

| Kind | Cancel | Engine crash | OS memory kill | Out-of-memory error (`MemoryError`; common on Windows) | Hang (§8.3) | Stalled / runaway (§8.4–8.5; background only) |
|---|---|---|---|---|---|---|
| `rebuild` | ended; build file left for GC; no stop | stop + poison; retried once; second fault: stays WARMING with the message | poison; one engine (§7.5); retried once alone | as memory kill | ended; stop + poison; retried once | n/a (P1 only) |
| `commit`/`create` before the commit point | ended; locks released by the OS; disk byte-identical, no journal | stop + poison; disk unchanged; not retried | poison; one engine; not retried | as memory kill | ended; stop + poison; not retried | P3 writers only: stopped and logged; "not saved, unchanged" |
| `commit`/`create` after the commit point | not ended: "Finishing your change…" | stop; outcome resolved from disk (§5.7) | as crash; then one engine | engine replies `engine.out_of_memory` and exits; resolved (§5.7) | ended only for a confirmed hang; then as crash. A protocol violation: read and discard, wait; a stall after it ends the engine (§5.4 step 5) | never stopped (except the stall after a violation) |
| `r4` | ended; closed record stays; saving paused, Run check now; queued saves refused | stop + poison; retried once; then saving paused (INV-22), Try again; queued saves refused | poison; one engine; retried once alone; then as crash | as memory kill | ended; as crash | stopped; saving paused until **Run check now** runs it at P1 with no automatic stop |
| `plan`, `preview` | ended | stop + poison; reported; Try again | poison; one engine; reported | as memory kill | ended; stop + poison; reported | n/a (P1) |
| `query`, `snapshot` | ended when no requester remains | stop + poison; retried once (idempotent); then reported | poison; one engine; retried once alone; then reported | as memory kill | ended; stop + poison; retried once | P3 `snapshot`: stopped, reported |
| `cheap` | ended | stop; retried once | as crash | as crash | ended; stop | n/a |
| `recover` | n/a | stop; retried once (idempotent: re-classifies from disk); then the workspace stays `RECOVERING`, writes blocked: "Your last change may or may not be saved. Reopen the workspace." | as crash | as crash | ended; as crash | n/a |

"Retried once" = once per (job, fingerprint), only while the pool is not `Unavailable` and the key not poisoned, after the pool's backoff (§8.7).

## 4. The scheduler

### 4.1 Lanes [B]

Per workspace:
1. **At most one exclusive job runs** (property `OneExclusivePerWorkspace`, within one session; across a sidecar crash, the locks carry safety, §5.8).
2. **Shared jobs and exclusive ones.** A shared job is not dispatched while a `commit`, `create` or `recover` of its workspace is running or queued ahead of it (it would almost always end `snapshot_moved`). **Except `r4`**, which waits only for a *running* exclusive job, never for a queued one. It is ordered as if enqueued right after the save it checks (review 3 F1; TLA v2 H1; property `R4NeverBehindItsGatedWrite`; seeded `r4_behind_queued_write`).
   - Shared jobs **may** run beside a `rebuild`, which changes no ledger file (review 2 F8).
   - A running shared job is not stopped when an exclusive one starts; its verification decides (§5.2).
3. **The R4 gate.** An exclusive writer waits until the R4 of the previous save has cleared its closed record (§5.5). It does not wait for other running shared jobs.
4. **Previews and the mirror.** A `preview` is dispatched only when the published mirror's content fingerprint is the one it plans on (its baseline errors come from that mirror, H2). It waits for a running or queued `rebuild`.
5. **No writes without a baseline.** While `WARMING` or `COLD`, a `commit` is refused at once (`WORKSPACE_NOT_READY {phase}`), never queued. A `create` is the only writer in `COLD`:
   - its gate compares only the disk;
   - its baseline errors are empty for a new ledger, and the load of the pre-image for adoption and migration (one extra load, ADR-0008 amendment);
   - it publishes the first generation (property `NoCommitWithoutBaseline`).
6. **Recovery first.** While `RECOVERING` (also `RECOVERING {owner_ws}`, a journal of another workspace over a file of this one, §5.7), or with an unresolved engine death past a commit point, only `recover` is dispatched for it.
   - Reads wait too. This avoids wasted loads; it is not needed for safety, since the verify sees the journal (TLA v2: the seed `query_during_recovery` cannot fail).
   - No rebuild is queued, and the focus check queues none (TLA F9).
   - The block is shown (property `NoWriteAfterUnresolvedDeath`).
7. **Order after earlier saves.** A `plan` or `preview` is ordered after every exclusive job of its workspace enqueued before it (property `PreviewAfterEarlierSaves`; review 2 F9).

The lane is a rule of order and resources, not of correctness: the §5 properties hold with it removed (§11). Before [B] the scheduler refuses every exclusive kind (none exists in [A]).

### 4.2 Priority, inheritance and aging [A]; through lanes [B]; through the R4 gate [C]

Classes, highest first:
- **P0:** recovery.
- **P1:** something the user waits for on screen in the viewed workspace: a save, plan or preview, query, the viewed rebuild, cheap jobs.
- **P2:** interactive work nobody is watching (a hidden dashboard, an AI tool call).
- **P3:** background (R4, background writers, prefetch).

Rules:
- **Order:** by effective class, then FIFO.
  - Exclusive jobs of one workspace keep FIFO among themselves whatever their class (ADR-0011's guarantee).
  - An `r4` sits right after its save (rule 2).
- **Inheritance:** a queued job that another waits for (rules 3, 4, 7; the reservation of §4.7; Run anyway, §7.3) is ordered at the higher class, transitively, recomputed at each event (a pure function). It changes the order of **queued** jobs only. A running job's OS priority is fixed at start (§10): an unprivileged parent cannot raise a child it lowered (review 2 F14; spike s5: EACCES).
  - A head does not inherit from a job that may pass it (§4.7).
- **Aging by count:** each time a queued job is passed by a job enqueued after it, its `passed` counter rises. At `aging_passes` (8) it moves up one class and the counter resets. No clock.
  - Bound, as a pure-table test obligation: a queued job is dispatched within `aging_passes × 4` passes once admission allows it (review 3 O12).

### 4.3 Preemption [A]

A queued P0 or P1 **heavy** job that cannot be dispatched (no slot, or admission refuses it) may preempt **one** preemptible running job. Cheap jobs never preempt.

Preemptible: read-only and preempted fewer than `preempt_max` (2) times:
- `query` at P2;
- `snapshot` at P3;
- `r4` while no save waits on it.

Preemption is our own end, with reason `preempt`; it is not a stop. The job goes back to the front of its class with its requesters and merge key. Under `Critical` (§7.4), preemptible P3 jobs are preempted even when nothing waits.

### 4.4 Merging and newest-only [A] queries, [B] rebuilds

- **Merging only among queued jobs.** A running engine's load is never shared with a later arrival; that would make the ledger resident (INV-10, ADR-0011). A batch is fixed at dispatch.
- **Requesters.** A merged job has a requester set; it ends only when the set is empty.
- **Per-statement outcomes.** Each statement gets its own result or refusal (`engine.result_too_large`, area review F6).
- **Rebuild, newest only:**
  - one queued per workspace;
  - a trigger while one is queued is absorbed;
  - a trigger while one runs keeps one queued rerun;
  - when the stat fingerprint moved but the content fingerprint equals the mirror's (a sync client re-downloaded an unchanged file), the mirror's stat fingerprint is re-stamped instead (review 2 O11).
- **Preview, newest only per edit session:** a new preview replaces the queued one and cancels a running one.

### 4.5 Queue bounds and "busy" [A]

Over a §3.1 bound or the global 64: refused at once with `BUSY_HEAVY_LOAD` (ARCHITECTURE §6.1, `Unavailable`, retryable; no `retry_after_ms`), nothing queued.
- Merged kinds (`rebuild`, `r4`, `recover`) are never busy.
- The frontend never retries `BUSY_HEAVY_LOAD` or `LEDGER_CHANGING` by itself; the user's Try again does.
- A busy refusal changes no table state; it is logged as a count (§9.3), with one `engine.queue.busy` per workspace and kind until the queue drains.

### 4.6 What "running" means [A]

- **The job ends**, and its **lane** is released, at the engine's final reply or at confirmed death.
- **The slot and memory reservation** are released only when the engine **and its process group** are gone (TLA F3; `EnginesBounded`):
  - POSIX: the leader reaped and `killpg(pgid, 0)` fails with **`ESRCH`**. `EPERM` means members remain (a zombie leader, or a member owned by someone else), and `0` means a member lives (review 3 spike E).
  - Windows: the Job Object's active process count is 0.
- **Draining** (review 3 F8; TLA v2 M2):
  - After its final reply the engine ends with `os._exit(code)`: no `atexit`, no joining of plugin threads.
  - If, after the leader's reap, `killpg(pgid, 0)` still succeeds, the pool records reason `drain` and sends `killpg(pgid, SIGKILL)` once. `drain` is our own end, never a stop, and the job stays `answered` (property `AnsweredJobNotAStop`).
  - A leader that has replied but does not exit is judged by the hang rule (§8.3). If ended, the reason is `drain`, the outcome stays `answered`, and it is not a stop.
  - Liveness property `SlotEventuallyFreed`.
- **A kill sent is never an end** (`RunningUntilAnsweredOrDead`; seeded `lane_on_kill_sent`, `lane_on_requester_left`).
- **The HTTP handler is not the job.** A disconnect is `RequesterLeft`: it cancels a read with no requester left, never a save (H2).
- **Outcomes after a crash.** A save's outcome is kept in recent outcomes, and durably as the closed record or its `.done` successor (§5.5), so a reloaded window can learn it even after a sidecar crash (BL-0092).
- **Cost:** an engine that cannot be ended (uninterruptible sleep on a dead network disk) keeps its slot, lane and **locks** until it ends; every verify and save over its files waits; the user sees "cannot be stopped" (§9.1). A descendant that left the group (`setsid`) keeps any inherited lock while it lives (spike s3); lock steps wait for it like any other.

### 4.7 Dispatch, as a pure function, with head-of-line reservation [A]

`next_dispatch(pool_view, queues) -> Dispatch | Preempt | Wait(reason)`:
- `pool_view` holds free slots, budget headroom, pressure, `single_engine`, `Unavailable`/`Incompatible`, and the Run-anyway chain (§7.3).

A candidate is **dispatchable** when:
- its lane rules allow it (§4.1);
- the pool is not `Unavailable` (heavy and large) and not `Incompatible` (all);
- its key is not poisoned;
- the backoff has passed (§8.7);
- admission allows it (§7.3).

**Reservation** (review 2 F1; review 3 F9; TLA v2 M7):
- **The head** is the queued large job of the highest effective class (then FIFO), across all queues, whose only obstacle is admission (pressure, budget, or "unmeasured runs alone").
- **While a head exists:**
  - no other large job **of the same or a lower class** is dispatched, except one that preempts on the head's behalf;
  - a large job of a **higher** class may be dispatched when admission allows it, and each such dispatch counts as a pass for the head's aging;
  - a cheap job may be dispatched into a free slot at `Normal`, but not at `Warn`, where a running cheap job would be the head's only obstacle.
- **When the running large jobs end**, the head is admitted: it is alone, and admitted at `Normal` or `Warn`.
- **The head is chosen afresh at each event.** A Run-anyway job becomes the head even over a head of the same class (TLA v2 overflow).
- **Evidence:**
  - Spike s6: without reservation, an 800 MB rebuild was still queued after 2,000 steps; with it, it ran at step 22.
  - `Admission.tla`: the reservation is starvation-free (`NoStarvationUnderAdmission`), and the higher-class rule removes the inversion that draft 2 had (`NoClassInversion`; seeded `pass_head`).

**`Wait(reason)`** carries what the user sees: `queued`, `waiting_for_memory`, `waiting_to_run_alone`, `backoff`.

**Re-run on every event:** enqueue, end, cancel, requester left, pressure change, tick, Run anyway, settings change. `next_dispatch` is an EP-17 pure table with exhaustive tests and random walks; its inputs and outputs are logged (§9.3).

### 4.8 Quit and quiesce [A] reads; [C] saves (review 2 F13; TLA F15)

`POST /api/app/quiesce` (failure-modes M7):
1. Stop dispatching.
2. End queued jobs (§4.9). A queued `recover` is left for the next open.
3. End running reads, plans, previews, rebuilds and R4s. An R4's closed record stays.
4. End saves before their commit point ("not saved, unchanged"; the unsaved guard W7 asked first).
5. Wait for saves past their commit point and a running `recover` ("Finishing your last change…").
   - No time limit. After `quit_patience` on-time ticks (10) with no phase change, the shell offers **Quit anyway**: "Finzytrack will finish or undo this change the next time it opens. Your ledger stays safe." The journal makes this true.
   - An OS logout or shutdown that cannot wait is the same.
   - Properties: `QuiesceTerminates`; `OutcomeTruthful` after Quit anyway (seeded `quitanyway_says_saved`).

**Before an app update** (ADR-0007, review 3 F15), quiesce first runs every owed R4 at P1, so an update does not supersede them; an R4 that cannot finish leaves its record, and the update proceeds with it superseded at the next start (`engine_changed`, logged).

### 4.9 Queued and running jobs on each event [A]; save rows [C]

| Event | Queued jobs | Running jobs |
|---|---|---|
| pool `Unavailable` | P1 and P2 heavy and large jobs end with E1's message and Try again. P3 stay queued. A `rebuild` stays queued (WARMING plus E1's message). Closed records stay (saving paused). `recover` stays queued and is admitted (not heavy). | not affected |
| pool `Incompatible` | all end with E6's message; journals and closed records stay on disk | none can exist |
| workspace poisoned for a key | its jobs of that key end "poisoned" | — |
| writes blocked (foreign bytes at recovery, recovery failed twice, an R4 that cannot run or was cancelled, an R4 mismatch) | queued saves refused at once with `WRITES_BLOCKED` and the block's message (TLA F7; `QueuedWriteLeavesQueue`). A save's gate never re-arms a cancelled R4; **Run check now** does (TLA v2 L2). | — |
| a journal of another workspace covers a file of this one | this workspace's reads and writes wait, `RECOVERING {owner_ws}`; that journal's `recover` runs (§5.7) | its verify ends `orphan_journal {owner_ws}` |
| the legacy app holds its lock on this ledger (single-instance ADR) | — | the save or copy refuses at its first step: "The old Finzytrack app is saving this ledger. Quit the old Finzytrack first, then try again." Nothing written, not a stop, not poison. |
| workspace closed or switched away | jobs end `cancelled`, except `recover` and saves past the commit point; closed records stay | reads ended; saves before the commit point continue unless cancelled (closing is not a cancel) |
| workspace forgotten (ADR-0019) | refused while a journal of it is open, a closed record of it exists, or a save is past its commit point; otherwise jobs end `cancelled`, and its measurements, `.done` records and caches are removed | as closed |
| sidecar exit or crash | lost; HTTP requests fail; journals, closed records and high-water marks are on disk | engines end with their group (§6.3); a save past its commit point is resolved at the next open (§5.7) |
| quiesce | §4.8 | §4.8 |
| `max_engines` or `memory_share` changed | re-evaluated at the event | never ended; the pool drains to the new bound |
| pressure rises to `Critical` | stay queued, `waiting_for_memory` | preemptible P3 preempted |
| pressure falls to `Warn` or `Normal` | re-evaluated at the event | — |

## 5. Consistency [B] unless marked

### 5.1 The two fingerprints

- The **stat fingerprint** decides freshness cheaply: the focus check (ARCHITECTURE §9.1) and opening.
- The **content fingerprint** decides correctness: every verify, publish condition and save gate.
- A stat fingerprint can miss a same-size save within one mtime tick; a content fingerprint cannot.
- Plugin code is in both, so a fixed plugin makes the mirror stale and resets poison (review 2 F3).
- Every path in both is a canonical path (§1).

### 5.2 Verified snapshot reads (H3; ADR-0012 F-6)

Every job that loads the ledger from disk returns a result only if the bytes it parsed were, at one instant, the whole ledger set with no write in progress over any of its files.

1. **Read.** The engine reads each file once, as bytes, records `(canonical path, size, sha256)`, and parses those bytes (spike S4). It records glob match lists, absent includes, the documents-root listing hashes at the start, and the plugin files after the load. It runs **without locks**.
   - **Cloud placeholders** (iCloud "Optimize Mac Storage", online-only Dropbox or OneDrive files) block the read while they download. `reading_files` reports `done` as bytes read, so a read making progress is never stalled, and one that makes none is (§8.4, review 3 F12).
2. **Verify, under the locks** (`waiting_for_lock` while waiting, §8.1):
   - **The ledger-set locks:** one lock file per canonical path of every file of the set, at `app_local_data/locks/<sha256(canonical path)>`, each taken once (duplicates removed), sorted by canonical path (review 2 F11, review 3 F2; ADR-0012 Q29).
     - Two workspaces sharing a file therefore exclude each other on it, however it is spelled (property `NoTwoWritersMidCommit`; seeded `lock_key_alias`).
     - Lock files are never deleted while a Finzytrack process may run: unlinking a held lock file would let two processes lock two inodes of one name.
   - **The set may change under the locks** (review 3 F3; TLA v2 H3): globs are re-expanded and every path re-resolved to its canonical path under the locks.
     - If the set grew or a spelling changed, each new lock is tried **without blocking** (`LOCK_NB`).
     - If all are free, the verify continues. If any is held, it releases every lock and starts step 2 again with the new set, in canonical order. That restart counts as one `snapshot_moved {cause: set_changed}`, bounded by `snapshot_retries`.
     - A blocking wait is only ever for a lock that sorts after every lock held (property: TLC deadlock freedom; seeded `relock_out_of_order`, `lock_unsorted`).
   - It reads the **journal index** for every file of the set: no journal may be open over any of them, from any workspace or session (review 3 F4). The index directory is durably synced (§5.4) before a check relies on "no journal" (F-8).
   - It recomputes the content fingerprint of the same set (absent paths, documents listings) and compares it with what was parsed. The re-hash reads only files the unlocked read had already brought local; a file that became a placeholder in between ends `snapshot_moved {cause: evicted}`.
   - Equal, and no journal: **verified at instant T**. Otherwise: `snapshot_moved {cause}` or `orphan_journal {owner_ws, session, job}`.
3. **Release.** A publishing job publishes first (§5.3).

The locks are held a few milliseconds (about 10 ms at 54k), never across user code. F-6's purpose (never build from a half-applied save) is kept: a lock-respecting writer holds the locks for its whole save, so the verify waits for it, or sees no journal and its finished bytes.

**Outcomes:**
- **`snapshot_moved` by one of our own exclusive jobs** of any workspace sharing the file, during the read: re-queued behind it; the retry counter does not move.
- **`snapshot_moved` by an outside writer:** re-queued at most `snapshot_retries` (3) times in a row, then `LEDGER_CHANGING` (§9.1). A rebuild then ends in WARMING with that message and is retried at the next focus check or Try again (condition-based).
- **`orphan_journal`:** the verifier holds the locks, so no live lock-respecting writer owns that journal. The workspace goes `RECOVERING` (or `RECOVERING {owner_ws}`), and §4.1 rule 6 applies (§5.7).

**Guarantee:**
- Against every lock-respecting writer (our saves in any workspace, an orphaned engine of a crashed sidecar session), a verified result is a committed state (`VerifiedReadsAreSnapshots`).
- Against a program that ignores our locks, only "the disk equalled what was parsed at instant T" (ADR-0013's accepted race).

### 5.3 Conditional publish and generation ids (H2)

A generation is published only by the engine that built it, inside a lock hold (§5.2 step 2, or its save's), and only when:
- the disk's content fingerprint under those locks equals the build's, and no journal is open over any file of the set (`PublishOnlyCurrent`);
- its id is greater than the current published id.

**Ids are allocated at publish, under the locks:** `id = 1 + max(current published id, the high-water id in app_local_data/engine/generations/<ws key>, the highest id among the files present)`. The high-water is written (atomic replace, `durable_sync`) before the pointer swap.
- Id order is therefore the order of the verified instants, so the newest state wins whatever order the builds finished in (`GenerationsMonotonic`, `PublishedNotOlder`).
- Keeping the high-water outside the cache survives a cache purge (seeded `hw_in_cache`).

**Mechanics:**
- A build writes `build-<job id>.db`.
- Publish writes `ledger_version` and both fingerprints into its meta, renames it `gen-<id>.db`, and swaps the pointer (a symlink; a pointer file on Windows).
- A build that fails the condition is dropped (`mirror.publish.refused_stale`), and one rebuild is queued.
- GC deletes `build-*` files of ended jobs; published generations keep ADR-0015's grace period.

**Why not compare-and-swap on the base generation (H2's option):** it would accept a build whose disk moved after the read, and refuse a newer save's build after an older-state rebuild. Seeded `id_at_dispatch` breaks `GenerationsMonotonic`.

### 5.4 The write path with the scheduler [C]

A `commit` runs ARCHITECTURE §9.2 step 4 in one fresh engine:

0. **Legacy-lock detection** (the single-instance ADR). If a known legacy install's config names a file of this workspace's set, the engine tries that install's `data/.write.lock` **once, without waiting** (`LOCK_EX | LOCK_NB`, portalocker 3.2.0's primitive and range). It opens the lock file read-only and only if it exists; it never creates it.
   - **Held:** the save ends at once, unchanged (§4.9 row): no wait, no Cancel needed, not a stop.
   - **Free:** it is held until the save ends, then released.
   - A `create` that copies a legacy ledger (Copy adoption) does the same before reading.
   - Because this is a single non-blocking try, taken before the set locks, it cannot deadlock or delay anything. It is the only way v0.4 touches a legacy lock.
1. **Locks.** Take the ledger-set locks (§5.2) over the union of the plan's pre set, its post set (files it will create) and the set's absent paths, in canonical order with the non-blocking relock rule (review 3 F3). A wait shows `waiting_for_lock`, with **Cancel**. It waits only for our own other jobs or an orphan of a crashed session, so it is bounded by those jobs' own rules.
2. **Gate, under the locks:**
   - the disk's content fingerprint equals the plan's;
   - the published mirror's content fingerprint equals the plan's (H2);
   - the journal index shows **no journal open over any file of the set**, from any workspace or session (TLA F6; review 3 F4);
   - **no closed record and no `.mismatch` record exists for this workspace** (TLA F1, F2; review 3 O7; `R4BeforeNextCommit`);
   - for a `create`, the disk half only (§4.1 rule 5).

   On failure:
   - a journal → `RECOVERING` (§5.7);
   - a closed record → wait for its R4 (re-armed if not running, §5.5);
   - `.mismatch` → `WRITES_BLOCKED`;
   - a stale mirror → `WORKSPACE_NOT_READY {WARMING}`;
   - a moved disk → step 2a (`GateOnCurrentMirror`).

   **2a. Our own earlier saves are not outside changes** (review 2 F9, review 3 F14, TLA v2 M5):
   - **Which changes are ours.** The disk moved, and every file that differs between the plan's fingerprint and the disk's was changed by a save of **this installation**: the file's post hash appears in a closed or `.done` record of any workspace, including saves resolved "saved" by recovery.
   - **End and requeue.** The job ends its engine before the commit point (our end, reason `replan`, not a stop), releases the locks, and is re-enqueued at the **front** of its workspace's exclusive lane with a re-planned plan (property `CleanEngineForUserCode`: no second user-code load in one engine).
   - **Re-plan.** The re-plan runs the mutator against the current mirror, re-resolving every `TransactionId` (ADR-0026). If the mirror is not at the disk's fingerprint, it waits for the queued rebuild (rule 4's ordering). Same intended change (same targeted entries and canonical texts) → proceed; otherwise `PLAN_OUTDATED`.
   - **Bounds.** At most `snapshot_retries` re-plans. Only a change not made by us gives `LEDGER_EXTERNALLY_CHANGED` (`OwnCommitsNeverExternal`; seeded `no_replan`).
3. **Load.** Re-run step 3 (the save's one full load) and compare error keys. Its CPU and peak (phases `parsing`..`validating`) become the R4 baseline (§1.1).
4. **The commit point, agreed by both sides.** The engine sends `gate {id, point: "commit"}` and waits on its input. In one step of its table, the sidecar:
   - ends the engine if a cancel is pending (cancelled, disk unchanged);
   - otherwise marks the job **past the commit point**, then sends `proceed`.

   The engine opens the journal only after `proceed` (`NoKillAfterCommitPoint`, `CancelBeforeCommitLeavesDiskUnchanged`; seeded `one_sided_gate`, `cancel_after_point`). From the commit point to its final reply, the engine ignores `SIGTERM` (failure-modes E7, review 3 F15). It still dies by `SIGKILL` and with its parent (§6.3); the journal covers that.
5. **Write** (ADR-0008, ADR-0012, ADR-0013, F-1..F-8):
   - **Durability** (review 3 F6; owner answer (a), 2026-10-27). Every sync is `FileSystem.durable_sync`:
     - macOS: `fcntl(fd, F_FULLFSYNC)`, falling back to `fsync` only where the volume refuses it, logged once per volume;
     - Linux: `fsync`;
     - Windows: `FlushFileBuffers`, and `MoveFileExW(MOVEFILE_WRITE_THROUGH)` for renames.

     Spike H: directory `fsync` 0.00 ms median against `F_FULLFSYNC` 4.78 ms (max 8.2 ms); macOS `fsync` does not flush the drive cache. About 8 syncs per single-file save, so about 40 ms.

     S5's durability assumption (`AckedCommitNeverRolledBack`) is now this port's contract.
   - **Journal open.** The journal names its writer as `(ws key, session id, job id)` (H5). It holds every field of the future closed record (all known after step 3): post content fingerprint, adopted digest, parse digest (§5.5), engine build id, R4 baseline (review 3 O6).
     - Before the journal is written, its **journal index** entries are created, one per covered file, and the index directories durably synced.
   - **Journal close** is a pure rename of `journal-<session>-<job>.open` into `journal-<session>-<job>.closed`, the **closed record**, with the journal's handle closed first (required on Windows), then a `durable_sync` of the directory.
     - The index entries are removed after the record exists (synced again).
     - The save has landed exactly when the record exists (§5.7).
   - **No user code after the commit point** (plugins ran in step 3). A hang after it is our bug or the OS's, and only the hang rule ends it.
   - **A protocol violation after the commit point** (review 3 F5; TLA v2 H5):
     - The engine is not ended at once. The sidecar keeps **reading and discarding** its output (so it never blocks on a full pipe) and waits for its exit.
     - Discarded bytes are not liveness: the hang rule judges on CPU and page-ins alone.
     - A stall with `phase` frozen at the violation **ends** it (reason `stalled_after_violation`, the only end past the commit point besides `hang`), and the outcome is resolved from disk (§5.7).
     - Seeded: `stop_reading_after_violation` (deadlock), `kill_on_violation_past`.
6. **Publish and reply.** Build the mirror from the in-memory result, publish under the same locks (§5.3), release, then reply with `usage` and the old→new TransactionId map. A save that cannot publish (`saved_nopub`) queues a rebuild.

Write serialization (ADR-0011) is the exclusive lane plus per-file locks (Q29), not INV-26 (design review L1).

### 5.5 The R4 check [C] (review 2 F2; review 3 F7; TLA F1, F2, F10; TLA v2 M1, M3, M4; owner answer 4)

**The gate is the closed record on disk**, so it holds across quit, crash and recovery:
- Every save or create that landed has a record.
- Recovery resolving "saved" re-arms R4 from it.
- Every write gate refuses while a record of the workspace exists.
- The in-memory gate in `JobQueue` is only a cache. Before a save waits on it, and at each restart, it is **re-read from disk**. If the record is gone (an orphaned R4 of the previous session finished it), the gate clears (TLA v2 M1; seeded `quit` config with `FIX_R4_REFRESH` off deadlocks).
- R4 removes the record under its locks when it ends `pass`, `superseded` or `nondeterministic`; it is renamed `.done`, and the last 16 per workspace are kept, so a reloaded window can still learn a recent save's outcome.
- A `mismatch` renames it `.mismatch`, which blocks saving until the user acknowledges the backup notice (then it becomes `.done`).

**Digests** (review 3 F7):
- **adopted digest:** SHA-256 over the canonical encoding of the result: entries in Beancount's sort order with ties broken by canonical text, the error keys, and the options plugins can read.
- **parse digest:** the same encoding of the `_parse_recursive` stage (entries, errors and options before booking).
- Engines run with `PYTHONHASHSEED=0` (§6.1). Review 3 spike F: a 9-line set-iterating plugin gave 5 digests in 5 processes, and one with the seed fixed.

**Steps:**
1. **Under the locks:** the disk's content fingerprint equals the record's post fingerprint, and the journal index shows no journal over the set; release. If the fingerprint differs: `superseded`.
2. **Without locks:** Beancount's own uncached load, `beancount.loader._uncached_load_file(abs_path, log_timings, extra_validations, encoding)`: all three extra positional arguments and an absolute path (review 3 O4). Never `load_file` with the cache disabled, which deletes `.<root>.picklecache` in the user's folder (spike S4; design review M5).
3. **Digest** and compare with the adopted digest.
4. **Under the locks again:** re-check the **whole** content fingerprint (globs, absent paths, documents listings; review 3 O3) and the journal index. Then:
   - **Equal digest, unchanged fingerprint:** `pass`. A torn read cannot reproduce the adopted digest, so no lock was needed across the load.
   - **Fingerprint moved:** `superseded` (logged), never a mismatch (`R4NoFalseMismatch`; seeded `r4_no_recheck`).
   - **Digest differs, fingerprint unchanged:** **confirm, without running user code**, in the same engine, which ran user code once (step 2) and runs none here (`CleanEngineForUserCode`; TLA v2 M3; seeded `r4_load_locked` fails `OnlySavesLockAcrossUserCode`). Still holding the locks, it runs Beancount's own `_parse_recursive` on the disk and the overlay replica on the same bytes (parser only; a few hundred ms at 54k), and compares their parse digests with each other and with the record's:
     - **overlay parse ≠ Beancount parse:** a real loader difference (the overlay replicates only `_parse_recursive`, ADR-0010). Outcome **`mismatch`**:
       - saving blocked, backup shown (ADR-0008);
       - `engine.r4.mismatch` at error;
       - the `load_file` result published (ADR-0010 guardrail 3 "adopts"; review 2 O8).
     - **both equal each other and the record's parse digest:** the difference arose in booking, plugins or validation, which are Beancount's own code over equal input, so a plugin is not deterministic (dates, network, or a drifting input that may since have returned; TLA v2 M4). Outcome **`nondeterministic`**:
       - `engine.r4.nondeterministic` at warning;
       - the `load_file` result published;
       - the notice of §9.1;
       - saving **not** blocked (owner answer 4).
     - **both equal each other but not the record's:** the parse input moved in a way the fingerprint missed. R4 starts again from step 1 once; if it recurs, saving is blocked as for `mismatch`, with "could not decide" (INV-22).
     - No case is undefined (`R4OutcomeDefined`).

**Rules around it:**
- **Superseded** means the disk moved after the save (an outside editor or sync client, or an old Finzytrack pointed at the folder, which is unsupported and caught as an outside edit) or, at the next start, the engine build changed (`engine_changed`). One guardrail-3 sample is lost, not a safety check of that save (its gate passed); the outside change sends the workspace to WARMING, whose rebuild replaces the adopted mirror (ADR-0008 amendment, TLA F10).
- **At the next open:** the fingerprint differs from the record's → superseded; the engine build differs → superseded `engine_changed`; otherwise R4 runs before writes.
- **Priority:** P3 at normal OS priority, never niced (§10); a queued save lends it its class; ordered right after its save (§4.1 rule 2).
- **Never unmeasured** (§1.1): an R4 that loops is stopped by §8.5 at P3, or flagged with Cancel when a save waits.
- **Failure:** an R4 that cannot complete (two crashes, a runaway, a user Cancel) leaves its record; saving stays paused (INV-22); queued saves are refused (§4.9). **Run check now** runs it at P1 with no automatic stop.
- **Synchronous R4 during rollout** (ADR-0010) runs inside the save's job before the reply, with the same steps.
- **Locks:** with the parse-only confirm, **no job holds the locks across user code except a save** (steps 1–6, any class), renamed `OnlySavesLockAcrossUserCode` (review 3 O8; was `NoBackgroundLockAcrossUserCode`).

### 5.6 Outside writers, the legacy app, other instances

- **Outside editors and sync clients** take no locks. They are detected:
  - by the focus check (WARMING and a rebuild);
  - by verification (`snapshot_moved`);
  - by the gate (`LEDGER_EXTERNALLY_CHANGED`);
  - by the swap's displaced-file hash (ADR-0013).

  Never excluded.
- **The legacy v0.3 app** (the single-instance ADR):
  - v0.4 never shares a ledger with it: Copy adoption only.
  - A held legacy lock is detected by one non-blocking try (§5.4 step 0), and the save or copy is refused; v0.4 never waits for it.
  - Verifies, R4 and recovery do not take the legacy lock.
  - An old app pointed at a v0.4 folder after the fact is unsupported; its rewrites are caught as outside edits.
- **A second v0.4 instance** on the same app root is refused at start by the app-instance lock (single-instance ADR, BL-0100, M2). Two installations, two `--app-root`s or two OS accounts writing one ledger are unsupported (owner answer 5; "if the user does that, it's on them"); each is an outside editor to the other.
  - ADR-0012 Q30 stays as a defence: a journal whose `(ws, session)` is not ours and is not from an earlier session of this installation refuses writes and asks the user to reopen.
  - Review 2's candidate root-file lock (BL-0091) is no longer needed for v0.4.0.
- **Orphaned engines of a crashed sidecar** are the one other lock-respecting writer left. They hold their locks until their watchdog ends their group (0.25 s on POSIX; at once on Windows), and the next session's verifies and saves wait for those locks.

### 5.7 Recovery and the outcome of a save whose engine died [C] (TLA F4, F8, F12, F15; TLA v2 L1; review 3 F4, O5)

When a save's engine ends past its commit point without its final reply (a crash, OS kill, hang or stall end, `MemoryError`, or the sidecar's own death), the outcome is **resolved, never left unknown** (BL-0091; `OutcomeTruthful`).

The lane stays held until death is confirmed (§4.6). Then a `recover` job of **the journal's workspace** runs (P0, exclusive, admitted at any pressure, owner answer 3):
- **Locks first, then read** (TLA v2 L1; review 3 O5). It takes the locks over the journal's files, waiting: on Windows a lock may lag `TerminateJobObject` (design review M6), so it is waited for, never read as a live writer. Only **after acquiring them** does it read the journal directory, the journal index and the closed records; a live writer may have closed its journal meanwhile (seeded `recover_journal_early`, `RecoveryNeverClobbersForeignBytes`).
  - ADR-0012's on-open recovery skipped when the locks were held. Here on-open and in-session recovery both wait, showing "Checking your last change…" (TLA F15).
- **`resolve(journal, closed record, disk hashes, plan pre/post hashes)`**, a pure function:
  - **journal open:** ADR-0012's rule. Every file pre or post → restore pre (root first, F-5): **not saved, put back**. Any other bytes → **blocked**, writes off, both versions shown (`BlockedOnlyOnForeignBytes`).
  - **no journal, this job's closed record present:** **saved**; R4 re-armed from the record.
  - **no journal, no record:** **not saved**; if the disk differs from the pre-image, add "your ledger was changed by another program" (TLA F4).
- **Whose journal:**
  - this session's → recover now;
  - an earlier session of this installation (same ws key, older session) → recovery on open, or now if found in session;
  - **another workspace's** journal over a file this workspace shares → `recover` runs **as that workspace's job, even when that workspace is not open**. This is the owner-approved exception to INV-31 (owner answer (b), 2026-10-27), limited to repairing that interrupted save: it reads that workspace's journal and backups, restores or confirms its files, and touches nothing else. Meanwhile this workspace shows `RECOVERING {owner_ws}`.
  - anything else → Q30 (§5.6).
- **A recovery that itself dies** is retried once (idempotent), then the workspace stays visibly blocked: "Your last change may or may not be saved. Reopen the workspace." (`RunningWriteEndsOrBlocks`; TLA F8). The retry is for the user, not for safety: the model shows a dropped retry cannot break a property (seed `recover_no_retry` dropped).
- **Then the mirror:** saved → publish if not yet, else nothing; not saved → the published mirror is still the pre state.
- **Order with lifecycle runs:** the write journal first, then the lifecycle run record (ADR-0012).

Before [C] there are no writes; failure-modes E1–E3 keep saying so.

### 5.8 What is not watertight, and what it costs

- **Lock-ignoring writers** are only detected (§5.6). ADR-0013's window remains where swap is unavailable.
- **Other instances, installations and OS accounts** writing one ledger are unsupported (§5.6). The instance lock refuses a second process on the same app root only from M2 (BL-0100); before that, nothing starts a second sidecar except a developer.
- **An unkillable process** keeps its slot, lane and locks (§4.6).
- **A first background run** has no usual value; a runaway or stalled first run holds its slot until a person cancels it (owner decision 4). Until the job list exists (BL-0092, M2), that means quitting.
- **Machine saturation:** with no idle CPU, a frozen engine is not judged hung until load drops. `stopped` (`SIGSTOP`) is judged even then.
- **Long `Critical` pressure:** only recovery and Run anyway start (§7.3); ledger work waits visibly (owner answer 6).
- **Files a plugin reads by itself** are invisible: they do not make the mirror stale or reset poison. User docs say so; **Rebuild now** in Settings forces a rebuild. Plugin code is tracked.
- **Two loads of one ledger** may run its plugins at once; a plugin with side effects must tolerate that (user docs).
- **Compressed memory:** handled by recording the macOS lifetime footprint (§6.1).
- **Orphans after a sidecar crash** are invisible to the next session's slot count and admission for up to 0.25 s (POSIX); safety holds through the locks. `EnginesBounded` and `OneExclusivePerWorkspace` are per session (TLA F13).
- **A plugin input that drifts between R4's load and the save's** can make R4 report `nondeterministic` for a deterministic ledger whose inputs changed; it never reports a false `mismatch` (`R4NoFalseMismatch`).

## 6. Engines [A]

### 6.1 A fresh process per heavy job (owner decision 5; H1); the pool as the only reaper

- **At most one heavy job per engine,** then exit. Cheap jobs and a `plan` may come first, never after (`CleanEngineForUserCode`). Reason: Beancount imports plugins with `importlib.import_module` and restores `sys.path`, not `sys.modules` (H1).
- **A clean engine that ran only cheap jobs** ends after `idle_ticks` (5) on-time ticks with no job, or at once when its slot is needed.
- **Spawn flags** (review 3 F7, O10). The engine is spawned with `-s -S -B -P -X pycache_prefix=<an engine-private empty directory>` instead of `-I` (`-I` ignores `PYTHONHASHSEED`; checked), in an environment the pool builds from scratch:
  - no inherited `FT_*` or `PYTHON*`;
  - plus `PYTHONHASHSEED=0`.

  A plugin iterating a set then gives the same order in every engine, and a `__pycache__` in the user's folder is never read or written. The packaged bootstrap (S0038) keeps its explicit path setup.
- **The pool is the only reaper.** It never calls `Popen.wait` or `Popen.poll`; after our own `wait4`, the standard library reports `returncode == 0` for a `SIGKILL`ed child (review 2 F7, spike s2). On POSIX:
  1. `os.waitid(P_PID, pid, WEXITED | WNOWAIT)`, leaving a readable zombie; on macOS, `proc_pid_rusage(pid, RUSAGE_INFO_V4)` for `ri_lifetime_max_phys_footprint` (counts compressed pages);
  2. `os.wait4(pid, 0)` for the status, CPU and `ru_maxrss`;
  3. the drain check of §4.6.

  On Windows, the Job Object's accounting gives the peak job commit and the total CPU.
- **Spare engine:** off by default on macOS and Linux (spike s5: start 0.08–0.09 s even at 2× oversubscription). Windows is decided from the BL-0087 measurement [M1b], confirmed [M11].

### 6.2 Start cost

`python … -c "import beancount.loader, beanquery"`: 0.07–0.10 s and 34 MB warm, 0.42 s cold, on the owner's Mac; review 2 measured 0.08–0.09 s under load.
- That is about 5 % of a 2 s load at 54k.
- At demo size it is small in absolute terms against the 1 s target.
- Windows is unmeasured [M1b, M11].

### 6.3 Process groups and Job Objects

- **POSIX:**
  - Each engine leads its own process group (`process_group=0`); our end is `killpg(pgid, SIGKILL)`.
  - The parent-death watchdog (0.25 s, spike S1) ends its whole group (`killpg(0, SIGKILL)`). No `PR_SET_PDEATHSIG`: it fires when the spawning *thread* ends.
  - Linux engines start at `oom_score_adj` +500.
  - A `setsid` child leaves the group (§4.6).
- **Windows** ([C] write path, ADR-0061 d6; M11 otherwise): each engine runs in its own Job Object, nested in the shell's, with kill-on-close and no breakaway; our end is `TerminateJobObject(job, EXIT_OUR_KILL)`.
- **A save past its commit point when the sidecar dies** (decides failure-modes E5): the engine is ended with its group on every OS; the journal makes it a crash mid-write; the next open recovers it and says which §5.7 outcome happened. No engine outlives the sidecar.

### 6.4 How an engine ended

The sidecar records the end reason **before** the signal. Reasons:
- `cancel`, `preempt`, `superseded`, `replan`;
- `hang`, `stalled`, `stalled_after_violation`, `runaway`;
- `quit`, `idle`, `drain`.

**A recorded reason wins over any exit status** (TLA F11; seeded `reason_after_kill`).

| Observation | Classified as | Stop? |
|---|---|---|
| final reply, then exit 0 (or ended with reason `drain`) | `answered` | no |
| ended by us, reason recorded | that reason | only `hang` and `stalled_after_violation` |
| `SIGKILL` (POSIX), no reason of ours | `killed_by_system`; on Linux corroborated by the cgroup's `memory.events` `oom_kill` | no; poison and one engine (§7.5) |
| exit `EXIT_OUT_OF_MEMORY` after `engine.out_of_memory`; Windows `0xC0000017` | `out_of_memory` | no; poison and one engine |
| a protocol violation before the commit point | `violation`, ended | yes |
| a protocol violation after the commit point | read and discard, wait (§5.4 step 5); `crashed` if the exit is abnormal | as `crashed` |
| any other exit or signal mid-job | `crashed` | yes |
| handshake refused, or the starting engine judged hung by §8.3 (no handshake bytes, no CPU, no page-ins over `hang_ticks`). There is no handshake time limit (review 3 F11). | `start_failed` | yes |
| another protocol version | `incompatible` | no (pool `Incompatible`) |

### 6.5 Out-of-memory errors inside the engine

Windows fails allocations at the commit limit instead of killing (`MemoryError`, or a native `STATUS_NO_MEMORY`); on macOS and Linux this happens under a hard limit. The engine catches `MemoryError` at the job's top level, drops its references, writes a **pre-encoded** `engine.out_of_memory` reply and exits with `EXIT_OUT_OF_MEMORY`. After the commit point, the outcome is resolved (§5.7), after ADR-0012's in-process rollback if it can run.

## 7. Admission and memory [A]

### 7.1 Budget (owner decision 2)

`budget = memory_share × total`, with `memory_share` = 0.25 (a setting).
- `total` is physical RAM (macOS `hw.memsize`, Linux `MemTotal`, Windows `ullTotalPhys`), lowered to a container limit (cgroup v2 `memory.max`, v1 `memory.limit_in_bytes`, or a Windows job limit).
- The budget covers engines and the sidecar's decoding of their replies.
- No CPU formula, no startup computation (owner decision 1; design review M1).

### 7.2 Estimates

`E = usual_peak(W,k) × max(1, size_now / size_at) × (provisional_margin if provisional) + usual_reply(W,k) × decode_factor`.
- `decode_factor` = 8 until BL-0082 measures it.
- A cheap job's estimate is the measured interpreter baseline (about 34 MB).
- An unmeasured large job runs alone. Spike S4's ratios are not used as a guess.
- The reaped peak is the job's absolute peak, the interpreter included; a memory kill teaches the estimate.

### 7.3 Admission rule (owner decision 2, amended by the owner's answer 6; answers 1 and 3)

| Pressure | Large job | Cheap job | `recover` |
|---|---|---|---|
| `Normal` (or `Unknown`, §7.4) | admitted if no large job runs, or all running large jobs and this one are measured or provisional and `Σ E(running) + E(this) ≤ budget`; on Windows also `E(this) ≤ ullAvailPageFile` | admitted (not while a head exists at `Warn`, §4.7) | admitted |
| `Warn` | admitted only if **no other job of any kind runs** (owner answer 6) | as a large job | admitted |
| `Critical` | not admitted, except the Run-anyway chain (below) | not admitted | admitted (owner answer 3: loads no ledger; refusing it would leave a half-applied save with writes blocked) |

- **Alone means admitted.** A job alone is always admitted at `Normal` or `Warn`, even if `E > budget`.
- **Running jobs are never stopped by admission** (`MemoryAdmission`; seeded `admit_under_pressure`, `admit_at_warn`).
- **Run anyway** (owner answer 1; review 3 F10; TLA v2 M6):
  - It is offered on a job the user started (P1, never a background job) while it waits for memory.
  - Pressing it makes the chosen job the head (§4.7), and **applies to every job it waits for**, transitively: saves ahead of it in its lane, the R4 of the previous save, the rebuild a preview waits for. These run first, one at a time, at any pressure, each as soon as no other large job runs.
  - The chain counts as the one Run-anyway job (at most one chain at a time).
  - The exemption ends when the chosen job ends or is cancelled.
  - It is a logged decision (§9.3); property `RunAnywayProgresses`.
- **The amendment, stated:** decision 2 ("no new job while the OS reports memory pressure") is amended by the owner's answer 6 to "one job at a time at warning, nothing new at critical", plus Run anyway (answer 1) and recovery (answer 3). Cheap jobs follow the rule (review 2 F5).

### 7.4 The pressure signal per OS (`MemoryPressurePort.level() -> Normal | Warn | Critical | Unknown`)

Read at every dispatch decision and on each watcher tick while jobs run (2.15 µs for the macOS sysctl, spike s1).

| OS | Source | Warn | Critical |
|---|---|---|---|
| **macOS** [A] | `sysctlbyname("kern.memorystatus_vm_pressure_level")`: 1, 2, 4; the kernel's own judgement | 2 | 4 |
| **Linux** [M11; CI from A] | the cgroup's `memory.pressure` (PSI) when limited, else `/proc/pressure/memory`; fallback `MemAvailable` or `memory.max − memory.current` | PSI `some avg10` ≥ 10 %; fallback available < 10 % | PSI `full avg10` ≥ 5 %; fallback < 5 % |
| **Windows** [M11] | `CreateMemoryResourceNotification(LowMemoryResourceNotification)`, `GlobalMemoryStatusEx` | `dwMemoryLoad` ≥ 90 % | the notification is signalled, or `ullAvailPageFile` is below the smallest measured estimate |

Thresholds are settings. **Unknown** (no readable signal): admission uses the budget only, and `engine.pressure.unknown` is logged once. The cost, stated: Finzytrack can then start a job into a machine already short of memory.

### 7.5 After a memory death

After `killed_by_system` or `out_of_memory`:
- the pool runs **one large engine at a time** (`single_engine`) until `single_engine_recover` (20) consecutive healthy large jobs have ended at `Normal`, or the next app start;
- the usual peak becomes at least the peak at death;
- the poison counter moves (§8.7).

### 7.6 Containers and hosting

- **Containers:** the budget is lowered to the container limit, and PSI is read from the container's cgroup.
- **[Hosted]:**
  - the pool and budget stay ADR-0041's single process-wide entry;
  - jobs carry their user, and cross-user round-robin becomes fairness and quotas (deferred);
  - measurements, generation high-water marks, lock files, the journal index and closed records live under each user's root;
  - R4 follows ADR-0041's hosted design.

## 8. Liveness and progress

### 8.1 Protocol v2 [A]

`PROTOCOL_VERSION` becomes 2; a v1 peer is `incompatible`. S0035's envelope rules stay. What changes:
- **Per request:** zero or more `progress` messages, at most one `gate` (saves and creates), then **exactly one** final reply.
  - **`progress`:** `{"v":2,"id":N,"op":op,"progress":{"seq":k,"phase":P,"cpu_ms":c,"peak_rss_bytes":m,"done":d,"total":t}}`.
    - `phase` is one of: `starting`, `reading_files`, `parsing`, `booking`, `plugins`, `validating`, `querying`, `waiting_for_lock`, `verifying`, `building_mirror`, `publishing`, `committing`, `serializing`, `recovering`.
    - Sent at each phase change and at least every `beat` (1 s).
    - `done` and `total` count progress; for `reading_files`, `done` is bytes read.
    - **`waiting_for_lock` is the only declared wait** (review 3 F12).
  - **`gate`** is answered by `proceed`, or the engine is ended (§5.4).
  - **Final replies** carry `usage {cpu_ms, peak_rss_bytes}`.
- **Domain outcomes are results:** `snapshot_moved {cause}`, `orphan_journal {owner_ws, session, job}`, `superseded`, `nondeterministic`, `mismatch`, `plan_outdated`, `legacy_lock_held`, `result_too_large` (area review F7). `error` keeps protocol and internal faults and `engine.out_of_memory`.
- **The output channel** has a lock, so no two messages interleave inside a line.
- **The request** carries `job {id, kind, ws_key, attempt}` and no time limit. `EnginePort.call(…, timeout)` becomes `submit(job) -> JobHandle` (`status()`, `cancel()`, `outcome()`).

### 8.2 The watcher and on-time ticks [A]

A watcher on the scheduler thread's event queue (§9.3) ticks every `tick` (1 s) per running engine. The schedule is the previous actual wake plus `tick`.

A tick is **on time** when all of these hold:
- it woke within `tick/2` of its schedule on `Clock.monotonic()`;
- `Clock.monotonic_with_sleep()` advanced by no more than `tick + tick/2` (no machine sleep inside it);
- the sidecar kept up: it is **not** on time when unread bytes wait in the pipe (`FIONREAD` > 0 on POSIX, `PeekNamedPipe` on Windows) and the reader read nothing since the last tick (review 3 F5; spike C: a blocked writer showed 65,535 bytes waiting and 0.000 s CPU for ever).

Clocks:
- macOS `CLOCK_MONOTONIC`: verified, it ran 235,414 s ahead of `time.monotonic` (spike s1).
- Linux `CLOCK_BOOTTIME`.
- Windows `QueryInterruptTime` against `QueryUnbiasedInterruptTime` (verify, M11).

Late ticks count for nothing. **App Nap:** the shell holds an `NSProcessInfo` activity while jobs run. The [A] slice verifies that the sidecar's ticks stay on time with the window hidden; if not, the sidecar takes the activity itself (review 3 O14).

Each tick samples, **from outside** (`ProcessProbe`):
- **group CPU:** a monotone accumulator over the engine's process group (review 3 F11). For each member seen in both samples, add its increase (`user + system + children_user + children_system`); for a new member, add its whole total; a member that disappeared adds nothing more. Spike D: a naive sum fell by 1.29 s when an orphaned grandchild exited. Members are listed by `proc_listpids(PROC_PGRP_ONLY)` (macOS), `/proc` (Linux), the Job Object (Windows).
- the group's RSS and **page-ins** (macOS `pageins`, Linux `majflt`, Windows the job's page-fault count);
- each member's process status;
- the system's idle CPU over the tick;
- bytes the reader read since the last tick. After a violation past the commit point, discarded bytes do not count.

These feed `liveness.judge(state, sample) -> state, verdict`, a pure, logged table (§9.3).

### 8.3 Hung [A]

An engine is **hung** after `hang_ticks` consecutive on-time ticks in each of which:
- no bytes arrived;
- the group's CPU advanced less than 1 % of the tick;
- the system had at least half a core idle;
- the group's **page-ins did not advance**, so it is not waiting on swap-ins. This holds at every pressure level (review 3 F11; draft 2 required `Normal` pressure, so a hang was never ended on a Mac sitting at "warning").

A `stopped` status (SIGSTOP, a debugger) counts as a hang tick even on a busy machine. Linux `D` (uninterruptible disk wait) never counts; the job shows "waiting for a disk". The same rule judges a starting engine (no handshake limit, §6.4) and a draining leader (§4.6). The verdict is an end with reason `hang`, a stop (except while draining, where the reason is `drain`).

`hang_ticks = max(10, 3 × the usual longest quiet stretch)` (§1.1). This is a count of observations of a condition no healthy run builds up, not a time budget:
- C code holding the GIL still uses CPU;
- a blocking read or a sleeping plugin keeps the progress thread speaking;
- a swap-in shows page-ins.

Review 2's spike s4: a GIL-holding regex used 0.13–1.0 s of CPU per tick and was never counted.

### 8.4 Stalled

**Stalled:** progress bytes arrive, but for `stall_ticks` consecutive on-time ticks:
- the group CPU advanced less than 1 % of the tick;
- `phase`, `done` and `total` did not change;
- the phase is not `waiting_for_lock`.

Examples: a plugin blocked on the network; a cloud placeholder that never downloads (`reading_files` with `done` flat; message "Waiting for a ledger file to download from your cloud storage…"). `stall_ticks = max(30, 3 × the usual longest stalled stretch)`.
- **Interactive (P0–P2):** a status, "No progress for a while", with **Cancel**; never stopped.
- **Background (P3):** stopped (`stalled`, not a stop, counted in poison), at warning. Never an unmeasured job, never a save past its commit point (owner answer 2).
- **After a violation past the commit point:** a stall with `phase` frozen ends the engine (`stalled_after_violation`, §5.4 step 5).

### 8.5 Runaway (owner decision 4) [A]

A **background** job is stopped (`runaway`, not a stop, poison, warning) when the group CPU exceeds `max(runaway_factor × B, B + runaway_floor)`:
- `B` is the usual CPU (§1.1), size-scaled;
- `runaway_factor` = 10;
- `runaway_floor` = 10 s.

The floor makes the factor larger than "about 10×" for jobs under about 1.1 s of CPU (review 2 O15; §13). Unmeasured and provisional jobs have no bound. Interactive jobs past the bound are flagged "much longer than usual", with Cancel.

### 8.6 Learning

After each run, `measurements.update(record, run)` (pure, logged, §9.3) applies §1.1. The file is written atomically, at most once per job. An unreadable file is treated as unmeasured.

### 8.7 Supervision: one table per engine, one for the pool, poison per workspace [A]

S0040's statechart is split (H6; seeded `stops_per_engine`).

**Per engine** (`app.platform.engine_process`):
- **States:** `Spawning → Handshaking → Clean → Running(job, phase, past_commit_point) → Draining(normal | discard) → Gone(classification)`, plus `Ending(reason)`.
- **Events:** `Spawned`, `SpawnFailed`, `HandshakeOk`, `HandshakeForeign`, `Dispatch`, `Progress`, `Gate`, `Proceed`, `Reply`, `Verdict(hang|stalled|runaway)`, `Cancel`, `End(reason)`, `OutputEnded`, `Reaped(status, usage)`, `GroupGone`, `Violated`.
- `Clean` accepts cheap jobs, a `plan`, and one heavy job.
- `Draining` holds the slot until `GroupGone`.
- Past the commit point, `End` is emitted only for `hang` and `stalled_after_violation` (`NoKillAfterCommitPoint`). `Violated` past the commit point leads to `Draining(discard)`.
- EP-17 tests apply; the reference is generated (`design/generated/statecharts/engine-process.md`).

**Pool** (`engine_pool.table`):
- **State:** engines by state, the stop times, `single_engine` with its healthy count, `unavailable`, `incompatible`.
- **Events:** `EngineGone(classification, ws, fingerprint, now)`, `Request(now)`, `PressureChanged`, `RunAnyway`, `Reset`, `Close`.
- **S0040's rules, at pool level:**
  - backoff `delays` (0.5, 2, 8 s);
  - `max_crashes` (3) stops within `window` (300 s) make the pool `Unavailable` (§4.9), until a stop leaves the window or the user presses Try again;
  - window and backoff run on `monotonic_with_sleep()`.
- `PoolNoHeavyWhileUnavailable`, `OwnKillNotAStop`.

**One ledger cannot trip the pool** (TLA F5; review 3 O1, O2):
- A fault enters the stop history only when it is a start failure, a cheap job's fault, or the first counted fault of its workspace at that content fingerprint.
- The workspace keeps the fingerprints of its last `max_crashes` counted stops, so a ledger toggling between two bad states does not add a stop per toggle.
- These are cleared by Try again.
- A fault before the job knows its content fingerprint (a crash mid-read) is keyed by the stat fingerprint taken at dispatch.
- Properties `OneLedgerCannotTripPool`, `BoundedFaultLoop`.

**Poison** (in `JobQueue`):
- A counter per (workspace, kind, fingerprint) of consecutive faulted ends: crash, hang, violation, memory death, runaway, stalled.
- At `poison_limit` (2) that key is refused until the fingerprint changes (a plugin fix included) or Try again; a healthy end resets it.
- Kept per kind (§13).

**Settings** (app-level `settings.yaml`, applied at the next event, logged as an input, §9.3; outside its range: refused, `settings.refused`):

| Setting | Default | Range | § |
|---|---|---|---|
| `max_engines` | 2 | 1–8 | 1 |
| `memory_share` | 0.25 | 0.05–0.9 | 7.1 |
| `decay` | 0.9 per run | 0.5–0.99 | 1.1 |
| `provisional_margin` | 1.5 | 1–4 | 1.1 |
| `runaway_factor`, `runaway_floor` | 10; 10 s | 2–100; 1–600 s | 8.5 |
| `beat`, `tick` | 1 s, 1 s | 0.25–5 s, `beat ≤ tick` | 8.1–8.2 |
| `hang_ticks`, `stall_ticks` floors | 10, 30 | 3–600, 10–3600 | 8.3–8.4 |
| `quit_patience` | 10 | 3–600 | 4.8 |
| `idle_ticks` | 5 | 1–600 | 6.1 |
| `checkpoint_ticks` | 60 | 10–3600 | 9.3 |
| `max_crashes`, `window`, `delays` | 3; 300 s; (0.5, 2, 8) s | 2–10; 60–3600 s; each 0–60 s | 8.7 |
| `poison_limit` | 2 | 1–5 | 8.7 |
| `single_engine_recover` | 20 | 1–1000 | 7.5 |
| `snapshot_retries` | 3 | 1–10 | 5.2 |
| `aging_passes`, `preempt_max` | 8, 2 | 1–100, 0–10 | 4.2–4.3 |
| `cheap_margin` | 32 MB | 0–1024 MB | 1 |
| queue bounds | §3.1; 64 per user | 1–1024 | 4.5 |
| pressure thresholds | §7.4 | 1–99 % | 7.4 |

## 9. What the user sees, the log, and replay

### 9.1 Messages [M2 UI, BL-0092; outcomes and codes from A/B/C]

Every message follows EP-27. Job state comes back in API responses as a tagged union; `GET /api/w/{ws}/jobs` and `POST …/jobs/{id}:cancel` [M2]. "Usual" is §1.1's.

| Situation | Message | Actions |
|---|---|---|
| queued | "Waiting for other ledger work to finish…" | Cancel (if cancellable) |
| waiting to run alone | "Waiting for other ledger work to finish, so this can run on its own…" | Cancel |
| waiting for memory | "Waiting for memory. Your computer is low on memory. Closing other apps helps." | Cancel; **Run anyway** (jobs you started) |
| running, per phase | "Reading your ledger files…", "Checking your ledger…", "Running your query…", "Saving…", "Updating your views…", "Checking your last change…" | Cancel where allowed |
| `waiting_for_lock` | "Waiting for other ledger work on these files to finish…" | Cancel |
| file downloading (stalled `reading_files`) | "Waiting for a ledger file to download from your cloud storage…" | Cancel |
| the old app is saving (legacy lock held) | "The old Finzytrack app is saving this ledger. Quit the old Finzytrack first, then try again." | Try again |
| much longer than usual | "This is taking much longer than usual for this ledger." | Cancel |
| no progress | "No progress for a while. It may be waiting for something." | Cancel |
| waiting for a disk / cannot be ended | "Finzytrack is waiting for a disk and cannot stop this yet. It will continue when the disk answers." | Copy diagnostics |
| Cancel past the commit point | "Finishing your change…", then "Your change is saved." | Undo where available |
| save cancelled before the commit point | "Your change was not saved. Your ledger is unchanged." | — |
| plan outdated by an earlier save | "Your earlier change moved this entry. Review it again." | Review |
| busy | "Finzytrack is busy with other ledger work. Please try again in a moment." | Try again |
| crash on a read | "The ledger engine stopped while working on this. Your ledger files are safe." | Try again, Copy diagnostics |
| memory death | "Your computer ran out of memory while Finzytrack worked on this. Your ledger files are safe. Close other apps and try again." | Try again |
| hang | "The ledger engine stopped responding and was restarted. Your ledger files are safe." | Try again |
| save resolved after an engine death | "Your change was saved." / "Your change was not saved. Your ledger is unchanged." / "Your change was not saved, and your ledger was changed by another program." / "Finzytrack could not finish your change and put your files back as they were." / "A ledger file was changed by another program while Finzytrack was saving. Saving is off until you review it." | — / — / Review files / — / Review files |
| another workspace's interrupted save covers a shared file | "A change in workspace *A* was interrupted in a file this workspace shares. Finzytrack is repairing it." | — |
| recovery failed twice | "Your last change may or may not be saved. Reopen the workspace to check." | Reopen, Copy diagnostics |
| poisoned | "Finzytrack stopped trying: the ledger engine failed twice on this ledger. Your files are safe. Change the ledger or try again." | Try again, Copy diagnostics |
| pool unavailable | failure-modes E1's message | Try again, Copy diagnostics |
| ledger keeps changing | "Another program is changing your ledger. Finzytrack will update when it stops." | Try again |
| R4 stopped, cancelled or failed | "Finzytrack could not double-check your last change. Saving is paused until the check runs." | Run check now |
| R4 mismatch, or could not decide | "Finzytrack's check of your last change found a difference. Saving is paused. Your backup is here." | Show backup, Copy diagnostics |
| R4 nondeterministic | "Your ledger's plugins give a different result each time they run. Finzytrack updated your views from a fresh load." | Learn more |
| quit during a save | "Finishing your last change…"; after `quit_patience`, **Quit anyway** with "Finzytrack will finish or undo this change the next time it opens. Your ledger stays safe." | Wait, Quit anyway |

### 9.2 Log events (§6A; safe kinds only)

**Jobs**
- `engine.job.ended` (info): kind, outcome, `duration_ms`, `queued_ms`, `admission_wait_ms`, `cpu_ms`, `peak_bytes`, attempt, `ws`.
- `engine.job.cancelled` (info: reason), `engine.job.preempted` (info), `engine.job.flagged` (info).
- `engine.job.runaway_stopped`, `engine.job.stalled_stopped` (warning).

**Engines and the pool**
- `engine.stopped` (warning: `crashed`, `hang`, `protocol_violation`, `stalled_after_violation`, `start_failed`).
- `engine.drained` (debug: leftover group members ended).
- `engine.killed_by_system`, `engine.out_of_memory` (warning).
- `engine.pool.single_engine` (warning), `…_ended` (info).
- `engine.unavailable` (error), `engine.unkillable` (error), `engine.workspace.poisoned` (error).

**Admission and queues**
- `engine.admission.waiting` (info), `engine.admission.run_anyway` (info).
- `engine.pressure.changed` (info), `engine.pressure.unknown` (info, once).
- `engine.queue.busy` (info, once per drain).

**Locks, reads and the mirror**
- `engine.lock.waiting` (info, once per wait: job kind, count of locks), `engine.lock.acquired` (debug: `waited_ms`), `engine.lock.relocked` (debug: set changed).
- `engine.legacy_lock.held` (info: the save or copy refused).
- `engine.snapshot.moved` (debug; info when retries run out).
- `mirror.publish.refused_stale` (info), `mirror.generation.published` (info).

**R4**
- `engine.r4.passed` (debug), `engine.r4.superseded` (info: `disk_moved` | `engine_changed`), `engine.r4.mismatch` (error), `engine.r4.nondeterministic` (warning), `engine.r4.undecided` (error).

**Saves, quit, durability and settings**
- `engine.commit.finishing` (info), `engine.commit.resolved` (warning: resolution, `owner_ws` when another workspace's), `engine.quiesce.quit_anyway` (warning).
- `fs.durable_sync.fallback` (warning, once per volume).
- `settings.refused` (warning).

### 9.3 Replayable decisions (requirement; owner, 2026-10-26: "Yes, add it to the scheduler spec as a requirement. Let's make sure this logging system design is documented and implemented")

**Goal:** rebuild, from a user's log, every decision Finzytrack made and why, with no ledger content in the log.

**R1. Decisions are pure, on one thread** [A] (review 3 F13).

The tables:
- `next_dispatch` with admission (§4.7, §7.3);
- the pool table and the per-engine statechart (§8.7);
- `liveness.judge` (§8.2–8.5);
- `measurements.update` and the estimate (§1.1, §7.2);
- `resolve` (§5.7) [C].

Each is a pure `(state, event) -> (state, effects)`: no clock, random number, environment or global; clocks arrive as event values (`monotonic_ms`, `with_sleep_ms`).

How they run:
- All tables run on **one scheduler thread** that takes events from one queue. `seq` is assigned when an event is taken, and its `sched.input` line is written before the table runs.
- No table iterates an unordered collection (every collection a decision depends on is insertion-ordered or sorted). The replay test runs under three `PYTHONHASHSEED` values.
- Each table has a `table_version` (a hash of its module, generated at build).

**R2. Every input and output is logged, content-free** [A], through S0039's registry and default-deny redaction, unchanged.

- **`sched.input`** (info). Fields:
  - `seq`, `table`, `event`, `ws`, `job`, `kind`, `class`, `attempt`, `reason`;
  - counts;
  - `monotonic_ms`, `with_sleep_ms`, `cpu_ms`, `pageins`, `peak_bytes`, `budget_bytes`, `estimate_bytes`, `avail_commit_bytes` (Windows);
  - `usual_cpu_ms`, `usual_peak_bytes`, `usual_quiet_ticks`, `size_ratio`, `provisional`;
  - `pressure`, `fingerprint` (a hex hash, `id` kind), `engine_build`, `plugin_set`, `table_version`.

  Never ledger text, paths outside the app dirs, account names or amounts.
- **`sched.settings`** (info): every §8.7 value, at session start and on each change.
- **`sched.decision`** (info): `seq` of its input, effects as enums with ids, `state_digest` (SHA-256 of the canonical state encoding).
- **`sched.checkpoint`** (info): full table states, measurement records and settings. Written at session start, every 1,000 inputs, and as the first line of each rotated file.
- **Ticks are run-length encoded:** the judge depends only on each tick's class and the cumulative CPU. A run is also written every `checkpoint_ticks` (60) ticks as a partial line, so a crash loses at most that many ticks.
- **Inputs that change no state** and emit only a refusal (busy) are logged as counts.
- **A gap in `seq`** is reported by the replay, which resumes at the next checkpoint.

**R3. Replay test in CI** [A]: `scheduler_replay(log) -> divergences` feeds checkpoints and inputs to the tables of the same `table_version` and compares effects and `state_digest`.
- Every scheduler scenario test is also a recording.
- A Hypothesis walk asserts `replay(record(walk)) == walk`.
- It runs under three hash seeds.
- A table changed without its version, and a gap, must both be reported.

**R4. Flight recorder** [A]: a ring of finer, non-decision events (raw tick samples, message sizes and timings, queue snapshots, lock-wait starts and ends), the last 4,096 events or 1 MiB, registered in `state_registry`.
- **Dumped** through the same redaction (`sched.flight`, with a `dump_id`) on an error event of §9.2, and on `engine.stopped {hang}`, `engine.job.stalled_stopped`, `engine.job.runaway_stopped` and `engine.unkillable` (review 3 F13). Cleared after a dump.
- **Placed in [A]:** the liveness rules are new there, and their rare false positives are what it is for; it is about a hundred lines on S0039's sink.

**R5. User-log replay tool** [M2, BL-0093]: `scripts/replay_scheduler_log.py` on a Report a problem bundle; the bundle includes decision lines and flight dumps by default.

**R6. Data-dependent reproduction** [M2+, BL-0060, BL-0093]: a consented, anonymized copy. `fingerprint`, `engine_build` and `plugin_set` confirm a *byte-identical* copy; an anonymized copy is compared by content-free structure (counts, size buckets, §6A.4).

**R7. Limits:**
- Replay reproduces decisions given the inputs *as observed*, not real thread or signal timing (covered by TLA+ and property tests) and not what the OS did.
- Non-deterministic plugins can make a reproduced load differ.
- A decision taken outside the listed tables shows up as an unexplained input.

**Volume:**
- **Per job:** a heavy job is about 25 lines at about 250 bytes, about 6 KB.
- **Per day:** a heavy day (one open, 50 saves with their R4s, 100 query batches) is about 200 jobs, about 1.2 MB, plus checkpoints.
- **Retention:** 10 MB × 5 rotation keeps about 6 weeks.
- **Timer-refreshed dashboards** cost nothing while `ledger_version` is unchanged (recipe memo, §3.1).

**ARCHITECTURE §6A changes** (the [A] slice applies them):
- §6A.1: a paragraph on decision lines.
- New §6A.7 "Replayable decisions": R1–R7, events, the run-length rule, checkpoints, the recorder and its triggers, volume.
- §6A.5: `sched.*` at info.
- §6A.6: the replay test and a `sched.*` redaction-corpus case.
- §6A.4 [M2]: bundling.
- §2.5: the recorder row.

ADR-0039 is amended to name the decision log as part of the logging design.

## 10. Cross-platform behaviour (ADR-0061)

| Concern | macOS (first) | Linux | Windows |
|---|---|---|---|
| Our end of an engine | `killpg(SIGKILL)` | same | `TerminateJobObject` |
| End with the sidecar | watchdog, then `killpg(0)` | same | Job Object kill-on-close |
| Confirmed gone | reaped and `killpg(pgid, 0)` = `ESRCH` (`EPERM` = members remain) | same | job active count 0 |
| Durable sync | `F_FULLFSYNC` (≈5 ms, spike H) | `fsync` | `FlushFileBuffers`, `MOVEFILE_WRITE_THROUGH` |
| Canonical path | `F_GETPATH` (+ absent-path rule) | `realpath` (+ case-folding dirs) | `GetFinalPathNameByHandleW` |
| OS memory response | jetsam `SIGKILL` (compression and swap first in most cases) | OOM killer; `oom_score_adj` +500 | allocation fails |
| Pressure | memorystatus sysctl | PSI / `MemAvailable` | low-memory notification, commit |
| Per-job peak and CPU | `waitid(WNOWAIT)` + footprint, `wait4` | `wait4` (kB) | job accounting |
| Group CPU, page-ins | `proc_listpids(PROC_PGRP_ONLY)` + psutil, `pageins` | `/proc`, `majflt` | Job Object |
| Sidecar behind on the pipe | `FIONREAD` (verified) | `FIONREAD` | `PeekNamedPipe` |
| Sleep-counting clock | `CLOCK_MONOTONIC` (verified) | `CLOCK_BOOTTIME` | `QueryInterruptTime` (verify) |
| Ticks delayed by the OS | App Nap; the shell (or the sidecar) holds an activity | — | — |
| Lock released after our end | at group end | same | may lag `TerminateJobObject` (verify): waited for |
| Legacy-lock detection | portalocker 3.2.0 `flock` | same | portalocker 3.2.0 `msvcrt.locking`, first 64 KiB (single-instance ADR) |
| Start cost | 0.07–0.10 s, 34 MB | expected similar | BL-0087 [M1b], M11 |
| Background OS priority | `nice +10` only for P3 jobs nothing can wait on (prefetch, P3 `snapshot`, vector runs); R4 and background writers at normal priority. `PRIO_DARWIN_BG` is settable and clearable (spike s5); whether clearing restores full speed is measured in [A] before use. | `nice +10`, same jobs | `BELOW_NORMAL_PRIORITY_CLASS`, same jobs |

The write-path parts (§5.4, §5.5, §5.7, the Job Object, `durable_sync`, `canonical_path`, the lock lag) are designed against Windows now and tested on the Windows runner from M1b (ADR-0061 d6, BL-0087). The lock lag is tested with a fake lock port reporting "held" for N attempts, plus one real smoke run. The rest is ported in M11; Linux T1 runs the pure tables and the Linux adapters from [A].

## 11. The TLA+ models for draft 3

### 11.1 What the models are

Two modules, both derived from the second TLA+ check (review:m1a/scheduler-design/tla/v2):
- **`EngineScheduler.tla`**: the main model, `EngineScheduler2.tla` of tla/v2 with every `FIX_*` of v2 made the only behaviour, the policy cuts of §11.8, and the draft-3 deltas below.
- **`Admission.tla`**: `next_dispatch` over abstract job sizes and classes, as in tla/v2.

Draft 3 and the models agree. §11.6 lists the abstractions and why none bears on safety or liveness.

**Draft-3 deltas to apply to `EngineScheduler2.tla`** (the slice, or the agent preparing the long run, applies them before running):
1. **R4's confirm** follows §5.5 (parse-only): `mismatch` iff the loaders differ at the parse stage (`LOADER_DIFF`); `nondeterministic` if both equal each other and the record; "restart once, then block as undecided" if both equal each other but not the record.
   - This replaces v2's `FIX_R4_CONFIRM_PASS` branch, which reached `pass` when a drifting plugin input had returned; draft 3 calls that `nondeterministic`.
   - Both are non-blocking and clear the record, so the safety and liveness properties are unaffected; `R4OutcomeDefined` and `R4NoFalseMismatch` stay.
2. **The legacy lock** becomes a guarded refusal: a nondeterministic environment flag `LegacyHeld(w)` that, when set, makes a save's or create's first step end `legacy_lock_held` with the disk unchanged. There is no legacy actor (§11.8).
3. **The R4 gate** is re-read from disk before every wait (`FIX_R4_REFRESH` as the only behaviour).
4. **Draft 1's 15 seeded bugs** are ported into `EngineScheduler.tla` where v2 lacks them (§11.5), so one module holds every seed. The draft-1 "Fixed" module (`tla/spec/EngineScheduler.tla`) is retired: its second-instance and legacy actors are cut by the policy, and its R4 and lane rules are superseded.

### 11.2 Scope

| | In the model |
|---|---|
| **Actors** | one sidecar (the single instance), with crash, restart, quiesce and Quit anyway; up to 3 engine process records over 2 slots, a fresh process per heavy job; orphaned engines of a crashed session and their watchdog; the user (cancel a save, cancel R4, Run check now, Run anyway, Quit anyway); a lock-ignoring editor; a sync client creating a file that a glob then matches; a drifting plugin input (`PluginDrift`); OS memory pressure `Normal`/`Warn`/`Critical`; a cache purge; the legacy lock as a held-or-free flag (refusal only) |
| **World** | files 1–3 as version tags (`ABS` = absent); lock keys 1–4 (4 = a seeded alias); workspace 1 = {2, 3} plus a glob that may match file 1; workspace 2 = {1, 2}, so file 2 is shared; one commit writes all its targets |
| **Jobs** | `commit`, `create`, `rebuild`, `query`, `preview`, `r4`, `recover` |
| **Mechanisms** | per-file lock keys and canonical order; the non-blocking relock with a restart bound; the app-wide journal index (checked by verify, gate, publish, R4 and recovery); verified snapshot reads; conditional publish with the id allocated at publish and the high-water mark outside the cache; the gate's checks (disk, mirror, journal index, closed and `.mismatch` records, legacy refusal); the commit point agreed by both sides; journal close into the closed record; R4 split (check, lock-free load, digest, check, parse-only confirm) with the R4 exemption and refresh; reads beside a rebuild; previews after earlier saves; `create` in `COLD`; re-plan by end-and-requeue with per-file "own" judged from durable records of all workspaces; a violation past the commit point (read and discard, stall end); draining with `drain` ends; recovery retried once, reading journals only after its locks, including another workspace's journal; quiesce and Quit anyway; pressure levels with the Run-anyway chain; poison per kind; pool stops capped per (workspace, fingerprint); `single_engine` |
| **Admission.tla** | the deterministic `next_dispatch` over sizes {small 1, large 2, unmeasured alone}, a budget of 2, 2 slots, classes P0–P3, refreshing query streams: the reservation, higher-class passing counted for aging, no cheap job at `Warn` while a head exists, Run anyway as the head |
| **Abstracted** | bytes as version tags; hang, stall and runaway as budgeted nondeterministic ends at any step (`Kill(hang)`, `Kill(stallv)`); priorities and aging only in `Admission.tla` (the main model uses strong fairness per job kind); memory arithmetic only in `Admission.tla` (main model: pressure flag and single-engine); no power loss (S5's domain; its durability assumption is `durable_sync`'s contract, §5.4); no cheap jobs or spare engine; no `plan` separate from its preview; canonical-path computation abstracted to "one key per file", with the seeded alias as the counter-case |

### 11.3 Safety properties (invariants, plus TLC's deadlock check in every safety config)

**Lanes, engines and admission**
- `OneExclusivePerWorkspace` (per session); `RunningUntilAnsweredOrDead`; `EnginesBounded` (per session).
- `MemoryAdmission`; `PoolNoHeavyWhileUnavailable`; `OneLedgerCannotTripPool`; `OwnKillNotAStop`; `AnsweredJobNotAStop`.

**The mirror and reads**
- `PublishOnlyCurrent`; `GenerationsMonotonic`; `PublishedIsCommittedState` (per workspace, with a shared file); `PublishedNotOlder`; `VerifiedReadsAreSnapshots`.

**Saves**
- `GateOnCurrentMirror`; `NoCommitWithoutBaseline`; `PreviewAfterEarlierSaves`; `OwnCommitsNeverExternal`.
- `NoKillAfterCommitPoint`; `CancelBeforeCommitLeavesDiskUnchanged`; `NoWriteAfterUnresolvedDeath`; `NoTwoWritersMidCommit` (two workspaces sharing a file, and orphans of a crashed session).
- `CleanEngineForUserCode`; `OnlySavesLockAcrossUserCode` (v2's `NoBackgroundLockAcrossUserCode`, renamed).

**R4**
- `R4BeforeNextCommit`; `R4NeverBehindItsGatedWrite`; `R4NoFalseMismatch`; `R4OutcomeDefined`.

**Outcomes and recovery**
- `OutcomeTruthful`; `AckedCommitNeverRolledBack` (given `durable_sync`); `RecoveryNeverClobbersForeignBytes`; `BlockedOnlyOnForeignBytes`.

**In `Admission.tla`:** `NoClassInversion`.

### 11.4 Liveness properties

The main model uses `LeanFairSpec` and its variants (`NormalFairSpec`, `QuitFairSpec`): weak fairness per process, strong fairness per job kind, and fairness of the environment's progress.
- `QueuedWriteLeavesQueue`; `RunningWriteEndsOrBlocks`; `QueriesEnd`; `PreviewsEnd`.
- `RecoveryResolves`; `MirrorEventuallyCurrent`; `R4EventuallyRuns`.
- `QuiesceTerminates`; `SlotEventuallyFreed`; `RunAnywayProgresses`.

In `Admission.tla`: `NoStarvationUnderAdmission`.

### 11.5 Seeded bugs (each must fail its target; configs `B_*` and `A_*`)

**From the second check** (`EngineScheduler2.tla`):

| Seed | Must fail |
|---|---|
| `r4_behind_queued_write` | `R4NeverBehindItsGatedWrite` (and `TEMPORAL` in its liveness config) |
| `r4_load_locked` | `OnlySavesLockAcrossUserCode` |
| `r4_no_recheck` | `R4NoFalseMismatch` |
| `journal_check_per_ws` | `PublishedIsCommittedState` |
| `relock_out_of_order` | deadlock |
| `lock_unsorted` | deadlock |
| `stop_reading_after_violation` | deadlock |
| `kill_on_violation_past` | `NoKillAfterCommitPoint` |
| `preview_before_saves` | `PreviewAfterEarlierSaves` |
| `admit_at_warn` | `MemoryAdmission` |
| `commit_in_cold` | `NoCommitWithoutBaseline` |
| `no_replan` | `OwnCommitsNeverExternal` |
| `quitanyway_says_saved` | `OutcomeTruthful` |
| `recover_journal_early` | `RecoveryNeverClobbersForeignBytes` |
| `lock_key_alias` | `NoTwoWritersMidCommit` (its corrected environment re-run is pending) |
| `pass_head` (`Admission.tla`) | `TEMPORAL` |
| `s6_noreserve` (`Admission.tla`) | `TEMPORAL` |

**Ported from draft 1:**

| Seed | Must fail |
|---|---|
| `lane_on_kill_sent` | `RunningUntilAnsweredOrDead` |
| `unconditional_publish` | `PublishOnlyCurrent` |
| `id_at_dispatch` | `GenerationsMonotonic` |
| `one_sided_gate` | `CancelBeforeCommitLeavesDiskUnchanged` |
| `stops_per_engine` | `PoolNoHeavyWhileUnavailable` |
| `verify_no_locks` | `VerifiedReadsAreSnapshots` |
| `gate_no_mirror` | `GateOnCurrentMirror` |
| `r4_not_persisted` | `R4BeforeNextCommit` |
| `hw_in_cache` | `GenerationsMonotonic` |
| `cancel_after_point` | `NoKillAfterCommitPoint` |
| `lane_on_requester_left` | `OneExclusivePerWorkspace` |
| `verify_no_journal_check` | `VerifiedReadsAreSnapshots` |
| `dispatch_while_unresolved` | `NoWriteAfterUnresolvedDeath` |
| `reason_after_kill` | `OwnKillNotAStop` |
| `admit_under_pressure` | `MemoryAdmission` |

**Dropped as unable to fail** (by the second check): `query_during_recovery` (rule 6 avoids waste, it is not needed for safety) and `recover_no_retry` (the retry is for the user, not for safety).

**Leave-one-out configs** (`M_*`) remove each fix in turn and must fail as listed in `configs.txt`.

### 11.6 Abstractions, and why none bears on safety or liveness

- **Priorities, aging and memory arithmetic are in `Admission.tla` only.** The main model replaces them by strong fairness per kind, which is exactly what `NoStarvationUnderAdmission` and the aging bound (pure-table test) establish. Safety in the main model holds for every dispatch order, and admission is checked separately as a deterministic function.
- **One commit writes all its targets:** the per-file write order, backups and fsync order are S5's model (`WriteProtocol.tla`), which composes underneath.
- **Hang, stall and runaway are nondeterministic budgeted ends at any step:** safety must hold whenever they fire (it does), and when they do not fire is the judge's tested behaviour, not the protocol's.
- **Canonical paths as "one key per file":** the computation is the `FileSystem` port's corpus test, and the seeded alias shows what breaks without it.
- **No power loss:** S5 checks it under the durability assumption that `durable_sync` now makes true on macOS.

### 11.7 What the models cannot show, and where it is covered

| Not shown | Covered by |
|---|---|
| aging's bound in the main model | pure-table test (§4.2's bound) |
| merging, preemption limits, cheap jobs, the spare engine, `plan` before `preview` | pure-table tests |
| the liveness judge's numbers (page-ins, group CPU, ticks) | `liveness.judge` tests with recorded samples; spike s4, C, D workloads |
| replay (§9.3) | the replay test |
| Beancount semantics; the parse-stage claim of §5.5 (a loader difference shows in `_parse_recursive`) | the corpus and ADR-0010's equivalence suite |
| power loss and fsync order | S5, plus `durable_sync`'s adapter test |
| how a canonical path is computed | the `FileSystem` corpus test |
| globs, absent paths, documents roots, same-size saves in one mtime tick | snapshot tests (§12 [B]) |
| the Windows lock lag | fake lock port |
| unkillable processes (assumed to end) | stated cost (§4.6) |
| larger worlds (1–2 saves, 1–2 queries, 1–2 faults) | `XL` configs, weekly |
| unsupported setups (second instance, other installations, a running legacy app writing) | removed by policy (§11.8) |

### 11.8 Removed by the single-instance policy (the single-instance ADR, S0044)

| Removed | Was |
|---|---|
| the legacy-app actor (`LegacyWant`, `LegacyLock`, `LegacyWrite`, `LegacyUnlock`), `NeedsL`, `FIX_LEGACY_BY_FILE`, `HAS_LEGACY`, `LEGACY_TWO_FILES` | a running v0.3 app writing a shared ledger under its lock |
| `NoSilentLostUpdateLocked`, `LegacyWriterProgress` | its safety and liveness |
| `_twowslegacy` configs; the legacy part of `_violation`, `F2_large_one` and `F2_large_two` (those configs stay, without it, as `F_violation`, `F_large_one`, `F_large_two`) | its environments |
| TLA v2 H4 (the legacy app writing through a file shared with a workspace that did not take its lock) | moot: no ledger is shared with a running legacy app |
| TLA F14 (the legacy app writing over a half-applied save before recovery) | moot |
| the draft-1 model's second-instance actor (`S2*`), `Fixed_second`, `Fixed_legacy`, `Live_fixed_legacy`, `Fixed_large` | a second v0.4 instance on the same app-local-data |

What stays: orphaned engines after a sidecar crash, two workspaces sharing a file, lock-ignoring editors and sync clients, crash recovery.

## 12. Test obligations per phase

**[A] the scheduler slice, before the read core** (amends ADR-0054 d4, ADR-0041, ADR-0039, ARCHITECTURE §2.0/§2.5/§6A/§7, failure-modes E1–E7, M1, M7):

- **The TLA+ models and their runner are a test obligation of [A].** Commit under `formal/tla/`:
  - `EngineScheduler.tla` and `Admission.tla` (§11.1, with the deltas applied);
  - `gen_cfgs.py` and `gen_admission.py`;
  - `configs.txt`: each config with its model, its **expected outcome** (`PASS`, `VIOLATE:<property>`, `DEADLOCK`, `TEMPORAL`) and its size;
  - `run_all.sh` / `run_all.py`: parallel and resumable, skipping finished configs whose model and config hashes are unchanged; writes `results/RESULTS.md`, ending in `ALL AS EXPECTED` or the surprises;
  - `HOW_TO_RUN.md`;
  - `MAPPING.md` (action → section → code function);
  - `fetch_tools.sh` and `tools.lock` pinning `tla2tools.jar` 1.7.4 (SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`, reports TLC 2.19) and Java 21 (Temurin LTS).

  Config names are tla/v2's without the `2` (`F2_r4` → `F_r4`, `B2_*` → `B_*`, `M2_*` → `M_*`, `LF2_*` → `LF_*`). Runs, by the times measured on the owner's M4 Pro with 3 workers (TLA v2; re-measured by the runner after the policy cuts, which shrink the legacy-bearing configs):
  - **T1 (each under about 3 min; the set in about 10 min):** every `A_*` of `Admission.tla` except the `A_large_*`; every seeded-bug `B_*` and leave-one-out `M_*` safety config (seconds each); `F_glob`, `F_linger`, `F_r4nondet` (≤ 8 s); `F_cold` (96 s); `F_r4` (102 s); `F_violation` (140 s); `F_recover` (170 s); `F_replan` (180 s); `LF_r4nondet` (42 s).
  - **T2 (minutes to about 40 min each):** `F_quit` (11 min); `F_pressure` (33 min); `F_beside` (36 min); `A_large_draft` and `A_large_fixed` (85 s, 303 s); the `M`-sized liveness configs (`LF_runanyway`, `LF_stuckcrit`, `LF_quitanyway`, and the seeds' liveness configs).
  - **Weekly and at each milestone exit (hours, possibly over a day):** `F_twows` (it passed 33.0M distinct states in 44 min and was still growing), `F_large_one`, `F_large_two`, and the `L`/`XL` liveness configs (`LF_glob`, `LF_linger`, `LF_cold`, `LF_recover`, `LF_stuckwarn`, `LF_violation`, `LF_r4`, `LF_quit`, `LF_replan`, `LF_pressure`, `LF_beside`, `LF_twows`).
  - A run that changes size class moves list by its measured time.

  Every action carries `@tla_action` in code once its code exists; the mapping test accepts actions of unbuilt phases listed in `MAPPING.md` with their phase.
- **Left to pure-table tests** (§11.7): the aging bound, merging, preemption limits, cheap jobs, the liveness judge's numbers, `EveryWaitHasAnExit` (every state that can last unboundedly has an enabled user action, or is a save past its commit point).
- **Tables:** exhaustive (state × event) tests and random walks for the per-engine and pool tables; generated references diff-checked.
- **`next_dispatch`:**
  - the §4.2 aging bound;
  - preemption limits;
  - merging with requesters;
  - spike s6's workload (a large job behind refreshing small ones is dispatched);
  - a P1 large job passes a P3 head when admissible, and the P3 head runs within the aging bound;
  - no cheap job at `Warn` while a head exists;
  - the Run-anyway chain runs a save's R4 and the save at `Critical`.
- **Protocol v2:** schema, contract tests, one final reply per request, no interleaving inside a line, a v1 peer is `incompatible`, and `legacy_lock_held` as a result.
- **Engines:**
  - fresh per heavy job: H1's `plug.py` as a test;
  - spawn flags: two engines give the same digest for a set-iterating plugin; no `__pycache__` written or read in a ledger folder.
- **Reaping and draining:**
  - the pool alone reaps;
  - a `SIGKILL`ed child is never `answered` (the `Popen.poll` trap);
  - the peak minus the interpreter baseline is within 5 % of a ≥ 200 MB allocation;
  - the macOS footprint is read from the zombie;
  - a plugin with a non-daemon sleeping thread and a detached subprocess: the job is `answered`, no stop is counted, the slot is freed after `drain`;
  - `EPERM` with a zombie leader is not "gone".
- **End classification:** our reason wins over any exit status; only `hang`, `stalled_after_violation`, `violation`, crashes and start failures are stops, at most one per (workspace, fingerprint), with the last `max_crashes` fingerprints kept; a pre-fingerprint crash is keyed by the stat fingerprint.
- **Liveness judge:**
  - a `SIGSTOP`ped engine is hung, also on a busy machine;
  - a GIL-holding regex is not;
  - a sleeping plugin is stalled;
  - an engine waiting on a CPU-bound child is not hung (group CPU), and the accumulator never decreases when a grandchild exits;
  - a hang at sustained `Warn` without page-ins is judged hung;
  - a full pipe that the sidecar does not read makes ticks not on time;
  - a stuck handshake is judged by the hang rule;
  - sleep and late wakes do not count;
  - a cloud-placeholder read with flat `done` stalls.
- **Stall and runaway:** background stopped; unmeasured and provisional never stopped; interactive flagged; the floor.
- **Admission:** the three pressure levels; recovery at `Critical`; single-engine mode ends after 20 healthy jobs; `Unknown`; provisional estimates after an update.
- **Usual:** decay with size-scaled values, invalidation on plugin change, provisional after an engine change, an unreadable file.
- **Pressure port:** macOS sysctl; Linux and Windows from fixtures.
- **Process groups:** a cancel ends children; the sidecar's death ends the group; `oom_score_adj`.
- **Settings:** ranges refused and logged.
- **Replay:** every scenario test replays with no divergence under three hash seeds; the Hypothesis walk; a version change and a gap are reported; a partial run line survives a simulated crash; `sched.*` in the redaction corpus.
- **Flight recorder:** bounded; dumped on the listed triggers; redacted.
- **App Nap:** the sidecar's ticks stay on time with the window hidden.
- **failure-modes.md:** E1–E7 (E3; E5 by §6.3; E7 by §5.4 step 4), M1, M7, and the known-gaps list.

**[B] with the mirror build:**
- **`canonical_path` corpus** per OS: case, NFC/NFD, symlink, symlinked directory, absent path; the registry deduplicates by it.
- **Lanes:**
  - exclusive jobs exclude each other;
  - shared jobs run beside shared ones and beside a rebuild;
  - a preview waits for a rebuild and for earlier saves;
  - a read waits for a running or earlier-queued save;
  - **a save confirmed while another runs commits after the first save's R4, and a query behind it ends** (review 3 F1);
  - saves are refused in WARMING and COLD;
  - reads wait while `RECOVERING`, with no rebuild loop.
- **Verified snapshot:**
  - an outside save gives `snapshot_moved`, and three give `LEDGER_CHANGING`;
  - a same-size, same-mtime save and a changed plugin file are caught;
  - a journal (also another workspace's, through the index) gives `orphan_journal` with its owner;
  - two workspaces sharing an include under two spellings exclude each other;
  - a newly matched glob file sorting before a held lock causes a release and restart, never a deadlock;
  - an evicted placeholder gives `snapshot_moved {evicted}`;
  - lock files are never deleted.
- **Conditional publish:** a stale build is dropped; out-of-order rebuilds publish the newer; ids are monotonic across a restart and a cache purge; an unchanged-content stat change re-stamps.
- **GC** of `build-*` files.
- **ADR-0015's 38,800-reads test** with two engines building.

**[C] M1b, with the write contract and BL-0091:**
- **Legacy-lock detection** (single-instance ADR): a held legacy lock refuses the save and the copy at step 0 without waiting, and writes nothing; a free one is held and released; the lock file is never created.
- **The commit gate:**
  - a cancel before `proceed` leaves the disk byte-identical (fault injection at every step);
  - after `proceed`, "Finishing…" and the save lands;
  - a property-based race of cancel against the gate;
  - `SIGTERM` is ignored past the commit point.
- **The gate's checks** each refuse on their own (disk, mirror, journal index, closed record, `.mismatch`).
- **Re-plan:** the end-and-requeue proceeds on the same intent, ends `PLAN_OUTDATED` on a moved entry, and treats a save resolved by recovery, and another workspace's save over a shared file, as ours.
- **`create`** in COLD, with the adoption and migration baselines and its closed record.
- **The closed record and the journal index:** close is one rename; saved iff a record exists; R4 re-armed after recovery and a restart; the in-memory gate re-read clears after an orphaned R4 finished; index entries exist before the journal and are gone after the record.
- **`durable_sync`:** the macOS adapter issues `F_FULLFSYNC` (T2 test), and the fallback is logged; the Windows write-through.
- **R4:**
  - pass without locks across the load;
  - superseded by an outside save;
  - parse-only confirm: `mismatch` on a seeded overlay difference, `nondeterministic` on a seeded drifting plugin, "undecided" blocks;
  - no user code under the locks;
  - Cancel R4 refuses queued saves; Run check now;
  - `.picklecache` is never touched;
  - `engine_changed`;
  - normal OS priority;
  - an update's quiesce runs owed R4s.
- **Fault injection** (BL-0091): the engine ended and `SIGSTOP`ped at each step after `proceed`, giving the right `resolve` outcome; an editor save between gate and journal; recovery waits for locks and reads journals after them; recovery of another workspace's journal while only the sharing workspace is open (the INV-31 exception) touches only that save's files.
- **Edge cases:** a recovery that dies is retried once, then blocks; queued saves are refused on a block; a violation past the commit point is read, discarded and the engine waited for, and a stall after it ends the engine; quit after `quit_patience` offers Quit anyway, and the next open resolves.
- **Windows runner** (BL-0087): the Job Object end; the lock lag (fake port and one real smoke run); `STATUS_NO_MEMORY`; `canonical_path`; `durable_sync`; start cost.

**[M2]:**
- BL-0092: machines and messages with model-based tests;
- job-list routes;
- BL-0094;
- BL-0093: the replay tool and bundling;
- BL-0100: the app-instance lock refuses a second sidecar on the same app root.

**[M11]:** Linux and Windows adapters on real machines; the Windows sleep clock; the spare-engine default.

## 13. Where this departs from the reviews, and why

**Kept from drafts 1 and 2** (unchanged reasons, briefly):
- safety rests on locks and content, not on the lane;
- verify under the locks with content hashes;
- locks held only for the verify;
- ids at publish;
- the commit point agreed by both sides;
- liveness judged from outside;
- exact peaks from the reaped process;
- no cheap lane beyond the cap;
- poison per kind with the pool capped per (workspace, fingerprint).

That last item departs from review 2 F6. Evidence: the first TLA+ check's 17-step counterexample to across-kinds poison (run `Minus_fix_poison_per_fp`, `OneLedgerCannotTripPool` violated). The second check kept per-kind poison and passed.

**Review 3: adopted** F1–F15 and O1–O14, with these choices:
- **F7, the "confirm" outcome when both parse digests equal the record:** `nondeterministic`, not `pass` (TLA v2 M4 proposed `pass`). With a parse-only confirm, the confirm cannot see that a drifting plugin input has returned, because that would need user code under the locks. Both outcomes are non-blocking and clear the record, so neither safety nor liveness changes. The notice is shown once for that save, which is honest: the plugin's result did change between the two loads. The model delta (§11.1 item 1) aligns the model.
- **F7, the case "both parse digests equal each other but not the record":** review 3 called it impossible to reach undecided. Draft 3 restarts R4 once and then blocks as "could not decide" (INV-22), because it can arise only from a fingerprint gap, and a fail-open there would hide that gap.
- **O9 (a timer-refreshed BQL step):** solved by the existing recipe memo, which keys on `ledger_version` (ADR-0029), instead of a new result cache. No new cache, so no new INV-10 bound.
- **F14 together with TLA v2 M5:** "own" is judged per file from this installation's durable closed and `.done` records of every workspace, not from the session's recent-outcomes chain, which a recovery or a sidecar restart loses.

**TLA v2: adopted** H1–H3, H5, M1–M7, L1–L2 and the overflow. **H4: not adopted, moot**: the single-instance ADR removes the shared-ledger case it needs.

**The single-instance policy (the single-instance ADR, S0044; owner, 2026-10-27: "Drop the coexistence policy; I approve the needed changes to INV-26 and ADR-0020") removed:**
- the legacy-lock waits in verify, save, R4 and recovery;
- the `waiting_for_lock (legacy)` status and Cancel;
- ADR-0005's "at least 30 s" question (owner answer (c), now moot);
- the legacy app as a lock-respecting writer, and TLA F14's stated limit;
- review 3 F2's legacy-lock deduplication;
- review 2's root-file lock candidate for BL-0091;
- the second-instance arguments for the closed record. The record stays, for restarts and orphans.

In the models it removed the legacy and second-instance actors, `NoSilentLostUpdateLocked`, `LegacyWriterProgress`, the `_twowslegacy` configs, H4, and the draft-1 baseline's `Fixed_second` and `Fixed_legacy` (§11.8).

It replaced all of this with one non-blocking try at the save's or copy's first step (§5.4 step 0), which refuses and never waits. Simplified as a result:
- `OnlySavesLockAcrossUserCode` needs no legacy clause;
- lock waits are only for our own jobs and orphans;
- R4 needs no legacy case among its superseded triggers;
- Q30 stays as a defence only.

**Departures from the owner's numbers:**
- The runaway floor makes the effective factor larger than "about 10×" for jobs under about 1.1 s of CPU.
- Decision 2 is amended by the owner's answer 6 (a change by the owner, not a departure).
- The INV-31 exception is the owner's (answer (b)).

## 14. What changes elsewhere (in the slices that build each phase)

- **ADR-0054 d4** [A]: the pool owns the engines (fresh per heavy job, at most `max_engines`) and admission; per-user `JobQueue`s own lanes and order; 34 MB per engine; protocol v2.
- **ADR-0041** [A]: the only process-wide entry is the engine pool.
- **ADR-0039** [A]: the decision log, checkpoints and the flight recorder are part of the logging design.
- **ADR-0011** [B]: the limiter becomes the lane; the semaphore becomes the pool; the BQL entries cache is withdrawn.
- **ADR-0015** [B]: ids at publish with the high-water outside the cache; both fingerprints and plugin code in meta; re-stamp; date-change triggers in both directions.
- **ADR-0017** [B]: the registry deduplicates by `canonical_path`.
- **ADR-0019** [B, C]: the `COLD` phase and `create`; reads blocked in `RECOVERING` and `RECOVERING {owner_ws}`; forgetting a workspace is refused while its journal, a closed record or a save past its commit point exists, and removes its records, measurements and caches.
- **ADR-0008** [C]: R4 completes before the next write or is superseded (disk moved, engine changed); the gate's checks of the journal index and the closed and `.mismatch` records; the parse-only confirm and `nondeterministic` (owner answer 4); the create baseline's extra load.
- **ADR-0010** [C]: guardrail 3 publishes the `load_file` result on a mismatch; a nondeterministic plugin does not block; the parse-stage claim that the overlay replicates only `_parse_recursive`, used by the confirm.
- **ADR-0012** [B, C]:
  - F-6 restated (locks held only for the verify);
  - **`durable_sync`** for every sync (owner answer (a));
  - the **app-wide journal index** keyed by canonical path, checked by verify, gate, publish, R4 and recovery;
  - journal close as a rename into the closed record;
  - the journal names `(ws, session, job)`;
  - recovery on open **waits** for the locks and **reads journals after taking them**;
  - in-session recovery, retried once;
  - recovery of another workspace's journal;
  - Q29 restated as per-file locks on canonical paths in canonical order with the non-blocking relock;
  - Q30 as a defence only.
- **ADR-0007** [C]: an update's quiesce runs owed R4s first.
- **ADR-0005 and ADR-0020** [C]: by the single-instance ADR (S0044; amendments in its slice): legacy-lock detection only, never waited for; Copy only. This spec relies on them and does not amend them itself.
- **INV-26**: amended by the single-instance ADR (owner-approved); this spec's §5.4 step 0 is its runtime check.
- **INV-31**: **amend** with the owner-approved exception (answer (b), 2026-10-27): "a `recover` job may repair an interrupted save of a workspace that is not open, when that save covers a file shared with the open workspace; it touches only that save's files."
- **ARCHITECTURE:**
  - §2.0 (protocol v2, supervision, spawn flags);
  - §2.5 (the pool as the process entry; `JobQueue`, `Measurements`, `DecisionLog`, `FlightRecorder`; `EngineClient` per engine; the BQL cache row removed);
  - §2.x the `FileSystem` port (`canonical_path`, `durable_sync`);
  - §5 (engine process groups and Job Objects; the shell's group kill no longer reaches engines);
  - §6.1 (new codes `WRITES_BLOCKED`, `PLAN_OUTDATED`, `LEDGER_CHANGING`, `RECOVERING {owner_ws}`, `LEGACY_LOCK_HELD`, with their classes and statuses; `BUSY_HEAVY_LOAD` without `retry_after_ms`);
  - §6A (§9.3's list);
  - §7 (two statecharts plus the liveness judge);
  - §8.3 (`EngineScheduler.tla` and `Admission.tla` beside `WriteProtocol.tla`; "requests behind the per-root limiter" becomes the lane and the pool; no legacy actor or second instance);
  - §9.1, §9.2 (the commit point, the gate's checks, the closed record, the R4 gate, the legacy-lock try), §9.6 (COLD, `create`).
- **failure-modes.md** [A]: E1–E7 (E5 by §6.3, E7 by §5.4 step 4), M1 (engines in their own groups), M7 (Quit anyway), and the known-gaps list.
- **glossary.md:** heavy, large, cheap, lane, slot, head, canonical path, journal index, closed record, commit point, usual, on-time tick.
- **capabilities.md/.toml:** cancel, job status, Run anyway, Run check now.
- **state_registry** [A]: the `process` entry; `EngineBoot` removed; the flight recorder.
- **Licence lists** [A]: `portalocker` 3.2.0 in the engine's, `psutil` in the main sidecar's.
- **User docs** [M2]:
  - one Finzytrack at a time;
  - quit the old app before saving a ledger it names;
  - files a plugin reads itself;
  - plugins run twice at once;
  - Rebuild now;
  - cloud placeholders.
- **Backlog:**
  - BL-0091: §5.7's resolve, the journal index, recovery across workspaces; the root-file lock dropped;
  - BL-0082: `decode_factor`;
  - BL-0087: Windows start cost, `durable_sync`, `canonical_path`;
  - BL-0093: §9.3 R5–R6;
  - BL-0100 (S0044): the app-instance lock.

## 15. Owner questions

None open. The owner's answers of 2026-10-27:
- (a) **True flush:** use the true flush for every durable step (§5.4).
- (b) **Repair across workspaces:** an interrupted save in a shared file may be repaired while only the other workspace is open, as an exception to INV-31 (§5.7, §14).
- (c) **The save's legacy-lock wait:** now moot under the single-instance policy, which detects the legacy lock and never waits for it (§5.4 step 0).

"What is usual?" is answered in §1.1, and replayable decisions are in §9.3.
