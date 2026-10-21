/**
 * Pure helpers for the Open Deposit quick action: the suggested interest income
 * account name, and the request sent to POST /api/investments/open-deposit.
 * See dev-docs/investment-view.md §10.4.
 */
import { OpenDepositRequest, DepositIncomeAccount } from '@/services/generated-api'

/**
 * A starting name for a deposit's own interest income account: the common parent
 * of your existing interest income accounts plus the deposit's last name part —
 * so it follows how you already name things. Without any, `Income:Interest:<part>`.
 * Only a suggestion the user can edit; nothing is classified by name.
 */
export function suggestIncomeAccount(depositAccount: string, interestAccounts: string[]): string {
  const leaf = depositAccount.trim().split(':').filter(Boolean).pop() ?? ''
  if (!leaf) return ''
  const parents = interestAccounts.map(a => a.split(':').slice(0, -1))
  let common: string[] = parents[0] ?? []
  for (const p of parents.slice(1)) {
    let i = 0
    while (i < common.length && i < p.length && common[i] === p[i]) i++
    common = common.slice(0, i)
  }
  const parent = common.length >= 2 ? common.join(':') : 'Income:Interest'
  return `${parent}:${leaf}`
}

export type DepositType = 'savings' | 'cd' | 'bond'

export interface OpenDepositForm {
  account: string
  type: DepositType
  currency: string
  openDate: string
  interestRate: string
  maturityDate: string
  createIncome: boolean
  newIncomeAccount: string
  existingIncomeAccount: string
  recordDeposit: boolean
  fromAccount: string
  amount: string
  depositDate: string
}

/** The request for the form's current values; `dryRun` for the preview. */
export function buildOpenDepositRequest(form: OpenDepositForm, dryRun: boolean): OpenDepositRequest {
  let income: DepositIncomeAccount | null = null
  if (form.createIncome && form.newIncomeAccount.trim()) {
    income = { mode: DepositIncomeAccount.mode.NEW, account: form.newIncomeAccount.trim() }
  } else if (!form.createIncome && form.existingIncomeAccount.trim()) {
    income = { mode: DepositIncomeAccount.mode.EXISTING, account: form.existingIncomeAccount.trim() }
  }
  const hasMaturity = form.type !== 'savings' && form.maturityDate !== ''
  return {
    account: form.account.trim(),
    account_type: form.type as OpenDepositRequest.account_type,
    currency: form.currency.trim(),
    open_date: form.openDate,
    interest_rate: form.interestRate.trim() || null,
    maturity_date: hasMaturity ? form.maturityDate : null,
    income_account: income,
    first_deposit: form.recordDeposit
      ? {
          from_account: form.fromAccount.trim(),
          amount: form.amount.trim(),
          date: form.depositDate || null,
        }
      : null,
    dry_run: dryRun,
  }
}

/** Fields that must be filled before a preview makes sense (empty = ready). */
export function missingFields(form: OpenDepositForm): string[] {
  const missing: string[] = []
  if (!form.account.trim()) missing.push('the account name')
  if (!form.currency.trim()) missing.push('a currency')
  if (!form.openDate) missing.push('a date')
  if (form.createIncome && !form.newIncomeAccount.trim()) missing.push('the income account name')
  if (form.recordDeposit && !form.fromAccount.trim()) missing.push('the account the money comes from')
  if (form.recordDeposit && !/^(\d+(\.\d*)?|\.\d+)$/.test(form.amount.trim())) missing.push('an amount')
  return missing
}
