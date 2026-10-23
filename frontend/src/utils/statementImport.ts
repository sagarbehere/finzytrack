// Pure helpers for Investments → Import statement (dev-docs/cas-import.md §9a.1, §13.3).
// Units and amounts stay decimal strings; only display formatting turns them into numbers.

import type {
  FundPlan,
  StatementCommitData,
  StatementPlanData,
  PlannedPosting,
  PlannedRow,
  RowCorrection,
  StatementPlanSummary,
  YearCheck,
} from '@/services/generated-api'
import { toMoney, sub, sign } from '@/utils/money'

export type Interpretation = PlannedRow['interpretation']
export type RowStatus = PlannedRow['status']

/** What the user may set a row to mean. `tax` rows follow their parent, so they're not offered. */
export const INTERPRETATION_LABELS: Record<string, string> = {
  buy: 'Purchase',
  sell: 'Redemption',
  reinvest: 'Dividend reinvested',
  dividend: 'Dividend paid out',
  switch_out: 'Switch out',
  switch_in: 'Switch in',
  merge_out: 'Merger out',
  merge_in: 'Merger in',
  tax: 'Tax or charge',
  segregation: 'Segregated portfolio',
  reversal: 'Reversal',
  info: 'Information only',
  unsupported: 'Not imported',
}

export const CHOOSABLE_INTERPRETATIONS = [
  'buy', 'sell', 'reinvest', 'dividend', 'switch_out', 'switch_in', 'merge_out', 'merge_in',
  'segregation', 'reversal', 'info',
] as const

export const STATUS_LABELS: Record<string, string> = {
  import: 'Imported',
  already: 'Already in ledger',
  before_start: 'Before start date',
  before_hand: 'Before your entries',
  info: 'Info only',
  unsupported: 'Not imported',
  excluded: 'Excluded',
  reversed: 'Reversed',
  failed: 'Failed',
}

/** Badge colour per row status (TW+ flat badge colours). */
export function statusTone(status: string): 'green' | 'gray' | 'yellow' | 'red' | 'blue' {
  switch (status) {
    case 'import': return 'green'
    case 'already': case 'before_start': case 'before_hand': return 'blue'
    case 'unsupported': case 'failed': return 'red'
    case 'excluded': case 'reversed': return 'yellow'
    default: return 'gray'
  }
}

const n = (x: number) => new Intl.NumberFormat('en-IN').format(x)

/** "This statement covers 2010–2026: 17 years, 2,475 transactions across 32 funds (7 still held)." */
export function spanSentence(s: StatementPlanSummary): string {
  const first = s.first_date ? s.first_date.slice(0, 4) : s.period_start.slice(0, 4)
  const last = s.last_date ? s.last_date.slice(0, 4) : s.period_end.slice(0, 4)
  const range = first === last ? first : `${first}–${last}`
  const years = s.years === 1 ? '1 year' : `${n(s.years)} years`
  const funds = s.funds === 1 ? '1 fund' : `${n(s.funds)} funds`
  return `This statement covers ${range}: ${years}, ${n(s.rows)} rows across ${funds} (${n(s.funds_held)} still held).`
}

/** Current holdings first; fully redeemed funds grouped (§9a.4). */
export function groupFunds(funds: FundPlan[]): { held: FundPlan[]; redeemed: FundPlan[] } {
  return { held: funds.filter(f => f.held), redeemed: funds.filter(f => !f.held) }
}

/** "12 purchases · 3 redemptions · 4 dividends" for a year's header. */
export function yearCountsText(y: YearCheck): string {
  const c = y.counts as Record<string, number>
  const parts: string[] = []
  const add = (count: number | undefined, one: string, many: string) => {
    if (count) parts.push(`${n(count)} ${count === 1 ? one : many}`)
  }
  add(c.buy, 'purchase', 'purchases')
  add((c.sell ?? 0) || undefined, 'redemption', 'redemptions')
  add((c.switch_in ?? 0) + (c.switch_out ?? 0) || undefined, 'switch', 'switches')
  add((c.reinvest ?? 0) + (c.dividend ?? 0) || undefined, 'dividend', 'dividends')
  add((c.merge_in ?? 0) + (c.merge_out ?? 0) || undefined, 'merger', 'mergers')
  add(c.unsupported, 'not imported', 'not imported')
  return parts.join(' · ') || 'No transactions'
}

/** Rows the user may pair `row` with: the other half of a switch or merger — opposite
 *  units, in a fund of the same fund house, within a week. */
export function pairCandidates(
  funds: FundPlan[], row: PlannedRow, institutionOf: (rowId: string) => string | undefined,
): { row: PlannedRow; fund: FundPlan }[] {
  if (!row.units) return []
  const rowSign = sign(toMoney(row.units))
  const inst = institutionOf(row.id)
  const day = Date.parse(row.date)
  const out: { row: PlannedRow; fund: FundPlan }[] = []
  for (const f of funds) {
    if (f.institution !== inst) continue
    for (const r of f.rows) {
      if (r.id === row.id || !r.units || r.status === 'excluded' || r.interpretation === 'tax') continue
      if (sign(toMoney(r.units)) !== -rowSign) continue
      if (Math.abs(Date.parse(r.date) - day) > 7 * 86_400_000) continue
      out.push({ row: r, fund: f })
    }
  }
  return out.sort((a, b) => a.row.date.localeCompare(b.row.date))
}

/** Merge one change into the corrections list (one entry per row; an empty change removes it). */
export function setCorrection(list: RowCorrection[], change: RowCorrection): RowCorrection[] {
  const rest = list.filter(c => c.row_id !== change.row_id)
  const prev = list.find(c => c.row_id === change.row_id)
  const merged: RowCorrection = { ...(prev ?? {}), ...change }
  const meaningful = merged.exclude || merged.interpretation || (merged.pair_with !== undefined && merged.pair_with !== null)
    || merged.date || merged.units || merged.amount
  return meaningful ? [...rest, merged] : rest
}

