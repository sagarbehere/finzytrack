import { describe, it, expect } from 'vitest'
import { formatAmount } from '@/utils/currencyFormat'

describe('formatAmount', () => {
  it('formats a normal currency amount', () => {
    // Non-breaking space between symbol and number in some ICU builds — assert loosely.
    const out = formatAmount(1234.5, 'USD')
    expect(out).toContain('1,234.5')
    expect(out).toContain('$')
  })

  // Regression: an empty or invalid currency code must NOT throw
  // (`RangeError: Invalid currency code`) — it degrades to a plain number, so a
  // synthetic/empty-data row can never crash a widget's render.
  it('degrades to a plain number for an empty currency code', () => {
    expect(() => formatAmount(1234.5, '')).not.toThrow()
    expect(formatAmount(1234.5, '')).toBe('1,234.50')
  })

  it('degrades to a plain number for an invalid currency code', () => {
    expect(() => formatAmount(42, 'NOTACODE')).not.toThrow()
    expect(formatAmount(42, 'NOTACODE')).toBe('42.00')
  })
})
