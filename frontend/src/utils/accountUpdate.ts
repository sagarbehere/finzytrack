/**
 * Build the account update request from the edit form. Metadata is sent as a
 * change, not a snapshot: only keys whose value changed go in `metadata`, and
 * keys the form no longer carries go in `remove_metadata` — the backend keeps
 * every key not named (dev-docs/investment-view.md §10.3, Q5).
 *
 * Currencies are sent only when the user changed the `open` directive's
 * constraint. The form pre-fills an unconstrained `open` with the currencies the
 * account currently holds; sending that back would add a constraint the user
 * never asked for (and can reject old postings in other currencies).
 */
import type { AccountUpdateRequest } from '@/services/generated-api'
import type { AccountFormSubmitData, AccountTreeNode } from '@/types/accounts'

export function buildAccountUpdateRequest(
  original: AccountTreeNode,
  data: AccountFormSubmitData,
): AccountUpdateRequest {
  const next: Record<string, string> = { ...data.metadata }
  if (data.description) next['description'] = data.description

  const set: Record<string, string> = {}
  for (const [k, v] of Object.entries(next)) {
    if (original.metadata[k] !== v) set[k] = v
  }
  const remove = Object.keys(original.metadata).filter(k => !(k in next))

  const sameSet = (a: string[], b: string[]) =>
    a.length === b.length && [...a].sort().join('|') === [...b].sort().join('|')
  const unconstrainedPrefill =
    original.declaredCurrencies.length === 0 && sameSet(data.currencies, original.currencyBadges)
  const currenciesChanged =
    !sameSet(data.currencies, original.declaredCurrencies) && !unconstrainedPrefill

  const isRenamed = data.name !== original.fullPath
  return {
    new_name: isRenamed ? data.name : undefined,
    open_date: data.openDate,
    close_date: data.closeDate !== undefined ? (data.closeDate || '') : undefined,
    currencies: currenciesChanged ? data.currencies : undefined,
    metadata: Object.keys(set).length > 0 ? set : undefined,
    remove_metadata: remove.length > 0 ? remove : undefined,
  }
}
