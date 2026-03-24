Table "postings" columns:

-- Transaction-level (same for all postings in a transaction):
transaction_id          TEXT    -- UUID
transaction_date        TEXT    -- YYYY-MM-DD
transaction_payee       TEXT    -- Payee/merchant name
transaction_narration   TEXT    -- Description/narration
transaction_flag        TEXT    -- '*' (cleared) or '!' (pending)
transaction_tags        TEXT    -- JSON array of tag strings
transaction_links       TEXT    -- JSON array of link strings

-- Posting-level (each transaction has 2+ postings that sum to zero):
account                 TEXT    -- Full path, e.g. "Expenses:Food:Groceries"
account_type            TEXT    -- First segment: Assets, Liabilities, Equity, Income, Expenses
amount                  REAL    -- Positive = debit, negative = credit
currency                TEXT    -- e.g. "USD", "INR"

-- Derived:
year                    INTEGER -- Year from transaction_date
year_month              TEXT    -- YYYY-MM
quarter                 INTEGER -- 1-4

Sign conventions (double-entry):
- Expenses: positive (debit). Refunds are negative — do NOT assume expenses are always positive.
- Income: negative (credit). Use -SUM(amount) or ABS() to display as positive.
- Assets: positive (debit).
- Liabilities: negative (credit).
- Use SUM(amount) for net figures — handles refunds automatically.
- Net worth = SUM(amount) WHERE account_type IN ('Assets', 'Liabilities').

Query rules:
- SQLite-compatible SQL only. Only SELECT statements.
- Use strftime() for dates, not DATE_TRUNC or EXTRACT.
- Group by month: use year_month or strftime('%Y-%m', transaction_date).
- Include ORDER BY when results have a natural ordering.
- Use LIMIT to avoid returning too many rows.
- Each transaction has 2+ postings summing to zero — be mindful of double-counting.
