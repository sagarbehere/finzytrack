import { toNumber, toMoney, type Money } from '@/utils/money'

const CURRENCY_LOCALES: Record<string, string> = {
  INR: 'en-IN',
}

export function getLocale(currencyCode: string): string {
  return CURRENCY_LOCALES[currencyCode] || 'en-US'
}

// Display formatter: lossy conversion to number happens here and only here.
// Money precision is preserved up to this point; see dev-docs/money-types.md.
export function formatAmount(value: Money | number | string, currencyCode: string): string {
  const n = typeof value === 'number' ? value : toNumber(toMoney(value))
  // A missing or invalid currency code (e.g. a synthetic/empty-data row with no
  // currency) makes Intl throw `RangeError: Invalid currency code`. Degrade to a
  // plain number instead of throwing — a widget must never crash the render over
  // a formatting edge case.
  if (!currencyCode) return n.toLocaleString(getLocale(currencyCode), NUMBER_OPTS)
  try {
    return n.toLocaleString(getLocale(currencyCode), {
      style: 'currency',
      currency: currencyCode,
    })
  } catch {
    return n.toLocaleString(getLocale(currencyCode), NUMBER_OPTS)
  }
}

const NUMBER_OPTS: Intl.NumberFormatOptions = { minimumFractionDigits: 2, maximumFractionDigits: 2 }

export function formatSignedAmount(value: Money | number | string, currencyCode: string): string {
  const n = typeof value === 'number' ? value : toNumber(toMoney(value))
  const formatted = formatAmount(Math.abs(n), currencyCode)
  if (n > 0) return '+' + formatted
  if (n < 0) return '-' + formatted
  return formatted
}
