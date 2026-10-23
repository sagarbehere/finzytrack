<template>
  <div class="overflow-auto h-full">
    <table class="w-full table-fixed">
      <thead class="bg-gray-50 dark:bg-gray-800 sticky top-0 z-10">
        <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
          <th
            v-for="header in headerGroup.headers"
            :key="header.id"
            scope="col"
            :style="header.column.getCanResize() ? { width: `${header.getSize()}px` } : undefined"
            class="relative px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 dark:text-gray-400 dark:border-gray-700"
            :class="alignClass(header.column.id)"
          >
            <FlexRender :render="header.column.columnDef.header" :props="header.getContext()" />
            <!-- Resize handle: a wide hit area around a visible divider line -->
            <div
              v-if="header.column.getCanResize()"
              class="group absolute inset-y-0 left-0 flex w-3 -translate-x-1/2 cursor-col-resize select-none touch-none justify-center"
              title="Drag to resize · double-click to reset"
              @mousedown="header.getResizeHandler()($event)"
              @touchstart="header.getResizeHandler()($event)"
              @dblclick="header.column.resetSize()"
            >
              <span
                class="my-2 w-px rounded-full transition-colors group-hover:w-0.5 group-hover:bg-indigo-500 dark:group-hover:bg-indigo-400"
                :class="header.column.getIsResizing() ? 'w-0.5 bg-indigo-500 dark:bg-indigo-400' : 'bg-gray-300 dark:bg-white/20'"
                aria-hidden="true"
              />
            </div>
          </th>
        </tr>
      </thead>
      <tbody class="bg-white dark:bg-gray-900">
        <tr
          v-for="row in table.getRowModel().rows"
          :key="row.id"
          :class="rowClass(row.original)"
        >
          <td
            v-for="cell in row.getVisibleCells()"
            :key="cell.id"
            class="px-4 py-2 text-sm whitespace-nowrap overflow-hidden text-ellipsis border-b border-gray-100 dark:border-white/5"
            :class="alignClass(cell.column.id)"
          >
            <!-- Tree column: indent, expand toggle, label (optionally a link) -->
            <div
              v-if="cell.column.id === TREE_COLUMN_ID"
              class="flex items-center gap-1 min-w-0"
              :style="{ paddingLeft: `${row.depth * 20}px` }"
            >
              <button
                v-if="row.getCanExpand()"
                type="button"
                class="flex-none rounded-sm p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-2 focus-visible:outline-indigo-600 dark:hover:bg-white/10 dark:hover:text-gray-200"
                :aria-label="row.getIsExpanded() ? `Collapse ${row.original.label}` : `Expand ${row.original.label}`"
                :aria-expanded="row.getIsExpanded()"
                @click="row.toggleExpanded()"
              >
                <ChevronRightIcon class="size-4 transition-transform" :class="{ 'rotate-90': row.getIsExpanded() }" aria-hidden="true" />
              </button>
              <span v-else class="w-5 flex-none" aria-hidden="true" />
              <router-link
                v-if="treeLink(row.original, row.index)"
                :to="treeLink(row.original, row.index)!"
                class="truncate hover:text-indigo-600 hover:underline dark:hover:text-indigo-400"
              >
                {{ row.original.label }}
              </router-link>
              <span v-else class="truncate">{{ row.original.label }}</span>
            </div>
            <span v-else>{{ formatValue(row.original, cell.column.id) }}</span>
          </td>
        </tr>
        <tr v-if="rows.length === 0">
          <td :colspan="columns.length + 1" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
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
import {
  useVueTable,
  getCoreRowModel,
  getExpandedRowModel,
  createColumnHelper,
  FlexRender,
  type ExpandedState,
  type ColumnSizingState,
} from '@tanstack/vue-table'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'
import type { TableColumn } from '@/types/recipes'
import { buildTreeRows, type TreeNode, type TreeOptions } from '@/utils/buildTreeRows'

interface Props {
  rows: Record<string, unknown>[]
  treeOptions: TreeOptions
  /** Header of the tree column. */
  treeLabel?: string
  /** Click-through for the tree column (format/link already resolved). */
  treeColumn?: TableColumn
  /** Value columns, already converted (format + per-row currency). */
  columns: TableColumn[]
  /** Levels expanded initially; undefined = everything expanded. */
  expandDepth?: number
  emptyText?: string
}

const props = defineProps<Props>()

const TREE_COLUMN_ID = '__tree'

const tree = computed(() => buildTreeRows(props.rows, props.treeOptions))

// Expansion: everything (TanStack's `true`) or the first `expandDepth` levels.
// It persists while the widget lives, so a parameter change (new data, same
// node ids) keeps what the user opened or closed.
function initialExpanded(): ExpandedState {
  if (props.expandDepth === undefined) return true
  const state: Record<string, boolean> = {}
  const walk = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      if (n.depth < props.expandDepth! && n.children.length) state[n.id] = true
      walk(n.children)
    }
  }
  walk(tree.value)
  return state
}
const expanded = ref<ExpandedState>(initialExpanded())
watch(() => props.expandDepth, () => { expanded.value = initialExpanded() })

const columnSizing = ref<ColumnSizingState>({})

const columnHelper = createColumnHelper<TreeNode>()
const tableColumns = computed(() => [
  columnHelper.display({
    id: TREE_COLUMN_ID,
    header: props.treeLabel || 'Name',
    // The tree column takes the remaining width; value columns are resized.
    enableResizing: false,
  }),
  ...props.columns.map((c) =>
    columnHelper.display({ id: c.key, header: c.label, size: 170, minSize: 80 }),
  ),
])

const table = useVueTable({
  get data() { return tree.value },
  get columns() { return tableColumns.value },
  state: {
    get expanded() { return expanded.value },
    get columnSizing() { return columnSizing.value },
  },
  onExpandedChange: (updater) => {
    expanded.value = typeof updater === 'function' ? updater(expanded.value) : updater
  },
  onColumnSizingChange: (updater) => {
    columnSizing.value = typeof updater === 'function' ? updater(columnSizing.value) : updater
  },
  getSubRows: (node) => node.children,
  getRowId: (node) => node.id,
  getCoreRowModel: getCoreRowModel(),
  getExpandedRowModel: getExpandedRowModel(),
  enableColumnResizing: true,
  columnResizeMode: 'onChange',
  // Handles sit on each value column's LEFT edge (the tree column fills the
  // rest), so dragging left must widen: resize deltas are mirrored.
  columnResizeDirection: 'rtl',
})

const columnByKey = computed(() => new Map(props.columns.map((c) => [c.key, c])))

function alignClass(columnId: string): string {
  const align = columnByKey.value.get(columnId)?.align
  return align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left'
}

function rowClass(node: TreeNode): string {
  if (node.kind === 'group') return 'bg-gray-50 font-semibold text-gray-900 dark:bg-white/5 dark:text-white'
  if (node.kind === 'branch') return 'font-semibold text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-white/5'
  return 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-white/5'
}

function formatValue(node: TreeNode, key: string): string {
  const column = columnByKey.value.get(key)
  const value = node.row[key]
  if (value === null || value === undefined || value === '') return ''
  return column?.format ? column.format(value, node.row) : String(value)
}

function treeLink(node: TreeNode, rowIndex: number): RouteLocationRaw | null {
  const column = props.treeColumn
  if (!column?.getLink) return null
  const link = column.getLink({ row: node.row, rowIndex, column, value: node.label })
  return link ? { name: link.name, query: link.query } : null
}
</script>
