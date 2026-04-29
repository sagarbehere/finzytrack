import { ref, computed, watch } from 'vue'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'

export interface ColumnConfig {
  id: string
  label: string
  defaultVisible: boolean
  defaultWidth: number
  minWidth: number
  resizable: boolean
  disabled?: boolean         // Column exists but can't be toggled yet (not implemented)
  disabledReason?: string   // Tooltip/label text for disabled columns
}

const COLUMN_CONFIGS: ColumnConfig[] = [
  { id: 'status', label: 'Status', defaultVisible: true, defaultWidth: 60, minWidth: 30, resizable: true },
  { id: 'index', label: '#', defaultVisible: true, defaultWidth: 50, minWidth: 30, resizable: true },
  { id: 'date', label: 'Date', defaultVisible: true, defaultWidth: 120, minWidth: 60, resizable: true },
  { id: 'flag', label: 'Flag', defaultVisible: false, defaultWidth: 60, minWidth: 30, resizable: true },
  { id: 'payee', label: 'Payee', defaultVisible: true, defaultWidth: 150, minWidth: 50, resizable: true },
  { id: 'memo', label: 'Memo', defaultVisible: false, defaultWidth: 150, minWidth: 50, resizable: true },
  { id: 'narration', label: 'Narration', defaultVisible: true, defaultWidth: 200, minWidth: 50, resizable: true },
  { id: 'tags_links', label: 'Tags/Links', defaultVisible: false, defaultWidth: 150, minWidth: 50, resizable: true },
  { id: 'account', label: 'Account', defaultVisible: true, defaultWidth: 180, minWidth: 50, resizable: true },
  { id: 'amount', label: 'Amount', defaultVisible: true, defaultWidth: 100, minWidth: 40, resizable: true },
  { id: 'currency', label: 'Currency', defaultVisible: true, defaultWidth: 80, minWidth: 30, resizable: true },
  { id: 'cost_amount', label: 'Cost Amount', defaultVisible: false, defaultWidth: 120, minWidth: 40, resizable: true },
  { id: 'cost_currency', label: 'Cost Currency', defaultVisible: false, defaultWidth: 100, minWidth: 30, resizable: true },
  { id: 'cost_date', label: 'Cost Date', defaultVisible: false, defaultWidth: 120, minWidth: 60, resizable: true },
  { id: 'price_amount', label: 'Price Amount', defaultVisible: false, defaultWidth: 120, minWidth: 40, resizable: true },
  { id: 'price_currency', label: 'Price Currency', defaultVisible: false, defaultWidth: 100, minWidth: 30, resizable: true },
  { id: 'price_type', label: 'Price Type', defaultVisible: false, defaultWidth: 90, minWidth: 30, resizable: true },
  { id: 'balance', label: 'Balance', defaultVisible: false, defaultWidth: 120, minWidth: 40, resizable: true, disabled: true, disabledReason: 'Coming Soon' },
  { id: 'actions', label: 'Actions', defaultVisible: true, defaultWidth: 100, minWidth: 40, resizable: true },
]

export function useTableColumns() {
  // Load persisted settings via storage adapter
  const loadPersistedVisibility = (): Record<string, boolean> => {
    return getStorageAdapter().get<Record<string, boolean>>(STORAGE_KEYS.TABLE_VISIBLE_COLUMNS) ?? {}
  }

  const loadPersistedWidths = (): Record<string, number> => {
    return getStorageAdapter().get<Record<string, number>>(STORAGE_KEYS.TABLE_COLUMN_WIDTHS) ?? {}
  }

  // Initialize visibility from defaults + persisted overrides
  const persistedVisibility = loadPersistedVisibility()
  const persistedWidths = loadPersistedWidths()

  const columnVisibility = ref<Record<string, boolean>>(
    COLUMN_CONFIGS.reduce((acc, config) => {
      acc[config.id] = persistedVisibility[config.id] ?? config.defaultVisible
      return acc
    }, {} as Record<string, boolean>)
  )

  const columnWidths = ref<Record<string, number>>(
    COLUMN_CONFIGS.reduce((acc, config) => {
      acc[config.id] = persistedWidths[config.id] ?? config.defaultWidth
      return acc
    }, {} as Record<string, number>)
  )

  // Computed properties
  const visibleColumns = computed(() => 
    COLUMN_CONFIGS.filter(config => columnVisibility.value[config.id])
  )

  const hiddenColumns = computed(() => 
    COLUMN_CONFIGS.filter(config => !columnVisibility.value[config.id])
  )

  const allColumns = computed(() => COLUMN_CONFIGS)

  // Column sizing for TanStack Table
  const columnSizing = computed(() => {
    const sizing: Record<string, number> = {}
    COLUMN_CONFIGS.forEach(config => {
      if (columnVisibility.value[config.id]) {
        sizing[config.id] = columnWidths.value[config.id]
      }
    })
    return sizing
  })

  // Methods
  const toggleColumnVisibility = (columnId: string) => {
    // Replace the object reference (not mutate in place) so TanStack's
    // internal memoization invalidates the visible-cell path. Otherwise
    // headers update via Vue reactivity but cells stay stale.
    columnVisibility.value = {
      ...columnVisibility.value,
      [columnId]: !columnVisibility.value[columnId],
    }
  }

  const setColumnWidth = (columnId: string, width: number) => {
    const config = COLUMN_CONFIGS.find(c => c.id === columnId)
    if (config) {
      columnWidths.value[columnId] = Math.max(width, config.minWidth)
    }
  }

  const resetToDefaults = () => {
    columnVisibility.value = COLUMN_CONFIGS.reduce((acc, config) => {
      acc[config.id] = config.defaultVisible
      return acc
    }, {} as Record<string, boolean>)

    columnWidths.value = COLUMN_CONFIGS.reduce((acc, config) => {
      acc[config.id] = config.defaultWidth
      return acc
    }, {} as Record<string, number>)
  }

  const getColumnConfig = (columnId: string): ColumnConfig | undefined => {
    return COLUMN_CONFIGS.find(config => config.id === columnId)
  }

  // Persist changes via storage adapter
  watch(columnVisibility, (newVisibility) => {
    getStorageAdapter().set(STORAGE_KEYS.TABLE_VISIBLE_COLUMNS, newVisibility)
  }, { deep: true })

  watch(columnWidths, (newWidths) => {
    getStorageAdapter().set(STORAGE_KEYS.TABLE_COLUMN_WIDTHS, newWidths)
  }, { deep: true })

  return {
    columnVisibility,
    columnWidths,
    columnSizing,
    visibleColumns,
    hiddenColumns,
    allColumns,
    toggleColumnVisibility,
    setColumnWidth,
    resetToDefaults,
    getColumnConfig
  }
}