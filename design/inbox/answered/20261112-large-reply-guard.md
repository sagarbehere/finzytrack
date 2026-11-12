# Move BL-0082's reply-size refusal and paging to M7, with the first op that returns query rows?
- Asked: 2026-11-12 by the S0106 implementer (S0106-measurements)
- blocks: nothing (S0106 measured what BL-0082 asks to measure; only the row's milestone waits on this)
- Rubric item: none of 1-8. An open M1a row moved to a later milestone needs your words on the row (the pre-push deferral check, PROCESS.md §6).
- ADRs checked: ADR-0054 (its Consequences ask for the measurement and say "page or stream" large BQL results, without a milestone for the paging); ADR-0064; none places the refusal.

## Context (plain language, a concrete example first)
The sidecar and the engine talk in lines of text, each at most 16 MiB. If the engine ever sends a longer line, the sidecar treats the engine as broken and stops it. BL-0082 asked to measure how big an answer can get, and to make the engine refuse an answer that is too big (with a clear "result too large" message) instead of being stopped.

Measured in S0106 on the 54k demo ledger: a query that returns every posting with all 29 mirror columns would be a 79.5-81.9 MB line (it depends on the dates the generator was given), almost five times the limit; with 7 columns (date, flag, payee, narration, account, number, currency), 9.4 MB, which fits. Decoding that 9.4 MB answer takes about 52 ms and 57 MB of memory. So a "show me every posting" BQL query will need paging when it exists.

But no engine operation returns query rows today. The BQL operation arrives with CAP-ENGINE-BQL in M7. Every answer the engine sends now is small and bounded by its fields (a save's answer is a few hundred bytes). A size check added now to every answer would also cover the save, and turning a save's "saved" answer into "result too large" would tell the user a landed save failed. So the check belongs to the ops that return rows, designed together with the paging, when the first one is built.

## Options
1. **Move the refusal and the paging to M7, with the BQL op (Recommended).** BL-0082 keeps its measurement (done) and the row moves to M7 with that work. M1a's exit item "the protocol overhead per write and per large BQL result measured" is met by S0106's numbers.
2. **Build a refusal now for read-only ops only.** Small, but it guards ops that cannot produce a large answer today, and the paging decision still waits for M7.
3. **Keep the row in M1a.** M1a cannot close until the BQL op exists, which is M7 work.

## Recommendation
Option 1. The numbers are in hand; the guard and the paging are one design that needs the op they protect.

## Answer (owner, verbatim, with date)
Owner, 2026-11-12, in session (relayed by the orchestrator): "Move to M7 with BQL (Recommended)". The option as put to the owner: "The numbers are measured and meet M1a's exit item; the refusal and paging are one design built with the op they protect. The row moves to M7." Recorded on BL-0082 (milestone M7) and in the S0106 journal.
