# ADR-0068: Step-back simplifications

This record keeps the save's durable core and removes, or turns into policy, the mechanisms around it that guard against things that are rare, advisory, or already guarded elsewhere. It also fixes the rule for saves the app starts itself, and adds two process rules.

- **Status:** Accepted. D6(a)'s red-team scope narrowed, and D6(b) moved into PROCESS.md §3's review budget, by IMP-0067 and IMP-0068 (2026-11-12; note below).
- **Date:** 2026-11-01
- **Deciders:** owner. The owner asked for a step back ("At what point are we going to take a 'step back' and do a broad review of what we have built so far?", then "Yes", 2026-11-01). Two fresh reviews ran at `4f84645e`: an area review of the save path and a proportionality review of every mechanism on it (harm, likelihood, cost, verdict) (review:m1a/step-back-2026-11-01). The orchestrator put their proposals to the owner, who answered, 2026-11-01, in session: **"I agree with 3,4,5,6, and 7."** and, after the orchestrator clarified proposals 1 and 2 and the review's High finding, **"Yes, go ahead with all of those"**. Decisions D1 to D8 are those proposals; what this record adds within them is marked "(agent)".
- **Area:** write path, data safety, engine, recovery, formal model, process
- **Supersedes:** ADR-0067 decision 3 (R4 compares and shows a notice) and the parts of ADR-0008 (step 3's full load run again inside the save; step 7, the save's load result adopted for the mirror; the 2026-10-25 amendment's R4), ADR-0010 (guardrail 3, the runtime re-check), ADR-0011 (the save's "one full load") that rest on it (D1); the fallback rules on volumes without the swap of ADR-0013's 2026-10-31 amendment and the owner's answers they followed (2026-10-29 "Unsafe fallback on such drives"; 2026-10-31 "Empty = never ours" and "Keep refuses …") (D2); the ledger-set locks: ADR-0012 Q29 and the file locks of its recovery, and the locks kept "as a second line" (ADR-0064's 2026-10-28 amendment, ADR-0063's "Retry too", owner 2026-10-29) (D3); ADR-0062 decision 2 and INV-26 for a save (D4); the engine-jobs model's scope beyond durability (D5); for D7: ADR-0064 decision 3's Cancel of another job, the quit notes of ADR-0064 decision 11 and fidelity review 3, ADR-0012/ADR-0063's record matching by lineage id, and the owner's requirement of 2026-10-26 for a decision log with a CI replay and a flight recorder, narrowed.
- **Unchanged:** Job 1 of ADR-0067 (integrity always blocks; ADR-0008 steps 1, 2, 2b, the fingerprint check of step 4, the atomic write with backup, journal and read-back, recovery; INV-01, INV-22); ADR-0012's journal, fsync order and recovery rule (not Q29's locks, D3); ADR-0013's swap on drives that have it; ADR-0063; ADR-0064's one engine, one job at a time, the queue, the commit point and the engine lock; ADR-0062 decisions 1, 3-6 and the legacy lock for Copy.

## Context

Finzytrack writes the user's ledger; Beancount never does. The one harm Finzytrack can add is to lose or tear bytes (the user's, or another program's) on a crash, a power cut, or an editor or sync client saving at the same moment, or to tell the user "saved", "undone" or "unchanged" when that is not true. The durable core guards exactly that: the write-intent journal, synced backups, the atomic swap with the displaced-file check, kept copies, the read-back, recover-before-open. The proportionality review found it proportionate.

Around it the review found mechanisms whose cost is out of line with what they protect:
- **The save loads the ledger again.** After the user confirmed the preview, the save ran Beancount's full load of the candidate a second time (about 1.2 s of the 1.3 s a save takes at 54k entries), re-checked the fingerprint after it, and then R4 loaded the disk a third time to compare. ADR-0067 already made all of this advisory: none of it can refuse a save. It protects no byte.
- **Drives without the swap** (exFAT, FAT, network shares, HFS+) got their own fallback with ownership rules (S0065: three plus three review rounds, three High findings), for a setup that is rare for a desktop ledger.
- **Per-file locks** on every ledger file duplicate the engine lock, which already keeps a second engine of ours from running.
- **The legacy app's lock** is tried before every save, although ADR-0062 makes the protection structural: a ledger reaches v0.4 only by Copy, so the old app never writes v0.4's files.
- **The formal model** grew past EP-19's durability scope: about 45% of its mutants check queue, screen and message behaviour that table and stateful tests already check.
- **Process:** S0066 and S0067 took four plus four rounds and four High findings, all in machinery ADR-0067 then removed; S0061 spent rounds 5 to 7 on permission corners that lose no byte.

