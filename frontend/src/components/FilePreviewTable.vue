<template>
  <div :class="fill
    ? 'flex flex-col flex-1 min-h-0'
    : 'rounded-lg border border-gray-200 dark:border-white/10 overflow-hidden'
  ">

    <!-- Sheet tabs (XLS with multiple sheets) -->
    <div
      v-if="sheets.length > 1"
      class="flex-none flex border-b border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-gray-800/50 overflow-x-auto"
    >
      <button
        v-for="(sheet, i) in sheets"
        :key="i"
        class="px-3 py-1.5 text-xs whitespace-nowrap border-r border-gray-200 dark:border-white/10 last:border-r-0 transition-colors"
        :class="activeSheet === i
          ? 'bg-white dark:bg-gray-800 font-medium text-gray-900 dark:text-gray-100'
          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'"
        @click="activeSheet = i"
      >{{ sheet.name }}</button>
    </div>

    <!-- Scrollable table -->
    <div :class="fill ? 'flex-1 min-h-0 overflow-auto' : 'overflow-auto max-h-80'">
      <table class="border-separate border-spacing-0 min-w-full text-xs">
        <thead>
          <tr>
            <!-- Left gutter header (row number, forward) -->
            <th class="sticky top-0 left-0 z-20 bg-gray-50 dark:bg-gray-800/75 border-b border-r border-gray-200 dark:border-white/10 px-2 py-1 min-w-[2.5rem] text-center text-gray-400 dark:text-gray-500 font-normal select-none">
              #
            </th>
            <!-- Column index headers -->
            <th
              v-for="ci in colIndices"
              :key="ci"
              class="sticky top-0 z-10 bg-gray-50 dark:bg-gray-800/75 border-b border-r border-gray-200 dark:border-white/10 px-2 py-1 font-semibold text-center text-gray-500 dark:text-gray-400 whitespace-nowrap"
            >{{ ci + 1 }}</th>
            <!-- Right gutter header (row number, reverse) -->
            <th class="sticky top-0 right-0 z-20 bg-gray-50 dark:bg-gray-800/75 border-b border-l border-gray-200 dark:border-white/10 px-2 py-1 min-w-[2.5rem] text-center text-orange-300 dark:text-orange-700 font-normal select-none">
              ↑
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, ri) in displayRows" :key="ri">
            <!-- Forward row number (left) -->
            <td class="sticky left-0 z-10 bg-white dark:bg-gray-800 border-b border-r border-gray-200 dark:border-white/10 px-2 py-px text-right text-gray-300 dark:text-gray-600 select-none font-mono min-w-[2.5rem]">
              {{ ri + 1 }}
            </td>
            <!-- Cells -->
            <td
              v-for="ci in colIndices"
              :key="ci"
              class="border-b border-r border-gray-100 dark:border-white/10/60 px-2 py-px"
            >
              <div class="font-mono text-gray-700 dark:text-gray-300 max-w-[14rem] truncate">{{ row[ci] ?? '' }}</div>
            </td>
            <!-- Reverse row number (right) -->
            <td class="sticky right-0 z-10 bg-white dark:bg-gray-800 border-b border-l border-gray-200 dark:border-white/10 px-2 py-px text-right text-orange-300 dark:text-orange-700 select-none font-mono min-w-[2.5rem]">
              {{ totalRows - ri }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Truncation notice -->
    <div
      v-if="truncatedCount > 0"
      class="flex-none px-3 py-1.5 border-t border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-gray-800/50 text-xs text-gray-400 dark:text-gray-500 italic"
    >… {{ truncatedCount }} more rows not shown</div>

  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const MAX_ROWS = 500

interface FileSheet {
  name: string
  rows: string[][]
}

const props = defineProps<{
  sheets: FileSheet[]
  fill?: boolean   // true = fills parent height (sidebar), false = max-h-80 (inline)
}>()

const activeSheet = ref(0)

const currentRows = computed(() => props.sheets[activeSheet.value]?.rows ?? [])

// Find the rightmost non-empty column across all rows to trim table width
const colCount = computed(() => {
  let max = 0
  for (const row of currentRows.value) {
    let last = row.length - 1
    while (last >= 0 && !String(row[last] ?? '').trim()) last--
    max = Math.max(max, last + 1)
  }
  return max
})

// 0-based for array access; displayed as 1-based in the header (matching user expectations)
const colIndices = computed(() => Array.from({ length: colCount.value }, (_, i) => i))

const totalRows = computed(() => currentRows.value.length)
const displayRows = computed(() => currentRows.value.slice(0, MAX_ROWS))
const truncatedCount = computed(() => Math.max(0, totalRows.value - MAX_ROWS))
</script>
