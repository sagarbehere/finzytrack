# Engine scheduler: concurrency, admission, liveness and cancel

> **DRAFT (2026-10-26), not yet adopted.** Committed by a later slice together with the ADR-0054 amendment (decision 4) and the ARCHITECTURE §2.5 rows it changes (§14). Until then the accepted ADRs win where they differ.

## Orientation
- **What this is:** how the main sidecar runs ledger-engine work: which jobs exist, which may run together, in which order, how many engine processes, how much memory, how a slow job is told from a sick engine, what Cancel does, and what the user sees. It answers the area review's F1 and F2 (2026-10-26) and applies the owner's five decisions and sequencing of 2026-10-26 (`design/inbox/20261026-engine-concurrency-and-cancel.md`), after the independent design review (review:m1a/design-review-concurrency H1–H6, M1–M7, L1–L5).
- **What exists:** nothing of this yet. Built today: one `EngineClient` per user container, one request in flight, a caller's time limit that counts as a crash (S0035, S0040, S0042). Every part below carries the phase that builds it: **[A]** M1a, the scheduler slice before the read core; **[B]** M1a, with the mirror build; **[C]** M1b, with the write contract and BL-0091; **[M2]** with BL-0092/BL-0094; **[M11]** the Linux and Windows sessions (ADR-0061); **[Hosted]** deferred (ADR-0041).
- **Entry points (planned):** `app.platform.engine_pool` (process-wide pool, budget, pressure, stop history; pure table `engine_pool.table`), `app.platform.engine_process` (one engine's statechart, replacing `engine_supervision`), `app.platform.adapters.engine_client` (one process's shell), `app.platform.job_queue` (per-user lanes and queues, pure core), `app.platform.ports.memory_pressure`, `app.platform.ports.process_probe`; engine side `ledger_engine.protocol` (protocol v2) and `ledger_engine.ledger.snapshot` (verified reads, conditional publish).
- **Decisions it applies:** ADR-0054 (two processes; decision 4 amended here), ADR-0041 (one process-wide entry), ADR-0008 (contract, R4 amendment), ADR-0010 (overlay load, guardrail 3), ADR-0011 (no resident ledger, write serialization), ADR-0012 (journal, Q29, Q30, F-1..F-8), ADR-0013 (swap), ADR-0015 (atomic publish, WARMING), ADR-0005 (legacy lock), ADR-0061 (platforms).
- **Invariants:** INV-01, INV-10, INV-11, INV-22, INV-26, INV-27, INV-30, INV-31.
- **Traps:** a heartbeat proves nothing (GIL-holding C code stops it; a sleeping plugin keeps it going): liveness is judged from outside (§8). The lane is not what keeps the mirror correct; the ledger-set locks and the content fingerprint are (§5). An engine that ran a ledger load is never reused (H1). `SIGKILL` from us and from the OS look the same unless we record ours first (§6.4). "Running" ends at the final reply or at confirmed death, never at "kill sent" (§4.6).
- **Main tests (planned):** per phase in §12; the TLA+ module `formal/tla/EngineScheduler.tla` with the properties of §11.

## In two minutes

Today Finzytrack sends the ledger engine one request at a time, and the only way to stop a request is a time limit that counts as a crash. That does not work for the jobs that are coming: loading a 54,000-entry ledger takes about 2 seconds of full CPU, and several such jobs will want to run at once.

**What changes:**
- **Up to 2 engine processes** run ledger jobs at the same time (a setting). Each heavy job gets a **fresh engine process** that ends when the job ends. Starting one costs about 0.1 s and 34 MB on your Mac. This also stops one ledger's plugin from leaking into another ledger's job.
- **A queue in the main sidecar** decides what runs next. Jobs you are waiting for go first; background checks go last but are never starved. Jobs that would load the same ledger for the same question share one load.
- **Two jobs that change the same workspace never run at the same time.** Reads can run beside a save, but a read is only used if the files did not change while it was reading; otherwise it is quietly redone.
- **Memory:** ledger work may use up to a quarter of your RAM (a setting). Finzytrack learns how much each kind of job needs for each ledger, from the jobs themselves (no measuring run at startup, so startup is not slower). A job it has never measured runs alone. No new job starts while your computer reports memory pressure.
- **Cancel** stops a read at once. A save can be cancelled until it starts writing files; after that Cancel shows "Finishing your change…" and the save completes.
- **A slow job is not a crash.** The sidecar watches the engine from outside: is it using CPU, is it sending anything. Only an engine that does neither, while the computer is awake and not overloaded, counts as hung. A long job that keeps working is never cut off by a guessed time limit. Background jobs that use far more CPU than usual for this ledger (about 10×) are stopped automatically and logged; the first run of a job is only stopped by hand.
- **Crash loops are bounded for the whole pool**, and a ledger that makes the engine fail twice in a row is not retried until it changes or you press Try again.

**What it costs:** about 0.1 s per heavy job for the fresh process; a little more memory when two jobs run; and a fair amount of design, most of which is built later (marked below). **What stays imperfect:** a program outside Finzytrack that saves your ledger without taking our locks can only be detected, not excluded (as today, ADR-0013); a process stuck on a dead network disk cannot be ended by anyone and keeps its slot until it ends (§5.8).

## 1. Terms

- **Job:** one unit of engine work for one workspace (or none, for cheap jobs), with a kind (§3), a priority, its requesters and a state: `queued → admitted → running → ended(outcome)`.
- **Heavy job:** reads ledger files or runs user code (plugins, `insert_pythonpath`). **Cheap job:** neither (static facts, parsing a small text given in the request).
- **Lane:** per workspace. **Exclusive** jobs (rebuild, commit, recover) run one at a time per workspace; **shared** jobs (reads) may run beside each other.
- **Engine:** one engine process. **Slot:** permission to have one engine. `max_engines` (default 2) bounds all engines, cheap ones included.
- **stat fingerprint:** ADR-0015's extended fingerprint (root path; `(path, mtime, size)` of every glob-expanded include; each glob with a hash of its match list; expected-but-absent paths; a listing hash per documents root). **content fingerprint:** the same set with each file's SHA-256 in place of `(mtime, size)`. About 10 ms at 54k (spike S4, `fp0`). `ledger_version` = (generation id, hash of the content fingerprint).
- **Commit point:** the moment in a commit after which the engine is never ended by our choice (§5.4).
- **On-time tick:** a watcher tick that ran on schedule with the machine awake (§8.2).
- **Settings** (all INV-10-style configuration, sized per deployment, never development targets): listed in §8.7's table.

## 2. Components and where they live

