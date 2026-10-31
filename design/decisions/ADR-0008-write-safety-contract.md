# ADR-0008: Every ledger write passes the write-safety contract

This record defines the contract that every ledger write must pass before any byte reaches disk.

- **Status:** Accepted. Amended by ADR-0064 (2026-10-27): a refusal caused by another program's change says so and does not enter `RECOVERING`; an R4 that fails to run three times can be acknowledged by the user. Amended on 2026-10-27 (S0047): the bounded parse cache is built only if the measured save misses the 5 s target.
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
- **The save stores no parse digest.** R4's confirm (spec `engine-jobs.md` §6.7 step 4) classes a difference `nondeterministic` when Beancount's parse and the replica's agree and the replica read exactly the closed record's post bytes (it records which bytes it parsed): the replica's parse of the record's own bytes is the save's own parse of them, so it stands in for the stored one. About 0.5 s comes off every save at 54k entries (S0067 measured 1.89-1.96 s, from 2.23-2.32 s). The journal format goes to 3; a record of an older format is never passed.
- **The 5 s target counts jobs.** A save that must first finish the previous save's re-check (R4) counts as two jobs for the 5 s target at 54k entries: the check belongs to the earlier save. While it runs, the screen says "Checking your last change first…". Each job keeps the 5 s target, the mirror update included once it is built.
- **Revisited** with BL-0112 at M1b, when the publish exists and the 12-file ledger is measured.
- **Unchanged:** R4's order and its gate, every step of the contract, fail-closed (INV-22), and the parse-cache amendment of 2026-10-27.

