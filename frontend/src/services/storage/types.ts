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
  /**
   * Optional: call `onChange` with the new value when `key` is changed by
   * another instance of the app (e.g. another browser tab) — not for this
   * instance's own writes. Returns an unsubscribe function.
   */
  subscribe?<T>(key: string, onChange: (value: T | null) => void): () => void
}
