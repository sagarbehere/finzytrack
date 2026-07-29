/**
 * Partitioning helpers for the individual metadata editor (the Details drawer).
 *
 * Beancount metadata is arbitrary key/value, but the editor only exposes the
 * *free user* keys. The rest fall into three buckets (see dev-docs/bulk-edits.md §9):
 *   - Hidden        — parser internals (filename, lineno, …): never shown.
 *   - System        — id, content_hash, source_account: shown read-only.
 *   - Surfaced      — memo (own column) and document* (documents UI): not
 *                     duplicated as raw key/value here.
 */
import { PROTECTED_META_KEYS } from '@/utils/bulkOperations'

/** Read-only system keys surfaced in the drawer's collapsed "System" section. */
export const SYSTEM_READONLY_KEYS = ['id', 'content_hash', 'source_account'] as const

/** True for a document metadata key (`document`, `document2`, …). */
export function isDocumentKey(key: string): boolean {
  return /^document(\d+)?$/.test(key)
}

/** True for keys that have their own dedicated UI, so the editor skips them. */
export function isSurfacedElsewhereKey(key: string): boolean {
  return key === 'memo' || isDocumentKey(key)
}

export interface MetaField {
  key: string
  value: string
}

/** The free, user-editable metadata fields (order preserved). */
export function editableMetaFields(meta: Record<string, string> | undefined | null): MetaField[] {
  if (!meta) return []
  return Object.entries(meta)
    .filter(([k]) => !PROTECTED_META_KEYS.has(k) && !isSurfacedElsewhereKey(k))
    .map(([key, value]) => ({ key, value: String(value) }))
}

/** The read-only system fields present on this transaction, in a stable order. */
export function systemMetaFields(meta: Record<string, string> | undefined | null): MetaField[] {
  if (!meta) return []
  return SYSTEM_READONLY_KEYS.filter(k => k in meta).map(k => ({ key: k, value: String(meta[k]) }))
}

/**
 * Rebuild a full metadata dict from edited user fields, preserving every
 * non-editable key (protected + surfaced-elsewhere) untouched. Fields with an
 * empty key are dropped; later duplicates win.
 */
export function buildMetaFromFields(
  baseMeta: Record<string, string> | undefined | null,
  fields: MetaField[],
): Record<string, string> {
  const preserved: Record<string, string> = {}
  for (const [k, v] of Object.entries(baseMeta || {})) {
    if (PROTECTED_META_KEYS.has(k) || isSurfacedElsewhereKey(k)) preserved[k] = String(v)
  }
  const out = { ...preserved }
  for (const f of fields) {
    if (f.key) out[f.key] = f.value
  }
  return out
}
