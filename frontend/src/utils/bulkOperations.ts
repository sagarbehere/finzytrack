/**
 * Bulk edit operations for the Transactions view.
 *
 * Every bulk mutation — from the manual action bar or (later) autocategorize —
 * is one of these typed operations. Keeping the vocabulary small and closed is
 * deliberate: it makes each mutation auditable, keeps the review UI honest, and
 * guarantees no operation can touch a balance-affecting field (amount, currency,
 * cost, price) — those are simply not expressible here. See
 * `dev-docs/bulk-edits.md` for the rationale.
 *
 * These functions are pure with respect to the *store*: `applyOperationToTransaction`
 * mutates a single transaction object in place and reports whether it changed
 * anything. The store (`useTransactionStore`) is responsible for cloning,
 * refreshing modified flags, notifying reactivity, and the operation log.
 */
import type { TransactionViewModel } from '@/types/transactions'
import { INTERNAL_META_KEYS } from '@/utils/transactionMeta'

export type BulkOperation =
  | { type: 'replaceAccount'; from: string; to: string }
  | { type: 'addTag'; tag: string }
  | { type: 'removeTag'; tag: string }
  | { type: 'addLink'; link: string }
  | { type: 'removeLink'; link: string }
  | { type: 'setFlag'; flag: string }
  | { type: 'setPayee'; payee: string }
  | { type: 'appendPayee'; text: string }
  | { type: 'setNarration'; narration: string }
  | { type: 'appendNarration'; text: string }
  | { type: 'setMetadata'; key: string; value: string }
  | { type: 'removeMetadata'; key: string }
  | { type: 'renameMetadata'; from: string; to: string }

/**
 * Metadata keys that are system-managed and must never be edited through the
 * metadata editors (individual or bulk). `id` is the key the update endpoint
 * matches on, `content_hash` is recomputed on write, and `source_account`
 * drives commit/duplicate-detection. The parser-internal keys come from the
 * single source of truth in `transactionMeta.ts`.
 */
export const PROTECTED_META_KEYS: ReadonlySet<string> = new Set<string>([
  ...INTERNAL_META_KEYS,
  'id',
  'content_hash',
  'source_account',
])

/** True if `key` is a user-editable metadata key (not system-managed). */
export function isEditableMetaKey(key: string): boolean {
  return !PROTECTED_META_KEYS.has(key)
}

/**
 * Beancount metadata key syntax: starts with a lowercase letter, then lowercase
 * letters / digits / hyphen / underscore. Used to validate keys in the editors.
 */
export function isValidMetaKey(key: string): boolean {
  return /^[a-z][a-z0-9\-_]*$/.test(key)
}

/** Strip a single leading `#` (tags) or `^`/`ˆ` (links) marker if present. */
function stripMarker(value: string): string {
  return value.replace(/^[#^ˆ]/, '')
}

/**
 * Apply one bulk operation to a single transaction, mutating it in place.
 * Returns true iff the transaction actually changed — a no-op (e.g. adding a
 * tag it already has, replacing an account it doesn't use, editing a protected
 * metadata key) returns false so the store can skip it and leave it unmodified.
 *
 * This never touches amount/currency/cost/price, so it cannot unbalance a
 * transaction.
 */
export function applyOperationToTransaction(tx: TransactionViewModel, op: BulkOperation): boolean {
  switch (op.type) {
    case 'replaceAccount': {
      let changed = false
      for (const posting of tx.postings) {
        if (posting.account === op.from) {
          posting.account = op.to
          changed = true
        }
      }
      // Mirror the single-cell edit path: keep source_account in step when it
      // pointed at the account we just replaced (see useTransactionStore.updateField).
      if (changed && tx.meta['source_account'] === op.from) {
        tx.meta['source_account'] = op.to
      }
      return changed
    }

    case 'addTag': {
      const tag = stripMarker(op.tag.trim())
      if (!tag || tx.tags.includes(tag)) return false
      tx.tags = [...tx.tags, tag]
      return true
    }

    case 'removeTag': {
      const tag = stripMarker(op.tag.trim())
      if (!tag || !tx.tags.includes(tag)) return false
      tx.tags = tx.tags.filter(t => t !== tag)
      return true
    }

    case 'addLink': {
      const link = stripMarker(op.link.trim())
      if (!link || tx.links.includes(link)) return false
      tx.links = [...tx.links, link]
      return true
    }

    case 'removeLink': {
      const link = stripMarker(op.link.trim())
      if (!link || !tx.links.includes(link)) return false
      tx.links = tx.links.filter(l => l !== link)
      return true
    }

    case 'setFlag': {
      if (tx.flag === op.flag) return false
      tx.flag = op.flag
      return true
    }

    case 'setPayee': {
      if (tx.payee === op.payee) return false
      tx.payee = op.payee
      return true
    }

    case 'appendPayee': {
      const text = op.text.trim()
      if (!text) return false
      tx.payee = tx.payee ? `${tx.payee} ${text}` : text
      return true
    }

    case 'setNarration': {
      if (tx.narration === op.narration) return false
      tx.narration = op.narration
      return true
    }

    case 'appendNarration': {
      const text = op.text.trim()
      if (!text) return false
      tx.narration = tx.narration ? `${tx.narration} ${text}` : text
      return true
    }

    case 'setMetadata': {
      if (!isEditableMetaKey(op.key)) return false
      if (tx.meta[op.key] === op.value) return false
      tx.meta = { ...tx.meta, [op.key]: op.value }
      return true
    }

    case 'removeMetadata': {
      if (!isEditableMetaKey(op.key)) return false
      if (!(op.key in tx.meta)) return false
      const next = { ...tx.meta }
      delete next[op.key]
      tx.meta = next
      return true
    }

    case 'renameMetadata': {
      if (!isEditableMetaKey(op.from) || !isEditableMetaKey(op.to)) return false
      if (!(op.from in tx.meta) || op.from === op.to) return false
      const next = { ...tx.meta }
      next[op.to] = next[op.from]
      delete next[op.from]
      tx.meta = next
      return true
    }
  }
}

/**
 * Human-readable label for the operation summary. Intent-level ("Replace X → Y"),
 * not a field diff — the summary needs the *intent*, which a diff can't recover.
 */
export function describeOperation(op: BulkOperation): string {
  switch (op.type) {
    case 'replaceAccount':
      return `Replace account ${op.from} → ${op.to}`
    case 'addTag':
      return `Add tag #${stripMarker(op.tag.trim())}`
    case 'removeTag':
      return `Remove tag #${stripMarker(op.tag.trim())}`
    case 'addLink':
      return `Add link ^${stripMarker(op.link.trim())}`
    case 'removeLink':
      return `Remove link ^${stripMarker(op.link.trim())}`
    case 'setFlag':
      return `Set flag ${op.flag}`
    case 'setPayee':
      return `Set payee "${op.payee}"`
    case 'appendPayee':
      return `Append to payee "${op.text.trim()}"`
    case 'setNarration':
      return `Set narration "${op.narration}"`
    case 'appendNarration':
      return `Append to narration "${op.text.trim()}"`
    case 'setMetadata':
      return `Set metadata ${op.key} = ${op.value}`
    case 'removeMetadata':
      return `Remove metadata ${op.key}`
    case 'renameMetadata':
      return `Rename metadata ${op.from} → ${op.to}`
  }
}