The area review also found a High defect: a second background save while one is queued got the first one's ticket, so it was told "saved" while its change was dropped (step-back area review F1).

## Decision

### D1: the save does no Beancount load of its own; a rebuild after the save updates the screens (owner)

- The save trusts the **preview's** load. The preview is a job before the confirm (BL-0106); it loads the candidate with Beancount and lists new errors (ADR-0067 decision 2). Its load was made on a fingerprint; the save checks that the disk still has that same fingerprint before it writes. Same bytes in, same result: the preview's result is the prediction.
- **Removed:** the save's own full load (spec §6.1 step 5), the re-check after it (step 6), R4 and its comparison (step 11, §6.7), the record's prediction fields (adopted digest, post fingerprint), the parse digest machinery (`digest.py`), R4's engine op, protocol and sidecar work, and the log events `r4.differs` and `r4.not_compared`.
- **Added:** after every landed save, the sidecar queues a **background rebuild with Beancount's real loader** (the mirror publish, BL-0113). The screens always show Beancount's own result and its errors, right after "saved"; until the rebuild ends they show a short "Checking…".
- **Job 1 is unchanged:** parse, semantic diff and outside-span identity before writing (ADR-0008 steps 1, 2, 2b), the fingerprint check, the atomic write, the syncs, the read-back, the journal, the backup, recovery.
- **What is lost, stated honestly:** the "Beancount read your ledger differently" notice (a plugin that answers differently from run to run, or reads files outside the fingerprint, could make the screens differ from the preview without a notice; the screens then simply show the real result), and a recomputation of the errors at the moment of saving (the preview's list is the one the user accepted). Neither loses a byte or makes a false "saved".
- Example: you add a lunch expense. The preview loads the ledger with the new entry and shows "no new errors". You press Save. Before writing, Finzytrack checks that the files still have the fingerprint the preview loaded; they do, so it writes (backup, journal, swap, read-back) and says "Saved" in about 0.15 s instead of 1.3 s. A moment later the screens rebuild from Beancount's own load, and "Checking…" disappears.
- **Built in S0070**, not in S0069. Until then the code still has steps 5, 6 and R4.

### D2: drives without the swap: a simple best-effort mode (owner, the owner's own idea)

- On drives without the atomic swap (exFAT, FAT, network shares, HFS+), saving stays possible as a **best-effort mode**: the journal, the backup of every save and the read-back are kept; the file is replaced by a plain rename (no swap). There is **no** detection of another program saving the same file at the same instant, and none of S0065's ownership rules remain (claiming a missing file, "Empty = never ours", Keep refusing while a file is missing, network-retry ownership, the windows W1 to W4).
- A notice when such a workspace opens (BL-0166) says roughly: "This drive (for example a USB stick or network share) can't replace files in one safe step. Finzytrack still keeps a backup of every save, but if another program edits the same file at the very moment you save, its change can be lost. For full protection, keep your ledger on your computer's own disk."
- Recovery on such a drive restores from the backup with a plain rename.
- Regular disks keep the full swap (ADR-0013): Mac (APFS, `renamex_np` with `RENAME_SWAP`), Linux (ext4, btrfs, xfs: `renameat2` with `RENAME_EXCHANGE`), Windows (NTFS: `ReplaceFileW`).
- Example: your ledger is on a USB stick. You save; Finzytrack backs up the file, writes the new version beside it and renames it over the old one. If Dropbox wrote the same file in that same instant, Dropbox's version can be lost (the backup still holds the version before your save). On your Mac's own disk the swap would have caught it.
- **Built in S0071.**

### D3: no per-file ledger-set locks; the engine lock is the guard (owner)

- The lock files under `app_local_data/locks/` and the save's wait for them ("Waiting for your ledger files…") go. The engine lock (§5.1) already keeps any other engine of ours from running; the save op refuses to run without it, as recovery's ops already do.
- Reverses the owner's 2026-10-29 "keeping the per-file locks as a second line" (the "Retry recovers first" half of that answer stays).
- **Built in S0071.**

### D4: the legacy app's lock only for Copy (owner)

- The save no longer tries the legacy (v0.3) app's `.write.lock`. Copy keeps the try (ADR-0062 decision 2, narrowed to Copy). A ledger reaches v0.4 only by Copy, so the old app never writes v0.4's files; if a user points the old app at a v0.4 workspace anyway (unsupported, ADR-0062), its writes are outside edits, caught by the fingerprint check and the swap's displaced-file check.
- On a drive without the swap (D2) such an outside write in the same instant as our save is not detected either: the backup holds the bytes before our save.
- INV-26 is narrowed in the same words. The owner confirmed the narrowing on 2026-11-01, in session, choosing "Confirm (Recommended)". The save's try is removed in S0071.
- **Built in S0071.**

### D5: the engine-jobs TLA+ model checks durability only (owner; EP-19)

- Kept: save, rollback, recovery, power loss, the outside editor, create and copy, their durability mutants, and the save's conformance replay.
- Removed: the queue, switch, boot, quit, advice and screen actions, the liveness worlds (`LS_*`), `LM_free`, and the queue's trace replay. Queue behaviour stays covered by its table and stateful tests.
- **Built in S0072.**

### D6: process (owner)

- **(a)** A mechanism that neither writes ledger or user data nor gates a write gets the slice verifier only, never the data-safety red team. Every write-path brief states in one line "the bytes it protects"; "none" means the mechanism is advisory, and EP-30's question "can a policy forbid it?" goes to the owner before coding.
- **(b)** A second fix round on the same mechanism needs the owner's options note first (situation, likelihood, options, recommendation).
- (a) and (b) are in PROCESS.md §3 and §4 and IMP-0051 from S0069.
- **(c)** `design/specs/engine-jobs.md` is rewritten to rules only (about a third of its length); provenance stays in ADRs and journals. **S0073.**

### D7: smaller cuts (owner)

- The unbuilt quit notes are dropped from the spec.
- The decision log builds only the cheap, content-free `sched.input` and `sched.decision` log lines. The CI replay, the record-replay walk and the flight recorder are deferred until a field bug needs them (the owner's requirement of 2026-10-26, narrowed).
- Cancel only ever ends your own job (the "one rule", where a waiting user's Cancel could end another job running ahead, goes). A read whose requester has gone still ends. The trade-off against ADR-0064 decision 3's "every wait ends on the user's word" (a user waiting behind a long background job, such as the rebuild, can no longer end it) was put to the owner, who chose on 2026-11-01, in session: "Keep B7 (Recommended)".
- The process-group drain is simplified: signal the group, reap, never signal after the reap; the `libproc` listing goes.
- Records are matched by `path_key` only, not by lineage.
- **S0073** unless trivial.

### D8: a background save plans when it runs, and a second one joins it (owner, the High's fix)

- A save the app starts itself in the background (for example the future price update) **plans when it runs, not when it is requested**. A second request arriving while one is queued **joins** the queued job: its changes are included, and every caller gets the true outcome of the job that wrote its changes. A request is never answered with another job's ticket or outcome unless its own changes were in that job.
- **(agent) What S0069 builds:** the price planner does not exist yet, so the scheduler's rule is generic. A second background save of a workspace whose save is queued joins it only when the queued work declares it can take the new request's changes (`JoinableWork.joined`, answering the one work that saves both); both tickets then end with that job's outcome. Otherwise the second request is refused with its own answer, `ENGINE_QUEUE_FULL` ("Finzytrack is busy with other ledger work. Please try again in a moment."), which is true: nothing of it was saved. A save submitted with a fixed plan (`save.submit_save`) cannot join, since two fixed plans made on one fingerprint cannot both be written. The price feature's work will plan at run time and declare the join (BL-0177). The owner accepted this interim on 2026-11-01, in session: "OK (Recommended)".

## Alternatives considered and rejected

- **D1: keep the save's load but skip R4** (half the saving): the save's load is the expensive part and is advisory since ADR-0067; nothing it finds can stop the write.
- **D1: keep R4 as a background comparison only** (no save-time load): it protects no byte, and once the screens come from Beancount's real loader, the "unseen in the screens" reason for ADR-0067 decision 3 no longer holds.
- **D2: refuse saving on such drives** (the proportionality review's proposal; reading would still work): the owner preferred a best-effort mode with an honest notice, since a USB stick or network share is a real setup for some users and the backup still guards every save.
- **D2: keep HFS+ with the exclusive rename only** (the review's middle option): it keeps one of S0065's windows and its rules for an old file system; the best-effort mode is simpler and the same for every such drive.
- **D8: refuse every second background save** (the area review's fix): simpler, but a price update that arrives while another waits would be lost and must be re-fetched; the owner chose the join. The refusal remains for works that cannot join.
- **D8: answer the second request with the queued job's ticket** (the code before S0069): a false "saved".

## Consequences

- A save at 54k entries drops from about 1.3 s to about 0.15 s; loads per edit go from three to two (the preview and the rebuild).
- About 0.6k source and 1.2k test lines go with R4 and the save's load (S0070); about 200 source lines, the no-swap fake sweeps and their windows go with D2 (S0071); about 80 source and 300 test lines go with the per-file locks (S0071); the model loses about 37 of its 81 mutants (S0072).
- None of D1 to D5 adds a way to lose bytes or a false "saved", "undone" or "unchanged" on a drive with the swap. D2 accepts, by policy, that on a drive without the swap an outside save in the same instant can be lost; the backup and the notice say so.
- The live docs state the new rules now; code built in a later slice is marked "(to be built: S00xx)". The retired-terms check refuses the old words that no current document needs (S0069), and each later slice activates the terms its code removes.
- The area review's F2 (R4 deaths counted toward the stop limit), F4 (R4's notice has no way out) and F5 (step 5 mixing the preview's load and the save's own) become moot with D1; S0070's backlog row lists them so they are checked gone.

## References

- review:m1a/step-back-2026-11-01 (area report, proportionality report, area probes)
- `design/specs/engine-jobs.md` §2, §3, §5, §6.1, §6.7, §6.8, §13; `design/ARCHITECTURE.md` §9
- ADR-0008, ADR-0010, ADR-0011, ADR-0012, ADR-0013, ADR-0062, ADR-0063, ADR-0064, ADR-0067
- INV-01, INV-10, INV-22, INV-26; EP-19, EP-30
- Backlog: BL-0106 (preview), BL-0113 (publish), BL-0166 (open notice), BL-0177 (price planner join), BL-0178 to BL-0181 (S0070 to S0073)
- Journal: `design/journal/S0069-step-back.md`

## Note (2026-11-01): D1 built (S0070)

D1 is built (`design/journal/S0070-save-no-load.md`). Within D1 the implementer decided (agent): the save refuses a change to an encrypted file name itself (ADR-0059), since no load meets it now; the rebuild after a save is sent uncounted, so its engine death is no stop toward the restart bound (the area review's F2 recommendation); a moved disk tries again in a fresh engine, at most 3 tries; the journal format goes to 4 (no prediction fields; older formats unreadable). The "Checking…" text and the screens' update come with the mirror publish (BL-0113). Measured at 54k entries: the save 1.81 s → 0.096-0.122 s; the rebuild after the reply 1.31-1.32 s.

## Note (2026-11-01): D7 built in part (S0073 part A)

Built (`design/journal/S0073-spec-rules.md`): a Cancel ends only your own job (the queue's table version 9; a waiting user's answer says the earlier work cannot be stopped from here whenever the job ahead is not a waited-for job of its own requester, background work included); the drain holds the exited leader, signals the group once and reaps, with no listing of its members, so a member still exiting is waited for at the engine lock; the quit's notes are gone from the spec and from BL-0154. Within D7 the implementer decided (agent): the decision log's two lines stay in BL-0154, since they need a redacting encoder for every queue event with a must-not-leak test. **Not built:** records matched by `path_key` only. The lineage match keeps a moved workspace folder that holds a half-applied multi-file save from opening, or being saved over, without the blocked screen; matching by `path_key` alone would let it open silently. It went back to the owner with options (the journal's Brief).

- **Correction (2026-11-01, S0073 part B round 1):** the answer above is stated too loosely. As built (`queue._cancel_queued`, the step property in `test_engine_queue.py`), the waiting user reads a plain "Cancelled." only when no job runs, or the job ahead is a read, an open or a recover-all that its own requester still waits for. Behind any other job, a confirmed save, `create` or `copy` included, they read "cannot be stopped from here" (spec §4).

## Note (2026-11-01): D6c built (S0073 part B)

`design/specs/engine-jobs.md` now states rules only (`design/journal/S0073-spec-rules.md`, Part B). Its history moved here, to ADR-0064 and to the slices' journals. One quote the spec alone held is kept here: the decision log that D7 narrows was the owner's requirement of 2026-10-26, "Yes, add it to the scheduler spec as a requirement. Let's make sure this logging system design is documented and implemented". D7 keeps its `sched.input` and `sched.decision` lines; the `sched.checkpoint` lines are not in the spec, and the replay and the ring of recent events are named there only as deferred until a field bug needs them (§12).

## Note (2026-11-01): two D7 lines withdrawn, and D6c's length (owner)

S0073's reviews found that two D7 cuts had a cost the step-back review had not stated, and that D6c's length target was not met by rules alone. The owner answered, 2026-11-01, in session: **"I accept your recommendations for lineage match and drain fork race."** and **"Yes, I'm accepting the 65%"** (`design/inbox/answered/20261101-lineage-match.md`, `design/inbox/answered/20261101-drain-fork-race.md`).
- **Records stay matched by lineage** (D7's "matched by `path_key` only" is withdrawn): without it, a moved workspace folder holding a half-applied multi-file save would open silently and accept a save. No code change; BL-0191 closed.
- **The drain's member listing comes back** (D7's "the `libproc` listing goes" is withdrawn; "signal the group, reap, never signal after the reap" stays): on macOS one signal misses a plugin child that forks while it is delivered, and a survivor holding the engine lock blocks all ledger work until a restart. Restored in BL-0190, with BL-0158.
- **D6c:** the rules-only spec is 65% of the old words, not a third; the owner accepted it. Generating §11 from the text modules and folding §10/§15 into their rules is BL-0192.


## Note (2026-11-01): the drain's member listing restored (S0075)

Built (`design/journal/S0075-drain-listing.md`, BL-0190, BL-0158): the drain of dfe3aee8 is back (hold the exited leader, signal the group and list its members until none but the leader is left, reap, never signal after the reap), with one change: on macOS a listed member whose status reads zombie (`sysctl(KERN_PROC_PID)`, `p_stat == SZOMB`) is not counted, so a zombie whose parent left the group no longer keeps the drain going. The engine's own end when the sidecar is gone stays one signal, a limit of the spec's §15 (BL-0194); so does a quit's end, since the sidecar exits without waiting for the drain (S0075 verifier r1 F3).

## Note (2026-11-07): a quit waits for the drain (S0076)

The previous note's "so does a quit's end" no longer holds: owner, 2026-11-07, in session: "Yes, make quit wait for the drain." `close()` now waits for the drain of the engines that are ending, at most the display delay (ADR-0063 amendment of 2026-11-07; `design/journal/S0076-quit-drain.md`). The engine's own end when the sidecar is gone stays one signal (BL-0194).

## Note (2026-11-12): D6 in the review budget (IMP-0067, IMP-0068)

The owner answered, 2026-11-12, in session, on the M1a reflection: **"I agree with your view. Let's adopt it."** D6(a)'s red team now runs only on a slice that adds a new way to write, delete or move ledger or user data, or a new outcome text for a write; a slice that hardens an existing write path (a new check, a sync, an identity test, a gate's input) gets the verifier, and the data-safety red team joins when the verifier reports a class (a) finding (IMP-0067). "The bytes it protects" stays in every write-path brief. D6(b), the owner's options note before a second fix round on one mechanism, is unchanged and now sits in the review-budget table (IMP-0068) (S0107).
