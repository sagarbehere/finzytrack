/**
 * Helpers over the recognized metadata vocabulary (GET /api/metadata/vocabulary)
 * for the Investments → Setup & Metadata tab. Recognition only: custom keys and
 * values are always allowed. See dev-docs/investment-view.md §10.3.
 */
import type { MetadataFieldSpec } from '@/services/generated-api'

export type MetadataScope = 'commodity' | 'account' | 'income-account'

export function fieldsFor(fields: MetadataFieldSpec[], scope: MetadataScope): MetadataFieldSpec[] {
  return fields.filter(f => f.scope === scope)
}

/** Whether `field` is relevant to an item carrying `metadata` (e.g. maturity_date needs account-type cd/bond). */
export function fieldApplies(field: MetadataFieldSpec, metadata: Record<string, string>): boolean {
  if (!field.requires_key) return true
  const current = metadata[field.requires_key]
  if (!current) return false
  return (field.requires_values ?? []).includes(current.trim().toLowerCase())
}

/** The recognized spelling of `value` if it matches one (case-insensitive), else `value` trimmed. */
export function canonicalValue(field: MetadataFieldSpec, value: string): string {
  const v = value.trim()
  const match = (field.values ?? []).find(r => r.toLowerCase() === v.toLowerCase())
  return match ?? v
}

export function isRecognizedValue(field: MetadataFieldSpec, value: string): boolean {
  const v = value.trim().toLowerCase()
  return (field.values ?? []).some(r => r.toLowerCase() === v)
}

export interface MetadataChange {
  set: Record<string, string>
  remove: string[]
}

/**
 * The change that turns `original` into `edited` for the given `keys`. An
 * emptied value removes the key; untouched keys are left alone. Returns null
 * when nothing changed.
 */
export function diffMetadata(
  original: Record<string, string>,
  edited: Record<string, string>,
  keys: string[],
): MetadataChange | null {
  const set: Record<string, string> = {}
  const remove: string[] = []
  for (const key of keys) {
    if (!(key in edited)) continue
    const before = (original[key] ?? '').trim()
    const after = edited[key].trim()
    if (after === before) continue
    if (after === '') {
      if (key in original) remove.push(key)
    } else {
      set[key] = after
    }
  }
  return Object.keys(set).length || remove.length ? { set, remove } : null
}

/**
 * When the ledger declares no operating currencies, a commodity's asset-class
 * decides whether it counts as a currency (cash) or a holding; untagged counts
 * as a currency. Returns the role the commodity would have after the edit, or
 * null when the edit doesn't change it (or operating currencies decide).
 */
export function currencyRoleAfterEdit(
  isCurrencyNow: boolean,
  editedAssetClass: string,
  hasOperatingCurrencies: boolean,
  currencyAssetClasses: string[],
): 'currency' | 'holding' | null {
  if (hasOperatingCurrencies) return null
  const ac = editedAssetClass.trim().toLowerCase()
  const isCurrencyAfter = ac === '' || currencyAssetClasses.includes(ac)
  if (isCurrencyAfter === isCurrencyNow) return null
  return isCurrencyAfter ? 'currency' : 'holding'
}
