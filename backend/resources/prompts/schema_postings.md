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
amount                  TEXT    -- Decimal-as-string. Positive = debit, negative = credit. Cast with CAST(amount AS REAL) for aggregation/arithmetic.
cost_amount             TEXT    -- Decimal-as-string (nullable). Per-unit cost of the units. Cast with CAST(cost_amount AS REAL) for arithmetic.
price_amount            TEXT    -- Decimal-as-string (nullable). Per-unit conversion price. Cast with CAST(price_amount AS REAL) for arithmetic.
currency                TEXT    -- e.g. "USD", "INR"

-- Attachments:
document_count          INTEGER -- Number of documents attached to the transaction (document/document2/... metadata keys). 0 = none. Filter "transactions with documents" via WHERE document_count > 0.

-- Derived:
year                    INTEGER -- Year from transaction_date
year_month              TEXT    -- YYYY-MM
quarter                 INTEGER -- 1-4

Sign conventions (double-entry):
- Expenses: positive (debit). Refunds are negative — do NOT assume expenses are always positive.
- Income: negative (credit). Use -SUM(CAST(amount AS REAL)) or ABS() to display as positive.
- Assets: positive (debit).
- Liabilities: negative (credit).
- Use SUM(CAST(amount AS REAL)) for net figures — handles refunds automatically.
- Net worth = SUM(CAST(amount AS REAL)) WHERE account_type IN ('Assets', 'Liabilities'), grouped by currency. For money totals, exclude investment holdings — see "Currencies vs holdings" below.

Multi-currency:
- The ledger may contain multiple currencies (e.g. USD and INR). NEVER sum amounts across currencies.
- When aggregating amounts, always include "currency" in GROUP BY or filter to a single currency.
- Example: GROUP BY account, currency — or — WHERE currency = 'USD'.

Currencies vs holdings:
- The "currency" column holds ANY commodity code — real currencies (USD, INR) AND investment holdings (stocks/funds like VOO, VTI). The "commodities" table's is_currency column (1/0) says which is which.
- For money totals (net worth, total assets/liabilities) and anything a user reads as an amount of money, exclude holdings by joining commodities and filtering to currencies:
    SELECT currency, SUM(CAST(amount AS REAL)) AS amount
    FROM postings p JOIN commodities c ON p.currency = c.code
    WHERE c.is_currency = 1 AND account_type = 'Assets'
    GROUP BY currency
  Summing a share count into a dollar total is meaningless — do not do it.
- To analyze the holdings themselves (e.g. how many VOO shares over time), filter WHERE c.is_currency = 0, or query the raw quantities directly.

Valuing investments:
- A holding's MARKET VALUE = units held × its latest price. "Latest price" is the row with MAX(date) per (base_currency, quote_currency) in the "prices" table; join it on prices.base_currency = the holding's commodity code. Value each holding in its OWN quote currency — never convert or sum a holding's value across currencies.
    -- current market value per holding, in its quote currency:
    SELECT l.units_currency AS holding, lp.quote_currency AS currency,
           SUM(CAST(l.units_number AS REAL)) * lp.price AS market_value
    FROM lots l
    JOIN (SELECT base_currency, quote_currency, CAST(quote_number AS REAL) AS price
          FROM prices x WHERE x.date = (SELECT MAX(date) FROM prices y
            WHERE y.base_currency = x.base_currency AND y.quote_currency = x.quote_currency)) lp
      ON lp.base_currency = l.units_currency
    GROUP BY l.units_currency, lp.quote_currency
- REALIZED GAIN on a sale is derived STRUCTURALLY from the reducing posting (units < 0 with both cost_amount and price_amount set): gain = |units| × (price_amount − cost_amount). This works in ANY account — do not rely on an "Income:CapitalGains" account name.
- For portfolio VALUE-OVER-TIME or RETURNS (XIRR), do NOT hand-roll SQL — use the compute functions "portfolio_series" (as-of value/cost series) and "portfolio_returns" (XIRR + simple gain) via execute_compute or a recipe `compute` step. SQL cannot do as-of series or money-weighted return well.

Query rules:
- Money columns (amount, cost_amount, price_amount) are stored as TEXT for exact decimal precision. Always wrap them in CAST(... AS REAL) before SUM/AVG/arithmetic. SQLite will implicitly cast for plain comparisons (WHERE amount > 0), so those can stay unwrapped.
- SQLite-compatible SQL only. Only SELECT statements.
- Use strftime() for dates, not DATE_TRUNC or EXTRACT.
- Group by month: use year_month or strftime('%Y-%m', transaction_date).
- Include ORDER BY when results have a natural ordering.
- Use LIMIT to avoid returning too many rows.
- Each transaction has 2+ postings summing to zero — be mindful of double-counting.
- For "last year", "this month", etc. — derive the date from the data: e.g. (SELECT MAX(year) FROM postings) for current year, MAX(year)-1 for last year.

---

Beyond "postings", additional tables exist for account metadata, price history, investment lots, balance assertions, and more. If the user's question involves data not in the postings table (e.g. "what accounts are open?", "show price history for AAPL", "what are my lot positions?"), discover the available tables and their columns by running:
  SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
  PRAGMA table_info(<table_name>);
Key tables: accounts, account_balances, commodities, prices, lots, balance_assertions.
Numeric columns in these tables use TEXT for decimal precision — cast with CAST(value AS REAL) for arithmetic.
The "commodities" table has one row per commodity (declared or merely used) with: code, name, asset_class (e.g. "cash", "stock", "etf"), is_currency (1 = a currency / unit of account, 0 = an investment holding), declaration_date, metadata_json. Join it on postings.currency = commodities.code to separate currencies from holdings (see "Currencies vs holdings" above).
