# ADR-0011: No resident ledger; bounded memory and serialized writes

This record decides how the backend holds ledger data in memory and how it serializes writes and heavy work.

- **Status:** Accepted. Amended by ADR-0064 (2026-10-27): the heavy-load semaphore and the per-workspace write limiter are replaced by one engine doing one job at a time.
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** backend, performance, memory

## Context

A parsed ledger retains about 15 MB (demo) and about 152 MB (54k transactions), and 2.5–3× that during a commit. A future hosted service on a small, memory-limited server cannot keep one per user. In v0.3, one write blocked the whole event loop.

## Decision

(owner, 2026-10-24)
- **No resident parsed ledger.** Every load is per operation and released when it ends. "Snapshot" always means a transient load.
- **Reads never load the ledger.** They go to the on-disk mirror (ADR-0015) or the hardened `QueryRunner` (ADR-0016).
- **Planning parses only the files being edited** (parser only). The contract's baseline errors come from the mirror's `ledger_errors`, not from re-loading the old ledger. The mirror build uses the candidate's load result while it is still in memory.
- **BQL stays a transient full load** (it runs over Beancount entries). All BQL steps of one recipe run share one load. The recipe memo avoids repeated loads. An optional short-TTL entries cache is allowed on desktop and is off or tightly bounded in hosted mode.
- **A process-wide heavy-load semaphore** (1–2 slots, configurable) caps peak memory. It is the **only** process-wide mutable state.
- **Write serialization:** a per-workspace `anyio.CapacityLimiter(1)` plus thread offload, keeping the re-entrant lock and the file locks inside it.
- **The async edge only awaits:** every `async def` handler awaits; blocking work runs on worker threads behind the limiter or the semaphore (an AST test enforces it).
- **Every other cache** (recipe memo, chat history, plans, import sessions, model handles) is per user or per workspace, **bounded by a configurable limit** (generous on desktop, never a fixed development target), evictable, and optional in hosted mode (INV-10). Each is registered with its bound in the state registry.
- **Accepted costs:** booking, plugins and validation stay whole-ledger (about 0.18 s on the demo, 2.3 s at 54k). A two-phase write (preview, then confirm) runs the full load twice.

## Alternatives considered and rejected

- **A resident `LedgerStore` snapshot.** Rejected: memory per user is too large for hosting, and it was the source of stale-state bugs.
- **A dedicated writer thread.** Rejected: the limiter gives the same FIFO guarantee without thread lifecycle or deadlock risk.
- **Incremental booking.** Rejected: a correctness trap.
- **An incremental mirror diff.** Deferred (CAP-MIRROR-INCREMENTAL): a second code path for a modest saving.

## Consequences

- The user-facing write targets are ≤ 1 s at demo size and ≤ 5 s at 54k (ADR-0046, INV-20).
- Full-parse read paths of the old app (statement planning, notices, document scans) move to the mirror.

## References

review:D1 §10; review:10 D3, D4, D6, D7; review:18 §3; review:25 §2.5; ADR-0015, ADR-0016, ADR-0046; INV-10, INV-11, INV-20, INV-31.

## Amendment (2026-10-27): one engine, one job at a time (ADR-0064)

Owner: "if we are not most of the way there, or if we can't asses that with confidence, then I agree that we go back to one engine doing one job at a time and massively simplify everything." (2026-10-27, in session; the full record is ADR-0064.)
- **Superseded:** "a process-wide heavy-load semaphore (1–2 slots, configurable) caps peak memory". **Instead:** one engine process runs one job at a time, so one load at a time bounds memory (ADR-0064 decisions 1 and 6). The engine's queue lives in the main sidecar's `UserContainer` (INV-11).
- **Superseded (agent, a direct consequence; acknowledged by the owner, 2026-10-27: "I agree with all the recommendations. Proceed on that basis.", on the first golden review's recommendation that the limiter is retired):** the per-workspace `anyio.CapacityLimiter(1)` for writes. The queue runs every engine job, saves included, one at a time, and only one workspace is open (ADR-0063), so the limiter has nothing left to serialize. The ledger-set file locks stay; they exclude an orphaned engine of a crashed session (ADR-0012 Q29, ADR-0063).
- **Superseded (agent, a direct consequence of ADR-0064's fresh engine after user code; S0047, convergence audit C12):** "an optional short-TTL entries cache is allowed on desktop" for BQL. A BQL job loads the ledger, which runs its plugins, so its engine is ended after the job (engine-jobs spec §5.1), and an engine keeps nothing between jobs: the cache has no place to live. The recipe memo still avoids repeated loads.
- **Unchanged:** no resident parsed ledger; reads never load the ledger; planning parses only the edited files; BQL is a transient load; the async edge only awaits (blocking work runs on worker threads, now behind the engine queue); every other cache is bounded.

