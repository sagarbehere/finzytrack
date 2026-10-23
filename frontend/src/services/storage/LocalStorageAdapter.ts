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

  // The browser fires `storage` only in OTHER tabs of the same origin, which is
  // exactly "changed elsewhere".
  subscribe<T>(key: string, onChange: (value: T | null) => void): () => void {
    if (typeof window === 'undefined') return () => {}
    const full = this.prefixed(key)
    const listener = (e: StorageEvent) => {
      if (e.storageArea !== localStorage || e.key !== full) return
      try {
        onChange(e.newValue === null ? null : (JSON.parse(e.newValue) as T))
      } catch {
        onChange(null)
      }
    }
    window.addEventListener('storage', listener)
    return () => window.removeEventListener('storage', listener)
  }
}
