import type { StorageAdapter } from './types'
import { LocalStorageAdapter } from './LocalStorageAdapter'

/**
 * The active storage adapter. Defaults to LocalStorageAdapter.
 *
 * Call setStorageAdapter() at app startup to swap in a different backend
 * (e.g. TauriStoreAdapter, ElectronStoreAdapter). See dev-docs/frontend-ui-settings.md.
 */
let _adapter: StorageAdapter = new LocalStorageAdapter()

export function getStorageAdapter(): StorageAdapter {
  return _adapter
}

export function setStorageAdapter(adapter: StorageAdapter): void {
  _adapter = adapter
}

export { STORAGE_KEYS } from './keys'
export type { StorageAdapter } from './types'
export type { StorageKey } from './keys'
