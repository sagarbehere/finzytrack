import type { NLParserConfig } from './nlParser'

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
 * Send a natural language question to the LLM and get back an SQL query.
 * Uses the same OpenAI-compatible /v1/chat/completions endpoint as nlParser.
 */
export async function generateSQL(naturalLanguageQuery: string): Promise<string> {
  if (!_config.apiUrl) {
    throw new Error('LLM API not configured. Set VITE_LLM_API_URL in your environment.')
  }

  const systemPrompt = buildSQLSystemPrompt()

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
  const sql = content.replace(/^```(?:sql)?\s*/i, '').replace(/\s*```$/i, '').trim()
  return sql
}

/**
 * Check if the SQL assistant has an LLM configured.
 */
export function isSQLAssistantConfigured(): boolean {
  return !!_config.apiUrl
}
