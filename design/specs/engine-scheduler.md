# Engine scheduler: concurrency, admission, liveness and cancel

> **DRAFT 2 (2026-10-27), not yet adopted.** Draft 1 (2026-10-26, commit d68ce933) was checked by an independent design review (review:m1a/scheduler-design/review-2, F1–F15, O1–O18) and by a TLA+ model (review:m1a/scheduler-design/tla, F1–F15 and overflow; the "Fixed" model passed every property up to 179.6M distinct states). This draft folds in both reviews and the owner's answers of 2026-10-26 (review:m1a/scheduler-design/owner-answers-review2.md). A later slice commits it with the ADR amendments of §14. Until then the accepted ADRs win where they differ.

## Orientation
- **What this is:** how the main sidecar runs ledger-engine work: which jobs exist, which may run together and in which order, how many engine processes run, how much memory they may use, how a slow job is told apart from a sick engine, what Cancel does, what the user sees, and how a rare failure can be replayed from the log. It answers the area review's F1 and F2 (2026-10-26) and applies the owner's five decisions and their sequencing of 2026-10-26 (`design/inbox/20261026-engine-concurrency-and-cancel.md`), after the first design review (review:m1a/design-review-concurrency H1–H6, M1–M7, L1–L5), and then review 2, the TLA+ check and the owner's six answers to review 2's questions.
- **What exists:** none of this yet. Built today: one `EngineClient` per user container, one request in flight, and a caller's time limit that counts as a crash (S0035, S0040, S0042). Each part below carries the phase that builds it:
  - **[A]**: M1a, the scheduler slice, before the read core;
  - **[B]**: M1a, with the mirror build;
  - **[C]**: M1b, with the write contract and BL-0091;
  - **[M2]**: with BL-0092, BL-0093 and BL-0094;
  - **[M11]**: the Linux and Windows sessions (ADR-0061);
  - **[Hosted]**: deferred (ADR-0041).
- **Entry points (planned):**
  - `app.platform.engine_pool`: the process-wide pool, memory budget, pressure reading and stop history, with its pure table `engine_pool.table`;
  - `app.platform.engine_process`: one engine's statechart, replacing `engine_supervision`;
  - `app.platform.adapters.engine_client`: the shell for one engine process;
  - `app.platform.job_queue`: per-user lanes and queues; a pure core around `next_dispatch`;
  - `app.platform.liveness`: the pure tick judge;
  - `app.platform.ports.memory_pressure`, `app.platform.ports.process_probe` and `app.platform.ports.clock`;
  - `app.platform.observability.decisions`: the decision log and the flight recorder;
  - on the engine side, `ledger_engine.protocol` (protocol v2) and `ledger_engine.ledger.snapshot` (verified reads, conditional publish, the closed record).
  - Model: `formal/tla/EngineScheduler.tla` (§11).
- **Decisions it applies:**
  - ADR-0054: two processes; decision 4 is amended here.
  - ADR-0041: one process-wide entry.
  - ADR-0008: the write contract and its R4 amendment.
  - ADR-0010: the overlay load and its guardrail 3.
  - ADR-0011: no resident ledger, and write serialization.
  - ADR-0012: the journal and recovery, Q29, Q30, F-1 to F-8.
  - ADR-0013: atomic swap.
  - ADR-0015: atomic publish and WARMING.
  - ADR-0005: the legacy lock.
  - ADR-0061: platforms.
  - The owner's decision 2 is amended by the owner's answer 6 (§7.3).
- **Invariants:** INV-01, INV-10, INV-11, INV-22, INV-26, INV-27, INV-30, INV-31.
- **Traps:**
  - A heartbeat proves nothing. C code that holds the GIL stops it, and a sleeping plugin keeps it going. Liveness is judged from outside the engine (§8).
  - The lane does not keep the mirror correct. The per-file ledger locks and the content fingerprint do (§5), and the model checks this with the lane switched off.
  - An engine that ran user code is never reused (H1).
  - A `SIGKILL` from us and one from the OS look the same unless we record ours first (§6.4).
  - `Popen.poll()` reports a killed child as exit 0 once we have reaped it ourselves, so the pool is the only reaper (§6.1).
  - "Running" ends at the final reply for the lane, and at confirmed death of the whole process group for the slot. It never ends at "kill sent" (§4.6).
  - "Usual" has one definition (§1.1). Nothing else may invent another.
- **Main tests (planned):** per phase in §12; the TLA+ module and its configs (§11, §12 [A]); the replay test of the decision log (§9.3).

## In two minutes

Today Finzytrack sends the ledger engine one request at a time, and the only way to stop a request is a time limit that counts as a crash. That does not work for the jobs that are coming: loading a 54,000-entry ledger takes about 2 seconds of full CPU, and several such jobs will want to run at once.

**What changes:**
- **Up to 2 engine processes** run ledger jobs at the same time (a setting). Each job that runs your ledger's code gets a **fresh engine process**, which ends when the job ends. Starting one costs about 0.1 s and 34 MB on your Mac. This also stops one ledger's plugin from leaking into another ledger's job.
- **A queue in the main sidecar** decides what runs next. Jobs you are waiting for go first; background checks go last but are never starved. A large job that is waiting for memory is not overtaken for ever by smaller ones. Jobs that would load the same ledger for the same question share one load.
- **Two jobs that change the same workspace never run at the same time.** Reads run beside the rebuilding of your views. A read that overlaps a save waits for the save to finish. A read is only used if the files did not change while it was reading; otherwise it is quietly done again.
- **Memory:** ledger work may use up to a quarter of your RAM (a setting). Finzytrack learns what each kind of job **usually** needs for each ledger from the jobs themselves, so nothing is measured at startup and startup is not slower. "Usual" is defined once, in §1.1. A job it has never measured runs alone. While your computer reports memory pressure at "warning", one job runs at a time. At "critical", no new job starts unless you press **Run anyway** for a job you asked for.
- **Cancel** stops a read at once. A save can be cancelled until it starts writing files. After that, Cancel shows "Finishing your change…" and the save completes.
- **A slow job is not a crash.** The sidecar watches the engine from outside: is it using CPU, and is it sending anything. Only an engine that does neither, while the computer is awake and has CPU to spare, counts as hung. A long job that keeps working is never cut off by a guessed time limit.
  - Background jobs that use about 10 times their usual CPU time for this ledger, or that make no progress for much longer than usual, are stopped automatically and logged. The first run of a job can only be stopped by hand.
- **The check after a save (R4)** runs Beancount's own loader again and compares the result with the save's. Only a real difference between the two loaders pauses saving. A plugin that gives different answers each time (dates, network) shows a notice and does not block you.
- **Crash loops are bounded.** One bad ledger can add at most one stop to the engine's crash count until it changes, so it cannot switch off the engine for your other workspaces. A ledger that makes the engine fail twice in a row is not retried until it changes or you press Try again.
- **Rare failures can be replayed.** Every scheduling decision and what it was based on is written to the log without any ledger content, so a test can rebuild the decisions from a user's log.

**What it costs:**
- about 0.1 s per job for the fresh process;
- a little more memory while two jobs run;
- about 6 KB of log per job;
- a fair amount of design, most of which is built later (each part is marked).

**What stays imperfect:**
- A program outside Finzytrack that saves your ledger without taking our locks can only be detected, not excluded (as today, ADR-0013).
- Two Finzytrack installations writing one ledger are not supported in v0.4.0 (owner answer 5).
- A process stuck on a dead network disk cannot be ended by anyone, and it keeps its slot and its locks until it ends.
- Files that a plugin reads by itself are invisible to Finzytrack (§5.8).

## 1. Terms

- **Job:** one unit of engine work for one workspace (or for none, in the case of cheap jobs). A job has a kind (§3), a priority, its requesters and a state: `queued → admitted → running → ended(outcome)`.
- **Heavy job:** runs user code (plugins, `insert_pythonpath`) or loads the whole ledger. A heavy job needs a fresh engine (§6.1).
- **Large job:** a job whose usual peak (§1.1), or whose estimate when it is unmeasured, exceeds the interpreter baseline by more than `cheap_margin` (default 32 MB). Every heavy job is large. The parser-only `plan` is large but not heavy: spike S4 measured 306 MB for one 20 MB file. Large jobs go through memory admission (§7.3).
- **Cheap job:** neither heavy nor large: static facts, or parsing a small text given in the request.
- **Lane:** per workspace. **Exclusive** jobs (`rebuild`, `commit`, `create`, `recover`) run one at a time per workspace. **Shared** jobs (reads) may run beside each other.
- **Engine:** one engine process, the leader of its own process group (§6.3). **Slot:** permission to have one engine. `max_engines` (default 2) bounds all engines, cheap ones included.
- **stat fingerprint:** ADR-0015's extended fingerprint:
  - the root path;
  - `(path, mtime, size)` of every glob-expanded include;
  - each glob with a hash of its match list;
  - every expected but absent path;
  - a listing hash for each documents root;
  - and, new here, `(path, mtime, size)` of every plugin source file recorded by the last load.
- **content fingerprint:** the same set with each file's SHA-256 in place of `(mtime, size)`, **plus the code inputs.** The code inputs are `(realpath, sha256)` of every Python source file that the load imported from outside the engine's own bundled site-packages. In practice these are the plugin modules found through the ledger's `pythonpath` and `insert_pythonpath`, and their submodules. The engine records them from `sys.modules` after the load (review 2 F3; Beancount 3.2.0's loader prepends `options_map["pythonpath"]` and calls `import_module`). The cost is about 10 ms at 54k (spike S4, `fp0`) plus hashing the plugin files.
  - The mirror's meta stores both fingerprints: the stat one for the cheap focus check, the content one for every correctness check.
  - `ledger_version` = (generation id, hash of the content fingerprint).
- **Commit point:** the moment in a `commit` or `create` after which the sidecar never ends the engine by its own choice, except for a confirmed hang (§5.4).
- **Closed record:** the durable file that a commit's journal becomes at the journal close (§5.4). It serves three purposes: it proves the save landed, it holds the R4 that is still owed, and it gates the next write. It is new here (TLA F1, F2, F4).
- **On-time tick:** a watcher tick that ran on schedule, with the machine awake (§8.2).
- **Healthy run:** a job whose outcome is `answered` and, for a read, verified (§5.2). It was not cancelled, preempted, stopped or killed.
- **Settings:** all of them are configuration in the INV-10 style, sized per deployment, never development targets, each with an allowed range (§8.7).

### 1.1 What "usual" means

The owner asked: "What is 'usual' and how is that measured and known?" This is the only definition. Every rule and every message that says "usual", "much longer than usual" or "far over its usual CPU time" means exactly this.

**Usual is what healthy runs of the same kind of job, for the same workspace, on this computer, have recently needed.** It is kept per (workspace, job kind), for example "R4 for workspace *Household*" or "a query for workspace *Business*". It consists of four numbers:

| Number | Measured how | Used for |
|---|---|---|
| **usual CPU time** | the CPU seconds of the engine's whole process group, read from the finished process by the pool when it reaps it (§6.1). Exact; not a sample. | the runaway stop for background jobs (§8.5); the "taking much longer than usual" flag for jobs you wait for (§9.1) |
| **usual peak memory** | the peak of the finished process: macOS lifetime physical footprint, Linux `ru_maxrss`, Windows peak commit of the job object (§6.1). Includes the interpreter. | memory admission (§7.3) |
| **usual reply size** | bytes of the final reply | the sidecar's decoding share of the memory estimate (§7.2) |
| **usual longest quiet stretch** | the longest run of on-time ticks in a healthy run with no CPU and no bytes, and separately with bytes but no progress | the minimum tick counts for "hung" and "stalled" (§8.3, §8.4) |

**How it is learned:**
- **Only from healthy runs.** A cancelled, stopped or killed run teaches nothing about time. The one exception is that a memory death raises the usual peak, so the next estimate is never lower than what killed it.
- **A decaying high-water mark, counted per run, not per day.** After each healthy run, `usual ← max(this run, 0.9 × usual)`. Growth is followed at once. One unusually heavy run fades over about 20 runs, and while it fades it makes the rules more lenient, never stricter.
- **Scaled by ledger size.** When the ledger's files have grown since the usual value was set, the value is scaled up in proportion (`× size_now / size_then`, never scaled down). This covers a ledger that grows over months without anything being measured at startup (the owner's question of 2026-10-26).
- **R4 borrows its usual values** from the step-3 load of the save that it checks. That load ran the same bytes and the same plugins, so R4 is never unmeasured (review 2 F2).

**When there is no usual (the job is "unmeasured"):**
- the first run of that kind for that workspace;
- after an app update (another engine build);
- after the ledger's plugin code changed (another plugin-set hash);
- when the measurements file is missing, unreadable or of another schema version (review 2 O17).

An unmeasured job runs alone, is never stopped automatically, and is never flagged as "much longer than usual" (owner decision 4: a first run is only cancelled by hand).

**Honest limits:**
- It is **per computer.** The record lives in this machine's app-local data, so a new laptop starts unmeasured.
- Size scaling assumes cost grows roughly in proportion to bytes. That holds for Beancount's load (spike S4), but a plugin's cost may grow faster.
- A plugin that reads its own files (a price CSV, the network) changes its cost without Finzytrack seeing why. The next healthy run corrects the usual value.
- About 10× is coarse on purpose. It catches a loop, not a 3× slowdown.
- CPU time is not wall time. A job on a busy or sleeping machine uses no extra CPU time, so it is not judged slow. A job that waits on the network uses no CPU, so the runaway rule cannot catch it; the stall rule can (§8.4).

`Normal` (written in code font) in this spec means only the OS's pressure level (§7.4), never "usual"; "normal OS priority" means an unchanged `nice` value.

## 2. Components and where they live

