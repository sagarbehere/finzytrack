import type { NLParserConfig } from './nlParser'

export type QueryLanguage = 'sqlite' | 'beanquery'

// Re-use the same LLM config that nlParser uses
let _config: NLParserConfig = {}

export function configureSQLAssistant(config: NLParserConfig) {
  _config = { ..._config, ...config }
}

/**
 * Build a system prompt that teaches the LLM about the postings table schema
 * and instructs it to produce SQLite-compatible SQL.
 */
function buildSQLSystemPrompt(): string {
  return `You are an SQL assistant for a personal finance application. You translate natural language questions about financial data into SQLite queries.

DATABASE SCHEMA:
There is one main table called "postings" with these columns:

-- Transaction-level (same for all postings in a transaction):
transaction_id          -- UUID of the transaction
transaction_date        -- Date as TEXT in YYYY-MM-DD format
transaction_payee       -- Payee/merchant name (TEXT)
transaction_narration   -- Description/narration (TEXT)
transaction_flag        -- '*' (cleared) or '!' (pending)
transaction_tags        -- JSON array of tag strings
transaction_links       -- JSON array of link strings

-- Posting-level (each transaction has multiple postings that sum to zero):
account                 -- Full account path, e.g. "Expenses:Food:Groceries" (TEXT)
account_type            -- First segment: "Assets", "Liabilities", "Equity", "Income", "Expenses"
amount                  -- Decimal amount (REAL). Positive for debits, negative for credits.
currency                -- Currency code, e.g. "USD", "INR" (TEXT)

-- Derived columns:
year                    -- Year extracted from transaction_date (INTEGER)
year_month              -- YYYY-MM format (TEXT)
quarter                 -- Quarter 1-4 (INTEGER)

IMPORTANT RULES:
- Generate SQLite-compatible SQL only
- Income amounts are NEGATIVE in the database (money flowing in). Use -amount or ABS(amount) when displaying income.
- Expense amounts are POSITIVE in the database.
- Asset amounts are POSITIVE (what you own).
- Liability amounts are NEGATIVE (what you owe).
- Net worth = SUM of Assets + Liabilities (liabilities are already negative).
- Each transaction has at least 2 postings that sum to zero.
- Use strftime() for date functions (SQLite), not DATE_TRUNC or EXTRACT.
- When grouping by month, use year_month or strftime('%Y-%m', transaction_date).
- Always include an ORDER BY clause when results have a natural ordering.
- Use LIMIT when appropriate to avoid returning too many rows.
- Only produce SELECT statements. Never produce INSERT, UPDATE, DELETE, DROP, ALTER, or CREATE statements.

OUTPUT: Respond with ONLY the SQL query. No explanation, no markdown fences, no comments.`
}

/**
 * Build a system prompt for Beancount Query Language (BQL).
 * BQL operates on in-memory Beancount entries, not a database table.
 */
