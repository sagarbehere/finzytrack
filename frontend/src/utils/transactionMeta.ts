/**
 * Single source of truth for which transaction-metadata keys are *persisted*.
 *
 * Beancount stamps `filename`, `lineno`, and `__tolerances__` onto every entry
 * at load time. These are parser internals, not user content — they are
 * stripped before a transaction is written back, so they must not count toward
 * "is this transaction modified?" either. Using one filter for both the write
 * path (useTransactionUpdater) and edit-detection (transactionModification)
 * guarantees the two never disagree: a transaction is "modified" iff the
 * representation we would persist differs from the one we loaded.
 */
export const INTERNAL_META_KEYS = new Set(['filename', 'lineno', '__tolerances__'])

/**
 * The persisted view of a metadata dict: parser-internal keys dropped, values
 * coerced to strings (Beancount metadata values are always strings on the
 * wire). Key order is preserved; callers that need order-independent equality
 * should sort.
 */
export function filterInternalMetadata(
  meta: Record<string, any> | undefined | null,
): Record<string, string> {
  const clean: Record<string, string> = {}
  for (const [key, value] of Object.entries(meta || {})) {
    if (INTERNAL_META_KEYS.has(key)) continue
    clean[key] = String(value)
  }
  return clean
}
