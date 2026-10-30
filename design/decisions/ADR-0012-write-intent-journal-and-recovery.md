# ADR-0012: Write-intent journal and one recovery spec

This record makes multi-file writes crash-safe and defines the single recovery procedure for writes and lifecycle runs.

- **Status:** Accepted. Amended on 2026-10-30 (S0061): every write of ours goes first to a staging name, deleted whatever it holds; a temp name is ours only when it holds the pre or post bytes (the 2026-10-29 refinement and its post copy are replaced). Amended on 2026-10-29 (S0061): the temp rule refined (a temp is deleted only when it is empty, a cut write of ours, or our own bytes). Amended by ADR-0063 (2026-10-27): recovery runs before any workspace opens, for every journal in the app; a journal names each file by its canonical path, created files included. Amended by ADR-0064 (2026-10-27): a failed rollback pauses writes with Reopen instead of `RECOVERING`; F-6's locked snapshot reads are replaced; temps cut off mid-write never block.
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

## Amendment (2026-10-27): recovery before any workspace opens (ADR-0063)

Owner, approving the recovery rule: "Yes, write both into the hand-off and give me the prompt" (2026-10-27, in session, relayed by the orchestrator; the rule and the reasons are in ADR-0063 decision 2). Details marked "(agent)" are ADR-0063's.
- **Superseded:** "recovery runs when the workspace is next opened (`RECOVERING`), before any other step". **Instead:** before any workspace opens (at start, and at every switch after the current workspace has closed), every write journal in `local/` is recovered, whichever workspace it belongs to. For the workspace being opened, recovery is still the step before any other.
- **The journal records, per file, also its canonical path**, resolved once when the journal is written, and the save writes exactly those paths (agent). A file the save creates is recorded with pre state "absent"; recovery removes it only if it holds the post bytes. Recovery writes a recorded path only while it still resolves to that same canonical path; otherwise the journal is not recovered.
- **A journal that cannot be recovered** (a file unreachable or no longer at its recorded path, foreign bytes, a live writer's lock) stays and blocks the open of every workspace whose ledger set uses a file it names, its own included, also when its folder is reached by Locate or "Open folder…" (`RECOVERY_BLOCKED`, INV-32). A journal whose file list cannot be read blocks every open and copy. The way out of this ADR's stopped state, and of an unreachable or unreadable journal, is the deliberate action "Keep the files as they are now": it drops the journal, writes nothing to the ledger files, lists the files it releases (with both versions for foreign bytes) and is logged (owner: "Yes, I agree with the keep the files as they are now way out.", 2026-10-27, relayed by the orchestrator). Forget keeps the journal (agent).
- **Unchanged:** the per-file rule, the fsync order, in-process rollback, the order with lifecycle run records, the notice (it now names the workspace whose save was undone), Q29 and Q30 (recovery runs only in the open path, with the journal's file locks taken without waiting; a held lock means a live writer, so that journal stays).


## Amendment (2026-10-27): one engine, one job at a time (ADR-0064)

Owner, on the second golden review of the engine-scheduler spec (review 6): "I agree with the four simpler policies and two decisions that you proposed." (2026-10-27, in session, relayed by the orchestrator). Details marked "(agent)" are ADR-0064's.
- **Superseded:** "If rollback fails, the workspace enters `RECOVERING` (writes blocked) with a notice." **Instead:** the journal stays open, the workspace's engine jobs are refused with `WRITES_PAUSED {unfinished_save}`, reads continue from the published mirror, and the user is offered **Reopen**, a switch to the same workspace whose recover-all (ADR-0063) undoes the save. The same holds after an engine death with an open journal (Q30's "ask the user to reopen"). A read-back mismatch takes the same path.
- **Superseded (agent, ADR-0064 decision 9):** S5 gap F-6's "the WARMING rebuild and snapshot reads take the locks". With one engine job at a time no save of ours runs beside a load; loads read without locks, re-check the fingerprint after the load (under the locks for a publish), and refuse while a journal is open.
- **Temps** (owner, review 6 P1): a save's temp counts as a displaced outside save only when its file already holds the post bytes; otherwise recovery deletes it, whatever it holds. Recovery and rollback write their restore temps under their own names and delete them without a check. A journal is written as `.part` and renamed `.open`; recovery deletes a `.part` (no replace happens before the rename).
- **Unchanged:** the per-file rule, the fsync order, in-process rollback itself, the order with lifecycle run records, Q29 and Q30.

## Amendment (2026-10-29): the temp rule refined (S0061)

- **Deciders:** owner, 2026-10-29, in session, relayed by the orchestrator, answering what to do about the two narrow losses found under the review-6 temp rule (S0061 data-safety red team round 2, M1 and M2; verifier round 2, F2): "Refine the rule". The option as shown: "A temp that isn't empty, a prefix of our new bytes, or the old bytes becomes a kept copy instead of being deleted; recovery stops until you delete it. Closes both cases; more kept copies after power losses."
- **Superseded:** this record's amendment of 2026-10-27, "**Temps** (owner, review 6 P1): a save's temp counts as a displaced outside save only when its file already holds the post bytes; otherwise recovery deletes it, whatever it holds. Recovery and rollback write their restore temps under their own names and delete them without a check."
- **Instead:** a save's temp, or a restore temp, is deleted only when it is empty, a prefix of the bytes written into it (the post bytes for a save temp, the pre bytes for a restore temp: a write cut off at any point), or equal to the pre or the post bytes. Anything else is an outside save: it is moved to its kept name beside the file (ADR-0013's amendment of 2026-10-29), and the journal stops (`kept_copy`) until the user has deleted it. This holds whatever the file beside it holds. A temp cut off while it was written still never blocks.
- **Details (agent, S0061):** to recognise a cut write of the post bytes when the file is back at pre, the save writes a copy of each file's post bytes into its own backup folder with the backups (`<index>.post-<name>`, §6.1 step 8.1), used only when it hashes to the recorded post hash; without it, only an empty temp or one equal to the pre or post bytes would count as ours. The temp's own entry is synced before its swap, so an in-place write an editor makes through the file's name while the swap is not yet durable always keeps a durable name. The rule's one place is `ledger_engine.ledger.core.recovery.classify_temp`.
- **Unchanged:** the per-file rule, the fsync order, in-process rollback and everything else in this record.

