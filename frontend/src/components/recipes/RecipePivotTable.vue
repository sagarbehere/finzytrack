<template>
  <div class="overflow-auto h-full">
    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
      <thead class="bg-gray-50 dark:bg-gray-800 sticky top-0 z-10">
        <tr>
          <!-- Row header column -->
          <th
            scope="col"
            class="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 sticky left-0 bg-gray-50 dark:bg-gray-800 z-20"
          >
            {{ rowHeader }}
          </th>
          <!-- Data columns -->
          <th
            v-for="col in data.columns"
            :key="col"
            scope="col"
            class="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider dark:text-gray-400 whitespace-nowrap"
          >
            {{ col }}
          </th>
          <!-- Row totals column -->
          <th
            v-if="showRowTotals"
            scope="col"
            class="px-3 py-2 text-right text-xs font-semibold text-gray-700 uppercase tracking-wider dark:text-gray-300 bg-gray-100 dark:bg-gray-800/75"
          >
            Row totals
          </th>
        </tr>
      </thead>
      <tbody class="bg-white divide-y divide-gray-200 dark:bg-gray-900 dark:divide-gray-700">
        <!-- Data rows -->
        <tr
          v-for="row in data.rows"
          :key="row.label"
          class="hover:bg-gray-50 dark:hover:bg-gray-800/50"
        >
          <!-- Row label -->
          <td
            class="px-3 py-2 whitespace-nowrap text-gray-900 dark:text-gray-100 sticky left-0 bg-white dark:bg-gray-900"
          >
            {{ row.label }}
          </td>
          <!-- Data cells -->
          <td
            v-for="(col, colIndex) in data.columns"
            :key="col"
            class="px-3 py-2 whitespace-nowrap text-right text-gray-700 dark:text-gray-200"
            :style="cellStyle(row.values[col])"
          >
            <template v-if="row.values[col] !== undefined && row.values[col] !== 0">
              <!-- Clickable link if available -->
              <router-link
                v-if="getCellLink(row, col, colIndex)"
                :to="getCellLink(row, col, colIndex)!"
                class="text-indigo-600 hover:text-indigo-800 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
              >
                {{ formatValue(row.values[col]) }}
              </router-link>
              <!-- Select action (drives another widget) otherwise -->
              <button
                v-else-if="getCellSelect(row, col, colIndex)"
                type="button"
                class="text-indigo-600 hover:text-indigo-800 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
                @click="emit('select', getCellSelect(row, col, colIndex)!)"
              >
                {{ formatValue(row.values[col]) }}
              </button>
              <!-- Plain value otherwise -->
              <span v-else>
                {{ formatValue(row.values[col]) }}
              </span>
            </template>
          </td>
          <!-- Row total -->
          <td
            v-if="showRowTotals"
            class="px-3 py-2 whitespace-nowrap text-right font-semibold text-gray-900 dark:text-gray-100 bg-gray-50 dark:bg-gray-800"
          >
            {{ formatValue(row.total || 0) }}
          </td>
        </tr>

        <!-- Column totals row -->
        <tr
          v-if="showColumnTotals && data.columnTotals"
          class="bg-gray-100 dark:bg-gray-800/75 font-semibold"
        >
          <td
            class="px-3 py-2 whitespace-nowrap text-gray-900 dark:text-gray-100 sticky left-0 bg-gray-100 dark:bg-gray-800/75"
          >
            Grand totals
          </td>
          <td
            v-for="col in data.columns"
            :key="col"
            class="px-3 py-2 whitespace-nowrap text-right text-gray-900 dark:text-gray-100"
          >
            {{ formatValue(data.columnTotals[col] || 0) }}
          </td>
          <td
            v-if="showRowTotals"
            class="px-3 py-2 whitespace-nowrap text-right text-gray-900 dark:text-gray-100 bg-gray-200 dark:bg-gray-600"
          >
            {{ formatValue(data.grandTotal || 0) }}
          </td>
        </tr>

        <!-- Empty state -->
        <tr v-if="data.rows.length === 0">
          <td
            :colspan="data.columns.length + (showRowTotals ? 2 : 1)"
            class="px-3 py-8 text-center text-gray-500 dark:text-gray-400"
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
import type { PivotData, PivotRow, PivotLinkContext, ValueLinkConfig, SelectParams } from '@/types/recipes'
import { budgetStatusOf, hexToRgba, type BudgetStatusColors } from '@/utils/budgetStatus'
import { useDashboardTheme } from '@/composables/useDashboardTheme'
import { useTheme } from '@/composables/useTheme'

interface Props {
  data: PivotData
  rowHeader?: string
  valueFormat?: (value: number) => string
  showRowTotals?: boolean
  showColumnTotals?: boolean
  /**
   * Function to generate link for a cell value.
   * Return null/undefined for no link.
   */
  getValueLink?: (context: PivotLinkContext) => ValueLinkConfig | null | undefined
  /** Function to resolve a cell "select" action (dashboard params to set on click). */
  getValueSelect?: (context: PivotLinkContext) => SelectParams | null | undefined
  /** Budget-adherence heat-map: tint each cell by its value read as a usage fraction. */
  colorByValue?: boolean
  warnAt?: number
  colors?: BudgetStatusColors
}

const props = withDefaults(defineProps<Props>(), {
  rowHeader: 'Account',
  showRowTotals: true,
  showColumnTotals: true,
})
const emit = defineEmits<{ select: [params: SelectParams] }>()

// Cell tint from the active theme's valence band; strength = theme tints.heatmapAlpha.
const { valenceColor, heatmapAlpha, warnAtDefault } = useDashboardTheme()
const { isDarkMode } = useTheme()
function cellStyle(value: number | undefined): Record<string, string> | undefined {
  if (!props.colorByValue || value === undefined || value === 0) return undefined
  const status = budgetStatusOf(value, { warnAt: props.warnAt ?? warnAtDefault() })
  return { backgroundColor: hexToRgba(valenceColor(status, isDarkMode.value, props.colors), heatmapAlpha()) }
}

function formatValue(value: number): string {
  if (props.valueFormat) {
    return props.valueFormat(value)
  }
  // Default: format with commas and 2 decimal places, but hide .00
  const formatted = value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
  return formatted
}

/**
 * Get link for a cell value.
 * Checks both getValueLink function prop and row.links data.
 */
function getCellLink(row: PivotRow, column: string, columnIndex: number): RouteLocationRaw | null {
  const value = row.values[column]
  if (value === undefined || value === 0) return null

  // First check if row has pre-computed links
  if (row.links && row.links[column]) {
    const link = row.links[column]
    return { name: link.name, query: link.query }
  }

  // Then check getValueLink function prop
  if (props.getValueLink) {
    const context: PivotLinkContext = {
      rowLabel: row.label,
      rowData: row,
      column,
      columnIndex,
      value,
    }
    const link = props.getValueLink(context)
    if (link) {
      return { name: link.name, query: link.query }
    }
  }

  return null
}

/** Resolve a cell's "select" action (dashboard params to set on click), if any. */
function getCellSelect(row: PivotRow, column: string, columnIndex: number): SelectParams | null {
  const value = row.values[column]
  if (value === undefined || value === 0 || !props.getValueSelect) return null
  return props.getValueSelect({ rowLabel: row.label, rowData: row, column, columnIndex, value }) ?? null
}
</script>
