# When you rename an account, should its subaccounts and the metadata that names it be renamed too?
- Asked: 2026-10-25 by S0004 (the intent table, row `account.rename`)
- blocks: the `account.rename` row (built in M4); until then the oracle harness treats such renames as non-comparable. Nothing in M0 or M1.
- Rubric item: 1 (what is written to the ledger); ADR-0049 does not say.

## Context (plain language, a concrete example first)
Say you rename `Expenses:Food` to `Expenses:Meals`, and you also have `Expenses:Food:Groceries` and `Expenses:Food:Restaurants`. There are two separate questions.

1. **Subaccounts.** Should `Expenses:Food:Groceries` become `Expenses:Meals:Groceries`? The old app (v0.3) does this. ADR-0049 covers renaming one account but not its children.
2. **Metadata and other values that name the account.** Any entry can carry an account name as a metadata value, for example a deposit's `open` with `interest-account: Income:Interest:Deposit1`. A `custom` line can carry one too, for example a Fava sidebar link `custom "fava-sidebar-link" "Interest" "Income:Interest:Deposit1"`. If you rename `Income:Interest:Deposit1`, should those values follow? The old app rewrites such values on every kind of entry when the whole value is the account name (or a subaccount). Text that only mentions the name, like a note, is left alone. Budget lines are already covered: they name the account directly, so they are renamed in any case.

In both cases, if the rename does not follow, the ledger still loads but no longer means what you meant. The children stop sitting under the parent you renamed, and the deposit points at an account that no longer exists.

## Options
1. **Rename the whole subtree, plus metadata and `custom` values that are exactly the account (or one of its subaccounts).** The preview lists every account being renamed and every metadata line that changes.
2. Rename only the one account. Refuse while it has subaccounts or is named in metadata, and tell you to rename those first.
3. Rename the subtree but never touch metadata.

## Recommendation
Option 1. It matches what the old app does, so comparisons with it keep working, and it matches what "rename" means to a person. The preview keeps it visible: the dialog lists every account and every metadata line before you confirm. Values are changed only when the whole value is the account name, never when it merely contains it.

## Answer (owner, verbatim, with date)
