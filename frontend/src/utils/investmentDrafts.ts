/**
 * Convert assembler drafts → review-table view models.
 *
 * The backend investment assembler (`POST /api/investments/assemble`) returns
 * `DraftTransaction`s in the same shape the commit path consumes. This maps them
 * 1:1 to `TransactionViewModel` so they stage into the shared `TransactionTable`
 * exactly like imported rows. Used by the Quick Action forms and (later) the AI
 * import/NL paths — the single draft→table step (dev-docs/investment-view.md §4, §8.3).
 */
import { v7 as uuidv7 } from 'uuid'
import type { DraftTransaction, DraftPosting } from '@/services/generated-api'
import type {
  TransactionViewModel,
  PostingViewModel,
  ImportContext,
  TransactionImportBundle,
} from '@/types/transactions'
import { toMoney } from '@/utils/money'

function convertPosting(p: DraftPosting): PostingViewModel {
  const cost = p.cost_amount != null
    ? {
        amount: toMoney(p.cost_amount),
        currency: p.cost_currency ?? undefined,
        date: p.cost_date ?? undefined,
      }
    : undefined

  const price = p.price_amount != null
    ? {
        amount: toMoney(p.price_amount),
        currency: p.price_currency ?? undefined,
        type: (p.price_type as '@' | '@@' | undefined) ?? undefined,
      }
    : undefined

  return {
    account: p.account,
    amount: toMoney(p.amount),
    currency: p.currency,
    cost,
    price,
    meta: p.posting_meta ?? undefined,
  }
}

export function convertAssembledDraftsToViewModels(
  drafts: DraftTransaction[],
): TransactionImportBundle {
  const transactions: TransactionViewModel[] = []
  const importContext = new Map<string, ImportContext>()

  drafts.forEach(draft => {
    const id = uuidv7() // temporary preview id; backend assigns the stable UUIDv7 on commit
    const postings = draft.postings.map(convertPosting)

    // The commit path requires source_account in meta; the assembler carries it as a
    // top-level field, so merge it in (event meta already holds external_id / note).
    const meta: Record<string, string> = {
      ...(draft.meta ?? {}),
      source_account: draft.source_account,
    }

    // The settlement leg (cash/income/opening) is the last posting and carries the
    // cash/quote currency — a good source_currency helper for the table.
    const sourceCurrency = postings[postings.length - 1]?.currency ?? ''

    transactions.push({
      id,
      date: draft.date,
      flag: draft.flag || '*',
      payee: draft.payee || '',
      memo: draft.memo ?? undefined,
      narration: draft.narration || '',
      tags: draft.tags ?? [],
      links: draft.links ?? [],
      postings,
      meta,
      internal: {
        isNew: true,
        isModified: false,
        source_currency: sourceCurrency,
      },
    })

    importContext.set(id, { is_duplicate: false })
  })

  return { transactions, importContext }
}
