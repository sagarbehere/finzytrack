# ADR-0008: Every ledger write passes the write-safety contract

This record defines the contract that every ledger write must pass before any byte reaches disk.

- **Status:** Accepted. **Amended on 2026-11-28 (G1, S0165):** each wait (the preview's; from Confirm to the mirror's publish) meets the target. **Note of 2026-11-28 (S0167):** a plan is confirmed only in the run that made it; no per-run plan-id prefix (the note of that date). **Amended on 2026-11-15 (S0120):** the 5 s target at 54k entries holds for files of at most 15 MB; a larger file has no size limit and is written more slowly, with a tip to split it (the amendment of that date; measured in S0120). **Superseded in part by ADR-0068 (2026-11-01):** the save's own full load (step 3 run again inside the save) and the re-check after it, step 7's adoption of that load for the mirror, and the 2026-10-25 amendment's R4 re-check (as ADR-0067 left it), are removed; step 4 runs without the ledger-set locks (D3, S0071): the preview's load is the prediction and a background rebuild with Beancount's loader follows every save (D1, built in S0070). **Superseded in part by ADR-0067 (2026-10-31):** step 3 no longer refuses for new errors of any class (the preview lists them and the user may save anyway; steps 1, 2, 2b, 4-7 still block, failing closed), and R4 compares and shows a notice and gates nothing: the R4 rules of the amendments below (2026-10-25, 2026-10-27 D2, 2026-10-31's "two jobs") no longer hold. Amended by ADR-0064 (2026-10-27): a refusal caused by another program's change says so and does not enter `RECOVERING`; an R4 that fails to run three times can be acknowledged by the user. Amended on 2026-10-27 (S0047): the bounded parse cache is built only if the measured save misses the 5 s target.
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path, data safety

## Context

The owner's priority #1 is no ledger corruption and no data loss; performance comes second. The legacy write path re-printed every file from loader output, gated only booking errors, failed open when its own check raised, and had no external-edit check (review:10 I2, I3, I9, I10, I11). Splicing (ADR-0009) changes only what is intended, but a contract that does not care how the text was produced is what guarantees it.

## Decision

(owner, 2026-10-24) Every write, from any code path, including workspace creation and demo seeding, passes these steps. **Each step fails closed** (INV-22): an exception, timeout or undecidable result refuses the write, and a fault-injection test per step proves that no byte reached disk.
1. **Parse old and new** texts of the edited files with Beancount's parser only.
2. **Semantic diff = intended change.** A canonical key per entry (printer output without location meta). Untargeted entries are identical; the targeted set equals exactly the intended inserts, replacements and removals; options, plugins and includes are unchanged unless targeted.
   - **2b. Outside-span identity:** the candidate equals the old text with exactly the planned spans replaced (a byte comparison of the complement). This catches comment and whitespace damage that step 2 cannot see.
3. **Full load gate:** an official full load (booking, plugins, validation) of the candidate through the overlay loader (ADR-0010). **No new errors of any type** relative to the baseline (`ledger_errors` in the mirror), except errors the user explicitly acknowledged.
   - **Error identity:** an error key = error type + canonical entry key + message with locations stripped, so a splice that shifts lines does not make old errors "new". The per-mutator intent table lists which keys each mutator may ask the user to acknowledge.
4. **Concurrency:** under the locks, the **full extended fingerprint** of the ledger set (every file, glob matches, absent paths, documents roots) equals the one the plan was made from; otherwise the write is refused and re-planned.
5. **Atomic write per file:** fail-closed backup (backup and directory fsynced before the first replace), temp file, fsync, replace by atomic swap (ADR-0013), directory fsync; children first, root last; symlinks written at their `realpath`; file mode preserved.
6. **Read-back:** every written file equals the intended bytes; on a mismatch, restore from the write journal and report (ADR-0012).
7. **Adopt:** the step-3 load result feeds the mirror build, then is released (ADR-0011).

A refusal says "Your ledger is unchanged" only when the disk equals the pre-image; otherwise the workspace enters `RECOVERING`.

## Alternatives considered and rejected

