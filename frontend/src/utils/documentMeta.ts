/**
 * Helpers for the transaction-level document metadata scheme.
 *
 * Documents are stored as the metadata keys `document`, `document2`,
 * `document3`, … (gapless, in order) — the same convention Fava's
 * `link_documents` plugin matches (any key `startsWith("document")`). See
 * dev-docs/documents.md invariant I2. These pure functions keep the scheme
 * gapless when adding/removing, so the badge count and the ledger stay in sync.
 */

export interface AttachedDocument {
  /** The metadata key (`document`, `document2`, …). */
  key: string
  /** The stored ledger-relative path (the metadata value). */
  path: string
}

/** The metadata key for the Nth document (1-based): document, document2, … */
export function documentKey(index: number): string {
  return index === 1 ? 'document' : `document${index}`
}

/** Parse the 1-based index out of a document key, or null if not one. */
function parseDocumentIndex(key: string): number | null {
  if (key === 'document') return 1
  const m = /^document(\d+)$/.exec(key)
  if (!m) return null
  const n = Number(m[1])
  return Number.isInteger(n) && n >= 1 ? n : null
}

/**
 * The attached documents, ordered by their numeric suffix. Tolerates
 * non-gapless / externally-edited schemes (e.g. `document`, `document3`).
 */
export function listDocuments(meta: Record<string, string> | undefined | null): AttachedDocument[] {
  if (!meta) return []
  const out: { key: string; path: string; index: number }[] = []
  for (const [key, value] of Object.entries(meta)) {
    const index = parseDocumentIndex(key)
    if (index !== null && typeof value === 'string' && value.length > 0) {
      out.push({ key, path: value, index })
    }
  }
  out.sort((a, b) => a.index - b.index)
  return out.map(({ key, path }) => ({ key, path }))
}

/** Number of documents attached to a transaction. */
export function documentCount(meta: Record<string, string> | undefined | null): number {
  return listDocuments(meta).length
}

/**
 * Return a new meta with `path` appended as the next document slot, keeping the
 * scheme gapless. Non-document keys are preserved untouched.
 */
export function addDocument(
  meta: Record<string, string>,
  path: string,
): Record<string, string> {
  const docs = listDocuments(meta)
  const next = docs.length + 1
  const nonDocs = Object.fromEntries(
    Object.entries(meta).filter(([k]) => parseDocumentIndex(k) === null),
  )
  const renumbered: Record<string, string> = {}
  docs.forEach((d, i) => { renumbered[documentKey(i + 1)] = d.path })
  renumbered[documentKey(next)] = path
  return { ...nonDocs, ...renumbered }
}

/**
 * Return a new meta with the document at `key` removed and the remaining
 * documents re-compacted to a gapless `document`/`document2`/… scheme.
 */
export function removeDocument(
  meta: Record<string, string>,
  key: string,
): Record<string, string> {
  const remaining = listDocuments(meta).filter((d) => d.key !== key)
  const nonDocs = Object.fromEntries(
    Object.entries(meta).filter(([k]) => parseDocumentIndex(k) === null),
  )
  const renumbered: Record<string, string> = {}
  remaining.forEach((d, i) => { renumbered[documentKey(i + 1)] = d.path })
  return { ...nonDocs, ...renumbered }
}
