<template>
  <div class="overflow-auto h-full">
    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
      <thead class="bg-gray-50 dark:bg-gray-800 sticky top-0">
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            scope="col"
            :aria-sort="sort?.key === column.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined"
            class="px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400"
            :class="{
              'text-left': column.align !== 'center' && column.align !== 'right',
              'text-center': column.align === 'center',
              'text-right': column.align === 'right',
            }"
          >
            <!-- Click to sort: ascending, then descending, then the query's order -->
            <button
              type="button"
              class="group inline-flex items-center uppercase tracking-wider"
              :title="`Sort by ${column.label}`"
              @click="toggleSort(column.key)"
            >
              {{ column.label }}
              <span
                :class="sort?.key === column.key
                  ? 'ml-1 flex-none rounded-sm bg-gray-100 text-gray-900 group-hover:bg-gray-200 dark:bg-gray-800 dark:text-white dark:group-hover:bg-gray-700'
                  : 'invisible ml-1 flex-none rounded-sm text-gray-400 group-hover:visible group-focus-visible:visible dark:text-gray-500'"
              >
                <ChevronUpIcon v-if="sort?.key === column.key && sort.dir === 'asc'" class="size-4" aria-hidden="true" />
                <ChevronDownIcon v-else class="size-4" aria-hidden="true" />
              </span>
            </button>
          </th>
        </tr>
      </thead>
      <tbody class="bg-white divide-y divide-gray-200 dark:bg-gray-900 dark:divide-gray-700">
        <tr
          v-for="{ row, index: rowIndex } in sortedRows"
          :key="rowIndex"
          :class="isRowActive(row, rowIndex)
            ? 'bg-indigo-50 dark:bg-indigo-500/10'
            : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'"
        >
          <td
            v-for="column in columns"
            :key="column.key"
            class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100"
            :class="{
              'text-left': column.align !== 'center' && column.align !== 'right',
              'text-center': column.align === 'center',
              'text-right': column.align === 'right',
            }"
          >
            <!-- Clickable link if available -->
            <router-link
              v-if="getCellLink(row, rowIndex, column)"
              :to="getCellLink(row, rowIndex, column)!"
              class="text-indigo-600 hover:text-indigo-800 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
            >
              {{ formatCell(row, column) }}
            </router-link>
            <!-- Select action (drives another widget) otherwise -->
            <button
              v-else-if="getCellSelect(row, rowIndex, column)"
              type="button"
              class="text-indigo-600 hover:text-indigo-800 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
              @click="emit('select', getCellSelect(row, rowIndex, column)!)"
            >
              {{ formatCell(row, column) }}
            </button>
            <!-- Plain value otherwise -->
            <span v-else>
              {{ formatCell(row, column) }}
            </span>
          </td>
        </tr>
        <tr v-if="data.length === 0">
          <td
            :colspan="columns.length"
            class="px-4 py-8 text-center text-gray-500 dark:text-gray-400"
          >
            {{ emptyText || 'No data available' }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { ChevronDownIcon, ChevronUpIcon } from '@heroicons/vue/20/solid'
import type { TableColumn, TableLinkContext, SelectParams } from '@/types/recipes'
import { sortTableRows, type TableSort } from '@/utils/tableSort'

interface Props {
  data: Record<string, unknown>[]
  columns: TableColumn[]
  emptyText?: string
  /** Current dashboard-param values a select would reproduce — the row whose
   *  select matches is highlighted as the active (drilled-in) row (master-detail). */
  activeParams?: SelectParams | null
  /** Keeps this table's sort across re-mounts (a widget reloading on a parameter
   *  change) for the session. Tables without one start unsorted each mount. */
  sortStateKey?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ select: [params: SelectParams] }>()

// ── Sorting ──
// Rows keep their index in `data` as `rowIndex`, so links, select actions and the
// active-row highlight refer to the same row however the table is sorted.
const sort = ref<TableSort | null>(props.sortStateKey ? sessionSorts.get(props.sortStateKey) ?? null : null)
watch(sort, (s) => {
  if (!props.sortStateKey) return
  if (s) sessionSorts.set(props.sortStateKey, s)
  else sessionSorts.delete(props.sortStateKey)
})

function toggleSort(key: string) {
  const s = sort.value
  if (s?.key !== key) sort.value = { key, dir: 'asc' }
  else if (s.dir === 'asc') sort.value = { key, dir: 'desc' }
  else sort.value = null
}

const sortedRows = computed(() =>
  sortTableRows(props.data, sort.value, props.columns.find((c) => c.key === sort.value?.key)?.currencyField),
)

/** A row is "active" when its select action would reproduce the current selection
 *  (e.g. the holdings row whose {holding} equals the dashboard's holding param). */
function isRowActive(row: Record<string, unknown>, rowIndex: number): boolean {
  const active = props.activeParams
  if (!active || Object.keys(active).length === 0) return false
  for (const column of props.columns) {
    if (!column.getSelect) continue
    const sel = column.getSelect({ row, rowIndex, column, value: row[column.key] })
    if (sel && Object.keys(active).every((k) => k in sel && String(sel[k]) === String(active[k]))) {
      return true
    }
  }
  return false
}

function formatCell(row: Record<string, unknown>, column: TableColumn): string {
  const value = row[column.key]
  if (column.format) {
    return column.format(value, row)
  }
  if (value === null || value === undefined) {
    return '—'
  }
  return String(value)
}

/**
 * Get link for a cell value.
 * Uses column.getLink function if available.
 */
function getCellLink(
  row: Record<string, unknown>,
  rowIndex: number,
  column: TableColumn
): RouteLocationRaw | null {
  if (!column.getLink) return null

  const value = row[column.key]
  const context: TableLinkContext = {
    row,
    rowIndex,
    column,
    value,
  }

  const link = column.getLink(context)
  if (link) {
    return { name: link.name, query: link.query }
  }

  return null
}

/** Resolve a cell's "select" action (dashboard params to set on click), if any. */
function getCellSelect(
  row: Record<string, unknown>,
  rowIndex: number,
  column: TableColumn,
): SelectParams | null {
  if (!column.getSelect) return null
  return column.getSelect({ row, rowIndex, column, value: row[column.key] }) ?? null
}
</script>

<script lang="ts">
import type { TableSort as SessionTableSort } from '@/utils/tableSort'
/** Per-table sort, by `sortStateKey`, for the session (not saved across reloads). */
const sessionSorts = new Map<string, SessionTableSort>()
</script>
