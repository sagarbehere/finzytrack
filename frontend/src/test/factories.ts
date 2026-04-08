import type { TransactionViewModel, PostingViewModel, ImportContext, LedgerContext } from '@/types/transactions'
import { v4 as uuidv4 } from 'uuid'

export function makePosting(overrides: Partial<PostingViewModel> = {}): PostingViewModel {
  return {
    account: 'Expenses:General',
    amount: 0,
    currency: 'USD',
    ...overrides,
  }
}

export function makeTx(overrides: Omit<Partial<TransactionViewModel>, 'postings'> & { postings?: Partial<PostingViewModel>[] } = {}): TransactionViewModel {
  const { postings: postingOverrides, ...txOverrides } = overrides
  return {
    id: uuidv4(),
    date: '2025-01-15',
    flag: '*',
    payee: 'Test Payee',
    narration: 'Test narration',
    tags: [],
    links: [],
    postings: postingOverrides
      ? postingOverrides.map(p => makePosting(p))
      : [
          makePosting({ account: 'Expenses:Food', amount: 50, currency: 'USD' }),
          makePosting({ account: 'Assets:Bank', amount: -50, currency: 'USD' }),
        ],
    meta: {},
    internal: { isNew: false, isModified: false },
    ...txOverrides,
  }
}

export function makeImportContext(overrides: Partial<ImportContext> = {}): ImportContext {
  return {
    is_duplicate: false,
    ...overrides,
  }
}

export function makeLedgerContext(overrides: Partial<LedgerContext> = {}): LedgerContext {
  return {
    ...overrides,
  }
}
