import type { StorageAdapter } from './types'

const NAMESPACE = 'finzytrack'

export class LocalStorageAdapter implements StorageAdapter {
  private prefixed(key: string): string {
    return `${NAMESPACE}:${key}`
  }

  get<T>(key: string): T | null {
    try {
      const raw = localStorage.getItem(this.prefixed(key))
      if (raw === null) return null
      return JSON.parse(raw) as T
    } catch {
      return null
    }
  }

  set<T>(key: string, value: T): void {
    try {
      localStorage.setItem(this.prefixed(key), JSON.stringify(value))
    } catch (err) {
      console.warn(`[Storage] Failed to persist "${key}":`, err)
    }
  }

  remove(key: string): void {
    try {
      localStorage.removeItem(this.prefixed(key))
    } catch {
      // ignore
    }
  }
}
