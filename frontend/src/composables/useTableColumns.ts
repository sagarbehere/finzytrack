import { ref, computed, watch } from 'vue'

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
  { id: 'status', label: 'Status', defaultVisible: true, defaultWidth: 60, minWidth: 60, resizable: false },
  { id: 'index', label: '#', defaultVisible: true, defaultWidth: 50, minWidth: 40, resizable: true },
  { id: 'date', label: 'Date', defaultVisible: true, defaultWidth: 120, minWidth: 100, resizable: true },
  { id: 'flag', label: 'Flag', defaultVisible: false, defaultWidth: 60, minWidth: 50, resizable: true },
  { id: 'payee', label: 'Payee', defaultVisible: true, defaultWidth: 150, minWidth: 100, resizable: true },
  { id: 'narration', label: 'Narration', defaultVisible: true, defaultWidth: 200, minWidth: 120, resizable: true },
  { id: 'tags_links', label: 'Tags/Links', defaultVisible: false, defaultWidth: 150, minWidth: 100, resizable: true },
  { id: 'account', label: 'Account', defaultVisible: true, defaultWidth: 180, minWidth: 120, resizable: true },
  { id: 'amount', label: 'Amount', defaultVisible: true, defaultWidth: 100, minWidth: 80, resizable: true },
  { id: 'currency', label: 'Currency', defaultVisible: true, defaultWidth: 80, minWidth: 60, resizable: true },
  { id: 'balance', label: 'Balance', defaultVisible: false, defaultWidth: 120, minWidth: 100, resizable: true, disabled: true, disabledReason: 'Coming Soon' },
  { id: 'actions', label: 'Actions', defaultVisible: true, defaultWidth: 100, minWidth: 80, resizable: false },
]

const STORAGE_KEYS = {
  VISIBLE_COLUMNS: 'finzytrack_table_visible_columns',
  COLUMN_WIDTHS: 'finzytrack_table_column_widths'
}

export function useTableColumns() {
  // Load persisted settings from localStorage
  const loadPersistedVisibility = (): Record<string, boolean> => {
    try {
      const saved = localStorage.getItem(STORAGE_KEYS.VISIBLE_COLUMNS)
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  }

  const loadPersistedWidths = (): Record<string, number> => {
    try {
      const saved = localStorage.getItem(STORAGE_KEYS.COLUMN_WIDTHS)
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
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
    columnVisibility.value[columnId] = !columnVisibility.value[columnId]
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

  // Persist changes to localStorage
  watch(columnVisibility, (newVisibility) => {
    try {
      localStorage.setItem(STORAGE_KEYS.VISIBLE_COLUMNS, JSON.stringify(newVisibility))
    } catch (error) {
      console.warn('Failed to save column visibility to localStorage:', error)
    }
  }, { deep: true })

  watch(columnWidths, (newWidths) => {
    try {
      localStorage.setItem(STORAGE_KEYS.COLUMN_WIDTHS, JSON.stringify(newWidths))
    } catch (error) {
      console.warn('Failed to save column widths to localStorage:', error)
    }
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