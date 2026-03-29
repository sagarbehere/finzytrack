import { AiService, ApiError } from '@/services/generated-api'

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

/**
 * Parse natural language text into a structured transaction.
 *
 * Calls the backend POST /api/ai/parse-nl-transaction endpoint.
 * Falls back to a regex stub when AI is not configured.
 */
export async function parseNaturalLanguageTransaction(
  text: string,
  defaultCurrency?: string,
): Promise<ParsedTransaction> {
  try {
    const resp = await AiService.parseNlTransaction({
      text,
      default_currency: defaultCurrency || null,
    })
    return resp.data as ParsedTransaction
  } catch (e: unknown) {
    if (e instanceof ApiError && e.body?.error?.code === 'AI_NOT_CONFIGURED') {
      return parseStub(text)
    }
    if (e instanceof ApiError) {
      const msg = e.body?.error?.message || e.message
      throw new Error(msg)
    }
    throw e
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
