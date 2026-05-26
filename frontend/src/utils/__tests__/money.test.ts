// Contract tests for the Money type. See dev-docs/money-types.md.

import { describe, it, expect } from 'vitest'
import {
  abs,
  add,
  div,
  eq,
  gt,
  lt,
  mul,
  neg,
  sign,
  sub,
  toFixed,
  toMoney,
  toNumber,
  zero,
} from '@/utils/money'

describe('toMoney', () => {
  it('normalises trailing zeros to canonical form', () => {
    expect(toMoney('100.00')).toBe('100')
  })

  it('accepts numbers and strings', () => {
    expect(toMoney(1.5)).toBe('1.5')
    expect(toMoney('1.5')).toBe('1.5')
  })

  it('preserves 8-decimal precision (BTC scale)', () => {
    expect(toMoney('0.12345678')).toBe('0.12345678')
  })

  it('returns zero for empty input', () => {
    expect(toMoney('')).toBe('0')
  })
})

describe('exact arithmetic', () => {
  it('0.1 + 0.2 === 0.3 (the float trap)', () => {
    expect(add(toMoney('0.1'), toMoney('0.2'))).toBe('0.3')
    expect(eq(add(toMoney('0.1'), toMoney('0.2')), toMoney('0.3'))).toBe(true)
  })

  it('subtracts exactly across many decimals', () => {
    expect(sub(toMoney('1000.0000'), toMoney('0.0001'))).toBe('999.9999')
  })

  it('multiplies preserving precision', () => {
    expect(mul(toMoney('0.1'), toMoney('0.1'))).toBe('0.01')
  })

  it('divides preserving precision', () => {
    expect(div(toMoney('1'), toMoney('3'))).toMatch(/^0\.3+/)
  })

  it('negation and abs are inverses on negative input', () => {
    expect(neg(toMoney('-5'))).toBe('5')
    expect(abs(toMoney('-5'))).toBe('5')
    expect(abs(toMoney('5'))).toBe('5')
  })
})

describe('comparison', () => {
  it('eq treats 100 and 100.00 as equal', () => {
    expect(eq(toMoney('100'), toMoney('100.00'))).toBe(true)
  })

  it('lt / gt order correctly', () => {
    expect(lt(toMoney('1'), toMoney('2'))).toBe(true)
    expect(gt(toMoney('2'), toMoney('1'))).toBe(true)
    expect(lt(toMoney('1'), toMoney('1'))).toBe(false)
  })

  it('sign returns -1, 0, 1', () => {
    expect(sign(toMoney('-5'))).toBe(-1)
    expect(sign(zero())).toBe(0)
    expect(sign(toMoney('5'))).toBe(1)
  })
})

describe('display conversions', () => {
  it('toNumber is the documented lossy boundary', () => {
    expect(toNumber(toMoney('1.5'))).toBe(1.5)
  })

  it('toFixed rounds to the requested precision', () => {
    expect(toFixed(toMoney('1.005'), 2)).toBe('1.01')
    expect(toFixed(toMoney('100'), 2)).toBe('100.00')
  })
})
