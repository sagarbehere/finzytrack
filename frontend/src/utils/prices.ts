/**
 * Pure helpers for the Investments → Prices tab: display formatting, input
 * checks, and turning a row edit into a `PUT /api/prices` batch.
 * See dev-docs/investment-view.md §10.2.
 */
import type { PriceEditRequest, PriceOverviewRow, PricePoint } from '@/services/generated-api'
import { getLocale } from '@/utils/currencyFormat'
import { todayLocal } from '@/utils/date'
import { gt, toMoney, toNumber, zero, type Money } from '@/utils/money'

// Mirrors COMMODITY_CODE_PATTERN in backend/app/core/constants.py (Beancount's
// own lexer rule). Must not be stricter than the backend.
const COMMODITY_CODE_RE = /^(?:[A-Z][A-Z0-9'._-]*[A-Z0-9]?\b|\/[A-Z0-9'._-]*[A-Z](?:[A-Z0-9'._-]*[A-Z0-9])?)$/

export function isCommodityCode(code: string): boolean {
  return COMMODITY_CODE_RE.test(code)
}

/** A price with the decimals it was written with (at least 2), then its quote
 * currency: "1,234.5678 USD". Display only — the lossy number conversion lives here. */
export function formatPrice(value: Money | string, quote?: string | null): string {
  const m = toMoney(value)
  // Decimals as written ("1.2500" keeps four), not as normalised.
  const written = /^-?\d*\.?\d*$/.test(String(value)) ? String(value) : m
  const decimals = (written.split('.')[1] ?? '').length
  const shown = Math.min(Math.max(decimals, 2), 12)
  const text = toNumber(m).toLocaleString(getLocale(quote ?? ''), {
    minimumFractionDigits: shown,
    maximumFractionDigits: shown,
  })
  return quote ? `${text} ${quote}` : text
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Oct 14" in `today`'s year, else "Oct 14, 2024". */
export function formatShortDate(iso: string, today: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const base = `${MONTHS[m - 1]} ${d}`
  return iso.slice(0, 4) === today.slice(0, 4) ? base : `${base}, ${y}`
}

/** "today", "yesterday", "6 days ago", "3 months ago", "2 years ago"; a
 * negative age (a date after today) is "in the future". */
export function formatAge(days: number): string {
  if (days < 0) return 'in the future'
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 60) return `${days} days ago`
  if (days < 730) return `${Math.floor(days / 30)} months ago`
  return `${Math.floor(days / 365)} years ago`
}

export function cadenceLabel(cadence: PriceOverviewRow['cadence']): string {
  switch (cadence) {
    case 'daily': return 'Daily'
    case 'monthly': return 'Monthly'
    case 'computed': return 'Computed'
    default: return 'Not fetched'
  }
}

export interface PriceInput {
  date: string
  commodity: string
  quote: string
  number: string
}

export type PriceInputErrors = Partial<Record<keyof PriceInput, string>>

/** Client-side checks before a save; the backend checks the same rules.
 * `today` is the user's local date (YYYY-MM-DD). */
export function validatePriceInput(input: PriceInput, today: string = todayLocal()): PriceInputErrors {
  const errors: PriceInputErrors = {}
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date)) errors.date = 'Enter a date.'
  else if (input.date > today) errors.date = "A price can't be dated after today."
  if (!isCommodityCode(input.commodity.trim())) {
    errors.commodity = 'Use an uppercase code, e.g. VOO or BRK.B.'
  }
  if (!isCommodityCode(input.quote.trim())) {
    errors.quote = 'Use an uppercase code, e.g. USD.'
  }
  // Commas are refused, not stripped: "12,5" must not become 125.
  const n = input.number.trim()
  if (n.includes(',')) {
    errors.number = 'Use a dot for decimals, without commas, e.g. 1234.50.'
  } else if (!/^(\d+(\.\d*)?|\.\d+)$/.test(n) || !gt(toMoney(n), zero())) {
    errors.number = 'Enter a price above zero.'
  }
  return errors
}

/** The number exactly as typed (already checked to be a plain decimal), so
 * "712.50" is saved as 712.50, not 712.5. */
function plainNumber(typed: string): Money {
  return typed.trim() as Money
}

/** The batch for a new price. It doesn't replace a price already on that
 * date (409 PRICE_EXISTS) until resent through `withReplace`. */
export function addRequest(input: PriceInput): PriceEditRequest {
  return {
    set: [{
      date: input.date,
      commodity: input.commodity.trim(),
      quote_currency: input.quote.trim(),
      number: plainNumber(input.number),
      replace: false,
    }],
  }
}

/** The same batch, allowed to replace prices already on its dates. */
export function withReplace(request: PriceEditRequest): PriceEditRequest {
  return { ...request, set: (request.set ?? []).map(p => ({ ...p, replace: true })) }
}

/** The batch for editing one stored price. A changed date is a remove plus a
 * set (prices are keyed by date) that won't replace a price already on the new
 * date until resent through `withReplace`; `null` when nothing changed. */
export function editRequest(
  original: PricePoint, commodity: string, edited: { date: string; number: string },
): PriceEditRequest | null {
  const number = plainNumber(edited.number)
  const sameNumber = toMoney(number) === toMoney(original.number)
  if (edited.date === original.date && sameNumber) return null
  const set = [{ date: edited.date, commodity, quote_currency: original.quote_currency, number }]
  if (edited.date === original.date) return { set }
  return {
    set: set.map(p => ({ ...p, replace: false })),
    remove: [{ date: original.date, commodity, quote_currency: original.quote_currency }],
  }
}

export function removeRequest(original: PricePoint, commodity: string): PriceEditRequest {
  return { remove: [{ date: original.date, commodity, quote_currency: original.quote_currency }] }
}

export interface PriceFreshness {
  /** Held holdings the fetcher prices (by cadence or computed). */
  priceable: number
  /** Newest latest-price date among them (ISO), or null when none has a price. */
  asOf: string | null
  /** How many of them need a newer price (per their cadence). */
  outOfDate: number
}

/** The price state a dashboard with the Update prices action shows beside it.
 * Only held holdings the fetcher actually prices count: currencies, money-market
 * funds, sold-out holdings and prices typed in another currency don't. */
export function priceFreshness(rows: readonly PriceOverviewRow[]): PriceFreshness {
  const fetched = rows.filter(r => r.held && r.cadence !== 'never' && !r.not_fetched_reason)
  const dates = fetched.map(r => r.latest?.date).filter((d): d is string => !!d).sort()
  return {
    priceable: fetched.length,
    asOf: dates.length ? dates[dates.length - 1] : null,
    outOfDate: fetched.filter(r => r.out_of_date).length,
  }
}

/** Whether a holding's Price symbol offers the Indian mutual fund (AMFI) finder:
 * holdings bought in INR. Elsewhere the ticker is printed on the statement. */
export function offersAmfiFinder(item: { is_currency?: boolean | null; cost_currency?: string | null }): boolean {
  return !item.is_currency && item.cost_currency === 'INR'
}
