# After "Merge A into B", what happens to A's own balance checks and other directives?
- Asked: 2026-10-25 by S0004 (the intent table, row `account.merge`)
- blocks: the `account.merge` row (built in M4). Nothing in M0 or M1.
- Rubric item: 1 (what is written to the ledger); ADR-0049 is not explicit.

## Context (plain language, a concrete example first)
Say you have two credit-card accounts for the same card, `Liabilities:Card:Old` (A) and `Liabilities:Card` (B), and you merge A into B. Every transaction of A moves to B. ADR-0049 settles that part.

A still has its own lines in the ledger: its `open`, perhaps a `close`, and **balance checks** such as "on 2024-06-30, A held −1,200.00 USD". After the merge, A holds nothing, so each of those balance checks now fails (A has 0, the check says −1,200). Beancount would report each one as an error.

ADR-0049 says two things here. The merge preview lists **B's** balance checks that newly fail. "Delete with reassign" is "merge, then remove A's open, close, balance checks and pads". It does not say what a plain merge does with A's own checks.

## Options
1. **Merge moves the transactions, notes and documents, and also removes A's balance checks and pads**, since they describe a balance that now lives in B. A's `open` (and `close`) stay, so A still exists and is empty; you can close or delete it afterwards. The preview lists the removed checks.
2. Merge leaves all of A's directives alone. A's balance checks then fail, and the preview lists them for you to acknowledge.
3. Merge is the same as "delete with reassign": A disappears completely.

## Recommendation
Option 1. A merge should not leave errors behind. Keeping A's `open` keeps merge distinct from delete, as ADR-0049 intends: delete removes A entirely, merge leaves an empty A. Moving A's balance checks onto B is not an option, because the amounts would be wrong for B.

## Answer (owner, verbatim, with date)
