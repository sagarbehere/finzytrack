<template>
  <div class="flex items-center border-b border-gray-200 dark:border-white/10 overflow-x-auto">
    <!-- Tabs -->
    <!-- Tabs reorder by drag and drop, or Alt+←/→ on the focused tab. -->
    <div
      v-for="(tab, index) in tabs"
      :key="tab.id"
      :data-tab-id="tab.id"
      role="tab"
      tabindex="0"
      draggable="true"
      @click="emit('select', tab.id)"
      @keydown.enter="emit('select', tab.id)"
      @keydown.alt.left.prevent="moveByKey(tab.id, index - 1)"
      @keydown.alt.right.prevent="moveByKey(tab.id, index + 1)"
      @dragstart="onDragStart($event, tab.id)"
      @dragover="onDragOver($event, index)"
      @drop.prevent="onDrop"
      @dragend="resetDrag"
      class="group relative flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-medium cursor-pointer whitespace-nowrap"
      :class="[
        tab.id === activeTabId
          ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
          : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:text-gray-400 dark:hover:border-white/20 dark:hover:text-gray-200',
        tab.id === draggedId ? 'opacity-50' : '',
      ]"
    >
      <!-- Where the dragged tab will land: before this tab, or after the last one -->
      <span v-if="showDropMarker && dropSlot === index" class="absolute inset-y-2 left-0 w-0.5 rounded-full bg-indigo-500 dark:bg-indigo-400" />
      <span v-if="showDropMarker && dropSlot === tabs.length && index === tabs.length - 1" class="absolute inset-y-2 right-0 w-0.5 rounded-full bg-indigo-500 dark:bg-indigo-400" />
      <span class="truncate max-w-[200px]">{{ tab.title }}</span>
      <button
        @click.stop="emit('remove', tab.id)"
        class="flex-shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-all"
        :class="{ 'opacity-100': tab.id === activeTabId }"
        title="Close tab"
      >
        <XMarkIcon class="h-4 w-4" />
      </button>
    </div>

    <!-- Add button -->
    <button
      @click="emit('add')"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors"
      title="Add dashboard"
    >
      <PlusIcon class="h-5 w-5" />
    </button>

    <!-- Spacer -->
    <div class="flex-1" />

    <!-- Reload recipes button -->
    <button
      @click="emit('reload')"
      :disabled="reloading"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      title="Reload recipes"
    >
      <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': reloading }" />
    </button>

    <!-- Manage recipes (gear icon) -->
    <button
      @click="router.push({ path: '/settings', query: { tab: 'dashboards' } })"
      class="flex items-center justify-center p-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors"
      title="Manage recipes"
    >
      <Cog6ToothIcon class="h-4 w-4" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { PlusIcon, XMarkIcon, ArrowPathIcon } from '@heroicons/vue/20/solid'
import { Cog6ToothIcon } from '@heroicons/vue/24/outline'
import type { DashboardTab } from '@/composables/useDashboardTabs'

const router = useRouter()

interface Props {
  tabs: readonly DashboardTab[]
  activeTabId: string | null
  reloading?: boolean
}

const props = withDefaults(defineProps<Props>(), { reloading: false })

const emit = defineEmits<{
  (e: 'select', dashboardId: string): void
  (e: 'remove', dashboardId: string): void
  /** Move a tab to `toIndex` in the tab order. */
  (e: 'move', dashboardId: string, toIndex: number): void
  (e: 'add'): void
  (e: 'reload'): void
}>()

// ── Reordering ──
// While dragging, `dropSlot` is the gap the tab would land in: 0 = before the
// first tab, tabs.length = after the last.
const draggedId = ref<string | null>(null)
const dropSlot = ref<number | null>(null)
const fromIndex = computed(() => props.tabs.findIndex((t) => t.id === draggedId.value))
// Dropping into the gap on either side of the dragged tab changes nothing.
const showDropMarker = computed(
  () => dropSlot.value !== null && dropSlot.value !== fromIndex.value && dropSlot.value !== fromIndex.value + 1,
)

function onDragStart(e: DragEvent, id: string) {
  draggedId.value = id
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', id) // Firefox won't start a drag without data
  }
}

function onDragOver(e: DragEvent, index: number) {
  if (draggedId.value === null) return // something else is being dragged over the tabs
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  // The tab's left half drops before it, the right half after it.
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  dropSlot.value = e.clientX < rect.left + rect.width / 2 ? index : index + 1
}

function onDrop() {
  if (draggedId.value !== null && showDropMarker.value) {
    // A gap after the tab's old place is one less once the tab is lifted out.
    const slot = dropSlot.value!
    emit('move', draggedId.value, slot > fromIndex.value ? slot - 1 : slot)
  }
  resetDrag()
}

function resetDrag() {
  draggedId.value = null
  dropSlot.value = null
}

async function moveByKey(id: string, toIndex: number) {
  if (toIndex < 0 || toIndex >= props.tabs.length) return
  emit('move', id, toIndex)
  // The tab element is re-ordered, not re-created; keep focus on it.
  await nextTick()
  Array.from(document.querySelectorAll<HTMLElement>('[data-tab-id]'))
    .find((el) => el.dataset.tabId === id)
    ?.focus()
}
</script>
