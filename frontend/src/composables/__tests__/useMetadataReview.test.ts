// Staged commodity edits → one batch save; account rows are not part of it.
// See dev-docs/investment-view.md §10.3 (Q5).

import { describe, it, expect, vi, beforeEach } from 'vitest'

const getMetadataReview = vi.fn()
const getMetadataVocabulary = vi.fn()
const updateCommodityMetadata = vi.fn()
vi.mock('@/services/generated-api', () => ({
  MetadataService: {
    getMetadataReview: () => getMetadataReview(),
    getMetadataVocabulary: () => getMetadataVocabulary(),
  },
  CommoditiesService: {
    updateCommodityMetadata: (body: unknown) => updateCommodityMetadata(body),
    listCommodities: () => Promise.resolve({ data: { commodities: [] } }),
  },
}))
vi.mock('@/utils/ErrorHandler', () => ({ errorHandler: { display: vi.fn() } }))

import { useMetadataReview } from '@/composables/useMetadataReview'

const vocabulary = {
  data: {
    currency_asset_classes: ['cash', 'currency'],
    fields: [
      { key: 'asset-class', scope: 'commodity', kind: 'enum', label: 'Asset class', adds: '', values: ['etf'], review: true },
      { key: 'name', scope: 'commodity', kind: 'text', label: 'Name', adds: '', review: false },
      { key: 'account-type', scope: 'account', kind: 'enum', label: 'Account type', adds: '', values: ['cd'], review: true },
    ],
  },
}
const reviewData = {
  data: {
    has_operating_currencies: false,
    summary: { commodity: 1, account: 1, 'income-account': 0 },
    items: [
      { scope: 'commodity', item: 'VOO', metadata: {}, unset: ['asset-class'], is_currency: true },
      { scope: 'commodity', item: 'VMFXX', metadata: { name: 'MMF', note: 'x' }, unset: [], is_currency: true },
      { scope: 'account', item: 'Assets:Bank', metadata: {}, unset: ['account-type'], closed: false },
    ],
  },
}

describe('useMetadataReview', () => {
  beforeEach(() => {
    getMetadataReview.mockReset().mockResolvedValue(reviewData)
    getMetadataVocabulary.mockReset().mockResolvedValue(vocabulary)
    updateCommodityMetadata.mockReset().mockResolvedValue({ data: { changed: [], commodities: [] } })
  })

  it('stages edits as set/remove changes for commodities only', async () => {
    const r = useMetadataReview()
    await r.load()
    const [voo, vmfxx, bank] = r.items.value
    r.setValue(voo, 'asset-class', 'etf')
    r.setValue(vmfxx, 'name', '')
    r.setValue(bank, 'account-type', 'cd')

    expect(r.valueOf(voo, 'asset-class')).toBe('etf')
    expect(r.isEdited(voo, 'asset-class')).toBe(true)
    expect(r.commodityChanges.value).toEqual([
      { code: 'VOO', set: { 'asset-class': 'etf' }, remove: [] },
      { code: 'VMFXX', set: {}, remove: ['name'] },
    ])
  })

  it('an edit back to the saved value is not a change', async () => {
    const r = useMetadataReview()
    await r.load()
    const vmfxx = r.items.value[1]
    r.setValue(vmfxx, 'name', 'MMF')
    expect(r.commodityChanges.value).toEqual([])
  })

  it('saves all commodity changes in one request, then clears them', async () => {
    const r = useMetadataReview()
    await r.load()
    r.setValue(r.items.value[0], 'asset-class', 'etf')
    r.setValue(r.items.value[1], 'name', 'Money market')

    expect(await r.saveCommodities()).toBe(true)
    expect(updateCommodityMetadata).toHaveBeenCalledTimes(1)
    expect(updateCommodityMetadata.mock.calls[0][0]).toEqual({
      changes: [
        { code: 'VOO', set: { 'asset-class': 'etf' }, remove: [] },
        { code: 'VMFXX', set: { name: 'Money market' }, remove: [] },
      ],
    })
    expect(r.commodityChanges.value).toEqual([])
  })

  it('keeps the edits when the save fails', async () => {
    updateCommodityMetadata.mockRejectedValue(new Error('422'))
    const r = useMetadataReview()
    await r.load()
    r.setValue(r.items.value[0], 'asset-class', 'etf')
    expect(await r.saveCommodities()).toBe(false)
    expect(r.commodityChanges.value).toHaveLength(1)
  })

  it('discard drops every staged edit', async () => {
    const r = useMetadataReview()
    await r.load()
    r.setValue(r.items.value[0], 'asset-class', 'etf')
    r.discard()
    expect(r.commodityChanges.value).toEqual([])
    expect(r.valueOf(r.items.value[0], 'asset-class')).toBe('')
  })
})