- **Fava's model** (write, then reload and show errors; non-atomic `open("w")`). Rejected: an invalid ledger sits on disk, and a crash can leave it there.
- **A booking-only gate, or counting errors before and after.** Rejected: other new errors pass, and line shifts break counting.
- **Failing open when a check cannot run.** Rejected: "could not check" must never mean "write anyway".

## Consequences

- The contract is built and tested (M1b) before any write capability exists (ADR-0046).
- Exotic ledgers may see a higher refused-write rate; the rate is reported at G1 as evidence, and a refusal is preferred to silent damage.
- Every write costs one full load; preview plus confirm costs two (ADR-0011).

## Amendment (2026-10-25): write cost at 54k (spike S4)

Owner: "I accept your recommendations for 24, 25 and 26. We can skip Q27" (owner, 2026-10-25, in session).
- **The re-check (R4) runs after the reply.** A write is acknowledged once it is durably written and the journal is closed; its re-check (a fresh full load compared with the prediction) then completes before the **next** write to that workspace is accepted. A failed re-check blocks writes and shows the backup, as the contract's refusal path does. The safety guarantee is unchanged: no write ever lands on an unverified state.
- **Two speed-ups everywhere:** Python's garbage collector is paused for the duration of a write, and the step-1 parse is reused by the full load.
- **Very large single-file ledgers:** a bounded cache of the parsed edited file is built in M1b and measured; only if it still misses the target does the owner revisit the number for that case.
- **The 5 s target at 54k entries includes the mirror update**: it is measured until everything on screen reflects the change.
- Spike S4 equivalence work carries over: the overlay takes bytes (CRLF inside strings), handles encrypted includes, and the D1 corpus gains the column-0 continuation-line case.


## Amendment (2026-10-27): refusals caused by another program, and an R4 that cannot run (ADR-0064)

Owner, on the second golden review of the engine-scheduler spec (review 6): "I agree with the four simpler policies and two decisions that you proposed." (2026-10-27, in session, relayed by the orchestrator). Decisions D1 and D2 of that proposal:
- **D1, superseded:** "A refusal says 'Your ledger is unchanged' only when the disk equals the pre-image; otherwise the workspace enters `RECOVERING`." **Instead:** "Your ledger is unchanged" is still said only after the targets were hashed again and equal the pre-image. A refusal because another program changed a file says so ("Your ledger changed after this preview, so Finzytrack did not save your change.") and offers **Review**; reads keep working and the workspace does not enter `RECOVERING`. Only an unfinished save of Finzytrack's own (its journal still open after a failed rollback or an engine death) pauses writes, with **Reopen**, which recovers it (ADR-0012 amendment of 2026-10-27; `design/specs/engine-jobs.md` §6.4, §6.6, §11).
- **D2, added to the 2026-10-25 amendment's R4 rule:** when the same re-check has failed to run three times (its engine crashed or could not start; a count, not a time; a user's Cancel does not count), it counts as "could not check": the user is shown the backup and offered **Resume saving** with a confirmation, as for a mismatch. The gate is lifted only by that explicit action; nothing lifts it by itself (spec §6.7).
- **Unchanged:** every step of the contract, fail-closed (INV-22), and "no write ever lands on an unverified state" except by the user's explicit Resume saving after a failed or impossible check.

## References

review:D1 §3, §12.1, §12.2, §12.9, §12.10; review:10 I2, I3, I9, I10, I11; ADR-0009, ADR-0010, ADR-0011, ADR-0012, ADR-0013; INV-01, INV-22; CAP-DATA-WRITE-GATE.

## Amendment (2026-10-27): the parse cache only if the measured save needs it (S0047)

