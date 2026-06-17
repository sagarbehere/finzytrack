import type { TransactionViewModel, PostingViewModel, ImportContext, LedgerContext } from '@/types/transactions'
import { v4 as uuidv4 } from 'uuid'
import { toMoney } from '@/utils/money'

export function makePosting(overrides: Partial<PostingViewModel> = {}): PostingViewModel {
  return {
    account: 'Expenses:General',
    amount: toMoney(0),
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
          makePosting({ account: 'Expenses:Food', amount: toMoney(50), currency: 'USD' }),
          makePosting({ account: 'Assets:Bank', amount: toMoney(-50), currency: 'USD' }),
        ],
    meta: {},
    internal: { isNew: false, isModified: false },
    ...txOverrides,
  }
}

import type { DocumentDetails, OrphanCandidateData } from '@/services/generated-api'

export function makeDocument(overrides: Partial<DocumentDetails> = {}): DocumentDetails {
  return {
    date: '2026-06-15',
    account: 'Assets:Bank:Checking',
    path: '../documents/2026/2026-06-15-statement-a1b2c3d4.pdf',
    display_name: '2026-06-15-statement-a1b2c3d4.pdf',
    tags: [],
    links: [],
    metadata: {},
    ...overrides,
  }
}

export function makeOrphan(overrides: Partial<OrphanCandidateData> = {}): OrphanCandidateData {
  return {
    path: '../documents/2026/2026-06-15-orphan-deadbeef.pdf',
    display_name: '2026-06-15-orphan-deadbeef.pdf',
    size: 1234,
    modified: '2026-06-15T10:00:00',
    ...overrides,
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
