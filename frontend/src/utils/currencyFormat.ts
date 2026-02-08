const CURRENCY_LOCALES: Record<string, string> = {
  INR: 'en-IN',
}

function getLocale(currencyCode: string): string {
  return CURRENCY_LOCALES[currencyCode] || 'en-US'
}

export function formatAmount(value: number, currencyCode: string): string {
  return value.toLocaleString(getLocale(currencyCode), {
    style: 'currency',
    currency: currencyCode,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })
}

export function formatSignedAmount(value: number, currencyCode: string): string {
  const formatted = formatAmount(Math.abs(value), currencyCode)
  if (value > 0) return '+' + formatted
  if (value < 0) return '-' + formatted
  return formatted
}
