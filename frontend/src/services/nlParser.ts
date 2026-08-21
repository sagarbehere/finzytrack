import { AiService, ApiError } from '@/services/generated-api'
import type { ParsedTransaction, DraftTransaction } from '@/services/generated-api'

// Re-export so existing consumers keep working via this module
export type { ParsedTransaction, DraftTransaction }

export interface ParseResult {
  transactions: ParsedTransaction[]
  // Assembled investment drafts (from the typed-event channel) — review-table shaped.
  drafts: DraftTransaction[]
  warnings: string[]
}

/**
 * Parse natural language text into one or more structured transactions.
 *
 * A single description can describe several transactions — e.g. a transfer whose
 * money leaves one account and arrives in another on a different date is two.
 *
 * Calls the backend POST /api/ai/parse-nl-transaction endpoint.
 */
export async function parseNaturalLanguageTransaction(
  text: string,
  defaultCurrency?: string,
): Promise<ParseResult> {
  try {
    const resp = await AiService.parseNlTransaction({
      text,
      default_currency: defaultCurrency || null,
    })
    return {
      transactions: resp.data?.transactions ?? [],
      drafts: resp.data?.drafts ?? [],
      warnings: resp.data?.warnings ?? [],
    }
  } catch (e: unknown) {
    if (e instanceof ApiError) {
      const msg = e.body?.error?.message || e.message
      throw new Error(msg)
    }
    throw e
  }
}
