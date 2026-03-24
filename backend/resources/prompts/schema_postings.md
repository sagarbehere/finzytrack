# Postings Table Schema

There is one main table called `postings` with these columns:

## Transaction-level columns
*(Same for all postings in a transaction)*

| Column | Type | Description |
|---|---|---|
| `transaction_id` | TEXT | UUID of the transaction |
| `transaction_date` | TEXT | Date in YYYY-MM-DD format |
| `transaction_payee` | TEXT | Payee / merchant name |
| `transaction_narration` | TEXT | Description / narration |
| `transaction_flag` | TEXT | `*` (cleared) or `!` (pending) |
| `transaction_tags` | TEXT | JSON array of tag strings |
| `transaction_links` | TEXT | JSON array of link strings |

## Posting-level columns
*(Each transaction has multiple postings that sum to zero)*

| Column | Type | Description |
|---|---|---|
| `account` | TEXT | Full account path, e.g. `Expenses:Food:Groceries` |
| `account_type` | TEXT | First segment: `Assets`, `Liabilities`, `Equity`, `Income`, `Expenses` |
| `amount` | REAL | Decimal amount. Positive = debit, negative = credit |
| `currency` | TEXT | Currency code, e.g. `USD`, `INR` |

## Derived columns

| Column | Type | Description |
|---|---|---|
| `year` | INTEGER | Year extracted from `transaction_date` |
| `year_month` | TEXT | YYYY-MM format |
| `quarter` | INTEGER | Quarter 1–4 |

## Sign Conventions (double-entry accounting)

Beancount uses standard double-entry signs: **debit = positive, credit = negative**.

| Account type | Normal direction | Sign | Example |
|---|---|---|---|
| Expenses | debit | **positive** | Groceries +50.00 |
| Expenses (refund) | credit | **negative** | Grocery refund −10.00 |
| Income | credit | **negative** | Salary −5000.00 |
| Assets | debit | **positive** | Bank balance +1000.00 |
| Liabilities | credit | **negative** | Credit card −500.00 |

**Key rules:**
- Use `SUM(amount)` for net figures — it handles refunds automatically.
- Do NOT assume expenses are always positive — refunds produce negative expense postings.
- Only use `amount > 0` / `amount < 0` filters when explicitly separating debits from credits.
- Income is negative — use `-SUM(amount)` or `ABS(SUM(amount))` when displaying income as a positive number.
- Net worth = `SUM(amount)` where `account_type IN ('Assets', 'Liabilities')`.

## Query Rules

- Generate **SQLite-compatible SQL only**.
- Only produce **SELECT** statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, or CREATE.
- Use `strftime()` for date functions (SQLite), not `DATE_TRUNC` or `EXTRACT`.
- When grouping by month, use `year_month` or `strftime('%Y-%m', transaction_date)`.
- Always include `ORDER BY` when results have a natural ordering.
- Use `LIMIT` when appropriate to avoid returning too many rows.
- Each transaction has at least 2 postings that sum to zero — be mindful of double-counting.
