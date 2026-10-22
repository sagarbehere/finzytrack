// Synthetic statement-import API payloads for tests (no real statement data).
import type { FundPlan, PlannedRow, StatementParseData, StatementPlanData } from '@/services/generated-api'

export function row(over: Partial<PlannedRow> = {}): PlannedRow {
  return {
    id: 'r1', date: '2020-01-01', kind: 'PURCHASE', description: 'Purchase', amount: '1000.00', units: '100.000',
    nav: '10', balance: '100.000', interpretation: 'buy' as PlannedRow['interpretation'],
    proposed: 'buy' as PlannedRow['proposed'], status: 'import' as PlannedRow['status'],
    corrected: false, transaction: 0, ...over,
  }
}

export function fund(over: Partial<FundPlan> = {}): FundPlan {
  return {
    security_key: 'INF1', folio: 'F1', name: 'DSP Flexi Cap Fund - Regular Plan - Growth', institution: 'DSP Mutual Fund',
    investor: 'Investor One', distributor: 'ARN-1', holding_account: 'Assets:Broker', commodity: 'DSP-FLEXICAP-RG',
    commodity_status: 'new' as FundPlan['commodity_status'], isin: 'INF1', amfi: '100',
    close_units: '100.000', ledger_units: '100.000', reconciled: true, statement_consistent: true, held: true,
    counts: { import: 1 }, matched: 0, fallback: 1,
    years: [{ year: 2020, counts: { buy: 1 }, end_units: '100.000', statement_units: '100.000', ok: true }],
    rows: [row()], ...over,
  }
}

export function parseData(over: Partial<StatementParseData> = {}): StatementParseData {
  return {
    token: 'tok', source: 'cas', variant: 'detailed', period_start: '2010-01-01', period_end: '2024-12-31',
    investors: ['Investor One'],
    distributors: [{ key: 'ARN-1', label: 'ARN-1', funds: ['DSP Flexi Cap Fund - Regular Plan - Growth'] }],
    funds: [], row_count: 1,
    needs_dividend_account: false, needs_taxes_account: false, needs_gains_account: false, needs_cash_account: true,
    ledger_files: ['/l/main.beancount'],
    preselected: { holding_accounts: {} },
    ...over,
  }
}

export function planData(over: Partial<StatementPlanData> = {}): StatementPlanData {
  return {
    summary: {
      period_start: '2010-01-01', period_end: '2024-12-31', first_date: '2010-07-23', last_date: '2024-02-27',
      years: 15, rows: 2475, funds: 2, funds_held: 1, transactions: 1, new_commodities: 1, already_imported: 0,
      matched: 0, fallback: 1, investors: ['Investor One'],
    },
    funds: [fund(), fund({ security_key: 'INF2', name: 'HDFC Prudence Fund', institution: 'HDFC Mutual Fund', held: false,
                           close_units: '0', ledger_units: '0', commodity: 'HDFC-PRUDENCE-RG', rows: [row({ id: 'r2' })] })],
    transactions: [{ index: 0, date: '2020-01-01', narration: 'Buy DSP-FLEXICAP-RG', payee: 'DSP Mutual Fund', kind: 'buy',
                     row_ids: ['r1'], postings: [
                       { account: 'Assets:Broker', units: '100.000', currency: 'DSP-FLEXICAP-RG', cost: '10.0000000000', cost_currency: 'INR' },
                       { account: 'Equity:Funding', units: '-1000.00', currency: 'INR' },
                     ] }],
    commodities: [], balances: [], prices: [], removed_hand_entries: 0, unclaimed_bank_transactions: [],
    blocking: [], warnings: [], target_file: '/l/main.beancount', preview_hash: 'h1',
    ...over,
  }
}
