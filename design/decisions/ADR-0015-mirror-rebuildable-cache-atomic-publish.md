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

## Amendment (2026-11-07, extended 2026-11-08): the mirror folder is ours; grace period only, at most 2 extra copies; names never reused (S0085)

The publish's design was written as rules in `design/specs/mirror/publish.md` (S0085, BL-0113). Two questions there were the owner's; the orchestrator put them in session, 2026-11-07:
- **Ownership of the mirror folder** (`…/workspaces/<key>/mirror/`): owner chose **"Whole folder is ours"**: the publish and its cleanup may delete anything in that folder and replace whatever sits at `current.db`. The orchestrator's safety rule, stated to the owner with it: the cleanup never follows a link; a link at `mirror/` itself or inside it is removed as a link, never what it points to.
- **Reader pinning:** owner chose **"Grace period only (Recommended)"**: the amendment of 2026-10-25 is the whole rule: the previous generation is always kept, a replaced one is kept 10 minutes, then deleted. There is no pin tracking in the app: on macOS and Linux, removing a file that an open connection reads is safe. A read resolves `current.db` once and opens that file. This replaces "Old generations are removed once no reader holds them" and the 2026-10-25 amendment's "(if it is still pinned inside the app, later)".
- **Windows** (the pointer file, and a deletion that fails because a file is open, retried at the next cleanup) moves to M11 (ADR-0061): BL-0206. Until then the design is macOS and Linux only.
- Agent decisions within these (S0085): `current.db` a relative link, the number above every number still in a name and above the sidecar's floor, the cleanup first in every `open` and `rebuild` job, a replaced generation's modification time set at its replacement, `ledger_version` = `<N>/<digest>`, and the save's gate reading the published generation itself (`SaveRequest.mirror` goes). Their reasons are in the spec's §12 and the S0085 journal.
- **Copies kept during a burst of saves** (owner, 2026-11-08, in session, on `design/inbox/answered/20261108-mirror-generations-disk.md`): **"At most 2 extra (Recommended)"**: keep the 10 minutes, but at most 2 old copies besides the current and previous ones (oldest goes first).
- **Names never reused** (owner, 2026-11-08, in session, after S0085's round-1 reviews found that a number could come again after the OS purges the cache while a request or an outside tool still opens the old name): **"Random tag in name (Recommended)"**: names become `gen-<N>-<random>.db`, so a name is never reused whatever is purged; `N` only orders them; nothing is written outside the mirror folder; within a session the version never goes backwards; only after the OS purges the cache may the next session's number start lower (nothing compares versions across that). This is the one stated exception to "`ledger_version` … monotonic across restarts" above.
- Agent decisions after round 1 (S0085): the save's gate checks that the published generation is fresh against the disk on its own ledger set, beside the disk equal to the plan (comparing the generation with the plan's set refused every save that creates or includes a file); the folder sync after the swap is cut (the next job's folder sync makes the rename durable); each step works through one folder descriptor that follows no link. Reasons in `design/specs/mirror/publish.md` §7, §12 and the S0085 journal.


## Amendment (2026-11-14): Windows in M1b

Where this record says Windows "moves to M11 (ADR-0061): BL-0206", read M1b: the engine's write path is ported to Windows in M1b so its real-disk write-contract tests run on the Windows runner (ADR-0061 amendment of 2026-11-14; owner: "(b) for windows"), and the save gates on the published mirror, so the publish's Windows design (the pointer file, the retried deletion) comes with it (agent: a dependency of that port, an earlier move).

## Amendment (2026-11-18): the pointer file on Windows (S0130)

Owner, 2026-11-18, in session, on `design/inbox/answered/20261118-windows-pointer-swap.md`: "I agree with your recommendations on the 4 questions you raised. Proceed accordingly."
- **The pointer** (built in S0130, BL-0206; agent decisions within this record's "pointer file on Windows"): `mirror/current.txt`, whose whole content is the current generation's name. It is written as a new synced file and renamed over `current.txt` with POSIX semantics, so a reader that shares deletion never refuses it (probed: the plain rename is refused while any handle holds the target). Every reader of ours shares deletion. A removal refused because a generation is open is retried at the next cleanup (the amendment of 2026-10-25). The TLA+ model is unchanged (`formal/tla/mirror-publish/MAPPING.md` §6).
- **Where the POSIX rename is offered** (question 3, option 2): by what the volume offers, i.e. the flag `FILE_SUPPORTS_POSIX_UNLINK_RENAME` and not a network share, never by the file system's name or an error. Elsewhere (question 1, option 1) the swap takes the plain rename, which a read in flight can refuse; that is a stated limit (publish.md §11), never a silent one. The minimum Windows version is stated in the release notes. To build: BL-0294.
- **Read-only entries** (question 4, option 2): "the whole folder is ours" (2026-11-07) includes clearing the read-only attribute on what the cleanup removes and what the swap replaces in the mirror folder, so a read-only `current.txt` or generation neither blocks the publish nor stays on disk forever. To build: BL-0294.
- **Outside tools on Windows** (question 2, option 1): there is no stable path SQLite can open there. Copy path gives the current generation's own file, which Windows keeps while a tool holds it and the cleanup removes after the grace period; the user docs state it (CAP-MIRROR-EXTERNAL, M7: BL-0293). This narrows this record's "Outside tools may open the current generation read-only" on Windows to that generation's own path.

## Amendment (2026-11-19): a cache folder on a network share; never clear a linked file (S0132)

Both asked in session as rare-setup policy options before any fix (S0132's runner and round-1 reviews; BL-0294).
- **A cache folder on a network share** (the share's server refuses a folder's sync, `ERROR_INVALID_FUNCTION`, probed on the CI runner): owner, 2026-11-19, in session: "Best effort on a share (Recommended)". On a remote volume, a folder sync the server refuses counts as done for the mirror and is logged once, content-free; the mirror is a rebuildable cache, so a power loss there costs a rebuild, never ledger bytes. Only the mirror's own folder syncs; the ledger's (the save, restore, recover-all, create, copy) fail as before, and whether a ledger on a share may be written is BL-0087's question. The other options were a policy refusing a cache folder on a share, and deferring to BL-0087. Stated in `design/specs/mirror/publish.md` §11 "Mode (a cache folder on a network share)"; `formal/tla/mirror-publish/MAPPING.md` §6 records it as a departure from the model's step-6 availability.
- **Never clear a linked file** (S0132 round 1, verifier V1, red team F1: Windows keeps the read-only attribute on the file, so the amendment of 2026-11-18's clearing, through a hard link in the mirror folder, would unprotect a read-only ledger file elsewhere and, by removing the link, drop the count the save's `hard_linked` refusal reads): owner, 2026-11-19, in session: "Never clear a linked file (Recommended)". The publish clears the attribute only on a file with a single link; a read-only file with several links is left, counted as a removal that failed. The other option was to state it as a limit. This narrows the amendment of 2026-11-18's "clearing the read-only attribute on what the cleanup removes and what the swap replaces".

## Amendment (2026-11-19, later): a cache folder on a network share is refused; the share's best-effort sync removed (S0134)

Asked after S0132 found that no generation can be built on a share (BL-0298: SQLite does not open a UNC path given as `Path.as_uri()`), as a rare-setup policy option (`design/inbox/answered/20261119-mirror-cache-on-a-share.md`).
- **Refused, on Windows:** owner, 2026-11-19, in session: "Ok. Option 1 for Bl-0298." (after asking whether the app's own cache folder ever ends up on a network share); then, asked which scope the refusal should have: "Windows only (Recommended)" (the other option was every OS, as first built). On Windows a mirror cache folder on a network share is refused before any effect, with a named reason (`cache_on_share`) that tells the user to keep Finzytrack's cache on a local disk; every folder of the mirror's path is checked, since a share can be mounted, or linked, at any of them. The other options were opening SQLite in its UNC URI form on a share (the earlier recommendation), and both. On macOS and Linux a cache folder on a share is untested and not refused, a stated limit: no share was probed there (BL-0219's M11 probe). Stated in `design/specs/mirror/publish.md` §2 "On a network share" and §11.
- **The best-effort folder sync on a share removed:** owner, 2026-11-19, in session, right after: "Let's remove the best effort sync code, but only if it's dead code and unreachable". The mode was Windows-only (only the Windows adapter raised `SyncRefused`), and with the Windows refusal no folder sync of the publish runs on a Windows share, which S0134's journal shows site by site before the removal; this replaces the amendment above's "a cache folder on a network share" decision ("Best effort on a share (Recommended)"), which the refusal made unreachable.

## Amendment (2026-11-26): the read-only attribute outside the mirror (S0152)

The clearing of Windows' read-only attribute is no longer the mirror's publish's alone: the undo and the create's cleanup clear it on a folder their own job made, just before removing it (owner, 2026-11-26: "Clear it, then remove (Recommended)"; ADR-0069's amendment of that date). The publish's own rules (the mirror folder only, never a file with several links) are unchanged.

## References

review:D1 §11; review:10 I4, D4, D5; review:23 §1.2, §3.4; review:00 §1b; ADR-0011, ADR-0016, ADR-0017; INV-05; CAP-DATA-MIRROR, CAP-MIRROR-EXTERNAL.
