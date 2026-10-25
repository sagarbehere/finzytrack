# ADR-0049: Account rename, merge and delete are built on one move-postings primitive

This record decides how accounts are renamed, merged and deleted without breaking real balances, and how the accounts API is shaped.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** accounts, write path

## Context

In v0.3 the delete dialog took its count from the period-filtered table, so it could say nothing about transactions and then delete all of them. Delete removed only `open`, `close` and transactions; other directives stayed, the ledger went from 0 to 5 errors (including a failing assertion on a bank account), and the write gate let it through (review:10 I1, I11).

## Decision

(owner, 2026-10-24)
- **Principle:** an Income or Expenses account is a classification; the economic event lives in the other legs. Deleting a classification must not undo real cash movements. Deleting transactions is valid only when they are wrong (for example a bad import).
- **One primitive, `move_postings(A → B)`:** rewrite the account on every posting of A to B (token edits, ADR-0009), and re-point A's `note` and `document` directives.
  - **Rename:** B is new.
  - **Merge into…:** B exists; its own action.
  - **Delete with reassign:** merge into B, then remove A's `open`, `close`, `balance` and `pad`, and drop A's budget directives.
  - **Delete with delete:** remove every transaction touching A (every leg) and all of A's directives.
- **The delete dialog** uses a backend **all-time preview**, independent of the view's date range: count and date span, and one question with **no default**: "Move them to [account picker]" (existing accounts only) or "Delete them" (wrong data only), which lists the other accounts whose balances change and the assertions that will newly fail, to be acknowledged. It always offers **Close** ("stop using it, keep the history"). An account with no transactions skips the question.
- **Preview rules:** B's currency constraints allow the moved postings; B is open over the whole date span; balance-sheet merges list B's newly failing assertions; merging holdings at cost requires matching booking methods and a warning; A's own `balance`/`pad` are dropped, not moved; a transaction with postings to both A and B keeps two B postings.
- **Subaccounts:** delete and merge are blocked until children are handled.
- **The accounts API is split** into an account catalog and period balances (`accounts`, `accounts/balances?from&to`).
- The write contract catches any remaining new validation error (ADR-0008).

## Alternatives considered and rejected

- **"Also delete N transactions" checkbox** (legacy). Rejected: not a real choice, and the number was wrong.
- **Deleting only the account's own directives.** Rejected: leaves a broken ledger.

## Consequences

- Accounts can promote its multi-entry mutators before Transactions promotes single-entry replace (ADR-0014).


## Amendment (2026-10-25): rename covers the subtree and the values that name the account

Owner: "I accept your recommendations for the four questions in the design/inbox/" (2026-10-25, in session). Covers `design/inbox/answered/20261025-rename-subaccounts-and-metadata.md`.
- **Rename renames the whole subtree:** renaming A to B also renames every `A:…` account to `B:…`, with every reference to each of them.
- **Values that are exactly an account of the subtree follow:** metadata values on any entry, and `custom` values (string or account-typed), when the whole value is the account name. Text that merely contains the name is left alone.
- The preview lists every account renamed and every metadata or `custom` line that changes, before you confirm.

## Amendment (2026-10-25): what a merge leaves behind

Owner: "I accept your recommendations for the four questions in the design/inbox/" (2026-10-25, in session). Covers `design/inbox/answered/20261025-merge-leftover-directives.md`.
- **Merge keeps A's `open` (and `close`)**, so A remains an empty account that can be closed or deleted later; this keeps merge distinct from delete with reassign.
- **Merge drops A's `custom "budget"` directives**, listed in the preview; they are not moved to B.
- A's own `balance` and `pad` directives are dropped, as the preview rules above already say.

## References

review:10 §1a, I1, I11, D10; review:11 L3-06; ADR-0008, ADR-0009, ADR-0014, ADR-0035; INV-01, INV-15, INV-16.
