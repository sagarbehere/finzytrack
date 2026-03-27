import type { NLParserConfig } from './nlParser'
import { LedgerService } from '@/services/generated-api'

export type QueryLanguage = 'sqlite' | 'beanquery'

/**
 * Cached schema text fetched from the backend.
 * Populated lazily on first call to generateQuery with language='sqlite'.
 */
let _cachedPostingsSchema: string | null = null

/**
 * Fetch the postings table schema from the backend (single source of truth).
 * Falls back to a minimal inline schema if the fetch fails.
 */
async function fetchPostingsSchema(): Promise<string> {
  if (_cachedPostingsSchema) return _cachedPostingsSchema
  try {
    _cachedPostingsSchema = await LedgerService.getPostingsSchema()
    return _cachedPostingsSchema
  } catch {
    // Fall through to fallback
  }
  // Minimal fallback so the feature still works if the backend endpoint is unreachable
  return 'Table "postings": transaction_id, transaction_date, transaction_payee, transaction_narration, transaction_flag, transaction_tags, transaction_links, account, account_type, amount, currency, year, year_month, quarter. Amounts: positive=debit, negative=credit. SQLite only. SELECT only.'
}

/**
 * Build a system prompt that teaches the LLM about the postings table schema
 * and instructs it to produce SQLite-compatible SQL.
 */
async function buildSQLSystemPrompt(): Promise<string> {
  const schema = await fetchPostingsSchema()
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `You are an SQL assistant for a personal finance application. You translate natural language questions about financial data into SQLite queries.

Today is ${year}-${month}. Current year = ${year}, last year = ${year - 1}.

DATABASE SCHEMA:
${schema}

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
  language: QueryLanguage = 'sqlite',
  llmConfig?: NLParserConfig,
): Promise<string> {
  if (!llmConfig?.apiUrl) {
    throw new Error('AI not configured. Set api_url under the llm section in config.yaml.')
  }

  const systemPrompt = language === 'beanquery'
    ? buildBQLSystemPrompt()
    : await buildSQLSystemPrompt()

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 30_000)

  let response: Response
  try {
    response = await fetch(`${llmConfig.apiUrl}/v1/chat/completions`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(llmConfig.apiKey ? { Authorization: `Bearer ${llmConfig.apiKey}` } : {}),
      },
      body: JSON.stringify({
        model: llmConfig.model || 'gpt-oss-20b',
        temperature: llmConfig.temperature ?? 0.1,
        ...(llmConfig.maxTokens && llmConfig.maxTokens > 0 ? { max_tokens: llmConfig.maxTokens } : {}),
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: naturalLanguageQuery },
        ],
      }),
    })
  } catch (e: any) {
    if (e.name === 'AbortError') {
      throw new Error('AI request timed out after 30 seconds. Check that your AI server is running and responsive.')
    }
    throw e
  } finally {
    clearTimeout(timeoutId)
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`AI request failed (${response.status}): ${body}`)
  }

  const data = await response.json()
  const choice = data.choices?.[0]
  const content = choice?.message?.content

  if (!content) {
    const reason = choice?.finish_reason === 'length'
      ? 'Model ran out of tokens. Try a shorter or simpler question.'
      : 'AI returned empty response'
    throw new Error(reason)
  }

  // Strip markdown fences if the model wraps them.
  // Try to extract content between fences first (handles preamble text before the fence).
  const trimmed = content.trim()
  const fenceMatch = trimmed.match(/```(?:sql|bql|beanquery)?\s*\n([\s\S]*?)\n?\s*```/i)
  const query = fenceMatch
    ? fenceMatch[1].trim()
    : trimmed.replace(/^```(?:sql|bql|beanquery)?\s*/i, '').replace(/\s*```$/i, '').trim()
  return query
}
