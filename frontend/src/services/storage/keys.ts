/**
 * Central registry of all persisted frontend UI setting keys.
 *
 * Add new keys here — never use raw strings elsewhere in the codebase.
 */
export const STORAGE_KEYS = {
  THEME: 'theme',
  SIDEBAR_WIDTH: 'sidebarWidth',
  DASHBOARD_TABS: 'dashboardTabs',
  TABLE_VISIBLE_COLUMNS: 'tableVisibleColumns',
  TABLE_COLUMN_WIDTHS: 'tableColumnWidths',
  /** Per-widget user parameter selections (e.g. currency, year). Keyed by widget id. */
  WIDGET_SETTINGS: 'widgetSettings',
} as const

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS]
