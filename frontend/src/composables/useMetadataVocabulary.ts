/**
 * The recognized metadata vocabulary (GET /api/metadata/vocabulary), fetched once
 * and shared — it is static for the life of the backend. Module-level singleton.
 * See dev-docs/investment-view.md §10.3.
 */
import { ref } from 'vue'
import { MetadataService, type MetadataFieldSpec } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

const fields = ref<MetadataFieldSpec[]>([])
const currencyAssetClasses = ref<string[]>([])
let activePromise: Promise<void> | null = null

async function fetchVocabulary(): Promise<void> {
  if (fields.value.length > 0) return
  if (activePromise) return activePromise
  activePromise = (async () => {
    try {
      const response = await MetadataService.getMetadataVocabulary()
      fields.value = response.data?.fields ?? []
      currencyAssetClasses.value = response.data?.currency_asset_classes ?? []
    } catch (err) {
      errorHandler.display(err)
    } finally {
      activePromise = null
    }
  })()
  return activePromise
}

export function useMetadataVocabulary() {
  return { fields, currencyAssetClasses, fetchVocabulary }
}
