<template>
  <div class="flex h-full flex-col">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 pb-3">
      <div class="relative w-56">
        <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <input
          v-model="query"
          type="search"
          :placeholder="`Search ${(treeLabel || 'rows').toLowerCase()}…`"
          :aria-label="`Search ${(treeLabel || 'rows').toLowerCase()}`"
          class="block w-full rounded-md bg-white py-1 pl-8 pr-2 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
        />
      </div>

      <div v-if="levelCount > 1" class="flex items-center gap-1.5 text-sm" role="group" aria-label="Expand to level">
        <span class="text-gray-500 dark:text-gray-400">Expand to level</span>
        <button
          v-for="level in levelCount"
          :key="level"
          type="button"
          :aria-pressed="levelChoice === level"
          :class="[
            levelChoice === level
              ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300'
              : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white',
            'min-w-6 rounded-md px-1.5 py-0.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-indigo-600',
          ]"
          @click="expandToLevel(level)"
        >
          {{ level }}
        </button>
      </div>

      <div class="ml-auto flex items-center gap-2">
        <button
          type="button"
          class="inline-flex items-center gap-x-1.5 rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          title="Copy the visible rows; paste into a spreadsheet"
          @click="copyRows"
        >
          <CheckIcon v-if="copied" class="size-4 text-green-600 dark:text-green-400" aria-hidden="true" />
          <ClipboardDocumentIcon v-else class="size-4" aria-hidden="true" />
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-x-1.5 rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          title="Download the visible rows as a CSV file"
          @click="downloadCsv"
        >
          <ArrowDownTrayIcon class="size-4" aria-hidden="true" />
          CSV
        </button>
        <ColumnVisibilityControl
          v-if="hideableColumns.length"
          :column-visibility="columnVisibility"
          :all-columns="hideableColumns"
          :toggle-column-visibility="toggleColumn"
          :reset-to-defaults="resetColumns"
          align="right"
        />
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-auto">
      <table class="w-full table-fixed" role="treegrid" :aria-label="treeLabel || 'Tree'" :style="{ minWidth: `${tableMinWidth}px` }">
        <thead ref="tableHead" class="sticky top-0 z-10 bg-gray-50 dark:bg-gray-800">
          <tr v-for="headerGroup in table.getHeaderGroups()" :key="headerGroup.id">
            <th
              v-for="header in headerGroup.headers"
              :key="header.id"
              scope="col"
              :style="header.column.getCanResize() ? { width: `${header.getSize()}px` } : undefined"
              :aria-sort="sort?.key === header.column.id ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined"
              class="relative px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 dark:text-gray-400 dark:border-gray-700"
              :class="alignClass(header.column.id)"
            >
              <div v-if="header.column.id === TREE_COLUMN_ID" class="flex items-center gap-3">
                <span>{{ treeLabel || 'Name' }}</span>
                <button
                  v-if="table.getCanSomeRowsExpand()"
                  type="button"
                  class="rounded-sm text-xs font-medium normal-case tracking-normal text-indigo-600 hover:text-indigo-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:text-indigo-400 dark:hover:text-indigo-300"
                  @click="setAllExpanded(!table.getIsAllRowsExpanded())"
                >
                  {{ table.getIsAllRowsExpanded() ? 'Collapse all' : 'Expand all' }}
                </button>
              </div>
              <!-- Value column: click to sort siblings (largest first, smallest first, original order) -->
              <div v-else class="inline-flex items-center gap-1" :class="alignClass(header.column.id) === 'text-right' ? 'flex-row-reverse' : ''">
                <button
                  type="button"
                  class="group inline-flex items-center uppercase tracking-wider"
                  :title="`Sort by ${columnLabel(header.column.id)}`"
                  @click="toggleSort(header.column.id)"
                >
                  {{ columnLabel(header.column.id) }}
                  <span
                    :class="sort?.key === header.column.id
                      ? 'ml-1 flex-none rounded-sm bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white'
                      : 'invisible ml-1 flex-none rounded-sm text-gray-400 group-hover:visible group-focus-visible:visible dark:text-gray-500'"
                  >
                    <ChevronUpIcon v-if="sort?.key === header.column.id && sort.dir === 'asc'" class="size-4" aria-hidden="true" />
                    <ChevronDownIcon v-else class="size-4" aria-hidden="true" />
                  </span>
                </button>
                <!-- Column explanation: hover or focus the icon -->
                <span v-if="columnDescription(header.column.id)" class="group/info relative normal-case tracking-normal">
                  <span
                    tabindex="0"
                    role="img"
                    :aria-label="`About ${columnLabel(header.column.id)}: ${columnDescription(header.column.id)}`"
                    class="cursor-help select-none rounded-sm text-xs text-gray-400 focus-visible:outline-2 focus-visible:outline-indigo-600 dark:text-gray-500"
                  >ⓘ</span>
                  <span
                    class="invisible absolute top-full z-30 mt-1 w-64 whitespace-normal rounded bg-gray-800 px-2.5 py-1.5 text-left text-xs font-normal text-white shadow-lg group-hover/info:visible group-focus-within/info:visible dark:bg-gray-900"
                    :class="alignClass(header.column.id) === 'text-right' ? 'right-0' : 'left-0'"
                    aria-hidden="true"
                  >
                    {{ columnDescription(header.column.id) }}
                  </span>
                </span>
              </div>
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
        <tbody ref="tbody" class="bg-white dark:bg-gray-900" @keydown="onKeydown">
          <tr
            v-for="row in table.getRowModel().rows"
            :key="row.id"
            :data-row-id="row.id"
            role="row"
            :aria-level="row.depth + 1"
            :aria-expanded="row.getCanExpand() ? row.getIsExpanded() : undefined"
            :tabindex="row.id === focusRowId ? 0 : -1"
            :class="rowClass(row.original)"
            class="outline-none focus-visible:bg-indigo-50 dark:focus-visible:bg-indigo-500/10"
            @focus="focusRowId = row.id"
          >
            <td
              v-for="cell in row.getVisibleCells()"
              :key="cell.id"
              role="gridcell"
              class="px-4 py-2 text-sm whitespace-nowrap overflow-hidden text-ellipsis border-b border-gray-100 dark:border-white/5"
              :class="[alignClass(cell.column.id), row.original.kind === 'group' ? 'sticky z-[5] bg-gray-50 dark:bg-gray-800' : '']"
              :style="row.original.kind === 'group' ? { top: `${headHeight}px` } : undefined"
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
                  tabindex="-1"
                  class="flex-none rounded-sm p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-gray-200"
                  :aria-label="row.getIsExpanded() ? `Collapse ${row.original.label}` : `Expand ${row.original.label}`"
                  @click="toggleRow(row.id)"
                >
                  <ChevronRightIcon class="size-4 transition-transform" :class="{ 'rotate-90': row.getIsExpanded() }" aria-hidden="true" />
                </button>
                <span v-else class="w-5 flex-none" aria-hidden="true" />
                <router-link
                  v-if="treeLink(row.original)"
                  :to="treeLink(row.original)!"
                  tabindex="-1"
                  :title="row.original.path || undefined"
                  class="truncate hover:text-indigo-600 hover:underline dark:hover:text-indigo-400"
                >
                  {{ row.original.label }}
                </router-link>
                <span v-else class="truncate" :title="row.original.path || undefined">{{ row.original.label }}</span>
              </div>
              <!-- Share column: a small bar + percentage -->
              <div v-else-if="cell.column.id === SHARE_COLUMN_ID" class="flex items-center justify-end gap-2">
                <template v-if="shareOf(row.original) !== null">
                  <span class="h-1.5 w-16 flex-none overflow-hidden rounded-full bg-gray-200 dark:bg-white/10" aria-hidden="true">
                    <span class="block h-full rounded-full bg-indigo-500 dark:bg-indigo-400" :style="{ width: `${barWidth(row.original)}%` }" />
                  </span>
                  <span :class="shareOf(row.original)! < 0 ? 'text-red-600 dark:text-red-400' : ''">{{ formatShare(shareOf(row.original)) }}</span>
                </template>
              </div>
              <span v-else :class="isNegative(row.original, cell.column.id) ? 'text-red-600 dark:text-red-400' : ''">
                {{ formatValue(row.original, cell.column.id) }}
              </span>
            </td>
          </tr>
          <tr v-if="displayTree.length === 0">
            <td :colspan="table.getVisibleLeafColumns().length" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
              {{ query.trim() ? `Nothing matches “${query.trim()}”.` : (emptyText || 'No data available') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import {
  useVueTable,
  getCoreRowModel,
  getExpandedRowModel,
  createColumnHelper,
  type ExpandedState,
  type ColumnSizingState,
  type VisibilityState,
} from '@tanstack/vue-table'
import {
  ArrowDownTrayIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  ClipboardDocumentIcon,
  MagnifyingGlassIcon,
} from '@heroicons/vue/20/solid'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'
import type { TableColumn } from '@/types/recipes'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import {
  buildTreeRows,
  computeShares,
  filterTree,
  flattenTree,
  foldSingleChildChains,
  maxDepth,
  sortTree,
  type TreeNode,
  type TreeOptions,
} from '@/utils/buildTreeRows'

export interface TreeShareOptions {
  column: string
  label?: string
  base?: 'total' | 'parent'
  hidden?: boolean
  description?: string
}

interface Props {
  rows: Record<string, unknown>[]
  treeOptions: TreeOptions
  /** Header of the tree column. */
  treeLabel?: string
  /** Click-through for the tree column (format/link already resolved). */
  treeColumn?: TableColumn
  /** Value columns, already converted (format + per-row currency). */
  columns: TableColumn[]
  /** Keys of value columns that start hidden. */
  hiddenColumns?: string[]
  /** Column key → short explanation, shown from an info icon in the heading. */
  columnDescriptions?: Record<string, string>
  /** Levels expanded initially; undefined = everything expanded. */
  expandDepth?: number
  foldSingleChildren?: boolean
  share?: TreeShareOptions
  /** Row field that dims a row when truthy (e.g. a stale account). */
  dimField?: string
  emptyText?: string
  /** Remembers expansion, sort, columns and widths under this key; none = not remembered. */
  stateKey?: string
  /** Base name of a downloaded CSV file (e.g. the widget title). */
  exportName?: string
}

const props = withDefaults(defineProps<Props>(), { foldSingleChildren: true })

const TREE_COLUMN_ID = '__tree'
const SHARE_COLUMN_ID = '__share'

const router = useRouter()

// ── Remembered view state ─────────────────────────────────────────────────────
interface SavedState {
  expanded?: ExpandedState
  sort?: { key: string; dir: 'asc' | 'desc' } | null
  visibility?: VisibilityState
  sizing?: ColumnSizingState
}
function loadSaved(): SavedState {
  if (!props.stateKey) return {}
  return getStorageAdapter().get<Record<string, SavedState>>(STORAGE_KEYS.TREE_TABLE_STATE)?.[props.stateKey] ?? {}
}
const saved = loadSaved()

// ── Tree pipeline: build → fold → sort → filter ───────────────────────────────
const query = ref('')
const sort = ref<SavedState['sort']>(saved.sort ?? null)

const builtTree = computed(() => {
  const tree = buildTreeRows(props.rows, props.treeOptions)
  return props.foldSingleChildren ? foldSingleChildChains(tree) : tree
})
const shares = computed(() =>
  props.share ? computeShares(builtTree.value, props.share.column, props.share.base ?? 'total') : null,
)
const sortedTree = computed(() => {
  const s = sort.value
  if (!s) return builtTree.value
  return sortTree(builtTree.value, s.key === SHARE_COLUMN_ID && props.share ? props.share.column : s.key, s.dir)
})
const displayTree = computed(() => filterTree(sortedTree.value, query.value))

const levelCount = computed(() => (builtTree.value.length ? maxDepth(builtTree.value) + 1 : 0))

// ── Expansion ────────────────────────────────────────────────────────────────
// The user's own expansion is remembered; while searching, a temporary
// all-open expansion is used instead and the remembered one is left untouched.
function expandedToDepth(levels: number | undefined): ExpandedState {
  if (levels === undefined) return true
  const state: Record<string, boolean> = {}
  for (const n of flattenTree(builtTree.value)) {
    if (n.depth < levels && n.children.length) state[n.id] = true
  }
  return state
}
const expanded = ref<ExpandedState>(saved.expanded ?? expandedToDepth(props.expandDepth))
const searchExpanded = ref<ExpandedState>(true)
watch(query, () => { searchExpanded.value = true })
const levelChoice = ref<number | null>(null)

function setExpanded(next: ExpandedState) {
  if (query.value.trim()) searchExpanded.value = next
  else expanded.value = next
}
function setAllExpanded(open: boolean) {
  levelChoice.value = open ? levelCount.value : 1
  setExpanded(open ? true : {})
}
function expandToLevel(level: number) {
  levelChoice.value = level
  setExpanded(level >= levelCount.value ? true : expandedToDepth(level - 1))
}
function toggleRow(id: string) {
  levelChoice.value = null
  table.getRow(id).toggleExpanded()
}

// ── Columns: visibility, sizing ──────────────────────────────────────────────
function defaultVisibility(): VisibilityState {
  const v: VisibilityState = {}
  for (const c of props.columns) v[c.key] = !props.hiddenColumns?.includes(c.key)
  if (props.share) v[SHARE_COLUMN_ID] = !props.share.hidden
  return v
}
const columnVisibility = ref<VisibilityState>({ ...defaultVisibility(), ...(saved.visibility ?? {}) })
const columnSizing = ref<ColumnSizingState>(saved.sizing ?? {})

const shareLabel = computed(() => props.share?.label || (props.share?.base === 'parent' ? '% of parent' : '% of total'))
const hideableColumns = computed(() => [
  ...props.columns.map((c) => ({ id: c.key, label: c.label })),
  ...(props.share ? [{ id: SHARE_COLUMN_ID, label: shareLabel.value }] : []),
])
function toggleColumn(id: string) {
  columnVisibility.value = { ...columnVisibility.value, [id]: !(columnVisibility.value[id] ?? true) }
}
function resetColumns() {
  columnVisibility.value = defaultVisibility()
  columnSizing.value = {}
}

watch(
  [expanded, sort, columnVisibility, columnSizing],
  () => {
    if (!props.stateKey) return
    const all = getStorageAdapter().get<Record<string, SavedState>>(STORAGE_KEYS.TREE_TABLE_STATE) ?? {}
    all[props.stateKey] = {
      expanded: expanded.value,
      sort: sort.value,
      visibility: columnVisibility.value,
      sizing: columnSizing.value,
    }
    getStorageAdapter().set(STORAGE_KEYS.TREE_TABLE_STATE, all)
  },
  { deep: true },
)

// ── Table ────────────────────────────────────────────────────────────────────
const columnHelper = createColumnHelper<TreeNode>()
const tableColumns = computed(() => [
  // The tree column takes the remaining width; value columns are resized.
  columnHelper.display({ id: TREE_COLUMN_ID, enableResizing: false, enableHiding: false }),
  ...props.columns.map((c) => columnHelper.display({ id: c.key, size: 150, minSize: 80 })),
  ...(props.share ? [columnHelper.display({ id: SHARE_COLUMN_ID, size: 150, minSize: 110 })] : []),
])

// The tree column takes whatever width is left, so keep at least this much for
// it: past that, the table scrolls sideways instead of squeezing the names.
const TREE_COLUMN_MIN_WIDTH = 240

const table = useVueTable({
  get data() { return displayTree.value },
  get columns() { return tableColumns.value },
  state: {
    get expanded() { return query.value.trim() ? searchExpanded.value : expanded.value },
    get columnSizing() { return columnSizing.value },
    get columnVisibility() { return columnVisibility.value },
  },
  onExpandedChange: (updater) => {
    const current = query.value.trim() ? searchExpanded.value : expanded.value
    setExpanded(typeof updater === 'function' ? updater(current) : updater)
  },
  onColumnSizingChange: (updater) => {
    columnSizing.value = typeof updater === 'function' ? updater(columnSizing.value) : updater
  },
  onColumnVisibilityChange: (updater) => {
    columnVisibility.value = typeof updater === 'function' ? updater(columnVisibility.value) : updater
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

const tableMinWidth = computed(() =>
  TREE_COLUMN_MIN_WIDTH +
  table.getVisibleLeafColumns().filter((c) => c.id !== TREE_COLUMN_ID).reduce((sum, c) => sum + c.getSize(), 0),
)

// Sticky group rows sit just under the (sticky) header.
const tableHead = ref<HTMLElement | null>(null)
const headHeight = ref(0)
watch(tableHead, (el, _old, onCleanup) => {
  if (!el || typeof ResizeObserver === 'undefined') return
  const observer = new ResizeObserver(() => { headHeight.value = el.offsetHeight })
  observer.observe(el)
  onCleanup(() => observer.disconnect())
})

// ── Sorting ──────────────────────────────────────────────────────────────────
function toggleSort(key: string) {
  const s = sort.value
  if (s?.key !== key) sort.value = { key, dir: 'desc' }
  else if (s.dir === 'desc') sort.value = { key, dir: 'asc' }
  else sort.value = null
}

// ── Cells ────────────────────────────────────────────────────────────────────
const columnByKey = computed(() => new Map(props.columns.map((c) => [c.key, c])))

function columnLabel(id: string): string {
  return id === SHARE_COLUMN_ID ? shareLabel.value : columnByKey.value.get(id)?.label ?? id
}

function columnDescription(id: string): string | undefined {
  return id === SHARE_COLUMN_ID ? props.share?.description : props.columnDescriptions?.[id]
}

function alignClass(columnId: string): string {
  if (columnId === SHARE_COLUMN_ID) return 'text-right'
  const align = columnByKey.value.get(columnId)?.align
  return align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left'
}

function isDimmed(node: TreeNode): boolean {
  if (!props.dimField) return false
  const v = node.row[props.dimField]
  return v === true || v === 1 || v === '1' || v === 'true'
}

function rowClass(node: TreeNode): string {
  const dim = isDimmed(node) ? ' opacity-55' : ''
  if (node.kind === 'group') return 'font-semibold text-gray-900 dark:text-white' + dim
  if (node.kind === 'branch') return 'font-semibold text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-white/5' + dim
  return 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-white/5' + dim
}

function formatValue(node: TreeNode, key: string): string {
  const column = columnByKey.value.get(key)
  const value = node.row[key]
  if (value === null || value === undefined || value === '') return ''
  return column?.format ? column.format(value, node.row) : String(value)
}

function isNegative(node: TreeNode, key: string): boolean {
  const v = node.row[key]
  return (typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) && Number(v) < 0
}

function shareOf(node: TreeNode): number | null {
  return shares.value?.get(node.id) ?? null
}
function barWidth(node: TreeNode): number {
  return Math.max(0, Math.min(100, (shareOf(node) ?? 0) * 100))
}
function formatShare(v: number | null): string {
  return v === null ? '' : `${(v * 100).toFixed(1)}%`
}

function treeLink(node: TreeNode): RouteLocationRaw | null {
  const column = props.treeColumn
  if (!column?.getLink) return null
  const link = column.getLink({ row: node.row, rowIndex: 0, column, value: node.label })
  return link ? { name: link.name, query: link.query } : null
}

// ── Keyboard (treegrid pattern) ──────────────────────────────────────────────
// Up/Down move between rows; Right expands, then moves to the first child;
// Left collapses, then moves to the parent; Home/End jump; Enter opens the
// row's link; Space toggles.
const focusRowId = ref<string | null>(null)
const tbody = ref<HTMLElement | null>(null)
watch(displayTree, () => {
  const rows = table.getRowModel().rows
  if (!rows.some((r) => r.id === focusRowId.value)) focusRowId.value = rows[0]?.id ?? null
}, { immediate: true })

async function focusRow(id: string | undefined) {
  if (!id) return
  focusRowId.value = id
  await nextTick()
  // Compared directly, not via a selector: group ids contain a NUL separator,
  // which CSS.escape rewrites, so an attribute selector would never match.
  const rows = tbody.value?.querySelectorAll<HTMLElement>('tr[data-row-id]') ?? []
  Array.from(rows).find((el) => el.dataset.rowId === id)?.focus()
}

function onKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement
  if (target.tagName !== 'TR') return
  const rows = table.getRowModel().rows
  const i = rows.findIndex((r) => r.id === target.dataset.rowId)
  if (i === -1) return
  const row = rows[i]
  let handled = true
  switch (e.key) {
    case 'ArrowDown': focusRow(rows[i + 1]?.id); break
    case 'ArrowUp': focusRow(rows[i - 1]?.id); break
    case 'Home': focusRow(rows[0]?.id); break
    case 'End': focusRow(rows[rows.length - 1]?.id); break
    case 'ArrowRight':
      if (row.getCanExpand() && !row.getIsExpanded()) toggleRow(row.id)
      else if (row.getCanExpand()) focusRow(rows[i + 1]?.id)
      break
    case 'ArrowLeft':
      if (row.getCanExpand() && row.getIsExpanded()) toggleRow(row.id)
      else focusRow(row.getParentRow()?.id)
      break
    case ' ':
      if (row.getCanExpand()) toggleRow(row.id)
      break
    case 'Enter': {
      const link = treeLink(row.original)
      if (link) router.push(link)
      break
    }
    default:
      handled = false
  }
  if (handled) e.preventDefault()
}

// ── Export: the visible rows and columns, as shown ───────────────────────────
// Copy puts tab-separated text on the clipboard (a spreadsheet splits a paste
// into columns only on tabs); Download writes a CSV file. Values are raw (e.g.
// 2841.67, ISO dates), names are indented by level.
function exportTable(): string[][] {
  const groupField = props.treeOptions.groupBy
  const valueCols = props.columns.filter((c) => columnVisibility.value[c.key] !== false)
  const withShare = props.share && columnVisibility.value[SHARE_COLUMN_ID] !== false
  const header = [
    props.treeLabel || 'Name',
    'Path',
    ...(groupField ? [groupField.charAt(0).toUpperCase() + groupField.slice(1)] : []),
    ...valueCols.map((c) => c.label),
    ...(withShare ? [shareLabel.value] : []),
  ]
  const rows = table.getRowModel().rows.map(({ original: n }) => [
    '  '.repeat(n.depth) + n.label,
    n.path,
    ...(groupField ? [n.row[groupField]] : []),
    ...valueCols.map((c) => n.row[c.key]),
    ...(withShare ? [shareOf(n) === null ? '' : ((shareOf(n) as number) * 100).toFixed(1)] : []),
  ].map((v) => String(v ?? '')))
  return [header, ...rows]
}

const copied = ref(false)
async function copyRows() {
  const text = exportTable().map((r) => r.map((v) => v.replace(/[\t\n]/g, ' ')).join('\t')).join('\n')
  await navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function csvField(v: string): string {
  return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
}

function downloadCsv() {
  const csv = exportTable().map((r) => r.map(csvField).join(',')).join('\r\n')
  const name = (props.exportName || props.treeLabel || 'table').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const date = new Date().toISOString().slice(0, 10)
  // A BOM so spreadsheet apps read the file as UTF-8 (₹, ›, accents).
  const url = URL.createObjectURL(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${name || 'table'}-${date}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>
