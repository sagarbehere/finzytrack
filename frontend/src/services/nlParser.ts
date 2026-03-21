export interface ParsedTransaction {
  date?: string // YYYY-MM-DD — optional, defaults to today
  flag?: string // '*' or '!' — optional, defaults to '*'
  payee?: string // e.g. "Olive Garden"
  narration?: string // e.g. "Dinner"
  postings?: Array<{
    account?: string // e.g. "Expenses:Food:Restaurant"
    amount?: number | null
    currency?: string // e.g. "USD"
  }>
  tags?: string[]
  links?: string[]
}

export interface NLParserConfig {
  apiUrl?: string // e.g. "http://127.0.0.1:1234"
  model?: string // e.g. "gpt-oss-20b"
  apiKey?: string // optional, some local servers don't need one
  temperature?: number
  maxTokens?: number
}

export interface NLParserContext {
  accountNames: string[]
  currencies: string[]
  defaultAccount?: string // selected in the dropdown
  defaultCurrency?: string // selected in the dropdown
}

/**
 * Build the system prompt dynamically from the user's actual beancount accounts.
 *
 * Design goals for lightweight-model compatibility:
 * - Terse, structured instructions (no prose)
 * - Explicit JSON schema with field descriptions inline
 * - One concrete example
 * - Account list grouped by type so the model can pattern-match
 */
function buildSystemPrompt(ctx: NLParserContext): string {
  const today = new Date().toISOString().split('T')[0]

  // Group accounts by top-level type
  const grouped: Record<string, string[]> = {}
  for (const name of ctx.accountNames) {
    const type = name.split(':')[0] // "Assets", "Liabilities", etc.
    if (!grouped[type]) grouped[type] = []
    grouped[type].push(name)
  }

  const accountBlock = Object.entries(grouped)
    .map(([type, names]) => `${type}:\n  ${names.join('\n  ')}`)
    .join('\n')

  const currencyList = ctx.currencies.length
    ? ctx.currencies.join(', ')
    : 'USD'

  const defaultCurrency = ctx.defaultCurrency || ctx.currencies[0] || 'USD'

  return `You parse natural-language transaction descriptions into JSON.

OUTPUT: a single JSON object, no markdown, no explanation.

JSON schema:
{
  "date": "YYYY-MM-DD",       // default: "${today}"
  "flag": "*",                 // "*" = cleared, "!" = pending
  "payee": "string",           // merchant / counterparty name
  "narration": "string",       // short description of what was bought
  "postings": [
    { "account": "string", "amount": number, "currency": "string" },
    { "account": "string", "amount": number, "currency": "string" }
  ],
  "tags": [],                  // optional tags
  "links": []                  // optional links
}

RULES:
- postings MUST balance to zero (amounts sum to 0)
- First posting = source account (where money comes from). Amount is NEGATIVE for expenses.
- Second posting = destination account (where money goes). Amount is POSITIVE for expenses.
- Use ONLY account names from the list below. Pick the closest match.
- If no source account is mentioned, set the first posting's "account" to "".
- Infer currency from symbols: $ = USD, ₹ = INR, € = EUR, £ = GBP. If unclear, default to ${defaultCurrency}.
- Default date: ${today}
- Extract a clean payee name from the text (e.g. "Olive Garden", not "dinner at Olive Garden")
- narration = brief note about the purchase (e.g. "Dinner", "Groceries", "Gas fill-up")

ACCOUNTS:
${accountBlock}

CURRENCIES: ${currencyList}

EXAMPLE:
Input: "Paid $45 for dinner at Olive Garden on chase"
Output:
{"date":"${today}","flag":"*","payee":"Olive Garden","narration":"Dinner","postings":[{"account":"Liabilities:Chase:SapphireReserve","amount":-45,"currency":"${defaultCurrency}"},{"account":"Expenses:EatingOut","amount":45,"currency":"${defaultCurrency}"}],"tags":[],"links":[]}

Respond with ONLY the JSON object.`
}

/**
 * Parse natural language text into a structured transaction.
 *
 * When an API URL is configured via llmConfig, sends the text to an
 * OpenAI-compatible /v1/chat/completions endpoint. Otherwise falls back
 * to a regex stub.
 */
export async function parseNaturalLanguageTransaction(
  text: string,
  context?: NLParserContext,
  llmConfig?: NLParserConfig,
): Promise<ParsedTransaction> {
  if (llmConfig?.apiUrl) {
    return parseLLM(text, context, llmConfig)
  }
  return parseStub(text)
}

async function parseLLM(
  text: string,
  context: NLParserContext | undefined,
  llmConfig: NLParserConfig,
): Promise<ParsedTransaction> {
  const ctx: NLParserContext = context ?? { accountNames: [], currencies: [] }
  const systemPrompt = buildSystemPrompt(ctx)

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
        max_tokens: llmConfig.maxTokens ?? 2048,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: text },
        ],
      }),
    })
  } catch (e: any) {
    if (e.name === 'AbortError') {
      throw new Error('LLM request timed out after 30 seconds. Check that your LLM server is running and responsive.')
    }
    throw e
  } finally {
    clearTimeout(timeoutId)
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`LLM request failed (${response.status}): ${body}`)
  }

  const data = await response.json()
  const choice = data.choices?.[0]
  const content = choice?.message?.content

  if (!content) {
    const reason = choice?.finish_reason === 'length'
      ? 'Model ran out of tokens (reasoning took too long). Try a shorter input or simpler description.'
      : 'LLM returned empty response'
    throw new Error(reason)
  }

  // Parse the JSON — strip markdown fences if the model wraps them anyway
  const jsonStr = content.replace(/^```(?:json)?\s*/, '').replace(/\s*```$/, '').trim()
  try {
    const parsed: ParsedTransaction = JSON.parse(jsonStr)
    return parsed
  } catch (e: any) {
    throw new Error(`LLM returned invalid JSON: ${e.message}\nRaw: ${jsonStr.substring(0, 200)}`)
  }
}

async function parseStub(text: string): Promise<ParsedTransaction> {
  await new Promise(resolve => setTimeout(resolve, 300))

  const today = new Date().toISOString().split('T')[0]
  const amountMatch = text.match(/\$(\d+(?:\.\d{1,2})?)/)
  const amount = amountMatch ? parseFloat(amountMatch[1]) : null

  return {
    date: today,
    flag: '*',
    payee: text,
    narration: '',
    postings: [
      { amount: amount !== null ? -amount : null },
      { account: 'Expenses:Unknown', amount: amount },
    ],
  }
}