| Part | Lives in | Holds | Bound |
|---|---|---|---|
| `EnginePool` [A] | process-wide: the **one** `process` entry of `platform/state_registry.py`, replacing the heavy-load semaphore (ADR-0041, ADR-0054 d4 as amended) | the engine processes, the memory budget, the pressure reading, the pool's stop history, the pool table's state | `max_engines` engines; at most `max_crashes` stop times; one pressure value |
| `JobQueue` [A], lanes [B] | `UserContainer` (INV-11: limiters and job queues hang off it) | per workspace: queued jobs, the running set, lane state, the R4 gate, poison counters, recent outcomes | queue bounds of §4.5; outcomes: last 16 per workspace |
| `Measurements` [A] | `UserContainer`; persisted in `app_local_data/engine/measurements.json` (never the workspace folder, INV-27, INV-30) | per (workspace, job kind): peak bytes, CPU ms, reply bytes, longest healthy silent and stalled streaks, ledger byte size at measurement, engine version, plugin-set hash | one record per (registered workspace, kind); dropped when the workspace is forgotten |
| `MemoryPressurePort` [A] | platform port (EP-07), main sidecar | none (reads the OS) | — |
| `ProcessProbe` [A] | platform port; adapter on `psutil` (BSD-3-Clause, GPLv3-compatible; pinned per EP-24) | none (reads a child's CPU time, RSS, and system idle CPU) | — |

The pool pulls work: when a slot and memory are free, it asks each user's `JobQueue` for its best dispatchable job (§4.7) and takes the best across users, round-robin among equal priorities (fairness across users matters only in hosting [Hosted]; the seam is there now). The pool never sees ledger semantics; the queue never sees processes. `EngineClient` stops being a user-container service: each running engine is an `EngineClient` owned by the pool for that engine's life.

**Superseded:** ADR-0011's per-workspace `anyio.CapacityLimiter(1)` becomes the workspace's exclusive lane [B]; its FIFO guarantee is kept (§4.2). ARCHITECTURE §2.5's "optional BQL entries cache" row is dropped (area review F8: entries never cross the boundary, and a cache in the engine would be a resident ledger).

## 3. Job kinds

**Reconciliation** in this spec means R4, ADR-0010's guardrail 3 (a real `load_file` compared with the adopted result after a commit). Statement reconciliation (ADR-0028) reads the mirror and is no engine job.

### 3.1 Scheduling attributes

| Kind | Phase | Heavy | Lane | Priority (§4.2) | Preemptible | Cancellable: when, by whom | Merge (§4.4) | Queue bound per workspace |
|---|---|---|---|---|---|---|---|---|
| `rebuild` (WARMING / stale mirror, cold first build, after an outside edit, date change; also a write's mirror when its own build did not publish) | B | yes | exclusive | P1 if the workspace is viewed; not run otherwise (ADR-0015) | no | any time; by the system only: workspace closed or switched away, quit | newest only: 1 queued + 1 running | 1 (never "busy": merged) |
| `commit` (confirm: locks, fingerprint check, step-3 re-run, journal, replace, read-back, journal close, mirror build, publish) | C | yes | exclusive | P1 (user save, import, AI confirm); P3 for background writers (price fetch) | no | **before the commit point** only: the user (Cancel), quit; never after (owner decision 3) | never | 4 queued |
| `r4` (guardrail 3 after a commit) | C | yes | shared, and gates the next commit (§5.5) | P3; inherits a waiting commit's priority | only while no commit waits on it, at most `preempt_max` times | by the system only: superseded by an outside edit (§5.5) | one per commit | 1 |
| `preview` (contract steps 1–3 for a plan: parser parse, semantic diff, overlay load of the candidate) | C (first use) | yes | shared; waits for a running rebuild of its workspace | P1 | no (P1) | any time: the user, the requester leaving, a newer preview of the same edit session, quit | newest only per edit session | 4 |
| `query` (user or AI BQL; recipe `bql` steps) | A (op arrives with BQL) | yes | shared | P1 if a requester waits on screen; P2 otherwise | P2 only, by P0/P1 | any time: each requester (the job ends when none remains), workspace closed, quit | with queued `query` jobs of the same (workspace, `ledger_version`, `LoadPolicy`) into one batch of at most 16 statements | 4 batches |
| `snapshot` (other full loads: the adoption copy "loads the same" check, export-style reads, vector runs) | A | yes | shared | P1 or P3 as the caller says | if P3 | any time: the requester, quit | same as `query` when the result is a pure function of the load | 4 |
| `cheap` (static facts; parse a small text from the request) | A | no | none | P1 | no | any time: the requester | identical requests merge | 16 |
| `recover` (journal recovery: on open, or in session after an engine died past its commit point) | on open: C (M1b writer); in session: C (BL-0091) | no load; file I/O under locks | exclusive | P0 | no | never (only quit, which leaves the journal for the next open) | one per workspace | 1 |

Global bound: at most 64 queued jobs across all workspaces of a user (a setting). Facts that the read core needs often do not become `cheap` jobs: static ones (account grammar pattern, tolerance defaults, Beancount version) come in the handshake reply and are cached per engine build; ledger-derived ones (Ledger Profile, options, plugin list) are written into the mirror's meta by the build (EP-30: remove the waiting instead of scheduling around it).

### 3.2 Failure outcomes per kind

"Stop" = counted in the pool's stop history (§8.7). "Poison" = counted in the workspace's poison counter for (kind, content fingerprint). Every row is logged (§9.2).

| Kind | Cancel | Engine crash | OS memory kill | Out-of-memory error (`MemoryError`; Windows' usual case) | Hang (§8.3) | Stalled / runaway (§8.4, §8.5; background only) |
|---|---|---|---|---|---|---|
| `rebuild` | killed; unpublished build file left for GC (§5.3); no stop | stop + poison; retried once; second fault: workspace stays WARMING with the failure message | poison; pool to one engine (§7.5); retried once alone | as memory kill | killed; stop + poison; retried once | n/a (rebuilds run only at P1) |
| `commit` before the commit point | killed; locks released by the OS; disk byte-identical, no journal (S9); "not saved, unchanged" | stop + poison; disk unchanged; not retried (the user decides) | poison; one engine; not retried | as memory kill | killed; stop + poison; not retried | P3 writers only: stopped (own kill), logged; "not saved, unchanged" |
| `commit` after the commit point | not killed: "Finishing your change…" | stop; outcome resolved (§5.7); [C] in-session recovery; before [C] no writes exist | as crash; then one engine | engine reports `engine.out_of_memory` and exits; outcome resolved (§5.7) | killed only on hang; then as crash | never stopped |
| `r4` | (system only) dropped as superseded; logged | stop + poison; retried once; then writes stay blocked, fail closed (INV-22) with **Try again** | poison; one engine; retried once alone; then blocked as crash | as memory kill | killed; as crash | stopped (own kill); writes stay blocked until **Run check now** runs it at P1 without an automatic stop |
| `preview` | killed | stop + poison; reported; Try again | poison; one engine; reported | as memory kill | killed; stop + poison; reported | n/a (P1) |
| `query`, `snapshot` | killed when no requester remains | stop + poison; retried once (reads are idempotent); then reported | poison; one engine; retried once alone; then reported | as memory kill | killed; stop + poison; retried once | P3 `snapshot` only: stopped and reported |
| `cheap` | killed | stop; retried once | (unlikely) as crash | as crash | killed; stop | n/a |
| `recover` | n/a | stop; workspace stays `RECOVERING`, writes blocked, "reopen the workspace" (ADR-0012 Q30) | as crash | as crash | killed; as crash | n/a |

"Retried once" means once per (job, content fingerprint), only while the pool is not `Unavailable` and the workspace not poisoned for that key; the retry waits out the pool's backoff (§8.7).

## 4. The scheduler

### 4.1 Lanes [B]

Per workspace:
1. At most one exclusive job is running (property `OneExclusivePerWorkspace`).
2. A shared job is not dispatched while an exclusive job of its workspace is running; a running shared job is not stopped when an exclusive one starts (its verification decides, §5.2).
3. An exclusive job does not wait for running shared jobs, except a `commit`, which waits for the `r4` of the previous commit (§5.5).
4. A `preview` waits for a running or queued `rebuild` of its workspace, because its baseline errors come from the mirror (H2): it is dispatched only when the published mirror's fingerprint is the one it plans on.
5. While the workspace is `WARMING`, a `commit` is refused at once (`WORKSPACE_NOT_READY {phase: WARMING}`, ADR-0015), never queued.
6. While the workspace is `RECOVERING` or has an unresolved engine death past a commit point, only `recover` and shared reads are dispatched (property `NoWriteAfterUnresolvedDeath`).

Lane exclusivity is an ordering and resource rule. It is **not** what keeps the mirror or the reads correct: those hold even if two exclusive jobs ran at once in different processes (a second installation, a bug), because they rest on the ledger-set locks and the content fingerprint (§5). The TLA+ model checks the §5 properties with the lane removed (§11.3).

Before [B] lands, the scheduler refuses every exclusive kind (none exists in [A]: no operation of [A] writes a generation or a ledger file).

### 4.2 Priority, inheritance and aging [A], inheritance through lanes [B], through the R4 gate [C]

Classes, highest first: **P0** recovery; **P1** something the user is waiting for on screen in the viewed workspace (a save, a preview, a query, the viewed workspace's rebuild, cheap jobs); **P2** interactive work nobody is watching right now (a query whose dashboard is hidden, an AI tool call); **P3** background (R4, background writers, prefetch).

- **Order:** by effective class, then by enqueue order (FIFO). Exclusive jobs of one workspace keep FIFO among themselves regardless of class, so commits of a workspace commit in the order they were confirmed (ADR-0011's guarantee).
- **Inheritance:** a job that another job waits for (lane rule 3 or 4, the R4 gate) runs at the higher of the two classes, transitively. The effective class is recomputed at each scheduler event (pure function of the queue).
- **Aging by count, not by time:** each time a queued job is passed over by a job that was enqueued after it, its `passed` counter increases; at `aging_passes` (default 8) it moves up one class and the counter resets. So no job waits for ever behind a stream of later arrivals (property `NoStarvation`), and no clock is involved.

### 4.3 Preemption [A]

A queued P0 or P1 job that cannot be dispatched (no slot, or memory admission refuses it) may preempt **one** running job that is preemptible: read-only (`query` at P2, `snapshot` at P3, `r4` while no commit waits on it), and preempted fewer than `preempt_max` (default 2) times. Preemption is our kill (reason `preempt`, no stop); the job goes back to the front of its class with its requesters and its merge key. A job preempted `preempt_max` times is no longer preemptible. Under critical memory pressure (§7.4) the pool preempts preemptible P3 jobs even when nothing waits.

### 4.4 Merging and newest-only [A] for queries, [B] for rebuilds

- Merging happens **only among queued jobs**: a running engine's load is never shared with a later arrival, because handing more questions to an engine that holds a parsed ledger would make that ledger resident (INV-10, ADR-0011). A batch is fixed at dispatch.
- A merged job has a set of requesters; cancelling removes one; the job is killed only when the set is empty.
- Each statement of a batch gets its own result or its own refusal (`engine.result_too_large` per statement, area review F6); one statement's refusal does not fail the others.
- **Rebuild, newest only:** one queued rebuild per workspace. A trigger while one is queued is absorbed. A trigger while one runs leaves it running (its verification decides) and keeps one queued rerun.
- **Preview, newest only:** a new preview from the same edit session (the edit buffer's or the import session's id) replaces the queued one and cancels a running one.

### 4.5 Queue bounds and "busy" [A]

Bounds of §3.1 and the global 64. A request over a bound is refused at once with `BUSY_HEAVY_LOAD` (ARCHITECTURE §6.1, `Unavailable`, retryable) and nothing is queued. Merged kinds (rebuild, R4, recover) are never busy. A busy refusal is not logged per request at info (one `engine.queue.busy` per workspace and kind until the queue drains, so a dashboard cannot flood the log).

### 4.6 What "running" means [A]

A job holds its lane, its slot and its memory reservation from dispatch until **either** the engine's final reply was received **or** the engine process is confirmed dead (POSIX `waitpid`/`wait4` returned; Windows the process handle signalled). A kill sent is not an end (property `RunningUntilAnsweredOrDead`). The HTTP handler that waits for a job is not the job: a handler cancelled by a client disconnect is a `RequesterLeft` event, which cancels a read with no requester left and never a commit (H2's note that ADR-0011's anyio limiter is released on handler cancel). A commit's outcome is kept in the workspace's recent outcomes so a reloaded window can ask whether its save landed (BL-0092).

Cost: an engine that cannot be ended (a process in uninterruptible sleep on a dead network disk ignores `SIGKILL` until the I/O returns) keeps its slot and its lane until it ends. Nothing can prove such a process will not still write, so nothing else may take its place. The user sees §9.1's "cannot be stopped" message.

### 4.7 Dispatch, as a pure function [A]

`next_dispatch(pool_view, queues) -> Dispatch | Preempt | Wait(reason)`, where `pool_view` = free slots, budget headroom, pressure level, `single_engine` flag, `Unavailable`/`Incompatible`; and each queue offers its candidates in order. A candidate is **dispatchable** when: its lane rules allow it (§4.1); the pool is not `Unavailable` (heavy jobs) or `Incompatible` (all); its workspace is not poisoned for its key; the backoff after the last stop has passed (§8.7); and admission allows it (§7.3). `Wait(reason)` carries the reason the user is shown (`queued`, `waiting_for_memory`, `engine_unavailable`, `backoff`). Every scheduler event (enqueue, end, cancel, requester left, pressure change, tick) re-runs it. It is a pure table in the EP-17 sense, with an exhaustive test and random walks.

### 4.8 Quit and quiesce [A] for reads, [C] for commits

`POST /api/app/quiesce` (failure-modes M7): stop dispatching; cancel queued jobs; kill running reads, previews and commits before their commit point (the save reports "not saved, unchanged"; the window's unsaved guard asked first, W7); wait for commits past their commit point and for `recover` ("Finishing your last change…"); then close the pool. A pending R4 is recorded, not run (§5.5).

## 5. Consistency [B] unless marked

### 5.1 The two fingerprints

The **stat fingerprint** decides freshness cheaply (the focus-time version check, §9.1 of ARCHITECTURE; opening). The **content fingerprint** decides correctness: every verification, every publish condition and the commit's step-4 check (ADR-0008) compare content fingerprints. A stat fingerprint can miss a same-size save within one mtime tick; a content fingerprint cannot. The mirror's meta `fingerprint` is the content fingerprint of the state it was built from.

### 5.2 Verified snapshot reads (H3; ADR-0012 F-6)

Every heavy job that loads the ledger from disk returns a result only if the bytes it parsed were, at one instant, the whole ledger set with no write in progress:
1. **Read:** the engine reads each file once, as bytes, records `(realpath, size, sha256)`, and parses those bytes (the overlay loader already parses bytes, spike S4). It records each glob's match list, each absent include, and a listing hash of each documents root taken at the start. The load runs **without locks**, so no other writer waits for seconds.
2. **Verify:** at the end, the engine takes the locks in canonical order (every recorded legacy lock, then the ledger-set lock, ADR-0005, ADR-0012 Q29), waiting if they are held (phase `waiting_for_lock`, §8.1); fsyncs the journal directory (F-8); checks that no write journal of this workspace exists; recomputes the content fingerprint of the same set (re-expanding globs, re-checking absent paths and documents listings) and compares it with what it parsed. Equal and no journal: **verified at instant T** (inside the lock hold). Otherwise the outcome is `snapshot_moved {cause: changed}` or `orphan_journal`.
3. **Release** the locks (a publishing job first publishes, §5.3).

Holding the locks for the verification only (milliseconds; about 10 ms at 54k) instead of for the whole load is a departure from F-6's wording (§13): F-6's purpose, never building from a half-applied commit, is kept, because a lock-respecting writer holds the locks for its whole commit, so the verify either waits for it or sees no journal and its finished bytes. Waiting (not non-blocking) at the verify cannot deadlock: the verifier holds no other lock and takes them in canonical order; the TLA+ liveness config checks it with the legacy actor (§11).

- `snapshot_moved` caused by one of our own exclusive jobs of the workspace that ran during the read: the job is re-queued behind it, and the retry counter does not move.
- Otherwise (an outside writer): re-queued, at most `snapshot_retries` (default 3) times in a row; then the job ends with `LEDGER_CHANGING` ("being changed by another program", §9.1); a rebuild ends in WARMING with that message and is retried at the next focus check or Try again (condition-based, no timer).
- `orphan_journal`: the verifier holds the locks, so no live lock-respecting writer owns that journal; the workspace goes `RECOVERING` (ADR-0012; Q30 for a journal of another session; [C] in-session recovery for one of ours, §5.7).
- **R4 is the exception** (§5.5): it must use Beancount's own file reading, which our reader cannot record, so it holds the locks for its whole load.

What this guarantees: against every lock-respecting writer (our commits, a second instance on the same app-local-data folder, the legacy app), a verified result is a committed state (property `VerifiedReadsAreSnapshots`). Against a program that ignores our locks (an editor, Fava, a sync client), it guarantees the disk equalled what was parsed at instant T, which is what reloading the file in that editor gives, and no more (ADR-0013's accepted race).

### 5.3 Conditional publish and generation ids (H2)

A generation is published only by the engine that built it, inside the lock hold of §5.2 step 2 (a rebuild) or of its commit (a commit), and only when:
- the disk's content fingerprint under those locks equals the build's fingerprint, and no journal is open after the journal-directory fsync (property `PublishOnlyCurrent`); and
- its generation id is greater than the current published id.

**The id is allocated at publish, under the locks:** `id = 1 + max(current published id, the high-water id recorded in app_local_data/engine/generations/<ws key>, the highest id among generation files present)`; the high-water is written (atomic replace, fsync) before the pointer swap. Because verification and allocation happen under the same locks, id order is the order of the verified instants, so the newest state wins whatever order builds finished in (property `GenerationsMonotonic`). Keeping the high-water outside the cache directory keeps ids monotonic when the OS purges the cache (ADR-0015 says "monotonic across restarts"; a cache purge would otherwise restart at 1).

Builds write `build-<job id>.db`; publish writes `ledger_version` into the new file's meta, renames it `gen-<id>.db` and swaps the pointer (symlink; pointer file on Windows, ADR-0015). A build that fails its condition is dropped, logged `mirror.publish.refused_stale`, and the workspace gets one queued rebuild (newest only). GC deletes `build-*` files whose job is no longer running (review L4); published generations keep ADR-0015's grace period.

Why not a compare-and-swap on the base generation (review H2's option): it would accept a build whose base is current but whose disk state moved after the build read it (an outside save mid-build), and it would refuse a newer commit's build when an older-state rebuild published first. Disk equality under the locks has neither hole.

### 5.4 The write path with the scheduler [C]

A `commit` job runs ARCHITECTURE §9.2 step 4 in one fresh engine:
1. Take the legacy locks, then the ledger-set lock. A wait shows phase `waiting_for_lock` with **Cancel**; the wait has no deadline (ADR-0005 asks for "at least 30 s"; an unbounded wait with a visible status and Cancel meets it and needs no guessed number).
2. **Gate:** the disk's content fingerprint equals the plan's, **and** the published mirror's fingerprint equals the plan's (H2: the baseline error keys of step 3 come from that mirror's `ledger_errors`; a stale mirror could accept a reintroduced error). Otherwise `LEDGER_EXTERNALLY_CHANGED` or `WORKSPACE_NOT_READY {WARMING}` (property `GateOnCurrentMirror`).
3. Re-run step 3 (the commit's one full load), compare error keys.
4. **Commit point, two-party:** the engine sends `gate {id, point: "commit"}` and waits on its input. The sidecar, in one step of its table: if a cancel is pending, it kills the engine (the commit is cancelled; disk unchanged); else it marks the job **past the commit point** and then sends `proceed {id}`. The engine opens the journal only after reading `proceed`. So the sidecar can never kill an engine that may have touched a file for a cancel (properties `NoKillAfterCommitPoint`, `CancelBeforeCommitLeavesDiskUnchanged`). A one-sided "the engine reports `committing`" (review H5) races: the cancel's kill can land after the engine moved on but before its message arrived.
5. Journal, backups, swaps, read-back, journal close (ADR-0008, ADR-0012, ADR-0013, F-1..F-8). No user code runs after the commit point: plugins ran in step 3. So a hang after it is our bug or the OS's (a frozen disk, `SIGSTOP`), and only the hang rule (§8.3) may end it.
6. Build the mirror from the load result still in memory and publish it under the same locks (§5.3; ADR-0011), release, reply with `usage` (§8.1) and the old→new TransactionId map.

Write serialization (ADR-0011) is the exclusive lane plus the ledger-set lock (ADR-0012 Q29), not "INV-26" (review L1). The journal names the writer as `(session id, job id)`, never the engine's pid (H5): a pid dies with every fresh engine.

### 5.5 The R4 gate [C]

ADR-0008's amendment: the R4 re-check completes before the next write to that workspace is accepted. Here:
- A commit that publishes enqueues `r4(W, post fingerprint)` and records "R4 pending" for W in `app_local_data/engine/r4-pending/<ws key>` (atomic replace), so a quit or crash cannot skip it: at the next open, if the disk's content fingerprint equals the recorded one, R4 runs before writes are accepted; if not, it is superseded.
- R4 takes the locks (waiting) for its whole `load_file` (it uses Beancount's own file reading, ADR-0010 guardrail 3), first checks that the disk's content fingerprint equals the commit's post fingerprint, then loads, digests and compares with the adopted digest. It must call Beancount's uncached load path, never `load_file` with the pickle cache disabled, which deletes `.<root>.picklecache` in the user's folder and can race with a second load (spike S4, review M5).
- **Superseded:** if the disk moved after the commit (an outside save), the comparison is meaningless; R4 ends `superseded`, logged `engine.r4.superseded`. This loses one guardrail-3 sample, not a safety check of that write (its gate passed); the outside change sends the workspace to WARMING, whose rebuild replaces the adopted mirror anyway.
- The next `commit` of W is not dispatched while R4 for W is pending or running (property `R4BeforeNextCommit`); a waiting commit lends R4 its class (§4.2).
- A mismatch blocks writes and shows the backup (ADR-0008); an R4 that cannot complete (crash twice, runaway) also blocks writes: "could not check" never means "write anyway" (INV-22).
- Synchronous R4 during rollout (ADR-0010) runs inside the commit job before the reply, at P1.

### 5.6 Outside editors, the legacy app, a second installation

- **Outside editors and sync clients** (no locks): detected by the focus-time stat check (WARMING and a rebuild), by verification (`snapshot_moved`), by the commit's gate (`LEDGER_EXTERNALLY_CHANGED`) and by the swap's displaced-file hash (ADR-0013). Never excluded.
- **The legacy v0.3 app** (ADR-0005, ADR-0020): every lock-taking step (verify, commit, R4, recovery) takes every recorded legacy lock first, with portalocker 3.2.0's primitive. The legacy app holds its lock through its whole re-export, so a verification may wait tens of seconds; the job shows `waiting_for_lock` and keeps its parsed result while it waits (one job's lifetime, so not resident in INV-10's sense); it usually ends `snapshot_moved` afterwards, because the legacy app wrote.
- **A second installation or app root** (gap found here, not in either review): the ledger-set lock files live under `app_local_data_dir/locks/` (ADR-0005), so two v0.4 installations with different app-local-data folders (a desktop app and a headless server with its own `--app-root`, writing one ledger) do not exclude each other; each is an outside editor to the other. The legacy lock still excludes both on a Share workspace. Detection (fingerprint, swap hash) still holds; exclusion does not. The fix belongs to the writer's lock design in M1b (lock files beside the ledger are refused by INV-30 for opening, and INV-27 forbids app state in the folder), so it is recorded here as a limitation and an item for the BL-0091 slice to decide, not designed here.

### 5.7 Recovery and the outcome of a save whose engine died [C]

When a commit's engine ends without its final reply after the commit point (crash, OS kill, hang kill, `MemoryError`), the save's outcome is **resolved**, never left unknown (BL-0091; property `EveryWriteEndsKnown`). The workspace's lane stays held by the job until death is confirmed (§4.6); then a `recover` job (P0, exclusive) runs:
- it takes the locks (waiting; on Windows a lock may stay held briefly after `TerminateJobObject`, review M6, so "held" right after our own kill is waited for, not read as a live writer);
- `resolve(journal, disk hashes, plan pre/post hashes)`, a pure function: no journal and disk = post → **saved**; no journal and disk = pre → **not saved, unchanged**; journal open → ADR-0012's rule (every file pre or post → restore pre → **not saved, put back**; any other bytes → **blocked**, writes off, both versions shown);
- the journal's `(session id, job id)` tells our own orphan (recover now, in session) from another session's (ADR-0012 Q30: refuse writes, ask to reopen).
- Then the mirror: saved → if the commit's generation was published, nothing; else a rebuild. Not saved → the published mirror is still the pre state (the commit publishes only after the journal close), nothing to do.

Before [C] there are no writes, and failure-modes rows E1–E3 keep saying so.

### 5.8 What is not watertight, and what it costs

- **Lock-ignoring writers** can only be detected (above); the window of ADR-0013 remains where swap is unavailable.
- **Different app-local-data folders** do not exclude each other (§5.6).
- **An unkillable process** keeps its slot and lane (§4.6). On a pool of 2, one stuck process halves throughput until it ends.
- **A first run** of a background job has no baseline, so a runaway first run uses CPU until a person cancels it (owner decision 4). Until the job list exists in the UI (BL-0092, M2) that person can only quit the app.
- **Machine saturation:** when the machine has no idle CPU, a frozen engine is not judged hung (§8.3) until load drops. This is the price of never calling a starved engine hung.
- **Compressed memory** on macOS makes RSS understate a job's footprint under pressure (review M1); admission starts jobs only at normal pressure, where little of our memory is compressed.

## 6. Engines [A]

### 6.1 Fresh process per heavy job (owner decision 5; H1)

- An engine runs **at most one heavy job**, then exits. It may run cheap jobs before that, never after: an engine is clean until it imports user code, and it imports user code at most once (property `CleanEngineForUserCode`). Beancount's loader imports plugins with `importlib.import_module` and restores `sys.path` but not `sys.modules` (H1, reproduced: workspace B ran workspace A's plugin), and a user who edits a plugin would otherwise be served the old module.
- After its final reply the sidecar closes the engine's stdin; the engine exits; the sidecar reaps it with `wait4` (POSIX) or reads the process's times and peak memory before closing its handle (Windows), which gives the job's exact CPU time and peak memory, also for an engine we killed (§7.2).
- **Spare engine:** the pool may keep one started, never-used engine to hide start latency; **off by default** on macOS and Linux (start cost below); decided for Windows from the BL-0087 runner's measurement (M1b) and confirmed in M11. A spare counts against `max_engines` and is ended first when a slot is needed for a heavy job of a different priority.

### 6.2 Start cost

Measured on the owner's Mac (M4 Pro, macOS 26; this design, 2026-10-26): `python -I -c "import beancount.loader, beanquery"` takes 0.07–0.10 s and 34 MB peak with a warm file cache, 0.42 s cold (the review measured 0.06–0.08 s, 34 MB). ADR-0054's "40–80 MB" was pessimistic. Against a 2 s load at 54k this is about 5 %; at demo size (0.16 s load, ADR-0010) it is large in proportion but small in absolute terms against the 1 s demo write target. Windows (CreateProcess, Defender scanning a fresh interpreter) is unmeasured: [M1b] on the BL-0087 runner, [M11] on real machines.

### 6.3 Process groups and Job Objects

- **POSIX:** each engine leads its own process group (`process_group=0` at spawn). Our kill is `killpg(pgid, SIGKILL)`, which also ends what the job started (gpg for encrypted files, a plugin's subprocess; review L4). Because the shell's group kill of the sidecar no longer reaches engines in their own groups, the engine's parent-death watchdog (0.25 s, spike S1) ends its **whole group** (`killpg(0, SIGKILL)`), not only itself. `PR_SET_PDEATHSIG` is not used: it fires when the spawning *thread* ends, and the sidecar spawns from worker threads. Stated limit: a child that calls `setsid` leaves the group.
- **Windows** [C for the write path, ADR-0061 d6; M11 otherwise]: each engine in its own Job Object (nested in the shell's), with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and without breakaway; our kill is `TerminateJobObject(job, EXIT_OUR_KILL)` with a fixed exit code; if the sidecar dies its handle closes and the job's processes end at once (better than POSIX's 0.25 s poll).

### 6.4 How an engine ended

The sidecar records the kill reason (`cancel`, `preempt`, `superseded`, `hang`, `stalled`, `runaway`, `quit`, `violation`) in the engine's state **before** it sends the kill (property `OwnKillNotAStop` depends on the order). On reap:

| Observation | Classified as | Stop? |
|---|---|---|
| final reply, then exit 0 | `answered` | no |
| killed, own reason recorded | that reason | only `hang` and `violation` |
| `SIGKILL` (POSIX) with no reason of ours | `killed_by_system` (on macOS and Linux usually memory: jetsam, the OOM killer; on Linux corroborated by the cgroup's `memory.events` `oom_kill` counter when readable) | no; poison + one engine (§7.5) |
| exit `EXIT_OUT_OF_MEMORY` after an `engine.out_of_memory` reply; Windows `0xC0000017` (`STATUS_NO_MEMORY`) | `out_of_memory` | no; poison + one engine |
| any other exit or signal mid-job | `crashed` | yes |
| output ended before handshake, handshake refused or timed out | `start_failed` | yes |
| another protocol version | `incompatible` | no (pool `Incompatible`) |

### 6.5 Out-of-memory errors inside the engine

Windows does not kill a process for memory; an allocation beyond the commit limit fails, which Python raises as `MemoryError` (or a native crash, `STATUS_NO_MEMORY`). On macOS and Linux it can happen under a hard limit (cgroup, `ulimit`). The engine catches `MemoryError` at the job's top level, drops its references, writes a **pre-encoded** `engine.out_of_memory` reply (no allocation needed) and exits with `EXIT_OUT_OF_MEMORY`. A `MemoryError` after the commit point is reported the same way, and the outcome is resolved as in §5.7 (the in-process rollback of ADR-0012 runs first if it can).

## 7. Admission and memory [A]

### 7.1 Budget (owner decision 2)

`budget = memory_share × total`, `memory_share` default 0.25 (a setting), where `total` is physical RAM (macOS `hw.memsize`; Linux `MemTotal`; Windows `GlobalMemoryStatusEx.ullTotalPhys`), lowered to the container's limit when one is set (cgroup v2 `memory.max`, v1 `memory.limit_in_bytes`; a Windows job memory limit). The budget covers engine processes and the sidecar's decoding of their replies, not the rest of the app. There is no CPU formula and no startup computation (owner decision 1; review M1: free-memory and core-count readings are 8× apart on this Mac and ignore cgroups).

### 7.2 Estimates and measurement

For a heavy job of kind k in workspace W: `E = peak_hw(W,k) × max(1, size_now / size_at_hw) + reply_hw(W,k) × decode_factor`, where `size` is the ledger set's total bytes (a stat), `decode_factor` = 8 until BL-0082 measures it (review L2). A **cheap** job's estimate is the interpreter baseline (measured from cheap jobs, about 34 MB).

- **Measured, never estimated, after the fact:** with a fresh process per job, the reaped process's peak (`ru_maxrss`, bytes on macOS and kB on Linux; Windows `PeakWorkingSetSize`, and `PeakPagefileUsage` for the commit check below) **is** the job's absolute peak, the interpreter included (review M2: a delta in a reused process under-reads by 2×). Verified for this design: `os.wait4(pid, 0)` returned 214 MB and 0.08 s CPU for a child that allocated 200 MB. A killed job's peak is measured the same way, so a memory kill teaches the estimate.
- **Decaying high-water per run, not per time:** after each measured run, `hw ← max(observed, decay × hw)`, `decay` = 0.9 (a setting). One large outlier fades over about 20 runs; growth is followed at once.
- **Unmeasured:** no record, or a record whose engine version or plugin-set hash differs from the current one (a Beancount upgrade or a new plugin changes the cost). An unmeasured heavy job **runs alone** (no other heavy job running, none started while it runs). Spike S4's ratios are not used as a guess: they came from generated ledgers without heavy plugins (review M2).
- The measurement is a by-product of real jobs, so the owner's question "will it slow startup?" has the answer: nothing is measured at startup.

### 7.3 Admission rule

A heavy job is admitted at dispatch iff the pressure level is `Normal` (or `Unknown`, §7.4) **and** either no heavy job is running, or every running and the candidate job are measured and `Σ E(running) + E(candidate) ≤ budget`; and on Windows also `E(candidate) ≤ ullAvailPageFile` (the commit still available; otherwise the job would fail with `MemoryError`). The first job is always admitted at normal pressure even if `E > budget` (else a large ledger could never load). Cheap jobs are admitted except at `Critical`. Recovery (no load) is admitted at any level. A job already running is never stopped by admission (property `MemoryAdmission`).

While a job waits for memory, it shows "Waiting for memory" (§9.1). Whether the user may override with **Run anyway** is owner question 1 (§15); until answered, there is no override, and a P1 job waits until pressure returns to normal or the user cancels.

### 7.4 The pressure signal, per OS (`MemoryPressurePort.level() -> Normal | Warn | Critical | Unknown`)

Read at every dispatch decision and on each watcher tick while jobs run (all reads are microseconds).

| OS | Source | Warn | Critical |
|---|---|---|---|
| **macOS** [A] | `sysctlbyname("kern.memorystatus_vm_pressure_level")` (1 normal, 2 warn, 4 critical; reads 1 on the owner's Mac). The kernel's own judgement, which already accounts for compression and swap; it is what the system uses to tell apps to free memory. | 2 | 4 |
| **Linux** [M11; CI from A] | In a cgroup v2 with a limit: the cgroup's `memory.pressure` (PSI); else `/proc/pressure/memory` (kernel ≥ 4.20 with PSI enabled; some distributions boot with `psi=0`). Fallback when PSI is absent: `MemAvailable` from `/proc/meminfo`, or `memory.max − memory.current` in a limited cgroup. Linux overcommits, so a job rarely sees `MemoryError`; the OOM killer sends `SIGKILL`. | PSI `some avg10` ≥ 10 %; fallback: available < 10 % of total | PSI `full avg10` ≥ 5 %; fallback: available < 5 % |
| **Windows** [M11] | `CreateMemoryResourceNotification(LowMemoryResourceNotification)` with `QueryMemoryResourceNotification` (the system's own low-memory signal); `GlobalMemoryStatusEx` for `dwMemoryLoad`, `ullAvailPhys`, `ullAvailPageFile`. Windows does not kill for memory: allocations fail at the commit limit (§6.5), so admission also checks commit (§7.3). | `dwMemoryLoad` ≥ 90 % | the low-memory notification is signalled, or `ullAvailPageFile` < the smallest measured job estimate |

Thresholds are settings. **Unknown** (no signal readable: PSI off and `/proc` unreadable, a sandbox): admission uses the budget only; one `engine.pressure.unknown` info line per session. Cost, stated: without a signal Finzytrack can start a job into a machine that is already short of memory; the budget bounds only our own share.

### 7.5 After a memory death

After a `killed_by_system` or `out_of_memory` outcome, the pool runs **one heavy engine at a time for the rest of the session** (`single_engine`; review M1), logged `engine.pool.single_engine`. The job's estimate becomes at least its measured peak at death. The workspace's poison counter for that (kind, fingerprint) moves (§8.7), so the same job is tried at most twice.

### 7.6 Containers and hosting

The budget is lowered to the container's limit (§7.1) and PSI is read from the container's cgroup, so a container behaves like a smaller machine. In hosting [Hosted], the pool and budget stay one process-wide object (ADR-0041's single entry), jobs carry their user, and the pool's cross-user round-robin (§2) becomes per-user fairness and quotas, which ADR-0041 defers. The D2 background reconciliation is off in hosted mode (ADR-0041), so R4 runs synchronously there or not at all, as ADR-0041's hosted design decides.

## 8. Liveness and progress

### 8.1 Protocol v2 [A]

`PROTOCOL_VERSION` becomes 2; a v1 peer is `incompatible` (both ship in one bundle, so only a damaged install mixes them). The envelope rules of S0035 stay (`v` first-class, JSON Lines, 16 MiB per line, strict JSON). What changes:
- **Per request, the engine sends:** zero or more `progress` messages, at most one `gate` message (commit jobs only), then **exactly one** final reply. "One reply per request" becomes "one final reply per request".
  - `{"v":2,"id":N,"op":op,"progress":{"seq":k,"phase":P,"cpu_ms":c,"peak_rss_bytes":m,"done":d,"total":t}}`, where `phase` ∈ a closed enum: `starting`, `reading_files`, `parsing`, `booking`, `plugins`, `validating`, `querying`, `waiting_for_lock`, `verifying`, `building_mirror`, `publishing`, `committing`, `serializing`; `done`/`total` are optional counts (files read, statements run). Sent at each phase change and at least every `beat` (1 s) by an engine thread while a job runs. Waiting phases (`waiting_for_lock`, `reading_files`) are declared waits for §8.4.
  - `{"v":2,"id":N,"op":"commit","gate":{"point":"commit"}}`; the sidecar answers `{"v":2,"id":N,"op":"commit","proceed":true}` on the engine's input, or kills it (§5.4). The engine reads nothing else mid-request.
  - Final replies carry `usage {cpu_ms, peak_rss_bytes}` (self-reported, cross-checked with the reap).
- **Domain outcomes are results, not errors** (area review F7): `snapshot_moved`, `orphan_journal`, `superseded`, `result_too_large` (per statement) are tagged results inside `result`; `error` keeps protocol and internal faults plus `engine.out_of_memory`.
- **The engine's output channel** gets a lock so the progress thread and the main thread never interleave inside a line. A long final-reply write holds the lock, and its bytes count as liveness.
- **The sidecar's request** carries `job {id, kind, ws_key, attempt}` and no time limit. `EnginePort.call(op, params, timeout)` becomes `submit(job) -> JobHandle` (`status()`, `cancel()`, `outcome()`); the caller's timeout disappears from engine calls. Cheap jobs have no timeout either: the hang rule covers them.

### 8.2 The watcher and on-time ticks [A]

A watcher thread in the sidecar ticks every `tick` (1 s) for each running engine. A tick is **on time** when (a) it woke within `tick/2` of its schedule on the monotonic clock (a sidecar busy decoding a large reply under its GIL, review L2, wakes late), and (b) a clock that counts machine sleep advanced by no more than `tick + tick/2` since the previous tick (no sleep inside it). Python's `time.monotonic` does not count sleep on macOS (it is `mach_absolute_time`, checked on the owner's Mac), so (b) needs the sleep-counting clock: macOS `CLOCK_MONOTONIC`, Linux `CLOCK_BOOTTIME`, Windows `QueryInterruptTime` against `QueryUnbiasedInterruptTime`; each to be verified on its OS in the slice that builds it (the review's M6 left Windows open). Late ticks are not counted for anything and reset nothing.

On each tick the watcher samples, **from outside the engine** (`ProcessProbe`): the engine's CPU time (user + system, its children included), its RSS, the system's idle CPU over the tick, and whether bytes arrived: bytes the reader thread read since the last tick **or bytes waiting in the pipe** (`FIONREAD` on POSIX, `PeekNamedPipe` on Windows), so a sidecar slow to read is not mistaken for a silent engine ("drain before judging", review H4).

### 8.3 Hung [A]

An engine is **hung** after `hang_ticks` consecutive on-time ticks in each of which: no bytes arrived; its CPU time advanced by less than 1 % of the tick; the system had at least half a core idle (so the engine could have run); and memory pressure was `Normal` (so it is not waiting on swap-ins). A hung engine is killed (reason `hang`, a stop).

`hang_ticks = max(10, 3 × the longest such streak seen in a healthy run of this (workspace, kind))`. This is a count of observations of a condition, not a time budget: a healthy engine either uses CPU (C code holding the GIL included, which stops its heartbeat but not its CPU clock; H4's 6 s regex) or its progress thread speaks (a blocking read or a sleeping plugin releases the GIL), so a healthy run never accumulates such ticks, whatever the ledger size or the machine's speed. The floor of 10 only makes the rule more lenient. It catches what it should: a frozen process (`SIGSTOP`, a debugger), a deadlock that takes the progress thread with it, a native call that blocks while holding the GIL. It does not catch a plugin that loops (CPU advances: §8.5) or sleeps (bytes arrive: §8.4). Machine sleep, a loaded machine and a sidecar that reads late do not count, by the definition of an on-time tick and the idle-CPU condition.

### 8.4 Stalled

An engine is **stalled** when progress bytes arrive but for `stall_ticks` consecutive on-time ticks its CPU advanced less than 1 % of the tick, its `seq`-carrying progress showed no change of `phase`, `done` or `total`, and the phase is not a declared wait. Example: a plugin blocked on a network call. `stall_ticks = max(30, 3 × the longest healthy streak of (workspace, kind))`.
- Interactive (P0–P2): a status only ("No progress for a while", with **Cancel**); never stopped.
- Background (P3) [A]: stopped (reason `stalled`, not a stop, counted in poison), logged at warning; an unmeasured job (no healthy streak recorded) and a commit past its commit point never. This extends owner decision 4 from "far over its CPU time" to "using no CPU and making no progress"; owner question 2 (§15). Until answered, stalled background jobs are only flagged.

### 8.5 Runaway (owner decision 4) [A]

A **background** job is stopped (reason `runaway`, not a stop, counted in poison, logged at warning) when its CPU time exceeds `max(runaway_factor × B, B + runaway_floor)`, where `B = cpu_hw(W,k) × max(1, size_now / size_at_hw)`, `runaway_factor` = 10, `runaway_floor` = 10 s of CPU (so a 0.2 s demo job is not stopped at 2 s). CPU time does not advance while the machine sleeps or while the engine waits for the CPU, so a slow or loaded machine does not trip it; it is learned on this machine. An **unmeasured** job (no record, or another engine version or plugin set) has no bound: only a person can cancel its first run. Interactive jobs are never stopped by it; past the same bound they show "Taking much longer than usual" with **Cancel**.

### 8.6 Learning

After each healthy run (outcome `answered`, the snapshot verified), `Measurements` updates for (W, k): `peak_hw`, `cpu_hw`, `reply_hw` by §7.2's decay rule, and the longest silent and stalled streaks (for §8.3, §8.4) as a plain maximum that decays the same way. Runs that ended in any fault update `peak_hw` only (a memory death must teach). The file is written atomically after each update, at most once per job.

### 8.7 Supervision: one table per engine and one for the pool [A]

S0040's statechart is split (H6: its stop history dies with each short-lived `EngineClient`, so a segfaulting plugin would restart for ever):

**Per engine** (`app.platform.engine_process`, replacing `engine_supervision`): `Spawning → Handshaking → Clean → Running(job, phase, past_commit_point) → Draining (final reply received, waiting for the exit) → Gone(classification)`, plus `Killing(reason)`; events `Spawned`, `SpawnFailed`, `HandshakeOk`, `HandshakeForeign`, `HandshakeFailed`, `Dispatch`, `Progress`, `Gate`, `Proceed`, `Reply`, `Tick(on_time, bytes, cpu, idle, pressure)`, `Cancel`, `Kill(reason)`, `OutputEnded`, `Reaped(status, usage)`, `Violated`. `Clean` accepts cheap jobs and one heavy job; `Draining` never accepts another job. The table emits `Kill(reason)` only from `Running` with `past_commit_point = false`, except `hang` and `violation` (property `NoKillAfterCommitPoint`). The (state × event) exhaustive test and random walks of EP-17 apply; the reference is generated (`design/generated/statecharts/engine-process.md`).

**Pool** (`app.platform.engine_pool.table`): state = `{engines: count by state, stops: times of the last max_crashes stops, single_engine, unavailable_until_window, incompatible}`; events `EngineGone(classification, now)`, `Request(now)`, `PressureChanged`, `Reset`, `Close`. It keeps S0040's rules at pool level: backoff `delays` (0.5 s, 2 s, 8 s) before the next engine start after n recent stops; `max_crashes` (3) stops within `window` (300 s) make the pool `Unavailable` (heavy jobs refused with the E1 message; reads from the mirror go on) until a stop leaves the window or **Try again** (`Reset`). This window is S0040's accepted crash-loop rate limit, not a time limit on any job. Own kills other than `hang` and `violation`, memory deaths, runaway and stalled stops are not stops.

**Per-workspace poison** (in `JobQueue`): a counter per (workspace, kind, content fingerprint) of consecutive faulted ends (crash, hang, violation, memory death, runaway, stalled). At `poison_limit` (2) that key is refused ("poisoned", §9.1) until the ledger's content fingerprint changes or the user presses Try again. A healthy end resets it. One bad ledger therefore costs at most 2 faults per kind and change, and cannot by itself push the pool to `Unavailable` (3), which keeps other workspaces working (property `BoundedFaultLoop`).

**Settings (defaults):**

| Setting | Default | Section |
|---|---|---|
| `max_engines` | 2 | §1 (owner decision 1) |
| `memory_share` | 0.25 | §7.1 (owner decision 2) |
| `decay` | 0.9 per run | §7.2 |
| `runaway_factor`, `runaway_floor` | 10, 10 s CPU | §8.5 (owner decision 4) |
| `beat`, `tick` | 1 s, 1 s | §8.1, §8.2 (rates, not limits) |
| `hang_ticks` floor, `stall_ticks` floor | 10, 30 on-time ticks | §8.3, §8.4 |
| `max_crashes`, `window`, `delays` | 3, 300 s, (0.5, 2, 8) s | §8.7 (S0040) |
| `poison_limit` | 2 | §8.7 |
| `snapshot_retries` | 3 | §5.2 |
| `aging_passes`, `preempt_max` | 8, 2 | §4.2, §4.3 |
| queue bounds | §3.1; 64 per user | §4.5 |
| pressure thresholds | §7.4 | §7.4 |

## 9. What the user sees, and the log [M2 for the UI, BL-0092; the outcomes and codes from A/B/C]

### 9.1 Messages (EP-27: what happened, whether data is safe, what to do)

Job state comes back in API responses as a tagged union (kind, state, phase, `done/total`, cancellable, `much_longer_than_usual`), and `GET /api/w/{ws}/jobs` with `POST …/jobs/{id}:cancel` give the job list (review L3) [M2].

| Situation | Message | Actions |
|---|---|---|
| queued | "Waiting for other ledger work to finish…" | Cancel (if cancellable) |
| waiting for memory | "Waiting for memory. Your computer is low on memory. Closing other apps helps." | Cancel (Run anyway: owner question 1) |
| running, per phase | "Reading your ledger files…", "Checking your ledger…" (parsing to validating), "Running your query…", "Saving…", "Updating your views…" (building, publishing) | Cancel where allowed |
| `waiting_for_lock` (legacy) | "Waiting for the other Finzytrack app to finish saving…" | Cancel |
| much longer than usual (interactive) | "This is taking much longer than usual." | Cancel |
| no progress (interactive, stalled) | "No progress for a while. It may be waiting for something." | Cancel |
| Cancel on a save past the commit point | "Finishing your change…", then "Your change is saved." | none, then Undo where the mutator has one |
| save cancelled before the commit point | "Your change was not saved. Your ledger is unchanged." | none |
| busy | "Finzytrack is busy with other ledger work. Please try again in a moment." | Try again |
| crash on a read | "The ledger engine stopped while working on this. Your ledger files are safe." | Try again, Copy diagnostics |
| memory death | "Your computer ran out of memory while Finzytrack worked on this. Your ledger files are safe. Close other apps and try again." | Try again |
| hang | "The ledger engine stopped responding and was restarted. Your ledger files are safe." | Try again |
| save resolved after an engine death | "Your change was saved." / "Your change was not saved. Your ledger is unchanged." / "Finzytrack could not finish your change and put your files back as they were." / "A ledger file was changed by another program while Finzytrack was saving. Saving is off until you review it." | — / — / — / Review files |
| poisoned | "Finzytrack stopped trying: the ledger engine failed twice on this ledger. Your files are safe. Change the ledger or try again." | Try again, Copy diagnostics |
| pool unavailable | failure-modes E1's message | Try again, Copy diagnostics |
| ledger keeps changing | "Another program is changing your ledger. Finzytrack will update when it stops." | Try again |
| R4 stopped or failed to run | "Finzytrack could not double-check your last change. Saving is paused until the check runs." | Run check now |
| unkillable engine | "The ledger engine is waiting for a disk and cannot be stopped. Finzytrack will continue when it ends." | Copy diagnostics |

### 9.2 Log events (§6A; every field a safe kind: ids, enums, counts, durations, bytes)

`engine.job.ended` (info: kind, outcome, `duration_ms`, `cpu_ms`, `peak_bytes`, attempt, `ws`), `engine.job.cancelled` (info: reason), `engine.job.preempted` (info), `engine.job.runaway_stopped` and `engine.job.stalled_stopped` (warning: kind, `cpu_ms`, bound), `engine.stopped` (warning, kinds `crashed`, `hang`, `protocol_violation`, `start_failed`), `engine.killed_by_system` and `engine.out_of_memory` (warning, `peak_bytes`), `engine.pool.single_engine` (warning), `engine.unavailable` (error, as S0040), `engine.workspace.poisoned` (error: kind), `engine.admission.waiting` (info: reason, once per wait), `engine.pressure.changed` (info: level), `engine.pressure.unknown` (info, once), `engine.queue.busy` (info, once per drain), `engine.snapshot.moved` (debug; info when retries run out), `mirror.publish.refused_stale` (info), `mirror.generation.published` (info), `engine.r4.superseded` (info), `engine.commit.resolved` (warning: resolution), `engine.unkillable` (error). Queue and dispatch traces are debug.

## 10. Cross-platform behaviour (ADR-0061)

| Concern | macOS (first) | Linux | Windows |
|---|---|---|---|
| Our kill | `killpg(SIGKILL)` | same | `TerminateJobObject(job, EXIT_OUR_KILL)` |
| Engine and its children end with the sidecar | watchdog 0.25 s, then `killpg(0)` | same (no PDEATHSIG, §6.3) | Job Object kill-on-close, at once |
| OS memory response | jetsam `SIGKILL` (rare for user processes; usually compression and swap first) | OOM killer `SIGKILL`; `memory.events` `oom_kill` | allocation fails: `MemoryError` or `STATUS_NO_MEMORY` |
| Pressure | `kern.memorystatus_vm_pressure_level` | PSI (cgroup, then system), else `MemAvailable` | low-memory notification, `GlobalMemoryStatusEx`, commit check |
| Per-job peak and CPU | `wait4` rusage (bytes) | `wait4` rusage (kB) | `GetProcessMemoryInfo`, `GetProcessTimes` before closing the handle |
| Bytes pending in the pipe | `FIONREAD` | `FIONREAD` | `PeekNamedPipe` |
| Sleep-counting clock | `CLOCK_MONOTONIC` (verify) | `CLOCK_BOOTTIME` | `QueryInterruptTime` (verify) |
| Lock released after our kill | at process end | at process end | may lag `TerminateJobObject` (review M6, verify): waited for, never read as a live writer (§5.7) |
| Start cost | 0.07–0.10 s, 34 MB (measured) | expected similar (CI from A) | unmeasured: BL-0087 runner [M1b], real machines [M11] |
| Background priority | `nice +10` for P3 engines (macOS may also move them to efficiency cores; CPU baselines are per kind, so consistent) | `nice +10` | `BELOW_NORMAL_PRIORITY_CLASS` |

Write-path parts (§5.4, §5.7, §6.3's Job Object, the lock-release lag) are designed against Windows now and their engine tests run on the Windows runner from M1b (ADR-0061 d6, BL-0087). The rest is ported in M11; CI's Linux T1 runs the pure tables and the Linux adapters from [A].

## 11. Properties for the TLA+ model

### 11.1 Safety

- `OneExclusivePerWorkspace` (§4.1) — at most one exclusive job per workspace is running (running per §4.6).
- `RunningUntilAnsweredOrDead` (§4.6) — a lane, slot or reservation is released only after the final reply or confirmed death.
- `EnginesBounded` (§1) — live engine processes ≤ `max_engines`.
- `CleanEngineForUserCode` (§6.1) — no engine runs a second heavy job; no engine runs a cheap job after a heavy one.
- `PublishOnlyCurrent` (§5.3) — a generation is published only under the ledger-set locks, with the disk's content fingerprint equal to the build's and no journal open after a journal-directory fsync.
- `GenerationsMonotonic` (§5.3) — published ids strictly increase per workspace, across restarts and cache purges.
- `PublishedIsCommittedState` (extends S5's `SnapshotIsSomeCommittedState`) — every published generation equals a disk state with no journal open and no lock-respecting writer mid-commit.
- `VerifiedReadsAreSnapshots` (§5.2) — a returned read result equals a committed disk state at one instant, with respect to lock-respecting writers.
- `GateOnCurrentMirror` (§5.4) — a commit passes its gate only if the disk's and the published mirror's fingerprints both equal the plan's.
- `NoKillAfterCommitPoint` (§5.4) — after `proceed` is sent, the sidecar kills the engine only for `hang` or `violation`.
- `CancelBeforeCommitLeavesDiskUnchanged` (§5.4) — a commit that ends cancelled leaves every file byte-identical and no journal.
- `R4BeforeNextCommit` (§5.5) — no commit of W passes its commit point while the previous commit of W has no R4 result and no `superseded`.
- `NoWriteAfterUnresolvedDeath` (§5.7) — after an engine died past its commit point, no commit of W is dispatched until `resolve` and recovery have run.
- `OwnKillNotAStop` (§6.4) — kills with a recorded reason other than `hang` and `violation` never enter the stop history.
- `MemoryAdmission` (§7.3) — a heavy job starts only at Normal (or Unknown) pressure, and alone or within the budget with every running job measured.
- Kept from S5 and ADR-0012/0013, now with rebuild, R4 and recovery actors: `NoTornFile`, `NoPartialMultiFileCommitVisibleAfterRecovery`, `ExternalEditNeverSilentlyLost`, `RecoveryNeverClobbersForeignBytes`, `RefusedImpliesUnchangedOrFlagged`, `JournalDurableBeforeFirstReplace`, `ReadersSeeCompleteGeneration`, `AckedCommitNeverRolledBack`.

### 11.2 Liveness (under weak fairness of engines that answer or die, and pressure that returns to Normal infinitely often)

- `EveryJobEnds` — every queued job is eventually dispatched and ends, or is refused (busy, poisoned, unavailable) or cancelled.
- `NoStarvation` — with count-based aging, no job is passed over for ever.
- `EveryWriteEndsKnown` (BL-0091) — every commit request ends saved and reported, or not saved with the disk unchanged or put back, or blocked with both versions shown; never unknown.
- `MirrorEventuallyCurrent` — if the disk stops changing and the workspace is viewed, its published fingerprint eventually equals the disk's, or a build failure is reported.
- `BoundedFaultLoop` — faults per (workspace, kind, fingerprint) ≤ `poison_limit` between changes or resets; pool stops ≤ `max_crashes` per window.
- Deadlock freedom with waiting verifies, commits and the legacy actor (S5's `Liveness_no_timeouts` config extended).

### 11.3 Model shape [B for lanes and publish, C for commits and recovery]

A new module `formal/tla/EngineScheduler.tla` that instantiates `WriteProtocol.tla`'s file, lock, journal and mirror variables. Added variables: `queue[w]`, `running[e]` (job, phase, `pastCommit`, `killSent`, `alive`), `stops`, `poison`, `pressure`, `published[w]`, `highWater[w]`, `r4Pending[w]`. Added actions: `Enqueue`, `Dispatch` (the §4.7 rule, admission as a nondeterministic predicate respecting `MemoryAdmission`), `EngineReads`, `EngineVerify`, `EnginePublish`, `GateRequest`, `Proceed`, `CancelRequest`, `Kill(reason)`, `EngineCrash`, `OsKill`, `EngineHang`, `Reap`, `ResolveAndRecover`, `R4Run`, `OutsideSave`, `LegacyWrite`, `CachePurge`, `Restart`. Abstractions: time is ticks; liveness rules (§8.3–§8.5) appear only as the nondeterministic `Kill(hang|stalled|runaway)`, which must never fire with `pastCommit` except `hang`.

Configs: **small** (T1): 1 workspace, 2 engines, jobs {rebuild, commit, query}, 1 outside save, 1 crash, 1 cancel; **lanes off** (T1): the same with `OneExclusivePerWorkspace` removed from the scheduler, checking that `PublishOnlyCurrent`, `GenerationsMonotonic`, `PublishedIsCommittedState` and `VerifiedReadsAreSnapshots` still hold (§4.1); **legacy + liveness** (T2); **two workspaces** (T2).

Seeded bugs, each a CONSTANT flag that must fail its property: release the lane on kill sent (`RunningUntilAnsweredOrDead`, then `PublishOnlyCurrent` with lanes on); publish without the fingerprint condition (`PublishOnlyCurrent`); allocate the generation id at dispatch (`MirrorEventuallyCurrent` or `GenerationsMonotonic`); one-sided commit point (`CancelBeforeCommitLeavesDiskUnchanged`); stop history per engine (`BoundedFaultLoop`); verify without locks (`VerifiedReadsAreSnapshots`); gate without the mirror check (`GateOnCurrentMirror`); R4 pending not persisted (`R4BeforeNextCommit` across `Restart`); high-water only in the cache (`GenerationsMonotonic` across `CachePurge`). Every new action carries `@tla_action` in code (EP-19).

## 12. Test obligations per phase

**[A] the scheduler slice, before the read core** (amends ADR-0054 d4 and ARCHITECTURE §2.5, §2.0's supervision text, failure-modes E1–E7):
- Per-engine table and pool table: exhaustive (state × event), random walks; generated references diff-checked.
- `next_dispatch`: exhaustive small-world test against the rules of §4.2–§4.5, §7.3; aging; preemption limits; merge with requesters (cancel one of two keeps the job; cancel both kills it).
- Protocol v2: schema regenerated; contract tests both sides; one final reply per request; progress never interleaves inside a line (a progress thread and a 10 MB reply); an engine with a v1 peer is `incompatible`.
- Fresh engine per heavy job: a heavy job never shares a process (H1 repro from review `plug.py` as a test: workspace B gets B's plugin); cheap-then-heavy allowed, heavy-then-anything refused.
- Kill classification: own `SIGKILL` with reason vs foreign `SIGKILL` (a test kills the engine from outside) vs `MemoryError` (an engine test op allocates under `RLIMIT_DATA` on Linux CI or raises `MemoryError` directly) vs crash; only `hang`/`violation`/crash/start failure enter the stop history.
- Hang: a `SIGSTOP`ped engine is hung after `hang_ticks` on-time ticks (fake clock, fake probe); a GIL-holding regex (review `hb.py`'s case) is **not** hung; a `time.sleep` plugin stand-in is stalled, not hung; ticks spanning a simulated sleep or a late wake do not count; no idle CPU → not counted.
- Runaway: background job over `10 × B` stopped and logged; unmeasured job never stopped; interactive job flagged, not stopped.
- Admission: unmeasured runs alone; budget arithmetic; Warn/Critical blocks new heavy jobs, not running ones; Critical preempts P3; single-engine after a memory death; Unknown falls back to the budget.
- Measurement: `wait4` peak of a fresh process within 5 % of a known allocation; killed job's peak recorded; decay rule; invalidation on engine version or plugin set.
- Pressure port: macOS adapter reads the sysctl; Linux adapter parses PSI and cgroup files from fixtures (CI from A); Windows adapter against fixtures (real calls M11).
- Process groups: cancelling a job that started a child (`sleep` subprocess) ends the child; sidecar death ends engine and child.
- Bounded fault loop: a crashing test op on one workspace poisons that workspace at 2 and leaves the pool available for another.
- Cancel for reads; requester left; quiesce kills reads.
- failure-modes.md rows E1–E7 rewritten against the new tables (E3 "slow" no longer equals hang), with their tests cited.

**[B] with the mirror build:**
- Lanes: exclusive exclusion, shared beside shared, preview waits for rebuild, commit refused in WARMING.
- Verified snapshot: an outside save during the load gives `snapshot_moved` and a retry; three moves give `LEDGER_CHANGING`; a same-size same-mtime save is caught (content fingerprint); a journal under the locks gives `orphan_journal`; the verify waits for a held legacy lock (portalocker 3.2.0) and does not deadlock.
- Conditional publish: a build whose disk moved is dropped and a rebuild queued; two rebuilds finishing out of order publish the newer state; ids monotonic across a sidecar restart and across deleting the cache directory.
- GC of `build-*` files of ended jobs.
- TLA+ `EngineScheduler.tla` small and lanes-off configs in T1, seeded bugs, mapping test.
- 38,800-reads acceptance test of ADR-0015 re-run with two engines building.

**[C] M1b, with the write contract and BL-0091:**
- Commit gate: a cancel before `proceed` leaves the disk byte-identical (fault-injected at every step up to the gate); a cancel after `proceed` returns "Finishing your change…" and the save lands; property-based race test of cancel against the gate message.
- `GateOnCurrentMirror`: a stale mirror with a matching disk is refused.
- R4 gate: next commit waits; R4 inherits P1; superseded on outside save; pending R4 survives quit and runs at the next open; R4 never touches `.picklecache`.
- Engine killed and `SIGSTOP`ped at each step after `proceed` (acceptance-harness fault injection, BL-0091): `resolve` gives the right one of the four outcomes; in-session recovery for our journal; Q30 for another session's.
- `MemoryError` after the commit point resolves correctly.
- Windows runner (BL-0087): Job Object kill, lock release lag after `TerminateJobObject` waited for, `STATUS_NO_MEMORY` classification.
- TLA+ commit, recovery and R4 actions; `EveryWriteEndsKnown` liveness.

**[M2]** BL-0092's machines and messages of §9.1 with model-based tests; the job list routes; BL-0094's fresh status reads the pool table. **[M11]** Linux and Windows adapters on real machines; start cost on Windows; the spare-engine default per OS.

## 13. Where this departs from the reviews, and why

1. **Safety rests on locks and content, not on the lane** (review H2 made the lane primary). The lane alone cannot exclude a second process, and a bug in it would corrupt the mirror; disk equality under the locks cannot be fooled by scheduling. The lane stays for order, R4 and resources, and the model checks safety without it.
2. **Verification under the locks with content hashes** instead of "fingerprint before and after" (H3). Before/after without locks cannot exclude a change and change-back between the reads of two files; a stat fingerprint misses a same-size save in one mtime tick.
3. **Locks held only for the verify, waiting, not non-blocking for the whole rebuild** (ADR-0012 F-6's wording). Holding them for a 2 s load would stall every other writer, and non-blocking attempts would turn each legacy write into a failed rebuild with no condition to wait on except polling. F-6's purpose is kept; the ADR-0012 amendment must restate F-6 this way.
4. **The generation id is allocated at publish, under the locks** (the review allowed "sidecar allocates"). Allocation at dispatch publishes the older of two out-of-order builds and then refuses the newer.
5. **A two-party commit point** instead of "the engine reports `committing`" (H5), which races with the kill.
6. **Liveness judged from outside the engine** (H4 asked for beats carrying CPU and RSS). A beat cannot be sent while C code holds the GIL, so CPU must be read from outside; beats remain the progress channel.
7. **Exact per-job peak from the reaped process** (H4/M2 suggested heartbeat-sampled RSS): with a fresh process per job, `wait4` gives the true peak, also for killed jobs.
8. **No cheap lane beyond the cap** (area review F1 suggested a never-waiting lane): the owner's cap counts every engine; frequent facts move into the handshake and the mirror, and cheap jobs get P1.
9. **The commit's legacy-lock wait has no deadline** (ADR-0005: "at least 30 s"): an unbounded wait with status and Cancel meets the minimum without a guessed number (the owner's no-fixed-budget rule).
10. **New, in neither review:** R4 pending persisted across restarts (§5.5); the different-app-root lock gap (§5.6); the unkillable-process cost (§4.6); the shell's group kill no longer reaching engines in their own groups (§6.3); the generation high-water outside the purgeable cache (§5.3); idle-CPU and pressure conditions in the hang rule (§8.3).

## 14. What changes elsewhere (in the slices that build each phase)

- **ADR-0054 decision 4** [A]: "the sidecar's `EnginePool` owns the engines (fresh per heavy job, at most `max_engines`) and the memory admission; per-user `JobQueue`s own lanes and order". Consequences: 34 MB per engine, not 40–80.
- **ADR-0011** [B]: the per-workspace limiter becomes the exclusive lane; the heavy-load semaphore becomes the pool; the BQL entries cache is withdrawn.
- **ADR-0012** [B, C]: F-6 restated (§13 item 3); the journal records `(session id, job id)`; in-session recovery (BL-0091).
- **ADR-0005** [C]: the commit's wait without deadline (§5.4), if the owner reads "at least 30 s" as requiring a deadline.
- **ARCHITECTURE** §2.0 (protocol v2, supervision), §2.5 (rows: pool as the process entry, `JobQueue` and `Measurements` in `UserContainer`, `EngineClient` per engine, the BQL cache row removed), §5, §7 (two statecharts instead of one), §8.3 (the new module), §9.1, §9.2.
- **failure-modes.md** [A]: rows E1–E7.
- **state_registry** [A]: the `process` entry; `EngineBoot` removed (area review F10).
- **Backlog:** BL-0091 gains §5.6's second-installation lock question; BL-0082 measures `decode_factor`; BL-0087 measures Windows start cost.

## 15. Owner questions

1. **Run anyway while memory is low.** When your computer reports memory pressure, Finzytrack starts no new ledger job (your decision 2). A job you asked for then waits, maybe for a long time if another app keeps the memory. Should the waiting message offer **Run anyway**, which runs that one job alone? *Recommendation: yes, only for jobs you started yourself, one at a time; background jobs always wait.*
2. **Background jobs that stop making progress.** Your decision 4 stops background jobs that use about 10× their usual CPU. A background job can also hang without using CPU (for example a plugin waiting on the network for ever) and hold a slot and block saving. Should those stop automatically too, when they make no progress for much longer than usual (at least 30 checks, 1 per second, while the computer is awake)? *Recommendation: yes, the same rule as decision 4: logged, a first run only stopped by hand, and never a save that has started writing.*
