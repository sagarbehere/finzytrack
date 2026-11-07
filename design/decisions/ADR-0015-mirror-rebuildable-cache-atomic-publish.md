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

## Amendment (2026-10-25): a grace period for outside readers (spike S5, Q31)

Owner: "I accept your recommendation on Q31" (2026-10-25, in session).
- Garbage collection of mirror generations cannot see pins held by outside tools (a SQLite viewer, a user script). So a superseded generation is kept for a **grace period of 10 minutes** after it is replaced, and the **previous generation is always kept**; only then is it deleted (if it is still pinned inside the app, later).
- A deletion that fails because a file is open (Windows) is expected and retried at the next collection, never reported as an error.
- The grace period is a configuration value (INV-10 style bound), not a development target.

## Amendment (2026-10-29): the fingerprint is a content hash

Owner: "Yes, keep numbers beyond 28 digits as exact, with a warning and amend adr-0015 to content hashes" (2026-10-29, in session; `design/inbox/answered/20261028-adr-0015-fingerprint.md`).
- The ledger fingerprint is no longer `(path, mtime, size)` of every include. It is, as `design/specs/engine-jobs.md` §1 defines it and S0051 built it: per file the load reads, a SHA-256 of its bytes or its state (absent, or unreadable with its error); per `include` (a plain path too, which the loader also globs), the glob's match list in the loader's order; per documents folder, its listing with each name's presence; the presence of the file of each `document` entry Beancount checks.
- Why: an edit that keeps the size and restores the modification time is caught, and a `touch` or a sync tool rewriting the same bytes no longer forces a rebuild. Measured cost at 54k entries: about 3 ms per check (S0051).
- ADR-0064's "Unchanged: … ADR-0015" holds for everything else in this ADR.

## Amendment (2026-11-07): the mirror folder is ours; grace period only (S0085)

The publish's design was written as rules in `design/specs/mirror/publish.md` (S0085, BL-0113). Two questions there were the owner's; the orchestrator put them in session, 2026-11-07:
- **Ownership of the mirror folder** (`…/workspaces/<key>/mirror/`): owner chose **"Whole folder is ours"**: the publish and its cleanup may delete anything in that folder and replace whatever sits at `current.db`. The orchestrator's safety rule, stated to the owner with it: the cleanup never follows a link; a link at `mirror/` itself or inside it is removed as a link, never what it points to.
- **Reader pinning:** owner chose **"Grace period only (Recommended)"**: the amendment of 2026-10-25 is the whole rule: the previous generation is always kept, a replaced one is kept 10 minutes, then deleted. There is no pin tracking in the app: on macOS and Linux, removing a file that an open connection reads is safe. A read resolves `current.db` once and opens that file. This replaces "Old generations are removed once no reader holds them" and the 2026-10-25 amendment's "(if it is still pinned inside the app, later)".
- **Windows** (the pointer file, and a deletion that fails because a file is open, retried at the next cleanup) moves to M11 (ADR-0061): BL-0206. Until then the design is macOS and Linux only.
- Agent decisions within these (S0085): the names (`gen-<N>.db`, `current.db` a relative link), the number (above every number still in a name, and above the sidecar's floor), the cleanup first in every `open` and `rebuild` job, the replacement time of a generation read from its successor's modification time, `ledger_version` = `<N>/<digest>`, and the save's gate reading the published generation itself (`SaveRequest.mirror` goes). Their reasons are in the spec's §12 and the S0085 journal.
- Open: how many generations the grace period may keep during a burst of saves (`design/inbox/20261108-mirror-generations-disk.md`).

## References

review:D1 §11; review:10 I4, D4, D5; review:23 §1.2, §3.4; review:00 §1b; ADR-0011, ADR-0016, ADR-0017; INV-05; CAP-DATA-MIRROR, CAP-MIRROR-EXTERNAL.
