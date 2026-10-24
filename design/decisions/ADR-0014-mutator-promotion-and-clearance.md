# ADR-0014: Mutator promotion, clearance by kind of edit, and the old app as oracle

This record decides when a feature that writes to the books is cleared for real use.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path, process

## Context

Each mutator (an edit producer, ADR-0009) is new code that writes the owner's books. The old app can act as a comparison, but it is wrong by design in known ways, and about a third of the mutators have no old-app counterpart (new features, entries without the old `id` key). A waiting period per mutator would make calendar time the bottleneck and would mean about 50 sign-offs.

## Decision

(owner, 2026-10-24)
- **Promotion is per mutator, inside the new app.** Until promoted, a mutator runs **only on explicit test copies** (CAP-WS-TEST-COPY). The guard checks that every file the plan writes is inside the test-copy folder and not in the file set of any non-copy workspace; otherwise `UNPROMOTED_MUTATOR_ON_REAL_LEDGER`.
- **The old app is a differential oracle:** the harness launches v0.3.0 from the legacy worktree as a separate process, on its own ports and data dir, on a *copy* of the ledger. Both apps apply the same intent; results are compared with the contract's step-2 semantic diff.
- **Expected divergence classes**, each with a classifier: I2 (glob entries copied into the root), I3 (plugin entries persisted), I10 (elided amounts and `{}` baked in, comments lost), M28 (metadata contract, ADR-0026), and FIX-<finding-id> for deliberate behaviour fixes. An intent the old app cannot address is *non-comparable*, not a divergence. Any untagged divergence blocks promotion until explained and fixed, or added as a class by ADR.
- **Clearance by kind of edit** (one waiting period and one volume per kind; volume from agent-generated test edits on copies plus real use on copies):

| Kind of edit | Writes | Time |
|---|---|---|
| Add | 50 | 1 week |
| Change/delete one entry | 100 | 2 weeks |
| Many entries or several files | 30 | 2 weeks, including crash tests |
| No old-app comparison | twice the above | twice the above |

- A mutator with no old-app comparison is cleared on Beancount's own checks (balances before and after), hand-checked expected results, and twice the volume and time.
- Each mutator also passes the D1 corpus (every nasty case) and the property tests.
- **The owner signs one clearance page per milestone**, recorded in the journal with the owner's in-session confirmation quoted (ADR-0045).
- **Dogfood rules:** the ledger directory is git-tracked and every write's diff is reviewed; D2 reconciliation runs synchronously; never both apps writing one ledger. The final dogfood window is 4 weeks and includes one month-end reconciliation and one broker sync (ADR-0043).

## Alternatives considered and rejected

- **One waiting period and sign-off per mutator.** Rejected: calendar time becomes the bottleneck.
- **Not building features that have no comparison.** Rejected: it would cut needed capabilities.
- **"Any divergence outside comments blocks promotion."** Rejected: the old app is wrong by design in listed ways.
- **Byte-identical comparison with the old app.** Rejected: the old bytes carry I2, I3 and I10.

## Consequences

- The intent table records, per mutator, the v0.3 oracle expectation and the old endpoint (or n/a).
- Release requires every mutator promoted (ADR-0043).

## References

review:D1 §12.4–12.6; review:21 §3 steps 5–6; review:27 §0.4; review:F §2 item 4; ADR-0009, ADR-0026, ADR-0043, ADR-0045; INV-01, INV-24.
