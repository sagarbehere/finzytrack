// Converter contract: assembler DraftTransaction → review-table TransactionViewModel.
// See dev-docs/investment-view.md §4 (draft→table step) and §6 (draft shape).

import { describe, it, expect } from 'vitest'
import type { DraftTransaction } from '@/services/generated-api'
import { convertAssembledDraftsToViewModels } from '@/utils/investmentDrafts'
import { toMoney } from '@/utils/money'

const buyDraft: DraftTransaction = {
  date: '2024-06-03',
  flag: '*',
  payee: '',
  narration: 'Buy VOO',
  tags: [],
  links: [],
  source_account: 'Assets:Broker:VOO',
  meta: { external_id: 'BRK-1' },
  postings: [
    {
      account: 'Assets:Broker:VOO',
      amount: '10',
      currency: 'VOO',
      cost_amount: '417.45',
      cost_currency: 'USD',
      cost_date: null,
    },
    { account: 'Assets:Cash', amount: '-4174.50', currency: 'USD' },
  ],
}

describe('convertAssembledDraftsToViewModels', () => {
  it('maps a buy draft 1:1 into a view model with cost + merged source_account meta', () => {
    const { transactions, importContext } = convertAssembledDraftsToViewModels([buyDraft])
    expect(transactions).toHaveLength(1)
    const tx = transactions[0]

    expect(tx.narration).toBe('Buy VOO')
    expect(tx.flag).toBe('*')
    expect(tx.internal.isNew).toBe(true)
    // source_account is required by commit; the converter merges it into meta.
    expect(tx.meta.source_account).toBe('Assets:Broker:VOO')
    expect(tx.meta.external_id).toBe('BRK-1')
    // source_currency helper = the settlement (last) leg's currency.
    expect(tx.internal.source_currency).toBe('USD')

    const [holding, cash] = tx.postings
    expect(holding.currency).toBe('VOO')
    expect(holding.amount).toBe(toMoney('10'))
    expect(holding.cost?.amount).toBe(toMoney('417.45'))
    expect(holding.cost?.currency).toBe('USD')
    expect(holding.cost?.date).toBeUndefined() // null cost_date → undefined (Beancount stamps txn date)
    expect(holding.price).toBeUndefined()
    expect(cash.amount).toBe(toMoney('-4174.50')) // Money normalizes trailing zeros; compare by value
    expect(cash.cost).toBeUndefined()

    // Every draft gets a fresh id + a non-duplicate import context.
    expect(importContext.get(tx.id)).toEqual({ is_duplicate: false })
    expect(tx.id).toBeTruthy()
  })

  it('gives each draft a distinct id', () => {
    const { transactions } = convertAssembledDraftsToViewModels([buyDraft, buyDraft])
    expect(transactions[0].id).not.toBe(transactions[1].id)
  })
})
