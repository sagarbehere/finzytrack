import { describe, it, expect } from 'vitest'
import { formatAmount, formatCompact, groupDigits, sharedCurrency } from '@/utils/currencyFormat'

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

describe('formatCompact', () => {
  it('uses lakh/crore units for INR', () => {
    expect(formatCompact(32400, 'INR')).toBe('32.4K')
    expect(formatCompact(2639679, 'INR')).toBe('26.4L')
    expect(formatCompact(-18070724, 'INR')).toBe('-1.8Cr')
  })

  it('keeps k/M for other currencies', () => {
    expect(formatCompact(832606, 'USD')).toBe('832.6k')
    expect(formatCompact(2639679, 'EUR')).toBe('2.6M')
    expect(formatCompact(999, '')).toBe('999')
  })
})

describe('groupDigits', () => {
  it('groups the integer part by the currency locale', () => {
    expect(groupDigits('1129.41', 'USD')).toBe('1,129.41')
    expect(groupDigits('-1129.41', 'BRL')).toBe('-1,129.41')
    expect(groupDigits('2814969.99', 'INR')).toBe('28,14,969.99')
    expect(groupDigits('999.00', 'USD')).toBe('999.00')
  })

  it('keeps every decimal and beyond-float integers exactly', () => {
    expect(groupDigits('1234.123456789', 'USD')).toBe('1,234.123456789')
    expect(groupDigits('12345678901234567890.01', null)).toBe('12,345,678,901,234,567,890.01')
  })

  it('returns anything that is not a plain decimal unchanged', () => {
    expect(groupDigits('', 'USD')).toBe('')
    expect(groupDigits('abc', 'USD')).toBe('abc')
  })
})

describe('sharedCurrency', () => {
  it('returns the single currency of all postings, else undefined', () => {
    expect(sharedCurrency({ postings: [{ currency: 'INR' }, { currency: 'INR' }, { currency: null }] })).toBe('INR')
    expect(sharedCurrency({ postings: [{ currency: 'INR' }, { currency: 'USD' }] })).toBeUndefined()
    expect(sharedCurrency({ postings: [] })).toBeUndefined()
  })
})
