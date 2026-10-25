# The SQL column `postings.source_account`: keep v0.2.2's per-row answer?
- Asked: 2026-10-25 by orchestrator/S0021-mirror-surface (found while reviewing the slice)
- blocks: nothing now (the `source_account` and `source_account_type` rows in `design/specs/mirror/surface.toml` are marked `owner:` this file); the M1a mirror-build slice needs the answer before it fills these two columns
- Rubric item: 4 (amending ADR-0055) and 2 (what a column a user's SQL reads means)
- ADRs checked: ADR-0055 (decides the column's rule: legacy key, else the leg carrying the `import-key`, else the single Assets/Liabilities leg, else NULL; its Context quotes the user docs' "the originating Assets or Liabilities account for this transaction" but does not describe what v0.2.2 actually computes per row); ADR-0048 (v0.2.2 mirror names keep working through 0.4.x); ADR-0026 (v0.4 stops writing the `source_account` key); INV-21 (the legacy key is read forever).

## Context (plain language, a concrete example first)

The mirror's `postings` table has one row per posting (each leg of a transaction), and each row has a `source_account` column. v0.2.2 fills it **per row**, by these rules (`sqlite_exporter.py` `_compute_source_account` at the v0.2.2 tag):
1. if the transaction has a `source_account` key (every v0.2.2 import wrote one), use it;
2. else, if the transaction has two legs, use **the other leg**;
3. else, the first Assets or Liabilities leg other than this row's; else NULL.

ADR-0055, as written, gives the same answer for every row of a transaction. The two differ on hand-written entries:

| Hand-written entry | Row | v0.2.2 shows | ADR-0055 as written shows |
|---|---|---|---|
| Groceries: `Expenses:Food 50` / `Assets:Bank -50` | Expenses:Food | Assets:Bank | Assets:Bank (same) |
| | Assets:Bank | **Expenses:Food** | **Assets:Bank** |
| Transfer: `Assets:Savings 100` / `Assets:Bank -100` | Assets:Savings | Assets:Bank | **NULL** (two such legs, none marked) |
| | Assets:Bank | Assets:Savings | **NULL** |

Imported entries agree under both: v0.2.2 imports carried the key (rule 1, every row shows the bank), and v0.4 imports mark the bank leg with `import-key`.

A typical user query, "spending by card" (`WHERE account_type = 'Expenses' GROUP BY source_account`), reads only the expense rows, which agree. Queries over bank or transfer rows would change.

## Options
1. **Keep v0.2.2's per-row answer for every entry** (recommended): legacy key → the leg carrying `import-key` (every row) → v0.2.2's rules 2 and 3 → NULL. Every entry already in a ledger shows exactly what v0.2.2 showed, and new imports look like v0.2.2 imports did. Amends ADR-0055's rule; the docs' wording gets a sentence on the per-row fallback.
2. **ADR-0055 as written.** Matches the docs' one-line description, but hand-written bank and transfer rows change (some to NULL); release notes would say so.

## Recommendation
Option 1: it is the only rule under which existing users' SQL returns the same rows after the upgrade, it costs nothing extra to compute, and it keeps ADR-0055's real point (the mirror computes the column; v0.4 never writes the key).

## Answer (owner, verbatim, with date)
Owner: "I accept the recommendations for all three questions currently in the inbox" (2026-10-25, in session). Accepted: Option 1: keep v0.2.2's per-row answer for every entry.
