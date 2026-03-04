/**
 * Interface for persisting frontend UI settings.
 *
 * Implement this interface to add support for a new runtime environment
 * (e.g. Tauri, Electron). See dev-docs/frontend-ui-settings.md for details.
 */
export interface StorageAdapter {
  get<T>(key: string): T | null
  set<T>(key: string, value: T): void
  remove(key: string): void
}
