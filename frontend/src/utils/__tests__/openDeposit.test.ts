// Open Deposit helpers: the suggested income account name, the request, and the
// fields a preview needs. See dev-docs/investment-view.md §10.4.
import { describe, it, expect } from 'vitest'
import { buildOpenDepositRequest, missingFields, type OpenDepositForm } from '@/utils/openDeposit'

const base: OpenDepositForm = {
  account: 'Assets:Bank:CD-2026', type: 'cd', currency: 'USD', openDate: '2026-01-15',
  interestRate: '', maturityDate: '', createIncome: false, newIncomeAccount: '', existingIncomeAccount: '',
  recordDeposit: false, fromAccount: '', amount: '', depositDate: '2026-01-15',
}

describe('buildOpenDepositRequest', () => {
  it('sends only what is filled in', () => {
    expect(buildOpenDepositRequest(base, true)).toEqual({
      account: 'Assets:Bank:CD-2026', account_type: 'cd', currency: 'USD', open_date: '2026-01-15',
      interest_rate: null, maturity_date: null, income_account: null, first_deposit: null, dry_run: true,
    })
  })
  it('includes the income account and first deposit', () => {
    const req = buildOpenDepositRequest({
      ...base, interestRate: ' 4.50% ', maturityDate: '2027-01-15',
      createIncome: true, newIncomeAccount: 'Income:Interest:CD-2026',
      recordDeposit: true, fromAccount: 'Assets:Bank:Savings', amount: '10000.00',
    }, false)
    expect(req.interest_rate).toBe('4.50%')
    expect(req.maturity_date).toBe('2027-01-15')
    expect(req.income_account).toEqual({ mode: 'new', account: 'Income:Interest:CD-2026' })
    expect(req.first_deposit).toEqual({ from_account: 'Assets:Bank:Savings', amount: '10000.00', date: '2026-01-15' })
    expect(req.dry_run).toBe(false)
  })
  it('uses an existing income account only when not creating one', () => {
    expect(buildOpenDepositRequest({ ...base, existingIncomeAccount: 'Income:Interest' }, true).income_account)
      .toEqual({ mode: 'existing', account: 'Income:Interest' })
    expect(buildOpenDepositRequest({ ...base, createIncome: true, newIncomeAccount: 'Income:X', existingIncomeAccount: 'Income:Y' }, true).income_account)
      .toEqual({ mode: 'new', account: 'Income:X' })
  })
  it('never sends a maturity date for a savings account', () => {
    expect(buildOpenDepositRequest({ ...base, type: 'savings', maturityDate: '2027-01-15' }, true).maturity_date).toBeNull()
  })
})

describe('missingFields', () => {
  it('lists what a preview still needs', () => {
    expect(missingFields(base)).toEqual([])
    expect(missingFields({ ...base, account: ' ', createIncome: true, recordDeposit: true, amount: 'x' })).toEqual([
      'the account name', 'the income account name', 'the account the money comes from', 'an amount',
    ])
  })
})