## Amendment (2026-10-30): the staging name (S0061)

- **Deciders:** owner, 2026-10-30, in session, answering the orchestrator's question `design/inbox/answered/20261030-temp-staging-name.md` (S0061 round 3: verifier F1 High, F2 Medium, F3 Low; data-safety red team F1 and F2 Medium, F3 Low): "Staging name (Recommended)". The option as shown: "**A staging name (recommended).** Write the new bytes first under a separate staging name, `.<name>.ft-<session>-<job>.new`, and sync them. Then rename the staging file to the temp name, sync the folder, and swap. No swap ever touches the staging name, so anything there is our own write and recovery deletes it whatever it holds. A cut never blocks, even a torn one. The temp name then only ever holds our fully synced bytes or the displaced old object. The rule there becomes: equal to the old or new bytes is ours; anything else goes to a kept copy and the journal stops. The restore temp works the same way." with "Removed: the prefix clause, the "empty" clause, and the post copy `<index>.post-<name>` in the backup folder (it existed only to recognise prefixes)."
- **Why:** one temp name played two roles. Before the swap it is where the new bytes are written, so a crash can leave a cut write of ours there; after the swap it holds the file's old object, into which an editor that still has the file open can write. The refined rule could not tell the two apart: an editor shortening the file in place left a prefix of our bytes there, and recovery deleted it as our cut write (red team F2, reproduced on APFS; verifier F3); and a real volume can tear an unsynced file with zeros rather than as a prefix, so a cut write of ours became a kept copy and blocked (red team F3).
- **Superseded:** this record's amendment of 2026-10-29, "a save's temp, or a restore temp, is deleted only when it is empty, a prefix of the bytes written into it (the post bytes for a save temp, the pre bytes for a restore temp: a write cut off at any point), or equal to the pre or the post bytes", and its "Details": the post copy `<index>.post-<name>` in the save's backup folder.
- **Instead:** every write of ours, the save's and the restore's, goes first to a staging name (`.<name>.ft-<session>-<job>.new`, a restore's `.<name>.ft-restore-<session>-<job>.new`), is synced, renamed exclusively to its temp name, and that rename is synced before the swap (or the exclusive rename of a created file). A regular file at a staging name is ours whatever it holds. A temp name is ours only when it equals the pre or the post bytes; anything else there, an empty file and a prefix included, is an outside save, moved to its kept name, and the journal stops, whatever the file beside it holds. Anything that is not a regular file at any of these names is not ours. A created file at post is ours only while neither its temp nor its staging file is there.
- **Details (agent, S0061 round 3):** the same staging is used for a file the save creates (staging, temp, then the exclusive rename to the path): the created file's temp name can hold an editor's bytes after a power loss drops its exclusive rename, so it needs the equality rule too, and a cut write must then not land there. The restore's swap now has the rename and the folder sync before it (verifier F1 High: without that sync an editor's in-place write right after the restore swap could lose its only name in a power loss). The save refuses a target at or under a staging name and an entry standing at one before its first effect, and decode refuses a record that names one as a path. The rule's one place is `ledger_engine.ledger.core.recovery.classify_temp` and `classify_staging`; the names come from `ledger_engine.ledger.core.journal.scratch_names`.
- **Unchanged:** the per-file rule, the kept-name design (ADR-0013's amendment of 2026-10-29), the fsync order otherwise, in-process rollback and everything else in this record.

## References

review:D1 §12.10, §13.1, §13.3; review:15 §2; review:25 §8.3, §9.2, §9.7, §9.8, §14 item 4; ADR-0007, ADR-0008, ADR-0050; INV-01, INV-22, INV-30.
