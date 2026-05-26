// Money — branded string wrapping a Decimal value.
// See dev-docs/money-types.md for the contract.
//
// The brand prevents accidental string concatenation (`"100" + "200" === "100200"`)
// and accidental mixing with non-money strings like account names or currency codes.
// Math goes through the helpers below, which use decimal.js for exactness.

import Decimal from 'decimal.js'

export type Money = string & { readonly __brand: 'Money' }

const ZERO_MONEY = '0' as Money

export function toMoney(value: string | number | Decimal): Money {
  if (value === '' || value === null || value === undefined) {
    return ZERO_MONEY
  }
  // Validates by constructing a Decimal; throws for non-numeric input.
  const d = new Decimal(value)
  return d.toString() as Money
}

export function isMoney(value: unknown): value is Money {
  if (typeof value !== 'string') return false
  try {
    new Decimal(value)
    return true
  } catch {
    return false
  }
}

export function zero(): Money {
  return ZERO_MONEY
}

export function add(a: Money, b: Money): Money {
  return new Decimal(a).plus(new Decimal(b)).toString() as Money
}

export function sub(a: Money, b: Money): Money {
  return new Decimal(a).minus(new Decimal(b)).toString() as Money
}

export function mul(a: Money, b: Money | number): Money {
  return new Decimal(a).times(b as Decimal.Value).toString() as Money
}

export function div(a: Money, b: Money | number): Money {
  return new Decimal(a).dividedBy(b as Decimal.Value).toString() as Money
}

export function neg(a: Money): Money {
  return new Decimal(a).negated().toString() as Money
}

export function abs(a: Money): Money {
  return new Decimal(a).abs().toString() as Money
}

export function eq(a: Money, b: Money): boolean {
  return new Decimal(a).equals(new Decimal(b))
}

export function lt(a: Money, b: Money): boolean {
  return new Decimal(a).lessThan(new Decimal(b))
}

export function gt(a: Money, b: Money): boolean {
  return new Decimal(a).greaterThan(new Decimal(b))
}

export function sign(m: Money): -1 | 0 | 1 {
  const c = new Decimal(m).comparedTo(0)
  return c < 0 ? -1 : c > 0 ? 1 : 0
}

// Lossy: only for handing to Intl.NumberFormat or similar display APIs.
// See "Display" in dev-docs/money-types.md.
export function toNumber(m: Money): number {
  return new Decimal(m).toNumber()
}

export function toFixed(m: Money, decimals: number): string {
  return new Decimal(m).toFixed(decimals)
}
