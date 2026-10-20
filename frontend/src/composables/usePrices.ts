/**
 * Investments → Prices: the price overview, a run of the price fetcher, one
 * pair's history, and price edits. Function-scoped — the state belongs to the
 * tab. See dev-docs/investment-view.md §10.2.
 */
import { ref } from 'vue'
import {
  ApiError,
  PricesService,
  type PriceEditData,
  type PriceEditRequest,
  type PriceOverviewRow,
  type PricePoint,
  type PriceUpdateData,
} from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { formatPrice, formatShortDate, withReplace } from '@/utils/prices'

export const HISTORY_PAGE_SIZE = 50

export interface FetchRun {
  at: Date
  result: PriceUpdateData
}

/** A price already on a date that a save asked not to replace. */
export interface ExistingPrice {
  date: string
  number: string
  quote_currency: string
}

export type SaveOutcome =
  | { status: 'saved'; data: PriceEditData }
  | { status: 'exists'; existing: ExistingPrice[] }
  | { status: 'failed' }

// Price edit errors the user can act on in place: a toast, not a modal.
const EDIT_ERROR_DISPLAY = { PRICE_IN_LEDGER: 'Toast', PRICE_NOT_FOUND: 'Toast' } as const

function existingFrom(err: unknown): ExistingPrice[] | null {
  if (!(err instanceof ApiError) || err.body?.error?.code !== 'PRICE_EXISTS') return null
  return (err.body.error.details?.prices ?? []) as ExistingPrice[]
}

export function usePrices() {
  const rows = ref<PriceOverviewRow[]>([])
  const today = ref('')
  const isLoading = ref(false)
  const isFetching = ref(false)
  const isSaving = ref(false)
  const error = ref<string | null>(null)
  const lastRun = ref<FetchRun | null>(null)

  async function load(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const resp = await PricesService.getPriceOverview()
      rows.value = resp.data?.rows ?? []
      today.value = resp.data?.today ?? ''
    } catch (err) {
      error.value = 'Could not load prices.'
      errorHandler.display(err)
    } finally {
      isLoading.value = false
    }
  }

  /** Run the fetcher (same endpoint as the dashboards' button), keep its
   * result for the inline summary, then reload the overview. */
  async function fetchPrices(): Promise<boolean> {
    if (isFetching.value) return false
    isFetching.value = true
    try {
      const resp = await PricesService.updatePrices()
      if (resp.data) lastRun.value = { at: new Date(), result: resp.data }
      await load()
      return true
    } catch (err) {
      errorHandler.display(err)
      return false
    } finally {
      isFetching.value = false
    }
  }

  /** Save one batch of edits (one write), then reload the overview. A set
   * that may not replace a taken date comes back as `exists` (not an error). */
  async function save(request: PriceEditRequest): Promise<SaveOutcome> {
    isSaving.value = true
    try {
      const resp = await PricesService.editPrices(request)
      await load()
      return resp.data ? { status: 'saved', data: resp.data } : { status: 'failed' }
    } catch (err) {
      const existing = existingFrom(err)
      if (existing) return { status: 'exists', existing }
      errorHandler.display(err, EDIT_ERROR_DISPLAY)
      return { status: 'failed' }
    } finally {
      isSaving.value = false
    }
  }

  return { rows, today, isLoading, isFetching, isSaving, error, lastRun, load, fetchPrices, save }
}

/** One (commodity, quote) pair's prices, newest first, a page at a time. */
export function usePriceHistory() {
  const prices = ref<PricePoint[]>([])
  const total = ref(0)
  const page = ref(0)
  const isLoading = ref(false)
  // Guards against a slow response for a pair that is no longer selected.
  let requestId = 0

  async function load(commodity: string, quote: string, toPage = 0): Promise<void> {
    const id = ++requestId
    isLoading.value = true
    try {
      const resp = await PricesService.getPriceHistory(
        commodity, quote, HISTORY_PAGE_SIZE, toPage * HISTORY_PAGE_SIZE,
      )
      if (id !== requestId) return
      prices.value = resp.data?.prices ?? []
      total.value = resp.data?.total ?? 0
      page.value = toPage
    } catch (err) {
      if (id === requestId) errorHandler.display(err)
    } finally {
      if (id === requestId) isLoading.value = false
    }
  }

  function clear(): void {
    requestId++
    prices.value = []
    total.value = 0
    page.value = 0
    isLoading.value = false
  }

  return { prices, total, page, isLoading, load, clear }
}

/**
 * Save a batch, and when it would replace a price already on a date, ask first
 * and resend allowing the replacement. Resolves to whether a price was
 * replaced, or `null` if nothing was saved. Render `confirm` with ConfirmDialog
 * inside the calling component (so it stacks above that dialog).
 */
export function useSaveAskingToReplace(
  save: (request: PriceEditRequest) => Promise<SaveOutcome>,
  today: () => string,
) {
  const confirm = useConfirmDialog()

  async function saveAsking(request: PriceEditRequest): Promise<{ replaced: boolean } | null> {
    const first = await save(request)
    if (first.status === 'saved') return { replaced: false }
    if (first.status === 'failed') return null
    const lines = first.existing.map(
      e => `${formatShortDate(e.date, today())} already has a price of ${formatPrice(e.number, e.quote_currency)}.`,
    )
    const ok = await confirm.showConfirm({
      title: 'Replace price?',
      message: `${lines.join('\n')}\nSaving replaces it.`,
      confirmText: 'Replace',
      cancelText: 'Cancel',
      variant: 'warning',
    })
    if (!ok) return null
    const second = await save(withReplace(request))
    return second.status === 'saved' ? { replaced: true } : null
  }

  return { confirm, saveAsking }
}
