<template>
  <div
    v-if="visible"
    class="rounded-md bg-blue-50 p-4 dark:bg-blue-500/10 dark:outline dark:outline-blue-500/20"
  >
    <div class="flex">
      <div class="shrink-0">
        <InformationCircleIcon class="size-5 text-blue-400" aria-hidden="true" />
      </div>
      <div class="ml-3 flex-1 md:flex md:justify-between">
        <p class="text-sm text-blue-700 dark:text-blue-300">{{ hint.text }}</p>
        <p class="mt-3 text-sm md:mt-0 md:ml-6">
          <RouterLink
            :to="{ path: '/investments', query: { tab: 'metadata' } }"
            class="font-medium whitespace-nowrap text-blue-700 hover:text-blue-600 dark:text-blue-300 dark:hover:text-blue-200"
          >
            Add details
            <span aria-hidden="true"> &rarr;</span>
          </RouterLink>
        </p>
      </div>
      <div class="ml-auto pl-3">
        <div class="-mx-1.5 -my-1.5">
          <button
            type="button"
            class="inline-flex rounded-md bg-blue-50 p-1.5 text-blue-500 hover:bg-blue-100 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 focus-visible:ring-offset-blue-50 focus-visible:outline-hidden dark:bg-transparent dark:text-blue-400 dark:hover:bg-blue-500/10 dark:focus-visible:ring-blue-500 dark:focus-visible:ring-offset-1 dark:focus-visible:ring-offset-blue-900"
            @click="dismiss"
          >
            <span class="sr-only">Dismiss</span>
            <XMarkIcon class="size-5" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * A dashboard's optional `metadataHint`: a dismissible offer to add metadata,
 * shown only when the metadata review has unset items in the hint's scope. It
 * stays dismissed per dashboard in this browser (localStorage). Never a gate —
 * the dashboard is complete without it. See dev-docs/investment-view.md §10.3 (Q8).
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { InformationCircleIcon, XMarkIcon } from '@heroicons/vue/20/solid'
import { MetadataService } from '@/services/generated-api'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import type { JsonDashboardRecipe } from '@/types/recipes'

const props = defineProps<{
  dashboardId: string
  hint: NonNullable<JsonDashboardRecipe['metadataHint']>
}>()

const visible = ref(false)

function dismissedIds(): string[] {
  return getStorageAdapter().get<string[]>(STORAGE_KEYS.DISMISSED_METADATA_HINTS) ?? []
}

onMounted(async () => {
  if (dismissedIds().includes(props.dashboardId)) return
  try {
    const review = await MetadataService.getMetadataReview()
    visible.value = (review.data?.summary?.[props.hint.scope] ?? 0) > 0
  } catch {
    // An optional offer: if the review can't load, simply don't show it.
  }
})

function dismiss() {
  visible.value = false
  const ids = dismissedIds()
  if (!ids.includes(props.dashboardId)) {
    getStorageAdapter().set(STORAGE_KEYS.DISMISSED_METADATA_HINTS, [...ids, props.dashboardId])
  }
}
</script>
