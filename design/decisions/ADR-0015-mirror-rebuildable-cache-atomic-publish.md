# ADR-0015: The mirror is a rebuildable cache, published atomically

This record decides what the SQLite mirror is, where it lives, how it is published and how freshness is judged.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** mirror, data safety

## Context

In v0.3 a mirror rebuild dropped and re-created tables in place, so readers outside the lock saw empty tables, and a failed export could leave empty tables marked complete (review:10 I4). The mirror spike showed that renaming a new database over a path that has a stale `-wal` file corrupts it for every new reader. Its freshness fingerprint also missed glob-matched files, absent includes and documents.

## Decision

(owner, 2026-10-24)
- **The mirror is a rebuildable cache** of the ledger (INV-05). Its versioned `r_*` views are the public SQL surface.
- **Build a new generation file, then publish it atomically.** A failed build never publishes. Generations sit behind a symlink (`current.db`) on macOS/Linux and a pointer file on Windows (which cannot rename over an open file). The live path never sits next to a leftover `-wal`. Old generations are removed once no reader holds them.
- **Location:** the app cache dir, keyed by the workspace's registry key (`…/workspaces/<key>/mirror/`), never inside the workspace folder.
- **Outside tools may open the current generation read-only** (Settings shows Copy path / Reveal). The user docs state the contract: read-only, purgeable, `r_*` is the supported surface.
- **Freshness = the extended fingerprint:** the root path, `(path, mtime, size)` of every glob-expanded include, each glob with a hash of its match list, expected-but-absent paths, and a listing hash per documents root.
- **`ledger_version`** = generation + fingerprint hash, persisted in the mirror's meta and monotonic across restarts.
- **Stale with a previous generation:** the workspace opens in `WARMING`. Reads use the previous generation with a banner; writes are refused until the rebuild publishes. Only workspaces the user is viewing rebuild.
- The manual "export the mirror" endpoints of v0.3 are not rebuilt.

## Alternatives considered and rejected

- **Rebuilding in place** (DROP/CREATE on the live file). Rejected: readers see partial state.
- **Renaming a new file over the live path.** Rejected: the stale-WAL corruption.
- **The mirror inside the workspace folder.** Rejected: SQLite with WAL in a synced folder corrupts, and the file would be committed to git or synced (ADR-0017).
- **DuckDB as the engine.** Rejected (ADR-0016).
- **Incremental mirror diff.** Deferred (ADR-0011).

## Consequences

- A measured 38,800 reads during 5 s of continuous swaps gave 0 errors; this is an M1 acceptance test.
- The OS may purge the cache; the cost is one rebuild.

## References

review:D1 §11; review:10 I4, D4, D5; review:23 §1.2, §3.4; review:00 §1b; ADR-0011, ADR-0016, ADR-0017; INV-05; CAP-DATA-MIRROR, CAP-MIRROR-EXTERNAL.