export function clearCorrection(list: RowCorrection[], rowId: string): RowCorrection[] {
  return list.filter(c => c.row_id !== rowId)
}

/** Units difference shown when a fund doesn't reconcile (ledger − statement). */
export function unitsGap(f: FundPlan): string {
  return sub(toMoney(f.ledger_units), toMoney(f.close_units)).toString()
}

export function isZero(x: string | null | undefined): boolean {
  return !x || sign(toMoney(x)) === 0
}

/** One posting as a Beancount-like line: "Assets:X  10.000 CODE {12.5 INR, 2020-01-01} @ 15 INR". */
export function postingLine(p: PlannedPosting): string {
  let s = `${p.account}  ${p.units} ${p.currency}`
  if (p.cost != null) s += ` {${p.cost} ${p.cost_currency}${p.cost_date ? `, ${p.cost_date}` : ''}}`
  if (p.price != null) s += ` @ ${p.price} ${p.price_currency}`
  return s
}

/** What the plan knew about funds and prices, kept for the result message after Register. */
export interface ImportFacts { existingFunds: number; statementPrices: number }

export function importFacts(plan: StatementPlanData): ImportFacts {
  const existing = new Set(plan.funds.filter(f => f.commodity_status === 'existing').map(f => f.commodity))
  return { existingFunds: existing.size, statementPrices: plan.prices.length }
}

/** "32 funds already in your ledger, prices from the statement already stored." — no bare zeros. */
export function resultSentence(r: StatementCommitData, facts: ImportFacts | null): string {
  const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-IN')} ${n === 1 ? one : many}`
  const parts: string[] = []
  if (r.commodities_created) parts.push(plural(r.commodities_created, 'new fund', 'new funds'))
  if (facts?.existingFunds) {
    parts.push(`${plural(facts.existingFunds, 'fund', 'funds')} already in your ledger`
      + (r.commodities_updated ? ` (${r.commodities_updated} with details added)` : ''))
  } else if (r.commodities_updated) {
    parts.push(`details added to ${plural(r.commodities_updated, 'fund', 'funds')}`)
  }
  if (r.prices) parts.push(`${plural(r.prices, 'price', 'prices')} from the statement`)
  else if (facts?.statementPrices) parts.push("the statement's prices were already stored")
  if (r.removed_hand_entries) parts.push(`${plural(r.removed_hand_entries, 'hand-entered transaction', 'hand-entered transactions')} replaced`)
  if (!parts.length) return ''
  const text = parts.join(', ')
  return text.charAt(0).toUpperCase() + text.slice(1) + '.'
}

export interface StatCard { label: string; value: string; note: string }

/** The preview's summary cards, each saying what it counts in plain words. ``choices``
 *  are the user's cash-side accounts; the cash card reads differently with and without
 *  bank matching (no "0 matched" when matching was never asked for). */
export function statCards(
  plan: StatementPlanData,
  choices: { cash_fallback_account?: string | null; cash_matching_account?: string | null;
             cutoff_date?: string | null; cash_before_cutoff_account?: string | null },
): StatCard[] {
  const s = plan.summary
  const n = (x: number) => x.toLocaleString('en-IN')
  const included = plan.funds.filter(f => !f.excluded)
  const reconciled = included.filter(f => f.reconciled).length
  const checks = plan.balances.length

  const moved = s.matched + s.fallback
  const fallback = choices.cash_fallback_account || choices.cash_matching_account || 'the cash-side account'
  const cutoff = choices.cutoff_date && choices.cash_before_cutoff_account
    ? `; before ${choices.cutoff_date}, ${choices.cash_before_cutoff_account}` : ''
  let cash: StatCard
  if (!choices.cash_matching_account) {
    cash = { label: 'Cash movements', value: n(moved), note: `Money paid in or out, all to ${fallback}${cutoff}.` }
  } else if (moved > 0 && s.matched === 0) {
    cash = {
      label: 'Matched to bank transactions', value: `0 of ${n(moved)}`,
      note: `None matched. Check the amounts and dates of the bank transactions in ${choices.cash_matching_account}.`,
    }
  } else {
    cash = {
      label: 'Matched to bank transactions', value: `${n(s.matched)} of ${n(moved)}`,
      note: s.fallback ? `The other ${n(s.fallback)} go to ${choices.cash_fallback_account || fallback}${cutoff}.`
        : `All matched to transactions in ${choices.cash_matching_account}.`,
    }
  }

  return [
    {
      label: 'Transactions to write', value: n(s.transactions),
      note: `One can cover several statement rows (a switch, its stamp duty). Plus ${n(checks)} balance check${checks === 1 ? '' : 's'}.`,
    },
    {
      label: 'Funds matching the statement', value: `${n(reconciled)} of ${n(included.length)}`,
      note: 'Units after the import equal the statement\'s closing units.',
    },
    cash,
    {
      label: 'Already in the ledger', value: n(s.already_imported),
      note: 'Rows an earlier import already wrote. They are skipped.',
    },
  ]
}

/** A fund's cash movements, for its row: matched ones only when there are any. */
export function fundCashText(f: { matched?: number; fallback?: number }): string {
  const matched = f.matched ?? 0
  const other = f.fallback ?? 0
  if (!matched && !other) return ''
  if (!matched) return `${other} cash movement${other === 1 ? '' : 's'}`
  return `cash: ${matched} matched to bank transactions${other ? `, ${other} not` : ''}`
}
