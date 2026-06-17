<template>
  <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
    <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
      <h3 class="text-lg font-medium text-gray-900 dark:text-white">Documents</h3>
      <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
        Find files in your documents folder that are no longer referenced by any transaction or
        account, and remove them after review.
      </p>
    </div>

    <div class="p-6">
      <button
        type="button"
        @click="scan"
        :disabled="scanning"
        class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
      >
        {{ scanning ? 'Scanning…' : 'Scan for orphaned documents' }}
      </button>
    </div>

    <OrphanSweepModal
      :open="modalOpen"
      :orphans="orphans"
      :deleting="deleting"
      @update:open="modalOpen = $event"
      @confirm="onConfirm"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import OrphanSweepModal from '@/components/documents/OrphanSweepModal.vue'
import { useDocuments } from '@/composables/useDocuments'
import { useToast } from '@/composables/useNotifications'
import type { OrphanCandidateData } from '@/services/generated-api'

const { scanOrphans, deleteOrphans } = useDocuments()
const toast = useToast()

const scanning = ref(false)
const deleting = ref(false)
const modalOpen = ref(false)
const orphans = ref<OrphanCandidateData[]>([])

async function scan() {
  scanning.value = true
  try {
    const result = await scanOrphans()
    orphans.value = result.orphans
    modalOpen.value = true
  } catch {
    // useDocuments already surfaced the error.
  } finally {
    scanning.value = false
  }
}

async function onConfirm(paths: string[]) {
  deleting.value = true
  try {
    const result = await deleteOrphans(paths)
    const skippedNote = result.skipped.length > 0
      ? ` ${result.skipped.length} skipped (became referenced or could not be removed).`
      : ''
    toast.success('Documents Swept', `Deleted ${result.deleted.length} file(s).${skippedNote}`)
    // Re-scan so the list reflects the current state (skipped files remain).
    const rescan = await scanOrphans()
    orphans.value = rescan.orphans
    if (orphans.value.length === 0) modalOpen.value = false
  } catch {
    // surfaced by useDocuments
  } finally {
    deleting.value = false
  }
}
</script>
