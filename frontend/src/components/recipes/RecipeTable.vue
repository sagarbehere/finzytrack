<template>
  <div class="overflow-auto h-full">
    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
      <thead class="bg-gray-50 dark:bg-gray-800 sticky top-0">
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            scope="col"
            class="px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400"
            :class="{
              'text-left': column.align !== 'center' && column.align !== 'right',
              'text-center': column.align === 'center',
              'text-right': column.align === 'right',
            }"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody class="bg-white divide-y divide-gray-200 dark:bg-gray-900 dark:divide-gray-700">
        <tr
          v-for="(row, rowIndex) in data"
          :key="rowIndex"
          class="hover:bg-gray-50 dark:hover:bg-gray-800/50"
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
              class="text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
            >
              {{ formatCell(row, column) }}
            </router-link>
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
            No data available
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { TableColumn, TableLinkContext } from '@/types/recipes'

interface Props {
  data: Record<string, unknown>[]
  columns: TableColumn[]
}

defineProps<Props>()

function formatCell(row: Record<string, unknown>, column: TableColumn): string {
  const value = row[column.key]
  if (column.format) {
    return column.format(value)
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
</script>