function buildBQLSystemPrompt(): string {
  return `You are a BQL (Beancount Query Language) assistant for a personal finance application. You translate natural language questions about financial data into BQL queries.

BQL is an SQL-like language that queries Beancount ledger entries directly. It is NOT standard SQL.

AVAILABLE COLUMNS (posting-level, used in SELECT and WHERE):
date                    -- Transaction date (Date type, format YYYY-MM-DD)
flag                    -- Transaction flag: '*' (cleared) or '!' (pending)
payee                   -- Payee/merchant name (string)
narration               -- Description/narration (string)
tags                    -- Set of tag strings
links                   -- Set of link strings
account                 -- Full account path, e.g. "Expenses:Food:Groceries" (string)
number                  -- The posting amount as a number
currency                -- Currency code, e.g. "USD" (string)
position                -- The posting position (use with sum() for aggregation)
balance                 -- Running cumulative balance (special column)

AVAILABLE COLUMNS (transaction-level, used in FROM clause):
date, year, month, day, flag, payee, narration, tags, links, id

KEY FUNCTIONS:
- YEAR(date), MONTH(date), DAY(date): Extract parts of dates (return integers)
- COST(position), COST(inventory): Get cost basis as Amount
- UNITS(position), UNITS(inventory): Get units as Amount
- SUM(position): Aggregate positions into inventory (use in GROUP BY queries)
- COST(SUM(position)): Get total cost of aggregated positions
- COUNT(*): Count rows
- FIRST(...), LAST(...): First/last value seen
- MIN(...), MAX(...): Min/max value
- LENGTH(set): Length of a set (e.g. tags)
- PARENT(account): Parent account name

OPERATORS:
- =, !=, <, <=, >, >= (comparison)
- AND, OR, NOT (logical)
- ~ (regex match on strings, e.g. account ~ 'Expenses:Food')
- IN (set membership, e.g. 'trip' IN tags)

IMPORTANT BQL SYNTAX RULES:
- Use ~ for regex/pattern matching, NOT LIKE: account ~ 'Expenses:Food'
- Use sum(position) for aggregating amounts, NOT SUM(amount)
- Use COST(SUM(position)) to get cost basis totals
- FROM clause filters at the TRANSACTION level (e.g. FROM year = 2024)
- WHERE clause filters at the POSTING level (e.g. WHERE account ~ 'Expenses')
- Use YEAR(date), MONTH(date) for date parts, NOT strftime()
- Semicolons at the end of queries are optional
- GROUP BY can use column positions (GROUP BY 1, 2)
- ORDER BY and LIMIT work like standard SQL
- There is no "postings" table name — just write SELECT ... WHERE ... directly
- Only produce SELECT queries. Never produce destructive statements.
- Dates are compared directly: date >= 2024-01-01 (no quotes needed for dates)

EXAMPLES:
- Total by account: SELECT account, COST(SUM(position)) GROUP BY 1 ORDER BY 2
- Monthly expenses: SELECT YEAR(date) AS y, MONTH(date) AS m, COST(SUM(position)) WHERE account ~ 'Expenses' GROUP BY 1, 2 ORDER BY 1, 2
- Top spending: SELECT account, COST(SUM(position)) AS total WHERE account ~ 'Expenses' GROUP BY 1 ORDER BY 2 DESC LIMIT 10
- Account register: SELECT date, narration, position, balance WHERE account ~ 'Assets:Bank:Checking'
- Income for a year: SELECT account, COST(SUM(position)) FROM year = 2024 WHERE account ~ 'Income' GROUP BY 1

OUTPUT: Respond with ONLY the BQL query. No explanation, no markdown fences, no comments.`
}

/**
 * Send a natural language question to the LLM and get back a query
 * in the specified language (SQL or BQL).
 */
export async function generateQuery(
  naturalLanguageQuery: string,
  language: QueryLanguage = 'sqlite'
): Promise<string> {
  if (!_config.apiUrl) {
    throw new Error('LLM API not configured. Set VITE_LLM_API_URL in your environment.')
  }

  const systemPrompt = language === 'beanquery'
    ? buildBQLSystemPrompt()
    : buildSQLSystemPrompt()

  const response = await fetch(`${_config.apiUrl}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(_config.apiKey ? { Authorization: `Bearer ${_config.apiKey}` } : {}),
    },
    body: JSON.stringify({
      model: _config.model || 'gpt-oss-20b',
      temperature: 0.1,
      max_tokens: 2048,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: naturalLanguageQuery },
      ],
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`LLM request failed (${response.status}): ${body}`)
  }

  const data = await response.json()
  const choice = data.choices?.[0]
  const content = choice?.message?.content

  if (!content) {
    const reason = choice?.finish_reason === 'length'
      ? 'Model ran out of tokens. Try a shorter or simpler question.'
      : 'LLM returned empty response'
    throw new Error(reason)
  }

  // Strip markdown fences if the model wraps them
  const fencePattern = language === 'beanquery'
    ? /^```(?:sql|bql|beanquery)?\s*/i
    : /^```(?:sql)?\s*/i
  const query = content.replace(fencePattern, '').replace(/\s*```$/i, '').trim()
  return query
}

/**
 * Check if the SQL assistant has an LLM configured.
 */
export function isSQLAssistantConfigured(): boolean {
  return !!_config.apiUrl
}
