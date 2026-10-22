import { describe, it, expect } from 'vitest'
import {
  groupFunds, importFacts, pairCandidates, resultSentence, postingLine, setCorrection, spanSentence, yearCountsText, clearCorrection,
} from '@/utils/statementImport'
import { fund, planData, row } from '@/components/investments/__tests__/statementFixtures'

describe('statement import helpers', () => {
  it('states the span of the statement up front', () => {
    expect(spanSentence(planData().summary)).toBe(
      'This statement covers 2010–2024: 15 years, 2,475 rows across 2 funds (1 still held).')
  })

  it('puts current holdings first and groups fully redeemed funds', () => {
    const { held, redeemed } = groupFunds(planData().funds)
    expect(held.map(f => f.security_key)).toEqual(['INF1'])
    expect(redeemed.map(f => f.security_key)).toEqual(['INF2'])
  })

  it("summarises a year's rows", () => {
    expect(yearCountsText({ year: 2020, counts: { buy: 12, sell: 1, switch_in: 2, switch_out: 1, reinvest: 4 },
      end_units: '1', ok: true })).toBe('12 purchases · 1 redemption · 3 switches · 4 dividends')
    expect(yearCountsText({ year: 2020, counts: { info: 2 }, end_units: '1' })).toBe('No transactions')
  })

  it('offers the other half of a switch: opposite units, same fund house, within a week', () => {
    const out = row({ id: 'out', date: '2021-01-05', units: '-50.000' })
    const funds = [
      fund({ security_key: 'A', rows: [out] }),
      fund({ security_key: 'B', rows: [
        row({ id: 'in-ok', date: '2021-01-07', units: '10.000' }),
        row({ id: 'in-late', date: '2021-01-20', units: '10.000' }),
        row({ id: 'same-sign', date: '2021-01-05', units: '-1.000' }),
      ] }),
      fund({ security_key: 'C', institution: 'HDFC Mutual Fund', rows: [row({ id: 'other-house', date: '2021-01-05' })] }),
    ]
    const inst = (id: string) => funds.find(f => f.rows.some(r => r.id === id))?.institution
    expect(pairCandidates(funds, out, inst).map(c => c.row.id)).toEqual(['in-ok'])
  })

  it('keeps one correction per row and drops one that no longer changes anything', () => {
    let list = setCorrection([], { row_id: 'r1', exclude: true })
    list = setCorrection(list, { row_id: 'r1', interpretation: 'sell' as never })
    expect(list).toEqual([{ row_id: 'r1', exclude: true, interpretation: 'sell' }])
    expect(setCorrection([], { row_id: 'r2', pair_with: '' })).toEqual([{ row_id: 'r2', pair_with: '' }])
    expect(clearCorrection(list, 'r1')).toEqual([])
  })

  it('shows a posting the way the ledger will', () => {
    expect(postingLine({ account: 'Assets:B', units: '-5', currency: 'X', cost: '10', cost_currency: 'INR',
      cost_date: '2020-01-01', price: '12', price_currency: 'INR' })).toBe('Assets:B  -5 X {10 INR, 2020-01-01} @ 12 INR')
  })
})

describe('the result message after Register', () => {
  const base = { import_id: 'x', transactions: 1428, balances: 32, commodities_created: 0, commodities_updated: 0,
    prices: 0, removed_hand_entries: 0 }

  it('says funds were already in the ledger and prices already stored, instead of zeros', () => {
    const facts = { existingFunds: 32, statementPrices: 32 }
    expect(resultSentence(base, facts)).toBe("32 funds already in your ledger, the statement's prices were already stored.")
  })

  it('counts new funds and prices on a first import', () => {
    expect(resultSentence({ ...base, commodities_created: 32, prices: 32 }, { existingFunds: 0, statementPrices: 32 }))
      .toBe('32 new funds, 32 prices from the statement.')
  })

  it('counts a fund once even when it is in two folios', () => {
    const plan = planData({ funds: [fund({ commodity_status: 'existing' as never }), fund({ folio: 'F2', commodity_status: 'existing' as never })] })
    expect(importFacts(plan)).toEqual({ existingFunds: 1, statementPrices: 0 })
  })
})
