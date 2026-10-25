# ADR-0012: Write-intent journal and one recovery spec

This record makes multi-file writes crash-safe and defines the single recovery procedure for writes and lifecycle runs.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path, data safety, lifecycle

## Context

Each file is replaced atomically (ADR-0008 step 5), children first and the root last. A crash between two files still leaves a half-applied multi-file edit (for example a rename across includes), which may stop the ledger from loading. The lifecycle runs (migrations, adoption) had their own recovery idea.

## Decision

(owner, 2026-10-24)
- **A write that touches several files first records an intent**, then writes each file atomically, then clears the intent. The journal lives in machine-local state (`local/<key>/`), never in the workspace folder.
- **The journal records, per file:** the pre hash, the intended post hash and the backup path. Its format is versioned; an older app refuses a newer journal.
- **fsync order:** backups + their directory → journal + its directory → replaces.
- **Recovery rule, per file:** current = pre → nothing to do; current = post → restore pre; anything else → stop, block writes, and show both versions. Auto-recovery runs only when every file matches pre or post.
- **When:** recovery runs when the workspace is next opened (`RECOVERING`), before any other step. It takes the journal's file locks non-blocking; a held lock means a live writer, so recovery is skipped. Recovery on open is the one stated exception to INV-30 and shows a one-line notice.
- **In-process rollback:** any exception after the first replace rolls back from the journal at once. If rollback fails, the workspace enters `RECOVERING` (writes blocked) with a notice. A read-back mismatch takes the same path.
- **One recovery spec:** lifecycle runs use apply-then-record with per-run backups. A ledger write inside a lifecycle run uses the write-intent journal. Recovery order: the inner write journal first, then the lifecycle run record. "Roll forward" is not used.
- **Install never proceeds while a write journal is open** (ADR-0007).
- **Version-independent restore format:** `runs/<id>/RESTORE.json` plus plain file copies, frozen so any later version can restore it. A save that upgrades a file's format writes a pinned backup.
- The protocol is part of the TLA+ spec and the Hypothesis crash model (ADR-0050).

## Alternatives considered and rejected

- **No journal (children first, root last only).** Rejected: a crash leaves a half-applied edit.
- **Roll forward on recovery.** Rejected: the post bytes may be incomplete, and the user's foreign edits could be clobbered.
- **Full journaling of every lifecycle step plus a two-release rule.** Rejected as heavier than needed; apply-then-record with backups is enough there.
- **An in-folder "commit in progress" marker.** Deferred: rare case; the pre/post rule already prevents clobbering.

## Consequences

- `NoPartialMultiFileCommitVisibleAfterRecovery` and `RecoveryNeverClobbersForeignBytes` are TLA+ properties.
- A refusal may say "your ledger is unchanged" only when the disk equals the pre-image.

## Amendment (2026-10-25): protocol gaps found by the TLA+ model (spike S5)

Owner: "I accept 29 and 30." (owner, 2026-10-25, in session).
- **Q29, one lock for the whole ledger set:** every write takes the lock for the whole ledger set of its workspace, not only the files it changes (S5 gap F-7: with one journal and a whole-set fingerprint, two writes to different files could overwrite each other's journal). Writes are already serialized per workspace, so nothing is lost.
- **Q30, a leftover journal seen by a second running instance:** a second process that finds a journal it did not write refuses writes and asks the user to reopen the workspace; recovery runs only on open, under the rules of this ADR (S5 gap F-3).
- The other S5 gaps are agent-level tightenings applied with the writer in M1b: hash each backup against the planned pre-hash and fail closed (F-1); re-hash every target before saying "unchanged" (F-2); acknowledge a commit only after the journal close is durable (F-4); restore the root first (F-5); the WARMING rebuild and snapshot reads take the locks and refuse while a journal is open (F-6); a unique journal name per commit and an fsync of the journal directory before trusting "no journal" (F-8); the `FileSystem` port exposes `atomic_swap`, not `os.replace`.

## References

review:D1 §12.10, §13.1, §13.3; review:15 §2; review:25 §8.3, §9.2, §9.7, §9.8, §14 item 4; ADR-0007, ADR-0008, ADR-0050; INV-01, INV-22, INV-30.
