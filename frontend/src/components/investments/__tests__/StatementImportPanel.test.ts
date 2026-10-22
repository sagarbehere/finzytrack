// Investments → Import statement, as the user sees it.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, reactive } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { planData, parseData, fund, row } from './statementFixtures'

const s = vi.hoisted(() => ({ state: null as any }))
vi.mock('@/composables/useStatementImport', () => ({ useStatementImport: () => s.state }))
vi.mock('@/composables/useAccounts', () => ({ useAccounts: () => ({ fetchAccounts: vi.fn() }) }))
vi.mock('@/composables/useServerNotices', () => ({ useServerNotices: () => ({ check: vi.fn() }) }))
vi.mock('@/composables/useNotifications', () => ({ useToast: () => ({ success: vi.fn() }) }))
vi.mock('@/utils/ErrorHandler', () => ({ errorHandler: { display: vi.fn() } }))
vi.mock('@/services/generated-api', () => ({ PricesService: { updatePrices: vi.fn() } }))

import StatementImportPanel from '@/components/investments/StatementImportPanel.vue'

function state(over: Record<string, unknown> = {}) {
  return {
    parsed: ref(null), plan: ref(null), result: ref(null), resultFacts: ref(null), imports: ref([]), corrections: ref([]),
    mapping: reactive({ holding_accounts: {}, commodity_codes: {}, hand_entries: {}, cash_fallback_account: '',
      cash_matching_account: '', cash_before_cutoff_account: '', dividend_account: '', taxes_account: '',
      gains_account: '', match_window_days: 7, cutoff_date: null, history_start: null, target_file: null,
      tag_dividend_account: false }),
    reading: ref(false), previewing: ref(false), committing: ref(false),
    readError: ref(null), previewError: ref(null), commitError: ref(null),
    read: vi.fn(), preview: vi.fn(), correct: vi.fn(), undoCorrection: vi.fn(), commit: vi.fn(),
    loadImports: vi.fn(), undo: vi.fn().mockResolvedValue(true), reset: vi.fn(),
    ...over,
  }
}

function render() {
  return mount(StatementImportPanel, {
    global: { stubs: { AccountDropdown: true, RouterLink: { template: '<a><slot /></a>' } } },
    attachTo: document.body,
  })
}
const text = (w: ReturnType<typeof render>) => w.text().replace(/\s+/g, ' ')
const button = (w: ReturnType<typeof render>, label: string) => w.findAll('button').find(b => b.text().includes(label))!

beforeEach(() => { s.state = state() })
afterEach(() => { document.body.innerHTML = '' })

describe('StatementImportPanel', () => {
  it('asks for the PDF and its password first', async () => {
    const w = render()
    expect(text(w)).toContain('Indian mutual funds (CAS)')
    expect(button(w, 'Read statement').attributes('disabled')).toBeDefined()
    const input = w.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['%PDF'], 'cas.pdf')] })
    await input.trigger('change')
    await w.find('#stmt-password').setValue('pw')
    await button(w, 'Read statement').trigger('click')
    expect(s.state.read).toHaveBeenCalledWith(expect.any(File), 'pw')
  })

  it('shows the span, the distributors, and funds with current holdings first', async () => {
    s.state = state({ parsed: ref(parseData()), plan: ref(planData()) })
    const w = render()
    await flushPromises()
    const t = text(w)
    expect(t).toContain('This statement covers 2010–2024: 15 years, 2,475 rows across 2 funds (1 still held).')
    expect(t).toContain('ARN-1')
    expect(t).toContain('DSP Flexi Cap Fund - Regular Plan - Growth')
    expect(t).toContain('1 fund fully redeemed')
    expect(t).not.toContain('HDFC Prudence Fund')  // grouped, collapsed
    expect(button(w, 'Register 1 transactions').attributes('disabled')).toBeUndefined()
  })

  it('keeps Register disabled while something blocks, and says what', async () => {
    s.state = state({ parsed: ref(parseData()), plan: ref(planData({
      blocking: [{ code: 'ACCOUNT_REQUIRED', message: 'Choose a holding account for every distributor.' }] })) })
    const w = render()
    expect(text(w)).toContain('Choose a holding account for every distributor.')
    expect(button(w, 'Register').attributes('disabled')).toBeDefined()
  })

  it('opens a fund by year and sends a row correction', async () => {
    s.state = state({ parsed: ref(parseData()), plan: ref(planData({ funds: [fund({
      years: [{ year: 2020, counts: { buy: 1 }, end_units: '100.000', statement_units: '101.000', ok: false }],
      reconciled: false, ledger_units: '100.000', close_units: '101.000',
      rows: [row({ reason: 'Described as a transfer from another fund.' })],
    })] })) })
    const w = render()
    await button(w, 'Details').trigger('click')
    expect(text(w)).toContain('statement says 101.000')         // the failing year opens by itself
    expect(text(w)).toContain('Described as a transfer from another fund.')
    await button(w, 'Fix').trigger('click')
    await button(w, 'Exclude this row').trigger('click')
    expect(s.state.correct).toHaveBeenCalledWith({ row_id: 'r1', exclude: true })
  })

  it('lets the user change a new commodity code', async () => {
    s.state = state({ parsed: ref(parseData()), plan: ref(planData()) })
    const w = render()
    const input = w.find('input[id^="code-"]')
    await input.setValue('ppfas-flexi')
    await input.trigger('change')
    expect(s.state.mapping.commodity_codes).toEqual({ INF1: 'PPFAS-FLEXI' })
  })

  it('undoes an earlier import after confirming', async () => {
    s.state = state({ imports: ref([{ import_id: 'cas-1', date: '2026-10-22', source: 'cas', transactions: 3,
      file: '/l/main.beancount' }]) })
    const w = render()
    await button(w, 'Undo').trigger('click')
    const confirm = Array.from(document.body.querySelectorAll('button')).find(b => b.textContent?.trim() === 'Undo import')!
    confirm.click()
    await flushPromises()
    expect(s.state.undo).toHaveBeenCalledWith('cas-1')
  })
})

describe('StatementImportPanel — messages', () => {
  it('opens the fund and year a message points at', async () => {
    s.state = state({ parsed: ref(parseData()), plan: ref(planData({
      warnings: [{ code: 'UNPAIRED_SWITCH', row_id: 'r2', message: 'HDFC Prudence Fund, 2020-01-01, 1000.00: No matching other half…' }],
    })) })
    const w = render()
    expect(text(w)).not.toContain('Purchase Amount')
    await button(w, 'Show').trigger('click')
    await flushPromises()
    expect(text(w)).toContain('Hide details')                       // the redeemed fund is open
    expect(w.find('#row-r2').classes()).toContain('bg-yellow-50')    // its year is open, the row highlighted
  })
})
