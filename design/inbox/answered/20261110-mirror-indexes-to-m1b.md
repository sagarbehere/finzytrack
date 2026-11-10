# Move the mirror indexes (BL-0122) from M1a to M1b, where their first reader is built?
- Asked: 2026-11-10 by the orchestrator, from S0097-mirror-indexes
- blocks: S0097 (parked after its brief; nothing else waits on it)
- Rubric item: 5 (scope, by milestone). Moving an open backlog row to a later milestone needs the owner's words or an ADR on the row (the pre-push deferral check, PROCESS.md §6)
- ADRs checked: ADR-0015 (mirror as rebuildable cache: says nothing about indexes), ADR-0058 (gates parked until evidence asks; the same principle, not this row). BL-0122's own text already says "chosen when the first read that needs them is built (the TransactionId lookup of M1b's write plans)", but its milestone column says M1a.

## Context (plain language, a concrete example first)
An index is like a book's index: it makes one kind of lookup fast, costs space and makes the build a little slower. S0097 listed every place the product reads the mirror today (5 read sites: the mirror's info row, its error list, the user-SQL runner and the engine's own stamp read). None of them reads the postings table, which is the only table where an index matters, and the two readers that could (`Reader`, `QueryRunner`) have no caller in product code yet (their first route is BL-0126, M2).

Measured on the 54k demo mirror (S0097 journal): an index on `(account, transaction_date)` takes one account's year from 15.3 to 2.6 ms; one on `(transaction_id)` takes one transaction from 11.9 to 0.30 ms. Both together add 91 ms to the build and 12.7 MB to the 77.4 MB file. Both gains are on example queries that no product code runs today.

## Options
1. **Move BL-0122 to M1b**, with the TransactionId lookup of the write planner (BL-0059, ARCHITECTURE.md §9.2 step 2), its only named consumer; the index is chosen and measured on that lookup's real query then. Never UNIQUE (BL-0059).
2. **Build both candidate indexes now**, at M1a: +91 ms build, +12.7 MB, for reads that do not exist yet.
3. **Close BL-0122** and let each future read slice add the index it needs.

## Recommendation
Option 1. It keeps the row's own reasoning (choose an index against the read that uses it), costs nothing now, and keeps the measurement with the code that needs it. Option 3 loses the S0052/S0097 numbers' home; option 2 builds cost for no reader.

## Answer (owner, verbatim, with date)
Owner, 2026-11-10, in session (answer to the orchestrator's question): "Move to M1b (Recommended)". Applied: option 1, BL-0122 moved to M1b (S0097).