| Part | Lives in | Holds | Bound |
|---|---|---|---|
| `EnginePool` [A] | process-wide: the **one** `process` entry of `platform/state_registry.py`, replacing the heavy-load semaphore (ADR-0041 and ADR-0054 decision 4, as amended in §14) | the engine processes, the memory budget, the pressure reading, the pool's stop history, the pool table's state | `max_engines` engines; at most `max_crashes` stop times; one pressure value |
| `JobQueue` [A]; lanes [B] | `UserContainer` (INV-11: limiters and job queues hang off it) | per workspace: queued jobs, the running set, the lane state, the R4 gate's in-memory mirror of the closed records, poison counters, the workspace's pool-stop mark (§8.7), recent outcomes | queue bounds of §4.5; outcomes: the last 16 per workspace |
| `Measurements` [A] | `UserContainer`; persisted in `app_local_data/engine/measurements.json` (never in the workspace folder; INV-27, INV-30; under the user's root in hosted mode, review 2 O14) | the "usual" record of §1.1 per (workspace, kind), with the engine build and plugin-set hash it was measured with | one record per (registered workspace, kind); dropped when the workspace is forgotten |
| `DecisionLog` and `FlightRecorder` [A] | `UserContainer` | §9.3 | flight recorder: the last 4,096 events or 1 MiB |
| `MemoryPressurePort` [A] | platform port (EP-07), main sidecar | nothing (it reads the OS) | — |
| `ProcessProbe` [A] | platform port; adapter on `psutil` (BSD-3-Clause, GPLv3-compatible, pinned per EP-24, on the main sidecar's licence list) | nothing (it reads a process group's CPU time, RSS and status, and the system's idle CPU) | — |
| `Clock` port [A] | platform port | `monotonic()` and `monotonic_with_sleep()` (§8.2), so tests can inject a sleep (review 2 O3) | — |

The pool pulls work. When a slot and memory are free, it asks each user's `JobQueue` for its best dispatchable job (§4.7) and takes the best across users, round-robin among equal priorities. Fairness across users matters only in hosting [Hosted]; the seam is there now. The pool never sees ledger semantics, and the queue never sees processes. `EngineClient` stops being a user-container service: each running engine has its `EngineClient`, owned by the pool for that engine's life.

**Superseded:**
- ADR-0011's per-workspace `anyio.CapacityLimiter(1)` becomes the workspace's exclusive lane [B]. Its FIFO guarantee is kept (§4.2).
- ARCHITECTURE §2.5's "optional BQL entries cache" row is dropped (area review F8).

**Lock and fingerprint code moves into the engine:** `portalocker` 3.2.0 joins the engine's environment and licence list, because the engine now takes the locks (review 2 O7).

## 3. Job kinds

**Reconciliation** in this spec means R4, which is ADR-0010's guardrail 3. Statement reconciliation (ADR-0028) reads the mirror and is not an engine job.

### 3.1 Scheduling attributes

| Kind | Phase | Heavy / large | Lane | Priority (§4.2) | Preemptible | Cancellable: when, by whom | Merge (§4.4) | Queue bound per workspace |
|---|---|---|---|---|---|---|---|---|
| `rebuild`: WARMING or stale mirror; COLD first build; after an outside edit; date change in either direction (review 2 O12); a save's mirror when its own build did not publish | B | heavy | exclusive | P1 if the workspace is viewed; not run otherwise (ADR-0015) | no | any time, by the system only: workspace closed or switched away, quit | newest only: 1 queued + 1 running | 1 (never "busy": merged) |
| `commit`: confirm (locks, gate, step-3 re-run, journal, swaps, read-back, journal close into the closed record, mirror build, publish) | C | heavy | exclusive | P1 for a user save, an import or an AI confirm; P3 for background writers (price fetch) | no | **before the commit point** only: the user (Cancel), quit; never after (owner decision 3) | never | 4 queued |
| `create`: workspace creation, demo seeding, adoption copy, a lifecycle migration that writes ledger files (review 2 F4) | C | heavy | exclusive; the only writer allowed in `COLD` (§4.1 rule 5) | P1 | no | before its commit point, as `commit` | never | 1 |
| `r4`: guardrail 3 after a save | C | heavy | shared; gates the next write through the closed record (§5.5) | P3; a waiting save lends it its class while queued (§4.2) | only while no save waits on it, at most `preempt_max` times | any time: the user (Cancel on "Checking your last change…", M2), quit, the system (superseded). A cancelled R4 leaves saving paused, with **Run check now** (review 2 F2) | one per save | 1 |
| `plan`: contract step 1 (read and parser-parse the edited files; no plugins) | C | large, not heavy: may use a clean engine and leaves it clean | shared; ordered after earlier exclusive jobs (§4.1 rule 7) | as its preview | no | as its preview | runs as the first part of its preview, in the same engine | as preview |
| `preview`: contract steps 2–3 for a plan (semantic diff, overlay load of the candidate) | C (first use) | heavy | shared; ordered after earlier exclusive jobs (§4.1 rule 7) | P1 | no (P1) | any time: the user, the requester leaving, a newer preview of the same edit session, quit | newest only per edit session | 4 |
| `query`: user or AI BQL; recipe `bql` steps | A (the op arrives with BQL) | heavy | shared | P1 if a requester waits on screen; P2 otherwise | P2 only, by P0/P1 heavy jobs | any time: each requester (the job ends when none remains), workspace closed, quit | queued `query` jobs of the same (workspace, `ledger_version`, `LoadPolicy`) merge into one batch of at most 16 statements | 4 batches |
| `snapshot`: other full loads (the adoption "loads the same" check, export-style reads, vector runs) | A | heavy | shared | P1 or P3, as the caller says | if P3 | any time: the requester, quit | as `query`, when the result is a pure function of the load | 4 |
| `cheap`: static facts; parse a small text from the request | A | neither | none | P1; never preempts (review 2 O5) | no | any time: the requester | identical requests merge | 16 |
| `recover`: journal recovery on open, or in session after an engine died past its commit point | on open: C (M1b writer); in session: C (BL-0091) | neither (file copies and hashes under the locks) | exclusive | P0 | no | never, except by quit, which leaves the journal for the next open (TLA F15) | one per workspace | 1 |

**Global bound:** at most 64 queued jobs across all workspaces of a user (a setting).

Facts that the read core needs often do not become `cheap` jobs. Static ones (account grammar pattern, tolerance defaults, Beancount version) come in the handshake reply and are cached per engine build. Ledger-derived ones (Ledger Profile, options, plugin list, plugin file paths) are written into the mirror's meta by the build (EP-30: remove the waiting instead of scheduling around it).

### 3.2 Failure outcomes per kind

- **"Stop"** means the fault is counted in the pool's stop history, subject to the one-per-(workspace, fingerprint) rule of §8.7.
- **"Poison"** means it is counted in the workspace's poison counter for (kind, content fingerprint).
- Every row is logged (§9.2) and is a logged decision (§9.3).
- §4.9 says what happens to queued jobs on each failure.

| Kind | Cancel | Engine crash | OS memory kill | Out-of-memory error (`MemoryError`; the common case on Windows) | Hang (§8.3) | Stalled or runaway (§8.4, §8.5; background only) |
|---|---|---|---|---|---|---|
| `rebuild` | ended; the unpublished build file is left for GC (§5.3); no stop | stop + poison; retried once; on the second fault the workspace stays WARMING with the failure message | poison; pool to one engine (§7.5); retried once, alone | as a memory kill | ended; stop + poison; retried once | n/a: rebuilds run only at P1 |
| `commit` or `create` before the commit point | ended; the OS releases the locks; the disk is byte-identical and there is no journal | stop + poison; disk unchanged; not retried (the user decides) | poison; one engine; not retried | as a memory kill | ended; stop + poison; not retried | P3 writers only: stopped (our own end) and logged; "not saved, unchanged" |
| `commit` or `create` after the commit point | not ended: "Finishing your change…" | stop; the outcome is resolved from disk (§5.7) | as a crash; then one engine | the engine replies `engine.out_of_memory` and exits; the outcome is resolved (§5.7) | ended only for a confirmed hang; then as a crash. A protocol violation is not ended (§5.4 step 5) | never stopped |
| `r4` | ended; the closed record stays, so saving stays paused, with **Run check now** | stop + poison; retried once; then saving stays paused, failing closed (INV-22), with **Try again**; queued saves are refused at once (TLA F7) | poison; one engine; retried once alone; then as a crash | as a memory kill | ended; as a crash | stopped (our own end); saving stays paused until **Run check now** runs it at P1 with no automatic stop |
| `plan`, `preview` | ended | stop + poison; reported; Try again | poison; one engine; reported | as a memory kill | ended; stop + poison; reported | n/a (P1) |
| `query`, `snapshot` | ended when no requester remains | stop + poison; retried once (reads are idempotent); then reported | poison; one engine; retried once alone; then reported | as a memory kill | ended; stop + poison; retried once | P3 `snapshot` only: stopped and reported |
| `cheap` | ended | stop; retried once | as a crash | as a crash | ended; stop | n/a |
| `recover` | n/a | stop; retried once (recovery is idempotent: it classifies again from disk); then the workspace stays `RECOVERING` with writes blocked and "Your last change may or may not be saved. Reopen the workspace." (TLA F8) | as a crash | as a crash | ended; as a crash | n/a |

"Retried once" means once per (job, content fingerprint). It happens only while the pool is not `Unavailable` and the workspace is not poisoned for that key, and the retry waits out the pool's backoff (§8.7).

## 4. The scheduler

### 4.1 Lanes [B]

Per workspace:
1. At most one exclusive job is running (property `OneExclusivePerWorkspace`, within one sidecar session, TLA F13).
2. A shared job is not dispatched while a `commit`, `create` or `recover` of its workspace is running, or is queued ahead of it. Such a read would almost always end `snapshot_moved`.
   - A shared job **may** run beside a `rebuild`, which changes no ledger file. A query asked during a rebuild runs at once on the second engine (review 2 F8).
   - A running shared job is not stopped when an exclusive job starts. Its verification decides (§5.2).
3. An exclusive job does not wait for running shared jobs, with one exception: a write waits until the R4 of the previous save has cleared its closed record (§5.5).
4. A `preview` is dispatched only when the published mirror's content fingerprint is the one it plans on, because its baseline errors come from that mirror (H2). It waits for a running or queued `rebuild`.
5. While the workspace is `WARMING`, or `COLD` (no published generation yet), a `commit` is refused at once with `WORKSPACE_NOT_READY {phase}` and is never queued.
   - A `create` is the only writer allowed in `COLD`. Its gate compares only the disk (empty, or the pre-image it planned on).
   - Its baseline error set is empty for a new ledger. For adoption and migrations it is the load of the pre-image, which costs one extra load and is recorded in ADR-0008's amendment (§14).
   - It publishes the first generation (review 2 F4; property `NoCommitWithoutBaseline`).
6. While the workspace is `RECOVERING`, or has an unresolved engine death past a commit point, only `recover` is dispatched for it.
   - Shared reads wait too, because they would only end `orphan_journal` (review 2 O9).
   - No `rebuild` is queued, and the focus check queues none (TLA F9: an endless rebuild loop that each ended `orphan_journal`).
   - The workspace shows its block (property `NoWriteAfterUnresolvedDeath`).
7. A `plan` or `preview` is ordered after every exclusive job of its workspace that was enqueued before it (FIFO across the two), so it plans on the state that earlier saves produce (review 2 F9).

Lane exclusivity is a rule about order and resources. It is **not** what keeps the mirror or the reads correct. Those hold even if two exclusive jobs ran at once, for example in a second process, because they rest on the ledger locks and the content fingerprint (§5). The TLA+ config `Fixed_lanesoff` checks exactly this: `PublishOnlyCurrent`, `GenerationsMonotonic`, `PublishedIsCommittedState` and `VerifiedReadsAreSnapshots` hold with the lane removed.

Before [B] lands, the scheduler refuses every exclusive kind. None exists in [A]: no operation of [A] writes a generation or a ledger file.

### 4.2 Priority, inheritance and aging [A]; inheritance through lanes [B]; through the R4 gate [C]

Classes, highest first:
- **P0:** recovery.
- **P1:** something the user is waiting for on screen in the viewed workspace: a save, a plan or preview, a query, the viewed workspace's rebuild, cheap jobs.
- **P2:** interactive work nobody is watching right now: a query whose dashboard is hidden, an AI tool call.
- **P3:** background work: R4, background writers, prefetch.

Rules:
- **Order:** by effective class, then by enqueue order (FIFO). Exclusive jobs of one workspace keep FIFO among themselves whatever their class, so the saves of a workspace commit in the order they were confirmed (ADR-0011's guarantee).
- **Inheritance:** a queued job that another job waits for (lane rules 3, 4 or 7, the R4 gate, the memory reservation of §4.7) is ordered at the higher of the two classes, transitively. The effective class is recomputed at each scheduler event, as a pure function of the queue.
  - Inheritance changes the order of **queued** jobs only. A running job's OS priority is fixed when it starts (§10), because an unprivileged parent cannot raise a child it has lowered (review 2 F14; spike s5: `setpriority` from 10 to 0 was refused with EACCES).
- **Aging by count, not by time:** each time a queued job is passed over by a job that was enqueued after it, its `passed` counter increases. At `aging_passes` (default 8) the job moves up one class and the counter resets. No clock is involved. That this gives starvation freedom is checked by the pure-table tests, not by the model (§11.5).

### 4.3 Preemption [A]

A queued P0 or P1 **heavy** job that cannot be dispatched (no slot, or memory admission refuses it) may preempt **one** running job that is preemptible. Cheap jobs never preempt (review 2 O5).

A running job is preemptible when it is read-only and preempted fewer than `preempt_max` (default 2) times. That covers `query` at P2, `snapshot` at P3, and `r4` while no save waits on it.

Preemption is our own end of the engine, with reason `preempt`; it is not a stop. The job goes back to the front of its class with its requesters and its merge key. A job preempted `preempt_max` times is no longer preemptible. Under critical memory pressure (§7.4) the pool preempts preemptible P3 jobs even when nothing waits.

### 4.4 Merging and newest-only [A] for queries, [B] for rebuilds

- Merging happens **only among queued jobs.** A running engine's load is never shared with a later arrival, because handing more questions to an engine that holds a parsed ledger would make that ledger resident (INV-10, ADR-0011). A batch is fixed at dispatch.
- A merged job has a set of requesters. Cancelling removes one requester, and the job is ended only when the set is empty.
- Each statement of a batch gets its own result or its own refusal (`engine.result_too_large` per statement, area review F6). One statement's refusal does not fail the others.
- **Rebuild, newest only:** there is one queued rebuild per workspace.
  - A trigger while one is queued is absorbed.
  - A trigger while one runs leaves it running (its verification decides) and keeps one queued rerun.
  - When the stat fingerprint moved but the content fingerprint equals the mirror's (a sync client re-downloaded a file unchanged), the mirror's stat fingerprint is re-stamped instead of rebuilding (review 2 O11).
- **Preview, newest only:** a new preview from the same edit session (the edit buffer's or the import session's id) replaces the queued one and cancels a running one.

### 4.5 Queue bounds and "busy" [A]

The bounds are those of §3.1 and the global 64. A request over a bound is refused at once with `BUSY_HEAVY_LOAD` (ARCHITECTURE §6.1, class `Unavailable`, retryable), and nothing is queued. Merged kinds (`rebuild`, `r4`, `recover`) are never busy.

The frontend never retries `BUSY_HEAVY_LOAD` or `LEDGER_CHANGING` automatically; the user's Try again does (review 2 O16). A busy refusal leaves the table state unchanged, so it is logged as a count (§9.3), with one `engine.queue.busy` line per workspace and kind until the queue drains.

### 4.6 What "running" means [A]

There are two different ends:
- **The job ends**, and its **lane** is released, when the engine's final reply was received, or when the engine is confirmed dead.
- **The slot and the memory reservation** are released only when the engine **and its process group** are confirmed dead (TLA F3; property `EnginesBounded`; review 2 F10):
  - on POSIX, the engine is reaped and `killpg(pgid, 0)` fails with `ESRCH`;
  - on Windows, the Job Object's active process count is 0.
  - An engine that has sent its final reply but does not exit is a hang (§8.3): it is ended, and it counts as a stop of kind `hang`.

A kill sent is never an end (property `RunningUntilAnsweredOrDead`; seeded bugs `lane_on_kill_sent` and `lane_on_requester_left`).

The HTTP handler that waits for a job is not the job. A handler cancelled by a client disconnect is a `RequesterLeft` event. It cancels a read with no requester left, and never a save (H2; ADR-0011's anyio limiter is released on handler cancel).

A save's outcome is kept in the workspace's recent outcomes. Its durable proof is the closed record (§5.4), so a reloaded window can ask whether its save landed, even after a sidecar crash (BL-0092; TLA overflow).

**Cost:** an engine that cannot be ended keeps its slot, its lane and **its locks** until it ends. An example is a process in uninterruptible sleep on a dead network disk, which ignores `SIGKILL` until its I/O returns. Nothing can prove such a process will not still write, so nothing may take its place, and every verify and save of that workspace waits (review 2 O10). The user sees §9.1's "cannot be stopped" message.

A descendant that left the group (one that called `setsid`) keeps any lock it inherited. Spike s3 showed a `flock` held for as long as the forked child of a killed holder lived. Lock-taking steps wait for such a lock like any other.

### 4.7 Dispatch, as a pure function, with head-of-line reservation [A]

`next_dispatch(pool_view, queues) -> Dispatch | Preempt | Wait(reason)`.
- `pool_view` holds: free slots, budget headroom, pressure level, the `single_engine` flag, `Unavailable` or `Incompatible`, and any pending **Run anyway**.
- Each queue offers its candidates in order.

A candidate is **dispatchable** when all of these hold:
- its lane rules allow it (§4.1);
- the pool is not `Unavailable` (for heavy and large jobs) and not `Incompatible` (for all jobs);
- its workspace is not poisoned for its key;
- the backoff after the last stop has passed (§8.7);
- admission allows it (§7.3).

**Reservation, so that nothing overtakes a memory wait for ever** (review 2 F1):
- The **head** is the queued large job with the highest effective class (then FIFO) across all queues whose only obstacle is admission (pressure, budget, or "unmeasured runs alone").
- While a head exists, no other **large** job is dispatched, whatever its class, except one that preempts (§4.3) on the head's behalf.
- Cheap jobs may still be dispatched into a free slot when admission allows them (§7.3), because one cheap job (34 MB, milliseconds) never delays the head.
- When the running large jobs end, the head is admitted: it is alone, and a job alone is admitted at Normal or Warn (§7.3).
- The head is chosen afresh at each event, so a later P0 or P1 job can become head ahead of a waiting P3 job. Aging (§4.2) makes sure the P3 job is head eventually.
- Spike s6 shows the need. An 800 MB (or unmeasured) rebuild queued behind two refreshing 300 MB queries under a 1,000 MB budget was still queued after 2,000 steps without the reservation. With the reservation it ran at step 22.

`Wait(reason)` carries the reason the user is shown:
- `queued` for jobs behind a head or behind other work;
- `waiting_for_memory` or `waiting_to_run_alone` for the head;
- `backoff`.

Every scheduler event re-runs `next_dispatch`: enqueue, end, cancel, requester left, pressure change, tick, Run anyway. It is a pure table in the EP-17 sense, with an exhaustive test and random walks, and its inputs and outputs are logged (§9.3). What happens to queued jobs when the pool is `Unavailable` or `Incompatible` is in §4.9, not in a `Wait`.

### 4.8 Quit and quiesce [A] for reads; [C] for saves (review 2 F13; TLA F15)

`POST /api/app/quiesce` (failure-modes M7):
1. Stop dispatching.
2. End the queued jobs (§4.9). A queued `recover` is left for the next open, where its journal is recovered (ADR-0012).
3. End the running reads, plans, previews, rebuilds and R4s. An R4's closed record stays, so the next open runs it.
4. End the saves that are before their commit point. Each reports "not saved, unchanged"; the window's unsaved guard asked first (W7).
5. Wait for the saves past their commit point and for a running `recover`, showing "Finishing your last change…".
   - That wait has no time limit. After `quit_patience` on-time ticks (default 10) with no change of the job's phase, the shell offers **Quit anyway**, with the text "Finzytrack will finish or undo this change the next time it opens. Your ledger stays safe." This is true because of the journal (ADR-0012).
   - An OS logout or shutdown that cannot wait is treated the same as Quit anyway (property `QuiesceTerminates`, unless an engine cannot be ended).

### 4.9 Queued and running jobs on each event [A]; save rows [C] (review 2 F12; TLA F7)

| Event | Queued jobs | Running jobs |
|---|---|---|
| pool `Unavailable` | P1 and P2 heavy jobs end at once with E1's message and Try again. P3 jobs stay queued. `rebuild` stays queued (the workspace shows WARMING and E1's message). A closed record stays (saving paused). `recover` stays queued and is admitted (it is not heavy). | not affected; each ends by itself |
| pool `Incompatible` | all end with E6's message; journals and closed records stay on disk for the next start | none can exist |
| workspace poisoned for a key | its jobs of that key end "poisoned" | — |
| writes blocked (`RECOVERING` with foreign bytes, a recovery that failed twice, an R4 that cannot run, an R4 mismatch) | queued saves of the workspace are **refused at once** with `WRITES_BLOCKED` and the block's message, never left waiting (TLA F7; property `QueuedWriteLeavesQueue`) | — |
| workspace closed or switched away | its jobs end `cancelled`, except `recover` and saves past the commit point. A closed record stays on disk. | reads are ended (no requester left). Saves before the commit point continue to their end unless the user cancels: closing a window is not a cancel (W7's guard asked first). |
| sidecar exit or crash | lost; the HTTP requests fail. Nothing on disk depends on them: journals, closed records and the generation high-water are on disk. | engines end with their group (§6.3); a save past its commit point is resolved at the next open (§5.7) |
| quiesce | §4.8 | §4.8 |
| `max_engines` or `memory_share` changed | re-evaluated at the next event | never ended; the pool drains to the new bound |
| pressure becomes `Critical` | stay queued, visibly `waiting_for_memory` | preemptible P3 jobs are preempted (§4.3) |

## 5. Consistency [B] unless marked

### 5.1 The two fingerprints

The **stat fingerprint** decides freshness cheaply: the focus-time version check (ARCHITECTURE §9.1) and opening. The **content fingerprint** decides correctness: every verification, every publish condition and the save's gate (ADR-0008 step 4) compare content fingerprints. A stat fingerprint can miss a same-size save within one mtime tick; a content fingerprint cannot. Because plugin code is in both fingerprints (§1), a fixed plugin makes the mirror stale and resets the poison counters (review 2 F3).

### 5.2 Verified snapshot reads (H3; ADR-0012 F-6)

Every job that loads the ledger from disk returns a result only if the bytes it parsed were, at one instant, the whole ledger set, with no write in progress.

1. **Read.** The engine reads each file once, as bytes, records `(realpath, size, sha256)`, and parses those bytes (the overlay loader already parses bytes; spike S4). It records each glob's match list, each absent include, the listing hash of each documents root taken at the start, and, after the load, the imported plugin files. The load runs **without locks**, so no other writer waits for seconds.
2. **Verify.** At the end, the engine does the following:
   - It takes the locks in canonical order: every recorded legacy lock first (ADR-0005), then the **ledger-set locks**. There is one lock file per `realpath` of every file of the set, at `app_local_data/locks/<sha256(realpath)>`, taken sorted by realpath (review 2 F11; ADR-0012 Q29; S5's `LOCK_ALL_FILES`). Two workspaces that share a file therefore exclude each other on that file: a common include, the same folder registered twice through different paths, a copy whose include points back.
   - It waits if a lock is held, with phase `waiting_for_lock` (§8.1).
   - It re-expands the globs under the locks, and takes the lock of any newly matched file before trusting the set.
   - It fsyncs the journal directory (F-8).
   - It checks that no write journal of this workspace is open.
   - It recomputes the content fingerprint of the same set (re-checking absent paths and documents listings) and compares it with what it parsed.
   - Equal, and no journal: the read is **verified at instant T**, inside the lock hold. Otherwise the outcome is `snapshot_moved {cause: changed}` or `orphan_journal {session, job}`; the result carries whose journal it saw (TLA F12).
3. **Release** the locks. A publishing job publishes first (§5.3).

The locks are held for the verification only, a few milliseconds (about 10 ms at 54k), not for the whole load. F-6's purpose, never building from a half-applied commit, is kept: a lock-respecting writer holds the locks for its whole commit, so the verify either waits for it or sees no journal and its finished bytes. Waiting cannot deadlock, because the verifier holds no other lock and takes them in canonical order (`Fixed_legacy` and the liveness configs; §11).

Outcomes:
- **`snapshot_moved` caused by one of our own exclusive jobs** of the workspace that ran during the read: the job is re-queued behind it, and the retry counter does not move.
- **`snapshot_moved` caused by an outside writer:** the job is re-queued, at most `snapshot_retries` (default 3) times in a row. Then it ends with `LEDGER_CHANGING` ("Another program is changing your ledger", §9.1). A rebuild ends in WARMING with that message and is retried at the next focus check or Try again: condition-based, with no timer.
- **`orphan_journal`:** the verifier holds the locks, so no live lock-respecting writer owns that journal. The workspace goes to `RECOVERING` and §4.1 rule 6 applies (§5.7).
  - A journal of this session goes to in-session recovery [C].
  - A journal of an earlier session goes to recovery on open (ADR-0012).
  - A journal of another running instance is ADR-0012 Q30: refuse writes and ask the user to reopen (TLA F12).

**What this guarantees:** against every lock-respecting writer (our saves, a second instance on the same app-local-data folder, the legacy app), a verified result is a committed state (property `VerifiedReadsAreSnapshots`). Against a program that ignores our locks (an editor, Fava, a sync client), it guarantees only that the disk equalled what was parsed at instant T. That is what reloading the file in that editor gives, and no more (ADR-0013's accepted race).

### 5.3 Conditional publish and generation ids (H2)

A generation is published only by the engine that built it, inside a lock hold: the one of §5.2 step 2 (a rebuild), or the one of its save. It is published only when both of these hold:
- the disk's content fingerprint under those locks equals the build's fingerprint, and no journal is open after the journal-directory fsync (property `PublishOnlyCurrent`);
- its generation id is greater than the current published id.

**The id is allocated at publish, under the locks:** `id = 1 + max(current published id, the high-water id in app_local_data/engine/generations/<ws key>, the highest id among the generation files present)`. The high-water is written (atomic replace, fsync) before the pointer swap.

Because verification and allocation happen under the same locks, the order of the ids is the order of the verified instants. The newest state wins, whatever order builds finished in (properties `GenerationsMonotonic` and `PublishedNotOlder`). Keeping the high-water outside the cache directory keeps ids monotonic when the OS purges the cache (seeded bug `hw_in_cache`).

Mechanics:
- A build writes `build-<job id>.db`.
- Publishing writes `ledger_version` and both fingerprints into the new file's meta, renames it to `gen-<id>.db`, and swaps the pointer (a symlink; a pointer file on Windows, ADR-0015).
- A build that fails its condition is dropped and logged as `mirror.publish.refused_stale`, and the workspace gets one queued rebuild (newest only).
- GC deletes `build-*` files whose job is no longer running (design review L4). Published generations keep ADR-0015's grace period.

Why not a compare-and-swap on the base generation (H2's option): it would accept a build whose base is current but whose disk state moved after the build read it (an outside save mid-build). It would also refuse a newer save's build when an older-state rebuild had published first. Disk equality under the locks has neither hole. The seeded bug `id_at_dispatch` (lanes off) breaks `GenerationsMonotonic` in 18 steps.

### 5.4 The write path with the scheduler [C]

A `commit` job runs ARCHITECTURE §9.2 step 4 in one fresh engine:

1. **Locks.** Take the legacy locks, then the ledger-set locks (§5.2), in canonical order. A wait shows phase `waiting_for_lock` with **Cancel**. The wait has no deadline: ADR-0005 asks for "at least 30 s", and an unbounded wait with a visible status and Cancel meets it without a guessed number.
2. **Gate, under the locks:**
   - the disk's content fingerprint equals the plan's;
   - the published mirror's content fingerprint equals the plan's (H2: the baseline error keys of step 3 come from that mirror's `ledger_errors`);
   - **no write journal of the workspace is open**, from any session or instance (TLA F6: without this check a second instance's journal led recovery to block on our own bytes; `BlockedOnlyOnForeignBytes`);
   - **no closed record of the workspace exists**, from any session or instance, which means no R4 is still owed (TLA F1, F2; `R4BeforeNextCommit`).

   On failure:
   - a journal ends `RECOVERING` (§5.7);
   - a closed record waits for its R4, which is re-armed if it is not running;
   - a stale mirror ends `WORKSPACE_NOT_READY {WARMING}`;
   - a moved disk goes to step 2a (property `GateOnCurrentMirror`).

   For a `create`, only the disk half applies (§4.1 rule 5).
   - **2a. Our own earlier saves are not outside changes** (review 2 F9). When the disk moved and every change between the plan's fingerprint and the disk's was made by saves of this session (the workspace's recent outcomes chain the `pre → post` fingerprints), the job re-plans from the stored intent and re-runs steps 1–3. Re-planning means the mutator request again (ADR-0009), with every `TransactionId` re-resolved through the current mirror (ADR-0026).
     - If the new semantic diff is the same intended change (the same targeted entries and the same canonical texts), the job proceeds.
     - Otherwise it ends `PLAN_OUTDATED` ("Your earlier change moved this entry. Review it again.").
     - Only a change not made by us gives `LEDGER_EXTERNALLY_CHANGED` (property `OwnCommitsNeverExternal`).
3. **Load.** Re-run step 3 (the save's one full load) and compare the error keys. This load's CPU time and peak, measured over phases `parsing` to `validating`, become the R4 baseline (§1.1).
4. **Commit point, agreed by both sides.** The engine sends `gate {id, point: "commit"}` and waits on its input. The sidecar decides in one step of its table:
   - if a cancel is pending, it ends the engine; the save is cancelled and the disk is unchanged;
   - otherwise it marks the job **past the commit point** and only then sends `proceed {id}`.

   The engine opens the journal only after reading `proceed`. So the sidecar can never end, for a cancel, an engine that may have touched a file (properties `NoKillAfterCommitPoint` and `CancelBeforeCommitLeavesDiskUnchanged`; seeded bugs `one_sided_gate` and `cancel_after_point`).
5. **Write.** Journal, backups, swaps, read-back (ADR-0008, ADR-0012, ADR-0013, F-1 to F-8). The journal names its writer as `(session id, job id)`, never the engine's pid, because a pid dies with every fresh engine (H5).
   - **Journal close is one atomic rename** of `journal-<session>-<job>.open` into `journal-<session>-<job>.closed`, the **closed record**, in the same directory, followed by an fsync of the directory. The record holds: the post content fingerprint, the adopted digest, the engine build id and the R4 baseline (TLA F1, F4, overflow).
   - In one durable step the journal is gone and the record is present. The save has landed exactly when the record exists (§5.7).
   - No user code runs after the commit point: plugins ran in step 3. So a hang after it is our bug or the OS's (a frozen disk, `SIGSTOP`), and only the hang rule (§8.3) may end the engine.
   - **A protocol violation after the commit point does not end the engine.** The sidecar stops reading its output and waits for it to exit, with the hang rule still applying, then resolves the outcome from disk (§5.7). Ending it would only turn a probably finished save into a recovery (review 2 F13).
6. **Publish and reply.** Build the mirror from the load result still in memory, publish it under the same locks (§5.3; ADR-0011), release the locks, and reply with `usage` (§8.1) and the old→new TransactionId map. A save that cannot publish (`saved_nopub`) queues a rebuild.

Write serialization (ADR-0011) is the exclusive lane plus the ledger-set locks (ADR-0012 Q29), not INV-26 (design review L1). Two lock-respecting writers are never both mid-commit on one workspace, across instances (property `NoTwoWritersMidCommit`).

### 5.5 The R4 check [C] (review 2 F2; TLA F1, F2, F10; owner answer 4)

ADR-0008's amendment says the R4 re-check completes before the next write to that workspace is accepted. Here the gate is the **closed record** on disk (§5.4 step 5), so it holds across quits, crashes, recovery and a second instance (TLA F1, F2):
- Every save that landed has a closed record, whether or not it published or replied. Recovery that resolves a save as "saved" re-arms R4 from the record.
- Every write gate refuses while a closed record of the workspace exists.
- R4 removes the record under its locks when it ends `pass`, `superseded` or `nondeterministic`. A `mismatch` renames the record to `.mismatch`, which keeps saving blocked until the user acknowledges the backup notice.
- The last 16 removed records per workspace are kept as `.done` files, so a reloaded window can learn the outcome of a recent save even after a sidecar crash (TLA overflow; BL-0092).

R4's steps:
1. **Under the locks** (waiting, canonical order): check that the disk's content fingerprint equals the record's post fingerprint and that no journal is open, then release the locks. If the fingerprint differs, R4 ends `superseded`.
2. **Without locks:** run Beancount's own uncached load. Use `beancount.loader._uncached_load_file`, never `load_file` with the pickle cache disabled, which deletes `.<root>.picklecache` in the user's folder and can race a second load (spike S4; design review M5). Record the path of every file Beancount opened, through an audit hook on `open`: Beancount 3.2.0's `parse_file` opens through Python's `open`.
3. **Digest** the result and compare it with the record's adopted digest.
4. **Under the locks again:** re-check the content fingerprint of the recorded paths and the journal. Then decide:
   - **Digest equal and fingerprint unchanged:** `pass`. A torn read cannot produce the adopted digest by accident, so no lock was needed across the load.
   - **Fingerprint moved during step 2:** `superseded` (logged), never a mismatch.
   - **Digest differs and fingerprint unchanged:** **confirm** before blocking. Re-run steps 2–4 once *while holding the locks for the whole load*, and also run the overlay loader on the same bytes. This is the only case in which R4 holds the locks across user code.
     - Overlay(bytes) equals the adopted result but differs from `load_file`(disk): a real loader difference. Outcome **`mismatch`**:
       - saving is blocked and the backup is shown (ADR-0008);
       - `engine.r4.mismatch` is logged at error;
       - the `load_file` result is published as a generation (ADR-0010 guardrail 3 "adopts" it; review 2 O8).
     - Overlay(bytes) also differs from the adopted result: the ledger's plugins are not deterministic (dates, network). Outcome **`nondeterministic`**:
       - `engine.r4.nondeterministic` is logged at warning;
       - the `load_file` result is published;
       - the notice "Your ledger's plugins give a different result each time they run. Finzytrack updated your views from a fresh load." is shown;
       - saving is **not** blocked (owner answer 4).

Rules around it:
- **Superseded** means the disk moved after the save, through an outside editor or sync client, or through the legacy app (TLA F10). The comparison is then meaningless, so this one guardrail-3 sample is lost; that write's own gate passed. The outside change sends the workspace to WARMING, whose rebuild replaces the adopted mirror anyway. ADR-0008's amendment must say this (§14).
- **At the next open:**
  - if the fingerprint differs from the record's, the R4 is superseded;
  - if the engine build differs (an update in between), it is superseded with cause `engine_changed`, because another Beancount build cannot judge this build's overlay;
  - otherwise R4 runs before writes are accepted (review 2 F2).
- **Priority.** R4 is P3 and starts at normal OS priority, never `nice`, because a save may come to wait on it (§10). A queued save lends it its class.
- **R4 is never unmeasured** (§1.1). An R4 that loops is stopped by §8.5 when it runs at P3, and is flagged with Cancel when a save is waiting.
- **A failed R4 keeps saving paused.** An R4 that cannot complete (two crashes, a runaway, a user cancel) leaves its record, and saving stays paused: "could not check" never means "write anyway" (INV-22). **Run check now** runs it at P1 with no automatic stop.
- **Synchronous R4 during rollout** (ADR-0010) runs inside the save's job before the reply, at P1, with the same steps.

### 5.6 Outside editors, the legacy app, a second installation

- **Outside editors and sync clients** take no locks. They are detected by the focus-time stat check (WARMING and a rebuild), by verification (`snapshot_moved`), by the save's gate (`LEDGER_EXTERNALLY_CHANGED`) and by the swap's displaced-file hash (ADR-0013). They are never excluded.
- **The legacy v0.3 app** (ADR-0005, ADR-0020):
  - Every lock-taking step takes every recorded legacy lock first, with portalocker 3.2.0's primitive: verify, save, R4 steps 1 and 4, and recovery.
  - The legacy app waits for its lock with no time limit (`portalocker.lock(fp, LOCK_EX)` in v0.3's `write_lock.py`). While one of its threads waits, every other v0.3 write waits on its in-process lock.
  - So no v0.4 job holds the legacy lock across user code or an unbounded wait, except a save (steps 1–6) and an R4 confirmation. Both show their phase, and the save can be cancelled before its commit point (property `NoBackgroundLockAcrossUserCode`; review 2 F2).
  - The legacy app holds its lock through its whole re-export, so a verification may wait tens of seconds. The job keeps its parsed result while it waits; that is one job's lifetime, so not resident in INV-10's sense. It mostly ends `snapshot_moved` afterwards, because the legacy app wrote.
  - **Limit (TLA F14):** after one of our engines dies past its commit point, the OS releases our locks. The legacy app can then take its lock and write over a half-applied v0.4 save before our recovery takes the locks. Recovery then finds foreign bytes and blocks with both versions shown: a visible block, not a silent loss. The v0.3 app may meanwhile have exported a torn state into its own mirror. That cannot be fixed on our side.
- **Two installations, or two OS accounts, writing one ledger** are **not supported in v0.4.0** (owner answer 5). Each has its own app-local-data folder, so their lock files do not exclude each other, and each is an outside editor to the other: detection holds, exclusion does not.
  - The user docs say so.
  - The lock design that would make it safe is decided in M1b with BL-0091. Review 2's candidate is an advisory lock on the ledger **root file itself**: POSIX `flock` on a read-only descriptor, or on Windows `LockFileEx` on a byte range beyond end of file, which blocks no reader or writer of the content. It would be re-validated after acquiring, because the swap replaces the root's inode, and it creates nothing in the folder (INV-27, INV-30).
  - The legacy lock still excludes both installations on a Share workspace.

### 5.7 Recovery and the outcome of a save whose engine died [C] (TLA F4, F8, F12, F15)

When a save's engine ends without its final reply after the commit point (a crash, an OS kill, a hang end, a `MemoryError`, or the sidecar's own death, §6.3), the save's outcome is **resolved, never left unknown** (BL-0091; properties `OutcomeTruthful` and `EveryWriteEndsKnownSafety`).

The workspace's lane stays held until death is confirmed (§4.6). Then a `recover` job runs (P0, exclusive, admitted at any memory pressure: owner answer 3):
- **Locks.** It takes the locks, waiting. On Windows a lock may stay held briefly after `TerminateJobObject` (design review M6), so "held" right after our own end of an engine is waited for, never read as a live writer.
  - ADR-0012 says recovery **on open** takes the locks non-blocking and skips if they are held. Here both on-open and in-session recovery wait, showing "Checking your last change…", because a skip would leave the workspace unrecovered with no condition to retry on (TLA F15). §14 amends ADR-0012.
- **`resolve(journal, closed record, disk hashes, plan pre/post hashes)`**, a pure function:
  - **journal open:** ADR-0012's rule. If every file is pre or post, restore pre: **not saved, put back**. Any other bytes: **blocked**, writes off, both versions shown.
  - **no journal, closed record of this job present:** **saved**. R4 is re-armed from the record.
  - **no journal, no closed record:** **not saved**. If the disk differs from the plan's pre-image, add "your ledger was changed by another program" (TLA F4). This covers an editor saving between the gate and the journal, or writing pre bytes back after a closed journal. Without the record this case had no truthful answer.
- **Whose journal.** The journal's `(session id, job id)` tells our own orphan (recover now) from an earlier session's (recovery on open) and from another running instance's (ADR-0012 Q30: refuse writes and ask the user to reopen; TLA F12).
- **A recovery that itself dies** is retried once; it is idempotent, because it classifies again from disk. On the second fault the workspace stays visibly blocked: "Your last change may or may not be saved. Reopen the workspace." The property is restated as "a running save ends, or its workspace is visibly blocked" (`RunningWriteEndsOrBlocks`; TLA F8). The Fixed model blocks at the first death of a recovery, which is a stricter bound (§11.4).
- **Then the mirror.**
  - Saved: if the save's generation was published, nothing to do; otherwise a rebuild.
  - Not saved: the published mirror is still the pre state, because the save publishes only after the journal close, so nothing to do.

Before [C] there are no writes, and failure-modes rows E1–E3 keep saying so.

### 5.8 What is not watertight, and what it costs

- **Lock-ignoring writers** can only be detected (§5.6). The window of ADR-0013 remains where swap is unavailable.
- **Two installations or OS accounts** writing one ledger are not supported in v0.4.0 (§5.6).
- **An unkillable process** keeps its slot, lane and locks (§4.6). On a pool of 2, one stuck process halves throughput until it ends, and its workspace waits.
- **A first run** of a background job has no usual value, so a runaway or stalled first run uses its slot until a person cancels it (owner decision 4). Until the job list exists in the UI (BL-0092, M2), that person can only quit the app.
- **Machine saturation:** when the machine has no idle CPU, a frozen engine is not judged hung (§8.3) until the load drops. That is the price of never calling a starved engine hung. A `stopped` process (`SIGSTOP`) is the exception: it is judged hung even then.
- **Long memory pressure:** at `Critical`, nothing new starts except recovery and a job the user runs with **Run anyway** (§7.3). Ledger work can therefore wait as long as the pressure lasts, visibly (owner answer 6).
- **Files a plugin reads by itself** (a CSV of prices, a config file, the network) are inputs Finzytrack cannot see. A change to them does not make the mirror stale and does not reset poison. The user docs say so, and **Rebuild now** in Settings forces a rebuild (review 2 F3). Plugin *code* is tracked (§1).
- **Two loads of one ledger** may run its plugins at the same time. A plugin with side effects must tolerate that; the user docs say so (design review L5; review 2 O6).
- **The legacy app after our death** can write over a half-applied save before recovery takes the locks (§5.6, TLA F14).
- **Compressed memory** on macOS makes RSS understate a job's footprint (design review M1). The recorded peak on macOS is therefore the lifetime physical footprint, which counts compressed pages (§6.1).
- **Orphans after a sidecar crash** are invisible to the next session's admission and slot count for the up to 0.25 s that their watchdog takes. On Windows the Job Object ends them at once. Safety holds through the locks (`Fixed_restart`); `EnginesBounded` and `OneExclusivePerWorkspace` are properties of one session (TLA F13).

## 6. Engines [A]

### 6.1 A fresh process per heavy job (owner decision 5; H1), and the pool as the only reaper

- **One heavy job per engine.** An engine runs **at most one heavy job**, then exits. It may run cheap jobs and a `plan` before that, never after: an engine is clean until it imports user code, and it imports user code at most once (property `CleanEngineForUserCode`). The reason: Beancount's loader imports plugins with `importlib.import_module` and restores `sys.path` but not `sys.modules` (H1, reproduced: workspace B ran workspace A's plugin). A user who edits a plugin would otherwise be served the old module.
- **Idle clean engines.** A clean engine that ran only cheap jobs ends after `idle_ticks` on-time ticks with no job (default 5), or at once when its slot is needed (review 2 O13).
- **The pool is the only reaper.** After its final reply the sidecar closes the engine's stdin, the engine exits, and the pool reaps it. It never calls `Popen.wait` or `Popen.poll` on an engine: after our own `wait4`, the standard library reports `returncode == 0` for a child that died of `SIGKILL` (review 2 F7; spike s2). On POSIX it reaps in two steps:
  1. `os.waitid(P_PID, pid, WEXITED | WNOWAIT)`, which leaves the child a readable zombie. On macOS it then reads `proc_pid_rusage(pid, RUSAGE_INFO_V4)` for `ri_lifetime_max_phys_footprint`, which is what jetsam judges and which counts the compressed pages that RSS omits.
  2. `os.wait4(pid, 0)` for the exit status, the CPU time and `ru_maxrss` (bytes on macOS, kB on Linux).

  Spike s2: a child holding 300 MB and ended with `SIGKILL` gave `ru_maxrss` 316 MB, a lifetime footprint of 307 MB, and signal 9.
- **Windows.** The process handle and the engine's Job Object give `PeakProcessMemoryUsed` and `PeakJobMemoryUsed` (commit, children included), and `TotalUserTime + TotalKernelTime` of the whole job (`JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION`).
- **Recorded values (§1.1).** The recorded peak is the macOS footprint, Linux `ru_maxrss`, or the Windows job's peak commit. The recorded CPU time is that of the whole process group: the engine plus its reaped children, which `wait4` includes for children it waited for, with descendants summed by the probe (§8.2).
- **Spare engine.** The pool may keep one started, never-used engine to hide start latency. It is **off by default** on macOS and Linux, because the start is cheap even at 2× CPU oversubscription (spike s5: 0.08–0.09 s). For Windows it is decided from the BL-0087 runner's measurement (M1b) and confirmed in M11. A spare counts against `max_engines` and is ended first when a slot is needed.

### 6.2 Start cost

Measured on the owner's Mac (M4 Pro, macOS 26): `python -I -c "import beancount.loader, beanquery"` took 0.07–0.10 s and 34 MB peak with a warm file cache, and 0.42 s on a cold cache. Review 2 measured 0.08–0.09 s even with 14 or 28 CPU burners. ADR-0054's "40–80 MB" was pessimistic.

That is about 5 % of a 2 s load at 54k. At demo size (a 0.16 s load, ADR-0010) it is large in proportion, but small in absolute terms against the 1 s demo write target. Windows (CreateProcess, Defender scanning a fresh interpreter) is unmeasured: [M1b] on the BL-0087 runner, [M11] on real machines.

### 6.3 Process groups and Job Objects

- **POSIX:**
  - Each engine leads its own process group (`process_group=0` at spawn). Our end of an engine is `killpg(pgid, SIGKILL)`, which also ends what the job started (gpg for encrypted files, a plugin's subprocess; design review L4).
  - The shell's group kill of the sidecar no longer reaches engines in their own groups, so the engine's parent-death watchdog (0.25 s, spike S1) ends its **whole group** (`killpg(0, SIGKILL)`), not only itself.
  - `PR_SET_PDEATHSIG` is not used, because it fires when the spawning *thread* ends, and the sidecar spawns from worker threads.
  - On Linux each engine is spawned with `oom_score_adj` +500 (raising it needs no privilege), so the OOM killer takes an engine before the sidecar (review 2 F10).
  - Stated limit: a child that calls `setsid` leaves the group (§4.6).
- **Windows** ([C] for the write path, ADR-0061 decision 6; M11 otherwise): each engine runs in its own Job Object, nested in the shell's, with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and without breakaway. Our end of an engine is `TerminateJobObject(job, EXIT_OUR_KILL)` with a fixed exit code. If the sidecar dies, its handle closes and the job's processes end at once.
- **A save past its commit point when the sidecar dies** (this settles failure-modes E5's open rule, which BL-0091 was to pick): the engine is ended with its group, on every OS. The journal makes this a crash mid-write. The next open recovers it (ADR-0012) and tells the user which outcome of §5.7 happened. One rule holds on all three OSes: no engine outlives the sidecar (property `PastCommitPointSurvivesSidecarDeath`, checked by `Fixed_restart` as `OutcomeTruthful` across `SidecarCrash`).

### 6.4 How an engine ended

The sidecar records the end reason in the engine's state **before** it sends the signal. The reasons are `cancel`, `preempt`, `superseded`, `hang`, `stalled`, `runaway`, `quit` and `idle`. **A recorded reason wins over any exit status**, so a real crash that races our signal is classified by our reason (TLA F11; seeded bug `reason_after_kill`). On reap:

| Observation | Classified as | Stop? |
|---|---|---|
| final reply, then exit 0 | `answered` | no |
| ended by us, reason recorded (whatever the exit status) | that reason | only `hang` |
| `SIGKILL` (POSIX) with no reason of ours | `killed_by_system`. On macOS and Linux this is most often memory: jetsam, or the OOM killer, corroborated on Linux by the cgroup's `memory.events` `oom_kill` counter when readable | no; poison, and the pool goes to one engine (§7.5) |
| exit `EXIT_OUT_OF_MEMORY` after an `engine.out_of_memory` reply; Windows `0xC0000017` (`STATUS_NO_MEMORY`) | `out_of_memory` | no; poison, and the pool goes to one engine |
| a reply that breaks the protocol, before the commit point | `violation`: we end the engine | yes |
| a reply that breaks the protocol, after the commit point | not ended; wait for the exit (§5.4 step 5), then `crashed` if the exit is abnormal | as `crashed` |
| any other exit or signal mid-job | `crashed` | yes |
| output ended before the handshake; handshake refused or timed out | `start_failed` | yes |
| another protocol version | `incompatible` | no (the pool becomes `Incompatible`) |

### 6.5 Out-of-memory errors inside the engine

Windows does not kill a process for memory. An allocation beyond the commit limit fails, which Python raises as `MemoryError` (or as a native crash, `STATUS_NO_MEMORY`). On macOS and Linux this can happen under a hard limit (a cgroup, `ulimit`).

The engine catches `MemoryError` at the job's top level, drops its references, writes a **pre-encoded** `engine.out_of_memory` reply (no allocation needed) and exits with `EXIT_OUT_OF_MEMORY`. A `MemoryError` after the commit point is reported the same way, and the outcome is resolved as in §5.7; ADR-0012's in-process rollback runs first if it can.

## 7. Admission and memory [A]

### 7.1 Budget (owner decision 2)

`budget = memory_share × total`, with `memory_share` defaulting to 0.25 (a setting).
- `total` is physical RAM: macOS `hw.memsize`, Linux `MemTotal`, Windows `GlobalMemoryStatusEx.ullTotalPhys`.
- It is lowered to the container's limit when one is set: cgroup v2 `memory.max`, v1 `memory.limit_in_bytes`, or a Windows job memory limit.
- The budget covers the engine processes and the sidecar's decoding of their replies, not the rest of the app.
- There is no CPU formula and no startup computation (owner decision 1; design review M1).

### 7.2 Estimates

For a large job of kind k in workspace W:

`E = usual_peak(W,k) × max(1, size_now / size_at) + usual_reply(W,k) × decode_factor`

- `usual_peak` and `usual_reply` are defined in §1.1.
- `decode_factor` is 8 until BL-0082 measures it (design review L2).
- A **cheap** job's estimate is the interpreter baseline, measured from cheap jobs (about 34 MB).
- An **unmeasured** large job (§1.1) has no estimate and runs alone. Spike S4's ratios are not used as a guess, because they came from generated ledgers without heavy plugins (design review M2).
- With a fresh process per job, the reaped peak **is** the job's absolute peak, interpreter included; a delta in a reused process under-reads by 2× (design review M2). A killed job's peak is measured the same way, so a memory kill teaches the estimate.

### 7.3 Admission rule (owner decision 2, amended by the owner's answer 6; owner answers 1 and 3)

A job is admitted at dispatch according to the OS's pressure level (§7.4):

| Pressure | Large job | Cheap job | `recover` |
|---|---|---|---|
| `Normal` (or `Unknown`, §7.4) | admitted if no large job is running; or if every running large job and this one are measured and `Σ E(running) + E(this) ≤ budget`; on Windows also `E(this) ≤ ullAvailPageFile` (the commit still available, so the job does not fail with `MemoryError`) | admitted | admitted |
| `Warn` | admitted only if **no other job of any kind is running** (one at a time; owner answer 6) | as a large job | admitted |
| `Critical` | not admitted, except one job the user started and confirmed with **Run anyway** (owner answer 1) | not admitted | admitted (owner answer 3: it loads no ledger, and refusing it would leave a half-applied save on disk with writes blocked for as long as the pressure lasts) |

- The first job is always admitted when it is alone, at `Normal` or `Warn`, even if `E > budget`; otherwise a large ledger could never load.
- A job already running is never stopped by admission (property `MemoryAdmission`; seeded bug `admit_under_pressure`). Under `Critical`, preemptible P3 jobs are preempted (§4.3).
- **Run anyway** is offered on a job that a user started (P1, never a background job) while it waits for memory at `Warn` or `Critical`. Pressing it makes the job the head (§4.7), and it starts as soon as no other large job is running, preempting preemptible ones, whatever the pressure. At most one Run-anyway job exists at a time. It is a logged decision (§9.3).
- **The amendment, stated:** the owner's decision 2 said "no new job while the OS reports memory pressure". The owner's answer 6 (2026-10-26) amends it to "one job at a time at warning, nothing new at critical". Answer 1 adds Run anyway, and answer 3 exempts recovery. Draft 1 admitted cheap jobs under `Warn` without saying so; that is withdrawn (review 2 F5).

### 7.4 The pressure signal per OS (`MemoryPressurePort.level() -> Normal | Warn | Critical | Unknown`)

The level is read at every dispatch decision and on each watcher tick while jobs run. A read costs microseconds (spike s1: 2.15 µs for the macOS sysctl).

| OS | Source | Warn | Critical |
|---|---|---|---|
| **macOS** [A] | `sysctlbyname("kern.memorystatus_vm_pressure_level")`: 1 normal, 2 warn, 4 critical; it reads 1 on the owner's Mac. This is the kernel's own judgement, which already accounts for compression and swap. | 2 | 4 |
| **Linux** [M11; CI from A] | In a cgroup v2 with a limit, the cgroup's `memory.pressure` (PSI); otherwise `/proc/pressure/memory` (kernel ≥ 4.20 with PSI enabled; some distributions boot with `psi=0`). Fallback: `MemAvailable` from `/proc/meminfo`, or `memory.max − memory.current` in a limited cgroup. Linux overcommits, so the OOM killer sends `SIGKILL`. | PSI `some avg10` ≥ 10 %; fallback: available < 10 % of total | PSI `full avg10` ≥ 5 %; fallback: available < 5 % |
| **Windows** [M11] | `CreateMemoryResourceNotification(LowMemoryResourceNotification)` with `QueryMemoryResourceNotification`; `GlobalMemoryStatusEx` for `dwMemoryLoad`, `ullAvailPhys` and `ullAvailPageFile`. Allocations fail at the commit limit (§6.5), so admission also checks commit. | `dwMemoryLoad` ≥ 90 % | the low-memory notification is signalled, or `ullAvailPageFile` is below the smallest measured job estimate |

Thresholds are settings. **Unknown** means no signal is readable (PSI off and `/proc` unreadable, or a sandbox). Admission then uses the budget only, and one `engine.pressure.unknown` info line is logged per session. The cost, stated: without a signal Finzytrack can start a job into a machine that is already short of memory; the budget bounds only our own share.

### 7.5 After a memory death

After a `killed_by_system` or `out_of_memory` outcome, the pool runs **one large engine at a time** (`single_engine`; design review M1), logged as `engine.pool.single_engine`. It does so until `single_engine_recover` (default 20) consecutive healthy large jobs have ended at `Normal` pressure, or until the next app start; desktop sessions last weeks (review 2 O1). The job's usual peak becomes at least its measured peak at death (§1.1). The workspace's poison counter for that key moves (§8.7).

### 7.6 Containers and hosting

The budget is lowered to the container's limit (§7.1), and PSI is read from the container's cgroup, so a container behaves like a smaller machine.

In hosting [Hosted], the pool and budget stay one process-wide object (ADR-0041's single entry, as amended in §14), jobs carry their user, and the pool's cross-user round-robin (§2) becomes per-user fairness and quotas, which ADR-0041 defers. `measurements.json`, the generation high-water marks, the lock files and the closed records live under each user's root. R4 in hosted mode follows ADR-0041's hosted design (its D2 background reconciliation is off).

## 8. Liveness and progress

### 8.1 Protocol v2 [A]

`PROTOCOL_VERSION` becomes 2. A v1 peer is `incompatible`; both sides ship in one bundle, so only a damaged install mixes them. The envelope rules of S0035 stay: `v` first-class, JSON Lines, 16 MiB per line, strict JSON. What changes:

- **Per request, the engine sends** zero or more `progress` messages, at most one `gate` message (saves and creates only), then **exactly one** final reply. "One reply per request" becomes "one final reply per request".
  - **Progress:** `{"v":2,"id":N,"op":op,"progress":{"seq":k,"phase":P,"cpu_ms":c,"peak_rss_bytes":m,"done":d,"total":t}}`.
    - `phase` is one of a closed list: `starting`, `reading_files`, `parsing`, `booking`, `plugins`, `validating`, `querying`, `waiting_for_lock`, `verifying`, `building_mirror`, `publishing`, `committing`, `serializing`, `recovering`.
    - `done` and `total` are optional counts (files read, statements run).
    - A progress message is sent at each phase change and, by an engine thread, at least every `beat` (1 s) while a job runs.
    - `waiting_for_lock` and `reading_files` are declared waits for §8.4.
  - **Gate:** `{"v":2,"id":N,"op":"commit","gate":{"point":"commit"}}`. The sidecar answers `{"v":2,"id":N,"op":"commit","proceed":true}` on the engine's input, or ends the engine (§5.4). The engine reads nothing else mid-request.
  - **Final replies** carry `usage {cpu_ms, peak_rss_bytes}`, self-reported and cross-checked with the reap.
- **Domain outcomes are results, not errors** (area review F7): `snapshot_moved`, `orphan_journal {session, job}`, `superseded`, `nondeterministic`, `mismatch`, `plan_outdated`, and `result_too_large` (per statement) are tagged results inside `result`. `error` keeps protocol and internal faults, plus `engine.out_of_memory`.
- **The engine's output channel** gets a lock, so the progress thread and the main thread never interleave inside a line. A long final-reply write holds the lock, and its bytes count as liveness.
- **The sidecar's request** carries `job {id, kind, ws_key, attempt}` and no time limit. `EnginePort.call(op, params, timeout)` becomes `submit(job) -> JobHandle`, with `status()`, `cancel()` and `outcome()`. The caller's timeout disappears from engine calls; cheap jobs have none either, because the hang rule covers them.

### 8.2 The watcher and on-time ticks [A]

A watcher thread in the sidecar ticks every `tick` (1 s) for each running engine. "Schedule" means the previous actual wake plus `tick`, so one late tick causes no drift (review 2 O3).

A tick is **on time** when both of these hold:
- it woke within `tick/2` of its schedule on `Clock.monotonic()`. A sidecar busy decoding a large reply under its GIL wakes late (design review L2).
- `Clock.monotonic_with_sleep()` advanced by no more than `tick + tick/2` since the previous tick, so no machine sleep fell inside it.

The sleep-counting clock:
- macOS: `CLOCK_MONOTONIC`, **verified**. Spike s1: it ran 235,414 s ahead of `time.monotonic` (`mach_absolute_time`) since boot, which is the time asleep.
- Linux: `CLOCK_BOOTTIME`.
- Windows: `QueryInterruptTime` against `QueryUnbiasedInterruptTime`, to be verified in M11.

Late ticks count for nothing and reset nothing. On macOS, App Nap can make ticks late while the window is hidden; that only gives false negatives. The shell holds an `NSProcessInfo` activity while jobs run (review 2 O4).

On each tick the watcher samples, **from outside the engine** (`ProcessProbe`):
- the CPU time of the engine's **whole process group**: the engine plus every live descendant, summed from each process's own times. `psutil`'s `children_*` fields count only children already reaped; review 2's spike s4 showed an engine waiting on a CPU-bound subprocess read 0.0 s per tick for itself and 1.0 s for its tree. On Windows, the Job Object's total CPU.
- the group's RSS;
- the engine's process status;
- the system's idle CPU over the tick;
- whether bytes arrived: bytes the reader thread read since the last tick, **or bytes waiting in the pipe** (`FIONREAD` on POSIX, verified by spike s1; `PeekNamedPipe` on Windows).

These samples feed `liveness.judge(state, tick_sample) -> state, verdict`, a pure function whose inputs and verdicts are logged (§9.3).

### 8.3 Hung [A]

An engine is **hung** after `hang_ticks` consecutive on-time ticks in each of which all of these hold:
- no bytes arrived;
- the group's CPU time advanced by less than 1 % of the tick;
- the system had at least half a core idle, so the engine could have run;
- memory pressure was `Normal`, so the engine is not waiting on swap-ins.

A sample whose process status is `stopped` (SIGSTOP, a debugger) counts as a hang tick even when the machine is busy. A status of uninterruptible disk wait (Linux `D`) is never counted, because no signal can end it, and the job shows "waiting for a disk" (§9.1; review 2 F10). An engine that has sent its final reply and does not exit is judged by the same rule (§4.6). A hung engine is ended, with reason `hang`, and that is a stop.

`hang_ticks = max(10, 3 × the usual longest quiet stretch of (workspace, kind))` (§1.1). This is a count of observations of a condition, not a time budget. A healthy engine either uses CPU, or its progress thread speaks:
- C code holding the GIL stops the heartbeat but not the CPU clock (H4's 6 s regex);
- a blocking read or a sleeping plugin releases the GIL.

So a healthy run never builds up such ticks, whatever the ledger size or the machine's speed. The floor of 10 only makes the rule more lenient.

Review 2's spike s4 measured this. A GIL-holding regex sent no bytes for up to 33 ticks but used 0.13–1.0 s of CPU per tick, and was never counted, whether idle or beside 14 or 28 CPU burners. A native GIL-holding block was counted when the machine was idle, but not when it was saturated. Watcher ticks were at most 0.053 s late beside 28 burners.

### 8.4 Stalled

An engine is **stalled** when progress bytes arrive but, for `stall_ticks` consecutive on-time ticks:
- its group's CPU advanced by less than 1 % of the tick;
- the `phase`, `done` and `total` of its progress did not change;
- and the phase is not a declared wait.

An example is a plugin blocked on a network call. `stall_ticks = max(30, 3 × the usual longest stalled stretch of (workspace, kind))` (§1.1).
- **Interactive jobs (P0–P2):** a status only, "No progress for a while", with **Cancel**. They are never stopped.
- **Background jobs (P3)** [A]: stopped, with reason `stalled`. That is not a stop but counts in poison, and it is logged at warning. An unmeasured job and a save past its commit point are never stopped this way (owner answer 2, as decision 4).

### 8.5 Runaway (owner decision 4) [A]

A **background** job is stopped (reason `runaway`; not a stop, but counted in poison; logged at warning) when its group's CPU time exceeds `max(runaway_factor × B, B + runaway_floor)`:
- `B` is the usual CPU time of §1.1, scaled by size;
- `runaway_factor` is 10;
- `runaway_floor` is 10 s of CPU.

The floor makes the effective factor larger than the owner's "about 10×" for jobs under about 1.1 s of CPU, so a 0.2 s demo job is not stopped at 2 s. That is a departure, and it is harmless (review 2 O15; §13).

An **unmeasured** job has no bound, so only a person can cancel its first run. Interactive jobs are never stopped by this rule. Past the same bound they show "Taking much longer than usual" with **Cancel**.

### 8.6 Learning

After each healthy run, `Measurements` updates the usual values for (W, k) by §1.1's rule. Runs that ended in a memory death update the peak only. The file is written atomically after each update, at most once per job. An unreadable file is treated as unmeasured, never as an error (review 2 O17).

### 8.7 Supervision: one table per engine, one for the pool, poison per workspace [A]

S0040's statechart is split. Its stop history died with each short-lived `EngineClient`, so a segfaulting plugin would have restarted for ever (H6; seeded bug `stops_per_engine`).

**Per engine** (`app.platform.engine_process`, replacing `engine_supervision`):
- States: `Spawning → Handshaking → Clean → Running(job, phase, past_commit_point) → Draining` (final reply received, waiting for the exit) `→ Gone(classification)`, plus `Ending(reason)`.
- Events: `Spawned`, `SpawnFailed`, `HandshakeOk`, `HandshakeForeign`, `HandshakeFailed`, `Dispatch`, `Progress`, `Gate`, `Proceed`, `Reply`, `Verdict(hang|stalled|runaway)`, `Cancel`, `End(reason)`, `OutputEnded`, `Reaped(status, usage)`, `GroupGone`, `Violated`.
- `Clean` accepts cheap jobs, a `plan`, and one heavy job. `Draining` never accepts another job and holds the slot until `GroupGone`.
- The table emits `End(reason)` from `Running` with `past_commit_point = true` only for `hang` (property `NoKillAfterCommitPoint`). `Violated` past the commit point leads to "stop reading, wait".
- The (state × event) exhaustive test and random walks of EP-17 apply, and the reference is generated (`design/generated/statecharts/engine-process.md`).

**Pool** (`app.platform.engine_pool.table`):
- State: `{engines by state, stops: times of the last max_crashes stops, single_engine and its healthy count, unavailable, incompatible}`.
- Events: `EngineGone(classification, ws, fingerprint, now)`, `Request(now)`, `PressureChanged`, `RunAnyway`, `Reset`, `Close`.
- It keeps S0040's rules at pool level: a backoff of `delays` (0.5 s, 2 s, 8 s) before the next engine start after n recent stops, and `max_crashes` (3) stops within `window` (300 s) make the pool `Unavailable` (§4.9) until a stop leaves the window or the user presses **Try again** (`Reset`).
- The window and the backoff run on `Clock.monotonic_with_sleep()`, so a stop's age includes time asleep (review 2 O2). The window is S0040's accepted rate limit on crash loops, not a time limit on any job.
- Our own ends of an engine other than `hang`, memory deaths, runaway stops and stalled stops are not stops (property `OwnKillNotAStop`). With heavy jobs refused while `Unavailable`, the property `PoolNoHeavyWhileUnavailable` holds.

**One ledger cannot trip the pool** (TLA F5; review 2 F6):
- A fault enters the pool's stop history only when it is a start failure, a fault of a cheap job, or the **first** counted fault of its workspace at its current content fingerprint.
- The workspace keeps a mark of the fingerprint of its last counted stop. Further faults at that fingerprint are not counted, until the fingerprint changes or the user presses Try again.
- So one bad ledger adds at most one stop, and `max_crashes` (3) is reached only by faults spread over three ledgers or states, or by the engine itself (a damaged install fails every start). That is what the pool bound is for (properties `OneLedgerCannotTripPool` and `BoundedFaultLoop`).

**Per-workspace poison** (in `JobQueue`):
- A counter per (workspace, kind, content fingerprint) of consecutive faulted ends: crash, hang, violation, memory death, runaway, stalled.
- At `poison_limit` (2) that key is refused ("poisoned", §9.1) until the content fingerprint changes (which includes a plugin fix, §5.1) or the user presses Try again.
- A healthy end of that kind resets it.
- Keyed per kind, as in the Fixed model. A crashing query does not refuse the rebuild. The pool bound above is what keeps one ledger's faults from reaching other workspaces.

**Settings** (app-level, in `settings.yaml`, applied at the next scheduler event; a value outside its range is refused and logged as `settings.refused`; review 2 F12):

| Setting | Default | Allowed range | Section |
|---|---|---|---|
| `max_engines` | 2 | 1–8 | §1 (owner decision 1) |
| `memory_share` | 0.25 | 0.05–0.9 | §7.1 (owner decision 2) |
| `decay` | 0.9 per run | 0.5–0.99 | §1.1 |
| `runaway_factor`, `runaway_floor` | 10; 10 s CPU | 2–100; 1–600 s | §8.5 (owner decision 4) |
| `beat`, `tick` | 1 s, 1 s | 0.25–5 s, with `beat ≤ tick` | §8.1, §8.2 (rates, not limits) |
| `hang_ticks` floor, `stall_ticks` floor | 10, 30 on-time ticks | 3–600, 10–3600 | §8.3, §8.4 |
| `quit_patience` | 10 on-time ticks | 3–600 | §4.8 |
| `idle_ticks` | 5 | 1–600 | §6.1 |
| `max_crashes`, `window`, `delays` | 3; 300 s; (0.5, 2, 8) s | 2–10; 60–3600 s; each 0–60 s | §8.7 (S0040) |
| `poison_limit` | 2 | 1–5 | §8.7 |
| `single_engine_recover` | 20 | 1–1000 | §7.5 |
| `snapshot_retries` | 3 | 1–10 | §5.2 |
| `aging_passes`, `preempt_max` | 8, 2 | 1–100, 0–10 | §4.2, §4.3 |
| `cheap_margin` | 32 MB | 0–1024 MB | §1 |
| queue bounds | §3.1; 64 per user | 1–1024 | §4.5 |
| pressure thresholds | §7.4 | 1–99 % | §7.4 |

## 9. What the user sees, the log, and replay

### 9.1 Messages [M2 for the UI, BL-0092; the outcomes and codes from A/B/C]

Every message follows EP-27: what happened, whether the data is safe, what to do. Job state comes back in API responses as a tagged union (kind, state, phase, `done/total`, cancellable, `much_longer_than_usual`). `GET /api/w/{ws}/jobs` and `POST …/jobs/{id}:cancel` give the job list (design review L3) [M2]. "Usual" in these messages is §1.1's.

| Situation | Message | Actions |
|---|---|---|
| queued | "Waiting for other ledger work to finish…" | Cancel (if cancellable) |
| waiting to run alone (unmeasured, or the head of the reservation) | "Waiting for other ledger work to finish, so this can run on its own…" | Cancel |
| waiting for memory | "Waiting for memory. Your computer is low on memory. Closing other apps helps." | Cancel; **Run anyway** (jobs you started; one at a time) |
| running, per phase | "Reading your ledger files…", "Checking your ledger…" (parsing to validating), "Running your query…", "Saving…", "Updating your views…" (building, publishing), "Checking your last change…" (R4, recovery) | Cancel where allowed |
| `waiting_for_lock` (legacy) | "Waiting for the other Finzytrack app to finish saving…" | Cancel |
| much longer than usual (interactive, past the §8.5 bound) | "This is taking much longer than usual for this ledger." | Cancel |
| no progress (interactive, stalled) | "No progress for a while. It may be waiting for something." | Cancel |
| waiting for a disk (Linux `D`, or an engine that cannot be ended) | "Finzytrack is waiting for a disk and cannot stop this yet. It will continue when the disk answers." | Copy diagnostics |
| Cancel on a save past the commit point | "Finishing your change…", then "Your change is saved." | none, then Undo where the mutator has one |
| save cancelled before the commit point | "Your change was not saved. Your ledger is unchanged." | none |
| save planned before an earlier save moved its entry | "Your earlier change moved this entry. Review it again." | Review |
| busy | "Finzytrack is busy with other ledger work. Please try again in a moment." | Try again |
| crash on a read | "The ledger engine stopped while working on this. Your ledger files are safe." | Try again, Copy diagnostics |
| memory death | "Your computer ran out of memory while Finzytrack worked on this. Your ledger files are safe. Close other apps and try again." | Try again |
| hang | "The ledger engine stopped responding and was restarted. Your ledger files are safe." | Try again |
| save resolved after an engine death | "Your change was saved." / "Your change was not saved. Your ledger is unchanged." / "Your change was not saved, and your ledger was changed by another program." / "Finzytrack could not finish your change and put your files back as they were." / "A ledger file was changed by another program while Finzytrack was saving. Saving is off until you review it." | — / — / Review files / — / Review files |
| recovery failed twice | "Your last change may or may not be saved. Reopen the workspace to check." | Reopen, Copy diagnostics |
| writes blocked, queued saves refused | the block's own message | as the block |
| poisoned | "Finzytrack stopped trying: the ledger engine failed twice on this ledger. Your files are safe. Change the ledger or try again." | Try again, Copy diagnostics |
| pool unavailable | failure-modes E1's message | Try again, Copy diagnostics |
| ledger keeps changing | "Another program is changing your ledger. Finzytrack will update when it stops." | Try again |
| R4 stopped, cancelled or failed to run | "Finzytrack could not double-check your last change. Saving is paused until the check runs." | Run check now |
| R4 mismatch | "Finzytrack's check of your last change found a difference. Saving is paused. Your backup is here." | Show backup, Copy diagnostics |
| R4 nondeterministic | "Your ledger's plugins give a different result each time they run. Finzytrack updated your views from a fresh load." | Learn more |
| Quit with a save past its commit point | "Finishing your last change…"; after `quit_patience`: **Quit anyway** with "Finzytrack will finish or undo this change the next time it opens. Your ledger stays safe." | Wait, Quit anyway |

### 9.2 Log events (§6A; every field a safe kind: ids, enums, counts, durations, bytes)

These are the human-facing lifecycle events. The decision events of §9.3 are in addition to them.

- **Jobs:**
  - `engine.job.ended` (info): kind, outcome, `duration_ms`, `queued_ms`, `admission_wait_ms`, `cpu_ms`, `peak_bytes`, attempt, `ws`. With the two wait times the log answers "why was it slow?" (review 2 F15).
  - `engine.job.cancelled` (info): reason. `engine.job.preempted` (info).
  - `engine.job.flagged` (info): `much_longer` or `no_progress`, for interactive jobs.
  - `engine.job.runaway_stopped` and `engine.job.stalled_stopped` (warning): kind, `cpu_ms`, bound.
- **Engines:**
  - `engine.stopped` (warning; kinds `crashed`, `hang`, `protocol_violation`, `start_failed`).
  - `engine.killed_by_system` and `engine.out_of_memory` (warning; `peak_bytes`).
  - `engine.pool.single_engine` (warning) and `engine.pool.single_engine_ended` (info).
  - `engine.unavailable` (error, as S0040). `engine.unkillable` (error).
- **Workspaces, admission and queues:**
  - `engine.workspace.poisoned` (error; kind).
  - `engine.admission.waiting` (info; reason, once per wait). `engine.admission.run_anyway` (info).
  - `engine.pressure.changed` (info; level). `engine.pressure.unknown` (info, once).
  - `engine.queue.busy` (info, once per drain).
- **Locks** (for the v0.3 interplay):
  - `engine.lock.waiting` (info, once per wait; `lock_kind` legacy or set, and the job kind).
  - `engine.lock.acquired` (debug; `waited_ms`).
- **Reads and the mirror:**
  - `engine.snapshot.moved` (debug; info when the retries run out).
  - `mirror.publish.refused_stale` (info). `mirror.generation.published` (info).
- **R4:**
  - `engine.r4.passed` (debug). `engine.r4.superseded` (info; cause `disk_moved` or `engine_changed`).
  - `engine.r4.mismatch` (error; kind of difference, counts; ADR-0010's "logs loudly").
  - `engine.r4.nondeterministic` (warning).
- **Saves and quit:**
  - `engine.commit.finishing` (info: Cancel after the commit point).
  - `engine.commit.resolved` (warning; resolution).
  - `engine.quiesce.quit_anyway` (warning).
- **Settings:** `settings.refused` (warning).

Queue and dispatch traces are carried by §9.3, not by debug lines.

### 9.3 Replayable decisions (requirement; owner, 2026-10-26: "Yes, add it to the scheduler spec as a requirement. Let's make sure this logging system design is documented and implemented")

**Goal:** when a user hits a rare scheduling or liveness failure, the developer can rebuild, from that user's log, every decision Finzytrack made and why, without the log ever holding ledger content.

**R1. Decisions are pure** [A]. Every decision-making table is a pure function `(state, event) -> (state, effects)` that reads no clock, no random number, no environment and no global, and receives everything as event fields:
- `next_dispatch` with its admission rule (§4.7, §7.3);
- the pool table (§8.7);
- the per-engine statechart (§8.7);
- `liveness.judge` (§8.2–§8.5);
- `resolve` (§5.7) [C].

Clocks arrive as values: `monotonic_ms` and `with_sleep_ms`, as the table sees them. Each table has a `table_version` (a hash of its code module, generated at build time).

**R2. Every input and every output is logged, content-free** [A], through S0039's registry and default-deny redaction, both unchanged:
- **`sched.input`** (info). Fields:
  - `seq` (per session, gapless), `table`, `event` (enum), `ws` (registry key), `job` (id), `kind`, `class`, `attempt`, `reason`;
  - counts (`queued`, `slots_free`, `ticks`);
  - `monotonic_ms`, `with_sleep_ms`, `cpu_ms`, `peak_bytes`, `budget_bytes`, `estimate_bytes`;
  - `pressure`, `fingerprint` (a hex hash: `id` kind), `engine_build`, `plugin_set` (hash), `table_version`.

  **Never** ledger text, paths outside the app dirs, account names or amounts.
- **`sched.decision`** (info): `seq` of its input, `table`, the effects as enums with ids, and `state_digest` (SHA-256 of the canonical encoding of the new state, which itself holds only ids, kinds, counts and hashes).
- **`sched.checkpoint`** (info): the full table states, content-free, at session start, every 1,000 inputs, and as the first line of every rotated log file, so a single file replays alone.
- **Ticks are run-length encoded.** `liveness.judge` depends only on each tick's class (on time, bytes, CPU advanced, idle available, pressure, process status) and on cumulative CPU. So one `sched.input` line covers a run of identical classes (`ticks: n`, with the CPU at the end), and the replay is still exact.
- **Inputs that leave the state unchanged and emit no effect except a refusal** (busy refusals) are logged as counts. The replay does not need them one by one.
- A gap in `seq` (a line dropped because the log could not be written, S0039) makes the replay report the gap and resume at the next checkpoint.

**R3. A replay test in CI** [A]: `scheduler_replay(log) -> divergences`.
- It reads the checkpoints and inputs, feeds them to the tables of the same `table_version`, and compares every resulting effect list and `state_digest` with the logged `sched.decision`.
- T1 runs it over the logs of every scheduler scenario test (fake clock, fake probe, fake engine), so every test is also a recording.
- A Hypothesis random walk asserts that `replay(record(walk)) == walk`.
- A seeded divergence (a table changed without its version) must be reported.

**R4. A flight recorder** [A]. A bounded in-memory ring of finer events that are not decisions: each raw tick sample (exact CPU, RSS and idle values), message sizes and timings, queue snapshots, lock-wait starts and ends.
- Bound: the last 4,096 events or 1 MiB, whichever comes first; registered in `state_registry` (UserContainer).
- It is dumped through the same redaction as `sched.flight` lines, each with a `dump_id`, when an error-level event of §9.2 fires (`engine.unavailable`, `engine.workspace.poisoned`, `engine.unkillable`, `engine.r4.mismatch`, an `INTERNAL`, a blocked save). The ring is cleared after a dump, so repeated errors dump only new events.

  **Placed in [A], not M2:** the liveness rules (§8.2–§8.5) are new in [A] and depend on exact sample values. Their rare false positives are exactly what the recorder is for, and it is about a hundred lines in `observability`, reusing S0039's redaction and sink.
- **Report a problem** bundling of the dumps waits for M2 (BL-0093).

**R5. A user-log replay tool** [M2, BL-0093]: `scripts/replay_scheduler_log.py` reads a `finzytrack.jsonl` from a Report a problem bundle and prints the timeline, the decisions and any divergence, including `table_version` mismatches against the code at hand. Report a problem includes the decision lines and the flight dumps by default; they are content-free.

**R6. Data-dependent reproduction** [M2+, BL-0060, BL-0093]. When a failure depends on the ledger (a plugin that crashes, a slow load), the decision log alone cannot reproduce it. The route is a consented, anonymized copy through Report a problem (BL-0060's anonymization, BL-0093's bundle).
- The log's `fingerprint`, `engine_build` and `plugin_set` confirm that a *byte-identical* copy matches the failing state.
- An anonymized copy cannot match a fingerprint. The log therefore also carries content-free structure (entry, file and plugin counts, size buckets; ARCHITECTURE §6A.4) to judge similarity.

**R7. Limits, stated:**
- Replay reproduces decisions given the inputs *as observed*. It does not reproduce real thread and signal timing: which of two events arrived first in the sidecar is recorded, not re-derived. Those interleavings are covered by the TLA+ model and the property tests.
- It does not reproduce what the OS did, only what we observed.
- Non-deterministic plugins can make a reproduced load differ.
- A decision taken by code outside the listed tables (an adapter bug) shows up as an unexplained input, not as a divergence.

**Volume.**
- A heavy job costs about 25 lines: enqueue, dispatch, spawn, handshake, about 8 phase changes, about 2 tick runs, gate and proceed for saves, reply, reap, and their decisions. At about 250 bytes each that is about 6 KB.
- A heavy day: one open with a rebuild, 50 saves (a save and an R4 each), 100 merged query batches. That is about 200 jobs, or about 1.2 MB per day, plus checkpoints (about 2 KB each).
- With the default rotation of 10 MB × 5 files (S0039), that keeps about 6 weeks. A dashboard refreshing every few seconds is the main risk; its refusals are counted, and its merged queries cost one batch per refresh.

**What changes in ARCHITECTURE §6A** (applied by the [A] slice):
- §6A.1: a paragraph on the decision lines.
- New §6A.7 "Replayable decisions": R1–R7, the event names, the run-length rule, checkpoints, the flight recorder and its dump triggers, the volume.
- §6A.5: `sched.*` at info, `sched.flight` at info inside a dump.
- §6A.6: the replay test and the "no content in `sched.*`" case in the redaction corpus.
- §6A.4 [M2]: decision lines and flight dumps in the bundle.
- §2.5: the flight recorder row.

## 10. Cross-platform behaviour (ADR-0061)

| Concern | macOS (first) | Linux | Windows |
|---|---|---|---|
| Our end of an engine | `killpg(SIGKILL)` | same | `TerminateJobObject(job, EXIT_OUR_KILL)` |
| Engine and its children end with the sidecar | watchdog (0.25 s), then `killpg(0)` | same (no PDEATHSIG, §6.3) | Job Object kill-on-close, at once |
| Confirmed dead | reaped, and `killpg(pgid, 0)` gives `ESRCH` | same | job active process count 0 |
| OS memory response | jetsam `SIGKILL` (rare for user processes; compression and swap come first in most cases) | OOM killer `SIGKILL`; `memory.events` `oom_kill`; engines at `oom_score_adj` +500 | allocation fails: `MemoryError` or `STATUS_NO_MEMORY` |
| Pressure | `kern.memorystatus_vm_pressure_level` | PSI (cgroup, then system), else `MemAvailable` | low-memory notification, `GlobalMemoryStatusEx`, commit check |
| Per-job peak and CPU | `waitid(WNOWAIT)`, `proc_pid_rusage` footprint, then `wait4` | `wait4` (`ru_maxrss` in kB) | job accounting: peak job memory and total CPU |
| Group CPU while running | sum over the group (psutil) | same | Job Object total CPU |
| Bytes pending in the pipe | `FIONREAD` (verified) | `FIONREAD` | `PeekNamedPipe` |
| Sleep-counting clock | `CLOCK_MONOTONIC` (verified, spike s1) | `CLOCK_BOOTTIME` | `QueryInterruptTime` (verify) |
| Ticks delayed by the OS | App Nap while the window is hidden; the shell holds an activity while jobs run | — | — |
| Lock released after our end of an engine | at the end of the process group | same | may lag `TerminateJobObject` (design review M6; verify): waited for, never read as a live writer (§5.7) |
| Start cost | 0.07–0.10 s, 34 MB (measured) | expected similar (CI from A) | unmeasured: BL-0087 runner [M1b], real machines [M11] |
| Background OS priority | `nice +10` only for P3 jobs that nothing can come to wait on (prefetch, a P3 `snapshot`, vector runs). R4 and background writers start at normal priority (§4.2; review 2 F14). `PRIO_DARWIN_BG` can be set and cleared by the parent (spike s5); whether clearing it restores full speed is measured in the [A] slice before it is used. | `nice +10` for the same jobs | `BELOW_NORMAL_PRIORITY_CLASS` for the same jobs |

The write-path parts are designed against Windows now, and their engine tests run on the Windows runner from M1b (ADR-0061 decision 6, BL-0087): §5.4, §5.5, §5.7, the Job Object of §6.3, and the lock-release lag, tested with a fake lock port that reports "held" for N attempts after the end, plus one real smoke run (review 2 O18). The rest is ported in M11. CI's Linux T1 runs the pure tables and the Linux adapters from [A].

## 11. The TLA+ model

### 11.1 What exists

`EngineScheduler.tla` was written by the TLA+ check of draft 1 (review:m1a/scheduler-design/tla; mapping `tla/MAPPING.md`, configs from `spec/gen_cfgs.py`). It is a separate module from S5's `WriteProtocol.tla`. S5 already checks the per-file protocol (backups, fsync order, swap, power loss, F-1 to F-8), so here a save's file effect is S5's skeleton, one step each.

**What it models:**
- a pool of 2 slots over up to 3 process records, with a fresh process per job;
- the jobs `commit`, `rebuild`, `query`, `r4` and `recover`;
- the per-workspace lane;
- verified snapshot reads;
- conditional publish with the generation id allocated at publish, the high-water mark outside the cache, and a cache purge;
- the two-party commit gate against Cancel;
- our end of an engine for cancel, quit or hang at any step, and a crash or OS memory kill at any step;
- the R4 gate as the persisted record;
- recovery in session and on open, with the closed marker;
- a sidecar crash with orphaned engines, restart, quit and quiesce;
- the legacy v0.3 app writing one or two files under its lock;
- a lock-ignoring editor;
- a second v0.4 instance on the same app-local-data folder;
- the pool's stop history, poison, pressure and single-engine mode.

**The "Fixed" model** is draft 1 plus the fixes `FIX_R4_RECORD`, `FIX_SLOT_AT_REAP`, `FIX_RESOLVE_MARKER`, `FIX_POISON_PER_FP`, `FIX_REFUSE_ON_BLOCK` and `GATE_CHECKS_JOURNAL`. All of these are in this draft (§4.6, §4.9, §5.4, §5.5, §5.7, §8.7). The Fixed model passed every property below; the largest run, `Fixed_restart`, explored 179.6M distinct states in 7,433 s.

### 11.2 Safety properties

Each is checked by the Fixed model in every `Fixed_*` config unless marked. Where the TLA+ check added it, that is noted.

- `OneExclusivePerWorkspace` (§4.1): within one session; across orphans of a crashed sidecar it cannot hold (`OneExclusiveInclOrphans` fails). Safety then rests on the locks (TLA F13).
- `RunningUntilAnsweredOrDead` (§4.6).
- `EnginesBounded` (§4.6): the slot is held until the reap, within one session (TLA F3).
- `PublishOnlyCurrent`, `GenerationsMonotonic`, `PublishedIsCommittedState` (§5.3); also with the lane off (`Fixed_lanesoff`).
- `PublishedNotOlder` (§5.3; added by the TLA+ check).
- `VerifiedReadsAreSnapshots` (§5.2); also with the lane off.
- `GateOnCurrentMirror` (§5.4).
- `NoKillAfterCommitPoint`, `CancelBeforeCommitLeavesDiskUnchanged` (§5.4).
- `R4BeforeNextCommit` (§5.5): the persisted closed record, across a crash, a restart and a second instance (TLA F1, F2).
- `NoWriteAfterUnresolvedDeath` (§4.1 rule 6, §5.7).
- `OwnKillNotAStop` (§6.4).
- `MemoryAdmission` (§7.3): as modelled, heavy jobs only at `Normal` (§11.4).
- `OutcomeTruthful` (§5.7; added): a reported outcome matches what is on disk.
- `EveryWriteEndsKnownSafety` (§5.7; added): no save ends "unknown" (TLA F4).
- `AckedCommitNeverRolledBack` (S5's, kept).
- `BlockedOnlyOnForeignBytes` (S5's, kept; TLA F6).
- `PoolNoHeavyWhileUnavailable` (§8.7; added).
- `OneLedgerCannotTripPool` (§8.7; added; TLA F5).
- `NoTwoWritersMidCommit` (§5.4; S5's `SerializedLockedWriters` across instances; added).
- Plus TLC's deadlock check: `Idle` stutters only when nothing is pending.

### 11.3 Liveness properties

The liveness runs use `LeanFairSpec`: weak fairness per process, strong fairness per job kind (standing in for count-based aging), and fairness of the environment's progress. Configs: `Live_fixed_legacy`, `_pressure`, `_faults`, `_r4block`, `_crash`; all passed.
- `QueuedWriteLeavesQueue` (§4.9; TLA F7) and `RunningWriteEndsOrBlocks` (§5.7; TLA F8). These replace draft 1's `EveryWriteEndsKnown`, which cannot hold when the recovery itself dies.
- `QueriesEnd`.
- `RecoveryResolves`: a recovery resolves, or its workspace is visibly blocked.
- `MirrorEventuallyCurrent`: a queued rebuild ends, or the workspace is poisoned for rebuilds or visibly blocked (TLA F9).
- `R4EventuallyRuns`.
- Deadlock freedom with waiting verifies, saves and the legacy actor.

### 11.4 Where draft 2 deliberately differs from the Fixed model

Each difference is to be modelled in the slice named. Until then, the argument given carries it.

1. **R4's lock pattern** [C]. The model runs R4 as one step under the locks. Draft 2 checks under the locks, loads without them, and checks again under the locks, holding the locks across the load only to confirm a mismatch (§5.5; review 2 F2).
   - Argument: R4's verdict depends only on the content fingerprint at the two locked checks and on the digest. A torn read cannot yield the adopted digest, so `pass` is sound, and every other result either supersedes or re-runs under the locks.
   - Model change in [C]: split `R4Run` into `R4Check1`, `R4Load` and `R4Check2`, plus `R4Confirm`, and add `NoBackgroundLockAcrossUserCode` and `LegacyWriterProgress`.
2. **Queries beside a rebuild** [B]. In the model, any job of a workspace waits while an exclusive job runs. Draft 2 lets shared reads run beside a `rebuild` (§4.1 rule 2; review 2 F8). `Fixed_lanesoff` already shows that the read and publish properties hold with no lane at all. Model change in [B]: drop `rebuild` from the shared-read exclusion.
3. **Pressure levels** [A]. The model has `Normal` and one pressure level, at which no heavy job starts. Draft 2 admits one job alone at `Warn`, nothing at `Critical`, with Run anyway and the recovery exemption (§7.3; owner answers 1, 3, 6). Model change in [A]: three levels, admission over job sizes from {small, large, unmeasured} with a budget that fits two small jobs or one large one, and the head-of-line reservation, with the property `NoStarvationUnderAdmission` and the seeded bug "dispatch past a refused head" (review 2 F1). Under a free admission predicate the starvation of spike s6 is invisible.
4. **Recovery retried once before blocking** [C]. The model blocks at the first death of a recovery, which is stricter. A retry is idempotent (it re-classifies from disk).
5. **`plan`, `preview` and `create`, and the re-plan after our own saves** [C]. These are not modelled (the model's saves plan from the published mirror). Model change in [C]: a `create` in `COLD` (`NoCommitWithoutBaseline`) and the own-save re-plan (`OwnCommitsNeverExternal`).
6. **Per-file locks** [B]. The model has one set lock per workspace. Model change in [B]: two workspaces that share a file, with per-file locks in canonical order (review 2 F11; TLA overflow).
7. **A violation after the commit point is not ended** [C]. The model has no violation event. Model change in [C]: add it, with `NoKillAfterCommitPoint` unchanged.
8. **Quit anyway** [C]. The model's `Close` waits for every engine. Model change in [C]: add `QuitAnyway`, and check `PastCommitPointSurvivesSidecarDeath` and `QuiesceTerminates` (review 2 F13). `Fixed_restart` already checks `OutcomeTruthful` across `SidecarCrash`.

### 11.5 What the model cannot show (from the TLA+ check), and where it is covered instead

| Not shown by the model | Covered by |
|---|---|
| that count-based aging gives starvation freedom (the model assumes strong fairness) | pure-table tests of `next_dispatch` (§12 [A]) |
| merging, preemption limits, memory arithmetic, cheap jobs, the spare engine | pure-table tests |
| that the hang, stall and runaway rules never fire on a healthy engine | `liveness.judge` tests with recorded samples, and review 2's spike s4 workloads as tests |
| Beancount semantics | the corpus and the Hypothesis model (ADR-0050) |
| power loss and fsync order | S5's `WriteProtocol.tla` |
| globs, absent paths, documents roots, same-size saves within one mtime tick | snapshot tests (§12 [B]) |
| the Windows lock-release lag | the fake lock port (§10) |
| unkillable processes | stated cost (§4.6) |
| two installations with different app-local-data folders | unsupported in v0.4.0 (§5.6) |
| a byte-identical revert by an editor | stated (§5.2) |
| larger worlds: the model's bounds are 1–2 saves, 1 query and fault budgets of 1–3 | the T2 and weekly configs below |

Also added by review 2, and checked by tests: `EveryWaitHasAnExit` (every state that can last unboundedly has an enabled user action, or is a save past its commit point) and `LegacyWriterProgress` (until [C] adds it to the model).

### 11.6 Seeded bugs

There are 15, each a CONSTANT flag that must fail its target property in the Fixed model. All took about 1 s; trace lengths are in parentheses.
- `lane_on_kill_sent`: `RunningUntilAnsweredOrDead` (5)
- `unconditional_publish`: `PublishOnlyCurrent` (13)
- `id_at_dispatch`, lanes off: `GenerationsMonotonic` (18)
- `one_sided_gate`: `CancelBeforeCommitLeavesDiskUnchanged` (10)
- `stops_per_engine`: `PoolNoHeavyWhileUnavailable` (8)
- `verify_no_locks`: `VerifiedReadsAreSnapshots` (9)
- `gate_no_mirror`: `GateOnCurrentMirror` (7)
- `r4_not_persisted`: `R4BeforeNextCommit` (18)
- `hw_in_cache`: `GenerationsMonotonic` (9)
- `cancel_after_point`: `NoKillAfterCommitPoint` (9)
- `lane_on_requester_left`: `OneExclusivePerWorkspace` (7)
- `verify_no_journal_check`: `VerifiedReadsAreSnapshots` (15)
- `dispatch_while_unresolved`: `NoWriteAfterUnresolvedDeath` (11)
- `reason_after_kill`: `OwnKillNotAStop` (7)
- `admit_under_pressure`: `MemoryAdmission` (5)

The slices add "dispatch past a refused head" [A] and the R4 and per-file-lock bugs [B, C].

## 12. Test obligations per phase

**[A] the scheduler slice, before the read core.** It amends ADR-0054 decision 4 and ADR-0041, ARCHITECTURE §2.0, §2.5, §6A and §7, and failure-modes E1–E7.

- **The TLA+ model is a test obligation of [A].**
  - Commit `EngineScheduler.tla` under `formal/tla/` with its configs, `gen_cfgs.py`, `MAPPING.md`, and `tools.lock` pinning `tla2tools.jar` 1.7.4 (SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`) and Java 21 (Temurin LTS), as for S5.
  - Extend it with §11.4 item 3 (pressure levels and the reservation).
  - **T1:** `Fixed_poison`, `Fixed_twows`, `Fixed_r4block` and `Fixed_pressure` (each under a minute) and the 15 `BugOnly_*` configs.
  - **T2:** `Fixed_faults`, `Fixed_legacy`, `Fixed_second`, `Fixed_lanesoff` and `Fixed_outside` (6–24 min each), and the five `Live_fixed_*` configs.
  - **Weekly, and at each milestone exit:** `Fixed_restart` (about 2 h) and `Fixed_large`.
  - Every action carries an `@tla_action` marker in code once its code exists. The mapping test accepts actions of phases not yet built only when they are listed in `MAPPING.md` with their phase.
- **Left to pure-table tests** (§11.5): `NoStarvation` and `NoStarvationUnderAdmission` with aging, preemption limits, merging with requesters, memory arithmetic, cheap jobs, `EveryWaitHasAnExit`, and the liveness judge.
- **Per-engine table and pool table:** exhaustive (state × event) tests and random walks; generated references diff-checked.
- **`next_dispatch`:** an exhaustive small-world test against §4.2–§4.5, §4.9 and §7.3, covering aging, preemption limits (cheap jobs never preempt), and merging with requesters (cancelling one of two requesters keeps the job; cancelling both ends it). It also includes **spike s6's workload as a test**: a large or unmeasured job behind a stream of small measured jobs is dispatched once the running ones end.
- **Protocol v2:** schema regenerated; contract tests on both sides; one final reply per request; progress never interleaves inside a line (a progress thread beside a 10 MB reply); a v1 peer is `incompatible`.
- **A fresh engine per heavy job:** the H1 repro (the design review's `plug.py`) as a test, in which workspace B gets B's plugin. Cheap jobs then a heavy job are allowed; anything after a heavy job is refused.
- **Reaping:** the pool alone reaps. A child ended by `SIGKILL` is classified `killed_by_system` or by our reason, never `answered`: a regression test for the `Popen.poll` trap. The measured peak minus the interpreter baseline (measured by the same test) is within 5 % of a known allocation of at least 200 MB (spike s2: 216 MB for 200 MB allocated). The macOS footprint is read from the zombie.
- **End classification:** our `SIGKILL` with a reason; a foreign `SIGKILL`; `MemoryError` (an engine test op raises it); a crash. A crash racing our end is classified by our reason. Only `hang`, `violation`, crashes and start failures enter the stop history, and only once per (workspace, fingerprint).
- **Hang:**
  - a `SIGSTOP`ped engine is hung after `hang_ticks` on-time ticks, also on a busy machine;
  - a GIL-holding regex is **not** hung;
  - a `time.sleep` plugin stand-in is stalled, not hung;
  - an engine waiting on a CPU-bound child is not hung (group CPU);
  - a tick spanning a simulated sleep, or a late wake, does not count;
  - no idle CPU means the tick does not count;
  - a draining engine that never exits is hung.
- **Stall and runaway:** a background job past the bound is stopped and logged; an unmeasured job is never stopped; an interactive job is flagged, not stopped; the floor applies.
- **Admission:**
  - an unmeasured job runs alone, and the budget arithmetic holds;
  - at `Warn` one job runs at a time; at `Critical` nothing starts but `recover` and Run anyway;
  - running jobs are never stopped by admission; `Critical` preempts P3 jobs;
  - single-engine mode after a memory death ends after 20 healthy jobs;
  - `Unknown` falls back to the budget alone.
- **Usual (§1.1):** the decay rule, size scaling, invalidation on engine build or plugin-set change, and an unreadable file read as unmeasured.
- **Pressure port:** the macOS adapter reads the sysctl. The Linux adapter parses PSI and cgroup files from fixtures (CI from [A]). The Windows adapter is tested against fixtures, with real calls in M11.
- **Process groups:** cancelling a job that started a child ends the child; the sidecar's death ends the engine and the child; confirmed death waits for the group; the Linux `oom_score_adj` is set.
- **Bounded faults:** a crashing test op on one workspace poisons that kind at 2, adds one pool stop, and leaves the pool available for another workspace.
- **Settings:** values out of range are refused and logged.
- **Cancel for reads; requester left; quiesce** ends reads.
- **Replay (§9.3):** every scheduler scenario test is replayed from its log with no divergence; the Hypothesis random walk replays exactly; a table changed without its `table_version` is reported; a `seq` gap is reported and the replay resumes at the next checkpoint; the redaction corpus holds a `sched.*` case with hostile values.
- **Flight recorder:** bounded, dumped once per error-level event, redacted.
- **failure-modes.md:** rows E1–E7 rewritten against the new tables (E3 "slow" no longer equals hang; E5 decided by §6.3), with their tests cited.

**[B] with the mirror build:**
- **Lanes:** exclusive jobs exclude each other; shared jobs run beside shared jobs and beside a rebuild; a preview waits for a rebuild; a read waits for a save; a save is refused in WARMING and COLD; reads and rebuilds wait while `RECOVERING`, and no rebuild loop forms.
- **Verified snapshot:**
  - an outside save during the load gives `snapshot_moved` and a retry; three moves give `LEDGER_CHANGING`;
  - a same-size, same-mtime save is caught (content fingerprint);
  - a changed plugin file makes the mirror stale;
  - a journal under the locks gives `orphan_journal` with its owner;
  - the verify waits for a held legacy lock (portalocker 3.2.0) and does not deadlock;
  - two workspaces sharing an include exclude each other on it;
  - a newly matched glob file is locked before it is trusted.
- **Conditional publish:** a build whose disk moved is dropped and a rebuild queued; two rebuilds finishing out of order publish the newer state; ids are monotonic across a sidecar restart and across deleting the cache directory; an unchanged-content stat change re-stamps instead of rebuilding.
- **GC** of the `build-*` files of ended jobs.
- **Model:** the §11.4 items 2 and 6 extensions, with their seeded bugs.
- **ADR-0015's 38,800-reads acceptance test**, re-run with two engines building.

**[C] M1b, with the write contract and BL-0091:**
- **The commit gate:** a cancel before `proceed` leaves the disk byte-identical, fault-injected at every step up to the gate; a cancel after `proceed` returns "Finishing your change…" and the save lands; a property-based race of cancel against the gate message.
- **The gate's four checks** each refuse on their own: the disk, the mirror, an open journal, a closed record. The own-save re-plan proceeds on the same intent and ends `PLAN_OUTDATED` on a moved entry.
- **`create` in COLD**, including adoption and migration baselines.
- **The closed record:** journal close is one rename; a save is "saved" exactly when the record exists; R4 is re-armed after recovery and after a restart; a second instance's gate refuses while our record exists.
- **R4:** pass without locks across the load; superseded on an outside save and on a legacy write; confirm under the locks; mismatch blocks, publishes the `load_file` result and logs at error; a nondeterministic plugin stand-in shows the notice and does not block; Cancel leaves saving paused; R4 never touches `.picklecache`; the engine-changed path; normal OS priority.
- **Fault injection** (acceptance harness, BL-0091): the engine ended and `SIGSTOP`ped at each step after `proceed`; `resolve` gives the right one of the outcomes; an editor save between the gate and the journal gives "not saved, changed by another program"; in-session recovery for our journal; recovery on open for an earlier session's; Q30 for another instance's.
- **Recovery** that dies is retried once, then blocks visibly; queued saves are refused on a block.
- **Edge cases:** a `MemoryError` after the commit point resolves correctly; a protocol violation after the commit point is not ended; quit after `quit_patience` offers Quit anyway, and the next open resolves the save.
- **Windows runner** (BL-0087): the Job Object end; the lock-release lag through the fake port and one real smoke run; `STATUS_NO_MEMORY` classification; start cost.
- **Model:** the §11.4 items 1, 4, 5, 7 and 8 extensions; `RunningWriteEndsOrBlocks` and `QueuedWriteLeavesQueue` stay green.

**[M2]:**
- BL-0092: the machines and messages of §9.1, with model-based tests;
- the job list routes;
- BL-0094's fresh status, read from the pool table;
- BL-0093: the replay tool, and the Report a problem bundling of decision lines and flight dumps.

**[M11]:**
- the Linux and Windows adapters on real machines;
- the Windows sleep clock and start cost;
- the spare-engine default per OS.

## 13. Where this departs from the reviews, and why

**From the first design review and the area review (draft 1, kept):**
1. **Safety rests on locks and content, not on the lane.** H2 made the lane primary. The lane alone cannot exclude a second process, and a bug in it would corrupt the mirror. `Fixed_lanesoff` and the consequence run (74.2M states, the lane released when the kill is sent) show the locks and fingerprints carry file and mirror safety.
2. **Verification under the locks with content hashes,** instead of "fingerprint before and after" (H3). Before and after without locks cannot exclude a change and change-back between the reads of two files. The seeded bug `verify_no_locks` fails in 9 steps.
3. **Locks held only for the verify, waiting rather than non-blocking** (ADR-0012 F-6's wording). Holding them for a 2 s load would stall every other writer, the v0.3 app included. Non-blocking attempts would turn each legacy write into a failed rebuild that could only be retried by polling. ADR-0012's amendment restates F-6 this way.
4. **The generation id is allocated at publish, under the locks.** The seeded bug `id_at_dispatch` fails in 18 steps.
5. **A commit point agreed by both sides,** instead of "the engine reports `committing`" (H5). The seeded bug `one_sided_gate` fails in 10 steps.
6. **Liveness judged from outside the engine,** over the whole process group (H4 asked for beats carrying CPU). A beat cannot be sent while C code holds the GIL.
7. **The exact per-job peak from the reaped process** (H4 and M2 suggested heartbeat-sampled RSS).
8. **No cheap lane beyond the cap** (area review F1 suggested a never-waiting lane). The owner's cap counts every engine; frequent facts move into the handshake and the mirror.
9. **The save's legacy-lock wait has no deadline** (ADR-0005 says "at least 30 s"). Review 2 confirmed this is fine: the save holds no other lock while it waits.

**From review 2. Adopted:** F1–F5 and F7–F15; O1–O18.

**From review 2. Not adopted, or adopted with a change:**
- **F6, poison keyed on (workspace, fingerprint) across kinds: not adopted.** Poison stays keyed per (workspace, kind, fingerprint), and the pool bound becomes "at most one stop per (workspace, content fingerprint) until it changes or Try again" (§8.7).
  - Evidence: the TLA+ check found a 17-step counterexample to the across-kinds counter alone (TLA F5; run `Minus_fix_poison_per_fp`, `OneLedgerCannotTripPool` violated). A healthy end of one kind resets a shared counter between faults of another kind, so the pool still collects three stops from one ledger.
  - What bounds the pool is the per-(workspace, fingerprint) stop cap, which review 2 also proposed. Keeping the counter per kind then stops a crashing query from also refusing the workspace's rebuild.
- **F6's "first fault per (workspace, fingerprint) within `window`": adopted without the window.** The workspace's mark stays until the fingerprint changes or Try again, as in the Fixed model. A window would let one unchanged bad ledger add a stop every 300 s for ever. Combined with two other faulty states, that is a slow but endless trip of the pool.
- **F2: R4 "never started below normal priority": adopted, and extended to background writers** (§10), because a save may also wait on a P3 price-fetch save in the lane.
- **F4: the `plan` "may reuse a clean engine and leaves it clean": adopted, with one addition.** A `plan` followed by its `preview` in the same engine ends that engine, because the preview runs user code (§6.1).

**From the TLA+ check:** all of F1–F15 and the overflow are adopted. Where draft 2 behaves differently from the Fixed model, §11.4 lists it with its argument and the slice that models it.

**Departures from the owner's numbers:**
- The runaway floor of 10 s of CPU makes the effective factor larger than "about 10×" for jobs under about 1.1 s of CPU (review 2 O15). It only prevents stopping tiny jobs.
- Owner decision 2 is amended by the owner's answer 6 (§7.3); that is a change made by the owner, not a departure.

## 14. What changes elsewhere (in the slices that build each phase)

- **ADR-0054 decision 4** [A]: "the sidecar's `EnginePool` owns the engines (a fresh one per heavy job, at most `max_engines`) and memory admission; per-user `JobQueue`s own the lanes and the order". Consequences: 34 MB per engine, not 40–80 MB; protocol v2 (§8.1).
- **ADR-0041** [A]: "the only process-wide entry is the heavy-load semaphore" becomes "the engine pool" (review 2 O7).
- **ADR-0011** [B]: the per-workspace limiter becomes the exclusive lane; the heavy-load semaphore becomes the pool; the BQL entries cache is withdrawn.
- **ADR-0008** [C]:
  - R4 completes before the next write, **or is superseded** when the disk moved after the save, through an outside editor or the legacy app, or the engine build changed (TLA F10).
  - The gate refuses while a journal or a closed record exists.
  - R4's confirm step and its `nondeterministic` outcome (owner answer 4).
  - A `create`'s baseline for adoption and migrations costs one extra load.
- **ADR-0012** [B, C]:
  - F-6 restated (§13 item 3).
  - The journal records `(session id, job id)`; journal close is a rename into the closed record.
  - Recovery **on open waits for the locks** instead of skipping when they are held (TLA F15).
  - In-session recovery, retried once (BL-0091).
  - Q30 applies only to another running instance's journal.
  - The lock set is per realpath (Q29 restated).
- **ADR-0010** [C]: guardrail 3's mismatch publishes the `load_file` result (already "adopts"); a nondeterministic plugin does not block.
- **ADR-0005** [C]: the save's wait without a deadline (§5.4), if the owner reads "at least 30 s" as requiring one.
- **ADR-0015** [B]: generation ids allocated at publish, with the high-water outside the cache; both fingerprints in the meta, plugin code included; re-stamping on unchanged content; date-change triggers in both directions.
- **ARCHITECTURE:**
  - §2.0: protocol v2 and the supervision text.
  - §2.5: the pool as the process entry; `JobQueue`, `Measurements`, `DecisionLog` and `FlightRecorder` in `UserContainer`; `EngineClient` per engine; the BQL cache row removed.
  - §5: the engine's own process group and Job Object; the end of the shell's group kill reaching engines.
  - §6A: §9.3's list.
  - §7: two statecharts instead of one, plus the liveness judge.
  - §8.3: the new module.
  - §9.1, §9.2, §9.6: COLD and `create`.
- **failure-modes.md** [A]: rows E1–E7 (E5's open rule decided by §6.3).
- **state_registry** [A]: the `process` entry; `EngineBoot` removed (area review F10); the flight recorder.
- **Licence lists** [A]: `portalocker` 3.2.0 in the engine's; `psutil` in the main sidecar's.
- **User docs** [M2]: two installations are not supported; files a plugin reads itself; plugins running twice at once; Rebuild now.
- **Backlog:**
  - BL-0091 gains the root-file lock decision (§5.6) and §5.7's resolve.
  - BL-0082 measures `decode_factor`.
  - BL-0087 measures the Windows start cost.
  - BL-0093 gains §9.3's R5–R6 and the bundling.

## 15. Owner questions

None open. The owner answered all six of review 2's questions on 2026-10-26: Run anyway; stalled background jobs; recovery under pressure; non-deterministic plugins; two installations; one job at a time at warning. This draft's own two questions were the same as the first two. "What is 'usual'?" is answered in §1.1. The replayable-logging requirement is in §9.3.

One point the owner may want to confirm, without blocking anything: reading ADR-0005's "at least 30 s" legacy-lock wait as "no deadline, with Cancel" (§5.4 step 1). *Recommendation: confirm. A guessed deadline would turn a slow legacy re-export into a failed save.*
