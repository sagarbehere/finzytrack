import type { TransactionViewModel, PostingViewModel } from '@/types/transactions'
import { sign, toMoney, type Money } from '@/utils/money'

function isCostEmpty(cost: PostingViewModel['cost']): boolean {
  return !cost || cost.amount === undefined || cost.amount === null || cost.amount === '' || sign(cost.amount) === 0
}

function isPriceEmpty(price: PostingViewModel['price']): boolean {
  return !price || price.amount === undefined || price.amount === null || price.amount === '' || sign(price.amount) === 0
}

function isMetaEmpty(meta: PostingViewModel['meta']): boolean {
  return !meta || Object.keys(meta).length === 0
}

// Canonicalise money for comparison so "190.00" and "190" don't read as
// different: Beancount round-trips amounts with their authored precision,
// but toMoney() strips trailing zeros, so the user typing the same value
// would otherwise look like a modification.
function canonAmount(a: Money | null | undefined): string | null {
  if (a === null || a === undefined || a === '') return null
  return toMoney(a)
}

function canonCost(cost: PostingViewModel['cost']) {
  if (isCostEmpty(cost)) return null
  return { ...cost, amount: cost!.amount !== undefined ? canonAmount(cost!.amount) ?? undefined : undefined }
}

function canonPrice(price: PostingViewModel['price']) {
  if (isPriceEmpty(price)) return null
  return { ...price, amount: price!.amount !== undefined ? canonAmount(price!.amount) ?? undefined : undefined }
}

function normalizePosting(p: PostingViewModel) {
  return {
    account: p.account,
    amount: canonAmount(p.amount),
    currency: p.currency,
    cost: canonCost(p.cost),
    price: canonPrice(p.price),
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
