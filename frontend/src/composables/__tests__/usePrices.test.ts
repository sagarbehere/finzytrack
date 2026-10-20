// Prices tab state: overview load, a fetch run kept for the inline result,
// edits reloading the overview, and history ignoring stale responses.
// See dev-docs/investment-view.md §10.2.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const getPriceOverview = vi.fn()
const getPriceHistory = vi.fn()
const updatePrices = vi.fn()
const editPrices = vi.fn()
const { ApiError } = vi.hoisted(() => ({
  ApiError: class ApiError extends Error {
    body: unknown
    constructor(body: unknown) { super('api'); this.body = body }
  },
}))
vi.mock('@/services/generated-api', () => ({
  ApiError,
  PricesService: {
    getPriceOverview: () => getPriceOverview(),
    getPriceHistory: (...args: unknown[]) => getPriceHistory(...args),
    updatePrices: () => updatePrices(),
    editPrices: (body: unknown) => editPrices(body),
  },
}))
const display = vi.fn()
vi.mock('@/utils/ErrorHandler', () => ({ errorHandler: { display: (e: unknown) => display(e) } }))

import { HISTORY_PAGE_SIZE, usePriceHistory, usePrices, useSaveAskingToReplace } from '@/composables/usePrices'

const row = { commodity: 'VOO', quote_currency: 'USD', cadence: 'daily', held: true, count: 1, out_of_date: false }

describe('usePrices', () => {
  beforeEach(() => {
    getPriceOverview.mockReset().mockResolvedValue({ data: { rows: [row], today: '2026-10-20' } })
    updatePrices.mockReset()
    editPrices.mockReset()
    display.mockReset()
  })

  it('loads the overview', async () => {
    const p = usePrices()
    await p.load()
    expect(p.rows.value).toEqual([row])
    expect(p.today.value).toBe('2026-10-20')
  })

  it('keeps the fetch result and reloads', async () => {
    const result = { added: 2, updated: 1, total: 10, symbols: ['VOO'], skipped: [], failed: [] }
    updatePrices.mockResolvedValue({ data: result })
    const p = usePrices()
    expect(await p.fetchPrices()).toBe(true)
    expect(p.lastRun.value?.result).toEqual(result)
    expect(getPriceOverview).toHaveBeenCalledTimes(1)
  })

  it('saves one batch and reloads; shows errors and reports failure', async () => {
    const p = usePrices()
    editPrices.mockResolvedValueOnce({ data: { added: 1, updated: 0, removed: 0, total: 3 } })
    const body = { set: [{ date: '2026-10-20', commodity: 'VOO', number: '1', quote_currency: 'USD' }] }
    expect(await p.save(body)).toEqual({ status: 'saved', data: { added: 1, updated: 0, removed: 0, total: 3 } })
    expect(editPrices).toHaveBeenCalledWith(body)
    expect(getPriceOverview).toHaveBeenCalledTimes(1)

    const err = new ApiError({ error: { code: 'PRICE_IN_LEDGER' } })
    editPrices.mockRejectedValueOnce(err)
    expect(await p.save(body)).toEqual({ status: 'failed' })
    expect(display).toHaveBeenCalledWith(err)
    expect(p.isSaving.value).toBe(false)
  })

  it('returns a taken date as `exists`, without an error message', async () => {
    const existing = [{ date: '2026-10-13', commodity: 'VOO', quote_currency: 'USD', number: '400.00' }]
    editPrices.mockRejectedValueOnce(new ApiError({ error: { code: 'PRICE_EXISTS', details: { prices: existing } } }))
    const p = usePrices()
    expect(await p.save({ set: [] })).toEqual({ status: 'exists', existing })
    expect(display).not.toHaveBeenCalled()
  })
})

describe('usePriceHistory', () => {
  beforeEach(() => getPriceHistory.mockReset())

  it('pages by HISTORY_PAGE_SIZE', async () => {
    getPriceHistory.mockResolvedValue({ data: { prices: [], total: 120 } })
    const h = usePriceHistory()
    await h.load('VOO', 'USD', 2)
    expect(getPriceHistory).toHaveBeenCalledWith('VOO', 'USD', HISTORY_PAGE_SIZE, 2 * HISTORY_PAGE_SIZE)
    expect([h.total.value, h.page.value]).toEqual([120, 2])
  })

  it('drops a slow response for a pair that is no longer selected', async () => {
    let resolveFirst: (v: unknown) => void = () => {}
    getPriceHistory
      .mockReturnValueOnce(new Promise(r => { resolveFirst = r }))
      .mockResolvedValueOnce({ data: { prices: [{ date: '2026-10-20', number: '9', quote_currency: 'EUR', source: 'sidecar' }], total: 1 } })
    const h = usePriceHistory()
    const first = h.load('VOO', 'USD')
    await h.load('VOO', 'EUR')
    resolveFirst({ data: { prices: [{ date: '2026-01-01', number: '1', quote_currency: 'USD', source: 'sidecar' }], total: 1 } })
    await first
    expect(h.prices.value.map(p => p.quote_currency)).toEqual(['EUR'])
  })
})

describe('useSaveAskingToReplace', () => {
  const request = {
    set: [{ date: '2026-10-13', commodity: 'VOO', number: '380', quote_currency: 'USD', replace: false }],
  }
  const exists = { status: 'exists' as const, existing: [{ date: '2026-10-13', number: '400.00', quote_currency: 'USD' }] }
  const saved = { status: 'saved' as const, data: { added: 0, updated: 1, removed: 0, total: 3 } }

  it('asks, then resends allowing the replacement', async () => {
    const save = vi.fn().mockResolvedValueOnce(exists).mockResolvedValueOnce(saved)
    const { confirm, saveAsking } = useSaveAskingToReplace(save, () => '2026-10-20')
    const result = saveAsking(request)
    await vi.waitFor(() => expect(confirm.isOpen.value).toBe(true))
    expect(confirm.dialogOptions.value.message).toBe('Oct 13 already has a price of 400.00 USD.\nSaving replaces it.')
    confirm.handleConfirm()
    expect(await result).toEqual({ replaced: true })
    expect(save).toHaveBeenLastCalledWith({ set: [{ ...request.set[0], replace: true }] })
  })

  it('saves nothing more when the user cancels', async () => {
    const save = vi.fn().mockResolvedValueOnce(exists)
    const { confirm, saveAsking } = useSaveAskingToReplace(save, () => '2026-10-20')
    const result = saveAsking(request)
    await vi.waitFor(() => expect(confirm.isOpen.value).toBe(true))
    confirm.handleCancel()
    expect(await result).toBeNull()
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('does not ask when the date was free', async () => {
    const save = vi.fn().mockResolvedValueOnce(saved)
    const { confirm, saveAsking } = useSaveAskingToReplace(save, () => '2026-10-20')
    expect(await saveAsking(request)).toEqual({ replaced: false })
    expect(confirm.isOpen.value).toBe(false)
  })
})
