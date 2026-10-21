/**
 * Sorting for recipe tables (RecipeTable). Sorts by a column's raw value, not its
 * formatted text, so "$1,234.56" sorts as 1234.56.
 */

export interface TableSort {
  key: string
  dir: 'asc' | 'desc'
}

export interface IndexedRow {
  row: Record<string, unknown>
  /** The row's position in the unsorted data. */
  index: number
}

function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || (typeof v === 'string' && v.trim() === '')
}

function asNumber(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  return null
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Ascending order of two non-empty values: numbers (and numeric strings) by
 * value and before text; everything else as text (ISO dates sort correctly). */
function compareValues(a: unknown, b: unknown): number {
  const na = asNumber(a)
  const nb = asNumber(b)
  if (na !== null && nb !== null) return na - nb
  if (na !== null) return -1
  if (nb !== null) return 1
  return collator.compare(String(a), String(b))
}

/**
 * The rows in display order. With no sort, the data's own order. Otherwise:
 * - empty cells go last in either direction;
 * - summary rows (`isTotal`) stay at the bottom, in their original order;
 * - with `currencyField` (a currency-aware column), rows sort within each
 *   currency and the currencies keep the order they first appear in, so amounts
 *   in different currencies are never compared;
 * - ties keep their original order.
 */
export function sortTableRows(
  data: readonly Record<string, unknown>[],
  sort: TableSort | null,
  currencyField?: string,
): IndexedRow[] {
  const rows = data.map((row, index) => ({ row, index }))
  if (!sort) return rows
  const totals = rows.filter((r) => r.row.isTotal)
  const body = rows.filter((r) => !r.row.isTotal)

  const groupOrder = new Map<string, number>()
  if (currencyField) {
    for (const r of body) {
      const g = String(r.row[currencyField] ?? '')
      if (!groupOrder.has(g)) groupOrder.set(g, groupOrder.size)
    }
  }
  const sign = sort.dir === 'asc' ? 1 : -1

  body.sort((x, y) => {
    if (currencyField) {
      const gx = groupOrder.get(String(x.row[currencyField] ?? ''))!
      const gy = groupOrder.get(String(y.row[currencyField] ?? ''))!
      if (gx !== gy) return gx - gy
    }
    const a = x.row[sort.key]
    const b = y.row[sort.key]
    const ea = isEmpty(a)
    const eb = isEmpty(b)
    if (ea || eb) return ea === eb ? x.index - y.index : ea ? 1 : -1
    return sign * compareValues(a, b) || x.index - y.index
  })
  return [...body, ...totals]
}
