# A single ledger file over about 16 MB cannot be written: state a limit with a clean refusal, or lift it?
- Asked: 2026-11-15 by the orchestrator, after slice S0119 (BL-0112)
- blocks: nothing (S0119 has landed; this decides BL-0112's last part and what follows)
- Rubric item: 5 (scope: what ledger layouts writes support) and a user-facing absolute (ADR-0008's 5 s at 54k)
- ADRs checked: ADR-0008's amendments of 2026-10-25 (two speed-ups), 2026-10-27 (the parse cache only if the measured save misses 5 s) and 2026-10-31 (the 5 s target counts jobs); ADR-0054 and engine-jobs §5.5 (the protocol's 16 MiB line); none settles a layout that fails on size rather than speed

## Context (plain language, a concrete example first)
Example: someone keeps their whole ledger, 54,000 transactions with the import metadata v0.3 writes, in one 21 MB file. They add one transaction. Finzytrack's preview prepares the change. But its answer carries the whole new text of that file, and the line between the engine and the app may be at most 16 MiB. The app cuts the answer off, stops the engine and says the engine stopped. Nothing is written and nothing is lost. The change cannot be made, and the stop counts toward "engine unavailable".

S0119 measured one write per job on the product's path (generated ledgers, your Mac):

| Layout | Preview | Confirm to publish |
|---|---|---|
| 54k in one 8.7 MB file | 4.0 s | 3.2 s |
| 54k in 12 files | 2.2 s | 3.3 s |
| demo size | 0.48 s | 0.62 s |
| one file of 10 / 12 / 14 / 15 / 15.8 MB | 2.3 / 2.8-4.4 / 3.4 / 3.7 / 3.7-3.8 s | 1.8-3.7 s |
| one file of 16.2 MB or more | the engine is stopped (about 3.8 s) | not reached |

So **every layout that can be written meets the 5 s target in every job**, and the parse cache need not be built (ADR-0008, 2026-10-27). The one failure is a single file above about 16 MB. It fails on **size**, not speed: the step-1 parse reuse or the parse cache would not change it. The exact line is between 15.8 and 16.2 MB of file text for this ledger shape, since the answer is the file's text escaped as JSON, about 1.06 times its size.

How likely: a ledger of 54,000 transactions in one file with v0.3's import metadata on each. Splitting a ledger with `include` (by year, or by account) is common Beancount practice. The 12-file layout of the same ledger is fast.

## Options (cheapest first; PROCESS.md §5)
1. **A stated limit with a clean refusal.** The preview refuses a change to a file above a stated size, before any work, with a message such as "This ledger file is too large to edit (over 15 MB). Split it into smaller files with `include`." It is a refusal, not an engine stop, so it does not count toward "engine unavailable". This is one small slice: a size check and a new outcome text. BL-0112 closes: every writable layout meets its target, and no parse cache is built. The full fix (below) stays a backlog row for after v0.4.
2. **Lift the limit** with BL-0106's "the plan sends edits": the preview's answer and the save's request carry the changed spans instead of the whole file. It is a change to the plan's format between the engine and the app, on the write path the S0117 rounds just settled. Then measure the 21 MB file. Spike S4 measured 5.3-7.3 s for it, so the step-1 parse reuse and possibly the parse cache would likely follow. Several slices, each a write-path change with the full review.
3. **Raise the line limit** (one constant). The 21 MB file would then preview, but likely over 5 s (option 2's measurement). Each answer would also hold the whole file in memory twice more (the decode costs about 100 MB of objects at 16 MiB). It moves the wall rather than removing it.

## Recommendation
Option 1. It turns today's engine stop into a true refusal with a way out, closes BL-0112 on the measured numbers, and builds no mechanism for a layout a policy can cover. The mechanism option 2 would change is the write plan's format and the confirm's save. Its history: S0117 needed five verifier rounds and four red-team runs, with 4 Highs, all on the confirm's save; S0116, the prepare, needed one round.

## Answer (owner, verbatim, with date)
