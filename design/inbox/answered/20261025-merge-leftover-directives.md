# After "Merge A into B", does the empty account A stay open, and what happens to its budgets?
- Asked: 2026-10-25 by S0004 (the intent table, row `account.merge`; narrowed after the verifier's review)
- blocks: the `account.merge` row (built in M4). Nothing in M0 or M1.
- Rubric item: 1 (what is written to the ledger); ADR-0049 settles the rest of the merge, not this part.

## Context (plain language, a concrete example first)
Say you have two accounts for the same credit card, `Liabilities:Card:Old` (A) and `Liabilities:Card` (B), and you merge A into B. ADR-0049 already settles most of what happens:
- every transaction of A moves to B, along with A's notes and documents;
- A's own balance checks and pads are **dropped, not moved**, because they describe A's balance, which now lives in B;
- the preview lists any balance check of B that newly fails.

Two things are left that ADR-0049 does not say:
1. A's `open` line (and its `close`, if it has one). After the merge, A has no transactions. It can stay as an empty, still-open account, or it can disappear.
2. A's budget lines (`custom "budget" Liabilities:Card:Old ...`). They can be dropped, moved to B, or kept on the empty A.

"Delete with reassign" (also ADR-0049) is "merge into B, then remove A's `open`, `close`, balance checks and pads, and drop A's budgets".

## Options
1. **Merge keeps A's `open` (and `close`) and drops A's budgets.** A stays as an empty account that you can close or delete later. A budget on an empty account is meaningless, so it goes, and the preview lists it.
2. Merge keeps A's `open` and keeps A's budgets on the empty A.
3. Merge removes A completely. It is then the same as "delete with reassign", and there is only one action.

## Recommendation
Option 1. It keeps merge distinct from delete, as ADR-0049 intends: merge leaves an empty A, delete removes it. A budget on an account that can no longer have spending would only confuse the budget view. Moving the budget to B is not offered, because B usually has its own budget.

## Answer (owner, verbatim, with date)
"I accept your recommendations for the four questions in the design/inbox/" (owner, 2026-10-25, in session). Recorded in: ADR-0049 amendment (merge); intent table row `account.merge`. Option 1 (the recommendation) applies.
