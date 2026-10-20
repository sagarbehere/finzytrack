/**
 * Investments → Setup & Metadata: the recognized vocabulary, the review items,
 * and the edits waiting to be saved. Function-scoped — the edits belong to one
 * view. See dev-docs/investment-view.md §10.3.
 */
import { computed, reactive, ref } from 'vue'
import {
  CommoditiesService,
  MetadataService,
  type CommodityMetadataChange,
  type MetadataReviewItem,
} from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { useCommodities } from '@/composables/useCommodities'
import { useMetadataVocabulary } from '@/composables/useMetadataVocabulary'
import { diffMetadata, fieldsFor, type MetadataScope } from '@/utils/metadataVocabulary'

export function itemKey(item: Pick<MetadataReviewItem, 'scope' | 'item'>): string {
  return `${item.scope}|${item.item}`
}

export function useMetadataReview() {
  const { fields, currencyAssetClasses, fetchVocabulary } = useMetadataVocabulary()
  const items = ref<MetadataReviewItem[]>([])
  const summary = ref<Record<string, number>>({})
  const hasOperatingCurrencies = ref(true)
  const isLoading = ref(false)
  const isSaving = ref(false)
  const error = ref<string | null>(null)

  // Edited values by item key → metadata key → value ('' = cleared).
  const edits = reactive<Record<string, Record<string, string>>>({})

  const { fetchCommodities } = useCommodities()

  async function load(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const [, review] = await Promise.all([
        fetchVocabulary(),
        MetadataService.getMetadataReview(),
      ])
      items.value = review.data?.items ?? []
      summary.value = review.data?.summary ?? {}
      hasOperatingCurrencies.value = review.data?.has_operating_currencies ?? true
    } catch (err) {
      error.value = 'Could not load metadata.'
      errorHandler.display(err)
    } finally {
      isLoading.value = false
    }
  }

  function itemsFor(scope: MetadataScope): MetadataReviewItem[] {
    return items.value.filter(i => i.scope === scope)
  }

  /** The value shown for a cell: the pending edit if any, else the saved value. */
  function valueOf(item: MetadataReviewItem, key: string): string {
    const pending = edits[itemKey(item)]
    if (pending && key in pending) return pending[key]
    return item.metadata?.[key] ?? ''
  }

  /** The item's metadata with pending edits applied (drives which fields apply). */
  function effectiveMetadata(item: MetadataReviewItem): Record<string, string> {
    const out: Record<string, string> = { ...(item.metadata ?? {}) }
    for (const [k, v] of Object.entries(edits[itemKey(item)] ?? {})) {
      if (v.trim() === '') delete out[k]
      else out[k] = v
    }
    return out
  }

  function setValue(item: MetadataReviewItem, key: string, value: string): void {
    const k = itemKey(item)
    if (!edits[k]) edits[k] = {}
    edits[k][key] = value
  }

  function changeFor(item: MetadataReviewItem) {
    const keys = fieldsFor(fields.value, item.scope as MetadataScope).map(f => f.key)
    return diffMetadata(item.metadata ?? {}, edits[itemKey(item)] ?? {}, keys)
  }

  function isEdited(item: MetadataReviewItem, key: string): boolean {
    const change = changeFor(item)
    return !!change && (key in change.set || change.remove.includes(key))
  }

  const commodityChanges = computed<CommodityMetadataChange[]>(() =>
    itemsFor('commodity').flatMap(item => {
      const change = changeFor(item)
      return change ? [{ code: item.item, set: change.set, remove: change.remove }] : []
    }),
  )

  function discard(): void {
    for (const k of Object.keys(edits)) delete edits[k]
  }

  /** Save all commodity edits as one batch (one ledger write). Returns true on success. */
  async function saveCommodities(): Promise<boolean> {
    if (commodityChanges.value.length === 0) return true
    isSaving.value = true
    try {
      await CommoditiesService.updateCommodityMetadata({ changes: commodityChanges.value })
      for (const item of itemsFor('commodity')) delete edits[itemKey(item)]
      await Promise.all([load(), fetchCommodities(true)])
      return true
    } catch (err) {
      errorHandler.display(err)
      return false
    } finally {
      isSaving.value = false
    }
  }

  return {
    fields,
    currencyAssetClasses,
    items,
    summary,
    hasOperatingCurrencies,
    isLoading,
    isSaving,
    error,
    load,
    itemsFor,
    valueOf,
    effectiveMetadata,
    setValue,
    isEdited,
    commodityChanges,
    discard,
    saveCommodities,
  }
}
