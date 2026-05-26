import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
import { sign } from '@/utils/money'

function isCostEmpty(cost: PostingViewModel['cost']): boolean {
  return !cost || cost.amount === undefined || cost.amount === null || cost.amount === '' || sign(cost.amount) === 0
}

function isPriceEmpty(price: PostingViewModel['price']): boolean {
  return !price || price.amount === undefined || price.amount === null || price.amount === '' || sign(price.amount) === 0
}

function isMetaEmpty(meta: PostingViewModel['meta']): boolean {
  return !meta || Object.keys(meta).length === 0
}

function normalizePosting(p: PostingViewModel) {
  return {
    account: p.account,
    amount: p.amount,
    currency: p.currency,
    cost: isCostEmpty(p.cost) ? null : p.cost,
    price: isPriceEmpty(p.price) ? null : p.price,
    meta: isMetaEmpty(p.meta) ? null : p.meta,
  }
}

export function normalizeForComparison(tx: TransactionViewModel): string {
  return JSON.stringify({
    date: tx.date,
    flag: tx.flag,
    payee: tx.payee,
    memo: tx.memo,
    narration: tx.narration,
    tags: [...tx.tags].sort(),
    links: [...tx.links].sort(),
    postings: tx.postings.map(normalizePosting),
  })
}

export function isModified(tx: TransactionViewModel, baselines: TransactionViewModel[]): boolean {
  const baseline = baselines.find(b => b.id === tx.id)
  if (!baseline) return false
  return normalizeForComparison(tx) !== normalizeForComparison(baseline)
}
