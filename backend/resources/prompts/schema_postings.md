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
account                 TEXT    -- Full colon-separated path, e.g. "Expenses:Food:Groceries"
account_type            TEXT    -- First segment: Assets, Liabilities, Equity, Income, Expenses
-- When asked for "categories", GROUP BY account directly. Account paths ARE the categories.
-- Do NOT try to parse or split the account string. "Expenses:Food" is the category name.
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

Multi-currency:
- The ledger may contain multiple currencies (e.g. USD and INR). NEVER sum amounts across currencies.
- When aggregating amounts, always include "currency" in GROUP BY or filter to a single currency.
- Example: GROUP BY account, currency — or — WHERE currency = 'USD'.

Query rules:
- SQLite-compatible SQL only. Only SELECT statements.
- Use strftime() for dates, not DATE_TRUNC or EXTRACT.
- Group by month: use year_month or strftime('%Y-%m', transaction_date).
- Include ORDER BY when results have a natural ordering.
- Use LIMIT to avoid returning too many rows.
- Each transaction has 2+ postings summing to zero — be mindful of double-counting.
- For "last year", "this month", etc. — derive the date from the data: e.g. (SELECT MAX(year) FROM postings) for current year, MAX(year)-1 for last year.

---

Additional tables (ledger mirror — normalized views of non-transaction data):

accounts (name TEXT PK, open_date TEXT, close_date TEXT, currencies_json TEXT, booking TEXT, metadata_json TEXT)
account_balances (account TEXT, currency TEXT, balance TEXT, transaction_count INTEGER, last_transaction_date TEXT) — PK: (account, currency)
commodities (code TEXT PK, declaration_date TEXT, name TEXT, type TEXT, metadata_json TEXT)
commodity_usage (code TEXT PK, transaction_count INTEGER, total_volume TEXT, first_seen TEXT, last_seen TEXT)
prices (id INTEGER PK, date TEXT, base_currency TEXT, quote_number TEXT, quote_currency TEXT, metadata_json TEXT)
balance_assertions (id INTEGER PK, date TEXT, account TEXT, amount_number TEXT, amount_currency TEXT, passed INTEGER, diff_number TEXT)
pad_directives (id INTEGER PK, date TEXT, account TEXT, source_account TEXT)
lots (id INTEGER PK, account TEXT, units_number TEXT, units_currency TEXT, cost_number TEXT, cost_currency TEXT, acquisition_date TEXT, book_value TEXT)
ledger_errors (id INTEGER PK, source_file TEXT, line_number INTEGER, message TEXT)
training_data (id INTEGER PK, description TEXT, category TEXT)
notes (id INTEGER PK, date TEXT, account TEXT, comment TEXT)
events (id INTEGER PK, date TEXT, type TEXT, description TEXT)
documents (id INTEGER PK, date TEXT, account TEXT, filename TEXT)
ledger_options (key TEXT PK, value_json TEXT)

Notes on ledger mirror tables:
- balance and amount columns use TEXT for decimal precision. Cast to REAL for arithmetic: CAST(balance AS REAL).
- The postings table remains the primary table for aggregate/analytics queries. Use ledger mirror tables for account metadata, price history, lot tracking, and balance assertions.
- JOIN accounts with account_balances for accounts with their balances.
- JOIN commodities with commodity_usage for commodities with transaction stats.