Owner: "Yes, I agree with both" (2026-10-27, in session; the second of the two was this item, after the convergence audit's C27 found the roadmap saying "built and measured in M1b" while ADR-0064 called it a later performance project).
- **Superseded in the 2026-10-25 amendment:** "a bounded cache of the parsed edited file is built in M1b and measured". **Instead:** the save at 54k entries is measured first, in M1b, on the real write path (BL-0112, with BL-0082's S4 harness; R4 after the reply and the garbage collector paused, as that amendment says). A bounded cache of the edited file's parse is built only if that measurement misses the 5 s target. If it is built, it lives on disk in a safe format of our own (never Beancount's pickle cache, which stays off, L1-09), is bounded like every cache (INV-10), and survives the fresh engine each job gets (ADR-0064 decision 5), since an engine keeps nothing between jobs. Only if the save still misses 5 s with it does the owner revisit the number for that case.
- **Unchanged:** the 5 s and 1 s targets, the R4 order, and every step of the contract.

## Amendment (2026-10-31): no stored parse digest; a save that first finishes the previous save's check is two jobs (S0067, BL-0173)

Owner, answering `design/inbox/answered/20261031-r4-parse-digest-and-5s.md`, choosing the option labelled "Drop digest; 2 jobs (Recommended)" (2026-10-31, in session).
- **The save stores no parse digest.** R4's confirm (spec `engine-jobs.md` §6.7 step 4) classes a difference `nondeterministic` when Beancount's parse and the replica's agree and the replica read exactly the closed record's post bytes (it records which bytes it parsed): the replica's parse of the record's own bytes is the save's own parse of them, so it stands in for the stored one. About 0.5 s comes off every save at 54k entries (S0067 measured 1.89-1.96 s, from 2.23-2.32 s).
- **The 5 s target counts jobs.** A save that must first finish the previous save's re-check (R4) counts as two jobs for the 5 s target at 54k entries: the check belongs to the earlier save. While it runs, the screen says "Checking your last change first…". Each job keeps the 5 s target, the mirror update included once it is built.
- **Revisited** with BL-0112 at M1b, when the publish exists and the 12-file ledger is measured.
- **Unchanged:** R4's order and its gate, every step of the contract, fail-closed (INV-22), and the parse-cache amendment of 2026-10-27.

The owner's option said the format need not change (the save would stop filling the field). **The implementer chose otherwise** (S0067, agent decision, not the owner's): the journal format goes to 3 and the field is removed, so no reader can take an old value for a current one. A record of an older format is unreadable: R4 counts a closed one as a failure to run (it ends "could not check", never a pass), and recovery cannot roll back a format 2 `.open` journal, which blocks opening its workspace instead (fails closed). Format 2 existed only in development builds for one day; nothing in the product saves yet.


## Note (2026-10-31): superseded in part by ADR-0067

Owner, 2026-10-31, in session: "Yes, draft that ADR. I'm approving it already, so go ahead and implement it." (S0068).
- **Superseded:** step 3's refusal ("No new errors of any type … except errors the user explicitly acknowledged", and the intent table's lists of keys each mutator may ask to acknowledge): the full load is a preview whose new errors the user may save anyway; no error class is refused. A preview load that cannot run still refuses (INV-22). The 2026-10-25 amendment's R4 rule ("completes before the **next** write … A failed re-check blocks writes"), the 2026-10-27 amendment's D2 (three failures, Resume saving), and the 2026-10-31 amendment's "two jobs" half: R4 compares and shows a notice and never gates a save (ADR-0067 decision 3).
- **Unchanged:** steps 1, 2, 2b, 4, 5, 6 and 7, each failing closed; the error identity of step 3 (it decides which errors the preview lists); the parse-cache amendment; the dropped parse digest (2026-10-31).

## Note (2026-11-01): superseded in part by ADR-0068

- The save's own full load (step 3 run again inside the save) and the re-check after it, step 7's adoption of that load for the mirror, and the 2026-10-25 amendment's R4 re-check (as ADR-0067 left it), are removed; step 4 runs without the ledger-set locks (D3, S0071): the preview's load is the prediction and a background rebuild with Beancount's loader follows every save (D1, built in S0070) (owner, 2026-11-01, in session: "I agree with 3,4,5,6, and 7." and "Yes, go ahead with all of those"; ADR-0068).

## Note (2026-11-01): ADR-0068 D1 built, the save's speed (S0070)

- Built in S0070: the save does no Beancount load; after every landed save a background rebuild with Beancount's own loader (engine-jobs spec §6.1 steps 5, 6 removed, §6.7). Its publish to the screens is BL-0113.
- **Speed at 54k entries** (the demo generator's ledger, one 8.7 MB file, APFS, the owner's Mac, `engine/scripts/measure_save.py`, 5 runs): the save takes 0.096-0.122 s from the records check to the closed record; the rebuild after the reply 1.31-1.32 s, which nobody waits for; the preview's load 1.37-1.49 s, before the confirm. Before S0070 the save took 1.81 s and the background re-check 1.70-1.71 s. The 5 s target (mirror update included) is measured again with the publish and the 12-file ledger in M1b (BL-0112).

## Implementation note (2026-11-14): the keys as built (agent, S0116)

Not a change of the contract; how its two keys are computed, recorded here because the wording above names a means. Both serve the purpose the decision states: an entry or an error is the same when only its location moved, and different when anything the user wrote changed.
- **The entry key of step 2** is the parsed entry's own value (its `repr`) with its location (`filename`, `lineno`, of the entry and each posting) left out and its tags and links sorted, not the printer's text: the printer writes a payee `""` and no payee alike (S0116 round 1, verifier F4), and the parsed value holds every field exactly (each number with its decimals). What Beancount's parser does not tell apart no key can: a metadata value `Assets:Foo` or `"Assets:Foo"` (likewise a currency, and a tag value `#x` or `"x"`), and a metadata line the user named `lineno:` or `filename:`, whose value Beancount puts where the location goes, so the key drops it (S0116 round 2, verifier G2). Step 2b holds those bytes, in untargeted text and against the plan's own text inside each span.
- **The error key of step 3** is read from the `ledger_errors` rows, the form in which the baseline is published: the error type, the row's entry JSON with every `filename` and `lineno` dropped, and the message with the locations Beancount prints into it dropped (a booking error prints its posting with `'lineno': N`). The candidate's errors are keyed from the same rows, so both sides are keyed by one function. The printer's text cannot be had from a published row.
- `engine/src/ledger_engine/ledger/core/entry_keys.py`; journal `design/journal/S0116-contract-prepare.md`.


## Amendment (2026-11-15): no size limit on a ledger file; the 5 s target up to 15 MB per file (S0119, S0120)

Owner, 2026-11-15, in session, after S0119's measurements (`design/inbox/answered/20261115-large-single-file.md`): "Would it make sense to allow arbitrarily large files (i.e. no size limits) and let them take more than 5s? We also provide a friendly tip that they should split the files to less than X mb (15?). But we don't stop loading files.. if a user has a huge file, then it'll take time and memory to open.. that's just how it is. Would that be feasible without substantially changing the architecture?", then "Yes, write the amendment and brief it as 5b".

- **Measured (S0119; the owner's Mac, generated ledgers):** at 54k entries every job meets 5 s in 12 files (the preview 2.2 s, from the confirm to the publish 3.3 s) and in one 8.7 MB file (4.0 s, 3.2 s); single files up to 15.8 MB preview in at most 3.8 s. The preview grows about 0.23 s per MB of the edited file, and its engine's peak memory about 40 MB per MB. A single file above about 16 MB could not be written at all: the preview's answer carries the file's whole new text, and the protocol's 16 MiB line stopped the engine.
- **Decided:** a write meets no size limit of its own on a ledger file. The protocol's line limit becomes a guard against a broken engine (256 MiB), far above any ledger file. A preview whose answer would pass it is refused by the engine before it is sent, and a save whose request would pass it is refused by the sidecar before it is sent (as today): neither stops the engine. A guard on every other reply stays with the first op that returns rows (BL-0082, M7). The plan store keeps its own memory bound in bytes, since its bound was derived from the line limit. A change to a file larger than 15 MB is prepared and saved like any other, more slowly, and its preview carries a tip: splitting the file with `include` keeps Finzytrack fast.
- **The 5 s target at 54k entries** (the 2026-10-25 amendment, and INV-20) holds when no file the write touches is larger than 15 MB. A larger file is supported and takes longer, with the job's "Still working…" and Cancel (ADR-0064 decision 5). **Measured (S0120; the owner's Mac, generated ledgers, `backend/scripts/measure_write.py`, 3 runs each):** one 21.3 MB file (54k transactions with five metadata lines each, S0119's recipe): preview 5.33-5.55 s, save 0.36-0.39 s, rebuild with its publish 3.51-3.59 s, from the confirm to the publish 3.88-3.98 s (load 11.6-16.2); one 42.7 MB file (the same recipe on 108k transactions: two 54k demo ledgers, the older one's dates before the newer one's): preview 10.96-11.29 s, save 0.53-0.56 s, rebuild 6.76-7.37 s, confirm to publish 7.28-7.93 s (load 6.7-10.6). The preview's engine peaks at about 860 MB and 1.66 GB resident (about 40 MB per MB of the file), so memory, not the 256 MiB guard, is what a very large file meets first. The extrapolation this amendment first gave (about 6 s at 25 MB, 11-12 s at 50 MB for the preview) was a little low: between the two files the preview grows about 0.27 s per MB, so about 6.5 s at 25 MB and 13 s at 50 MB in this shape. The 1 s target at demo size is unchanged.
- **The parse cache** (the 2026-10-27 amendment) is not built: every layout within 15 MB per file met 5 s in S0119, and the cache would not change the size of what the protocol carries.
- **Unchanged:** every step of the contract, fail-closed (INV-22); the 2026-10-31 amendment's counting per job. The cleaner long-term transport, a plan that sends edits instead of whole texts (BL-0106's remaining part), is not needed for this and stays a backlog row.


## Amendment (2026-11-28): each wait meets the target (G1, S0165)

Owner, 2026-11-28, in session, on G1 item 4 (the spot audit's SA-F2: G1's speed passed only on S0119's "per job" reading, which cited the 2026-10-31 amendment's sentence about R4, half superseded by ADR-0067), choosing "Each wait counts (Recommended)". The option as put: the preview's wait and the wait from Confirm to the mirror's publish must each meet this ADR's target (≤ 1 s at demo size, ≤ 5 s at 54k entries); the G1 pack reports the totals beside them.
- **Decided:** a write has two waits, each judged against the target on its own: (1) the preview, from its submit to its answer; (2) from Confirm to the new mirror generation published (the save, then the rebuild with its publish). The total from the preview's submit to the publish is reported beside them, never judged. Example from S0119 (54k entries in one 8.7 MB file): preview 3.95-4.07 s and Confirm to publish 3.21-3.35 s, each within 5 s; the total 7.2-7.4 s is reported.
- **Where the wait ends (SA-F3):** both waits are measured until the mirror's publish, not until a screen shows the change: no screen reads the mirror before M2. "Until everything on screen reflects the change" (the 2026-10-25 amendment) is therefore not measured yet; the screen's own read joins the second wait when M2 builds it.
- **Superseded:** the 2026-10-31 amendment's "The 5 s target counts jobs" as the basis for judging speed (its R4 half was already superseded by ADR-0067); the 2026-11-15 amendment's "counting per job" reads as these two waits.
- **Unchanged:** the 1 s and 5 s targets, the 15 MB per-file condition (2026-11-15), every step of the contract.


## Note (2026-11-28): a plan is confirmed only in the run that made it; no per-run plan-id prefix (S0167)

Owner, 2026-11-28, in session, answering proposal 4 of `design/inbox/answered/20261128-m1b-simplifications.md` (from the M1b area review), choosing "Accept (Recommended)": fold BL-0268's per-run plan-id prefix into the built `PLAN_UNKNOWN`.
- **Where it sits:** step 4 refuses a write whose ledger set changed since its plan was made, and the change is previewed again. The plan itself lives only in the memory of the run that made it (`app.platform.plans.PlanStore`). A confirm of a plan id this run does not know (from an earlier run of the app, or let go after `PlanPolicy.max_used` later confirms) is answered `PLAN_UNKNOWN` (built in S0117 round 3): "Check your ledger first: an earlier confirm may have saved this change." Such a confirm is never planned again by itself and never invites a second write. (This ADR's step 4 is the concurrency check; the confirm is step 4 of ARCHITECTURE.md §9.2. No ADR had decided the prefix: it was BL-0268's own idea, S0117 round 2, red team RT2-F2.)
- **Dropped:** a per-run prefix on plan ids (the session id). Its only gain over `PLAN_UNKNOWN` was telling an earlier run's id from one never issued, which changes nothing the user does.
- **Unchanged:** every step of the contract. The confirm route's own rules stay with its row, BL-0268 (M3): a confirm resent while the workspace warms after its plan's own landed save is never told "preview it again" (engine-jobs §11), and the route's `Host`/`Origin` check (BL-0115).
