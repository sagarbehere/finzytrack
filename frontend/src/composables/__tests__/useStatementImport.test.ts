// The Import statement flow's state: read → pre-select only the user's own earlier
// choices → live re-plan on every change (stale replies dropped) → commit with the
// preview's hash.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises } from '@vue/test-utils'

const m = vi.hoisted(() => ({
  parse: vi.fn(), preview: vi.fn(), commit: vi.fn(), list: vi.fn(), undo: vi.fn(), display: vi.fn(),
}))
vi.mock('@/services/generated-api', () => {
  class ApiError extends Error { body: unknown; constructor(body: unknown) { super('api'); this.body = body } }
  return {
    ApiError,
    InvestmentsService: {
      parseStatement: m.parse, previewStatement: m.preview, commitStatement: m.commit,
      listStatementImports: m.list, undoStatementImport: m.undo,
    },
  }
})
vi.mock('@/utils/ErrorHandler', () => ({
  errorHandler: { display: m.display, messageOf: (e: { body?: { error?: { message?: string } } }) => e?.body?.error?.message ?? 'failed' },
}))

import { ApiError } from '@/services/generated-api'
import { useStatementImport } from '@/composables/useStatementImport'
import { parseData, planData } from '@/components/investments/__tests__/statementFixtures'

const file = new File(['%PDF'], 'cas.pdf', { type: 'application/pdf' })
// An ApiError as the generated client throws it (only `body` matters here).
const apiError = (code: string, message: string) =>
  Object.assign(Object.create(ApiError.prototype), { body: { error: { code, message } } })

beforeEach(() => {
  Object.values(m).forEach(f => f.mockReset())
  m.list.mockResolvedValue({ data: { imports: [] } })
  m.preview.mockResolvedValue({ data: planData() })
})

describe('useStatementImport', () => {
  it('pre-selects only the accounts the user chose last time, then previews', async () => {
    m.parse.mockResolvedValue({ data: parseData({ preselected: {
      holding_accounts: { 'ARN-1': 'Assets:Broker', 'ARN-9': 'Assets:Gone' }, cash_fallback_account: 'Equity:Funding' } }) })
    const s = useStatementImport(0)
    await s.read(file, 'pw')
    expect(m.parse).toHaveBeenCalledWith({ file, password: 'pw' })
    expect(s.mapping.holding_accounts).toEqual({ 'ARN-1': 'Assets:Broker' })  // only distributors in this statement
    expect(s.mapping.cash_fallback_account).toBe('Equity:Funding')
    expect(s.mapping.dividend_account).toBe('')
    expect(m.preview).toHaveBeenCalledTimes(1)
    expect(s.plan.value?.preview_hash).toBe('h1')
  })

  it('re-plans when a choice or a correction changes and ignores a stale reply', async () => {
    m.parse.mockResolvedValue({ data: parseData() })
    const s = useStatementImport(0)
    await s.read(file, 'pw')
    let resolveSlow: (v: unknown) => void = () => {}
    m.preview
      .mockImplementationOnce(() => new Promise(r => { resolveSlow = r }))
      .mockResolvedValueOnce({ data: planData({ preview_hash: 'new' }) })
    s.mapping.cash_fallback_account = 'Equity:A'
    await nextTick(); await new Promise(r => setTimeout(r, 1))
    s.correct({ row_id: 'r1', exclude: true })
    await nextTick(); await new Promise(r => setTimeout(r, 1)); await flushPromises()
    resolveSlow({ data: planData({ preview_hash: 'stale' }) })
    await flushPromises()
    expect(s.plan.value?.preview_hash).toBe('new')
    expect(m.preview.mock.calls.at(-1)![0].corrections).toEqual([{ row_id: 'r1', exclude: true }])
  })

  it('commits what was previewed, with its hash, and clears the review', async () => {
    m.parse.mockResolvedValue({ data: parseData() })
    m.commit.mockResolvedValue({ data: { import_id: 'cas-1', transactions: 1, balances: 1, commodities_created: 1,
      commodities_updated: 0, prices: 1, removed_hand_entries: 0 } })
    const s = useStatementImport(0)
    await s.read(file, 'pw')
    expect(await s.commit()).toBe(true)
    expect(m.commit.mock.calls[0][0]).toMatchObject({ token: 'tok', expected_preview_hash: 'h1', corrections: [] })
    expect(s.result.value?.import_id).toBe('cas-1')
    expect(s.parsed.value).toBeNull()
    expect(m.list).toHaveBeenCalled()
  })

  it('shows a wrong password inline, not as a notification', async () => {
    m.parse.mockRejectedValue(apiError('CAS_WRONG_PASSWORD', 'Wrong password.'))
    const s = useStatementImport(0)
    await s.read(file, 'nope')
    expect(s.readError.value).toBe('Wrong password.')
    expect(m.display.mock.calls[0][1]).toMatchObject({ CAS_WRONG_PASSWORD: 'Inline' })
  })

  it('starts over when the parsed statement has expired', async () => {
    m.parse.mockResolvedValue({ data: parseData() })
    const s = useStatementImport(0)
    await s.read(file, 'pw')
    m.preview.mockRejectedValue(apiError('STATEMENT_EXPIRED', 'gone'))
    await s.preview()
    expect(s.parsed.value).toBeNull()
  })
})
