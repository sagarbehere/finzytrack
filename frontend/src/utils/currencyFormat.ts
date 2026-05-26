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
  return n.toLocaleString(getLocale(currencyCode), {
    style: 'currency',
    currency: currencyCode,
  })
}

export function formatSignedAmount(value: Money | number | string, currencyCode: string): string {
  const n = typeof value === 'number' ? value : toNumber(toMoney(value))
  const formatted = formatAmount(Math.abs(n), currencyCode)
  if (n > 0) return '+' + formatted
  if (n < 0) return '-' + formatted
  return formatted
}
