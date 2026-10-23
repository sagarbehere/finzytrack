import { add, toMoney, zero, type Money } from '@/utils/money'

/**
 * Turns flat rows carrying a hierarchical path (e.g. an account name
 * `Assets:Liquid:Checking:BofA`) into a tree for the `tree-table` visualization.
 *
 * - Every path prefix becomes a node; a row's own values attach to its node.
 * - Optional `groupBy` adds a top level per distinct value of that field (e.g.
 *   one per currency), so totals never mix values that must not be summed.
 * - Parent and group nodes sum the `sumKeys` of everything beneath them (decimal
 *   arithmetic — see dev-docs/money-types.md). Other fields show on a parent only
 *   when every row beneath agrees (e.g. the currency); otherwise they are empty.
 * - A parent's `pathField` holds its own path prefix, so links templated on
 *   `{{row.<pathField>}}` work at every level; group nodes have an empty path.
 * - Children keep the order in which rows first mention them.
 */

export type TreeNodeKind = 'group' | 'branch' | 'leaf'

export interface TreeNode {
  id: string
  label: string
  kind: TreeNodeKind
  depth: number
  /** Values shown for this node: the row itself for a leaf, aggregates for parents. */
  row: Record<string, unknown>
  children: TreeNode[]
}

export interface TreeOptions {
  pathField: string
  separator?: string
  /** Leading path segments to leave out of the hierarchy (e.g. 1 drops `Assets`). */
  skipLevels?: number
  groupBy?: string
  /** Row field holding a leaf's display label; falls back to its last path segment. */
  labelField?: string
  sumKeys: string[]
}

interface Building {
  node: TreeNode
  ownRow: Record<string, unknown> | null
  childIndex: Map<string, Building>
  children: Building[]
}

function newBuilding(id: string, label: string, kind: TreeNodeKind, depth: number): Building {
  return { node: { id, label, kind, depth, row: {}, children: [] }, ownRow: null, childIndex: new Map(), children: [] }
}

function child(parent: Building, key: string, make: () => Building): Building {
  let found = parent.childIndex.get(key)
  if (!found) {
    found = make()
    parent.childIndex.set(key, found)
    parent.children.push(found)
  }
  return found
}

const UNSET = Symbol('unset')
const MIXED = Symbol('mixed')

/** Fill `node.row` bottom-up; returns every source row beneath (for "all agree"). */
function finish(b: Building, opts: TreeOptions): Record<string, unknown>[] {
  const below: Record<string, unknown>[] = b.ownRow ? [b.ownRow] : []
  for (const c of b.children) below.push(...finish(c, opts))
  b.node.children = b.children.map((c) => c.node)

  if (b.children.length === 0 && b.ownRow) {
    b.node.row = { ...b.ownRow }
    return below
  }

  const common: Record<string, unknown> = {}
  const seen: Record<string, unknown> = {}
  for (const r of below) {
    for (const [k, v] of Object.entries(r)) {
      const prev = k in seen ? seen[k] : UNSET
      seen[k] = prev === UNSET || prev === v ? v : MIXED
    }
  }
  for (const [k, v] of Object.entries(seen)) {
    if (v !== MIXED && !opts.sumKeys.includes(k)) common[k] = v
  }
  for (const key of opts.sumKeys) {
    let total: Money = zero()
    let any = false
    for (const r of below) {
      const v = r[key]
      if (typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)))) {
        total = add(total, toMoney(v))
        any = true
      }
    }
    common[key] = any ? total : null
  }
  b.node.row = common
  return below
}

export function buildTreeRows(rows: Record<string, unknown>[], opts: TreeOptions): TreeNode[] {
  const sep = opts.separator ?? ':'
  const skip = Math.max(0, opts.skipLevels ?? 0)
  const root = newBuilding('', '', 'branch', -1)

  for (const row of rows) {
    const fullPath = String(row[opts.pathField] ?? '')
    const segments = fullPath === '' ? [] : fullPath.split(sep)
    let parent = root
    let depth = 0
    let idPrefix = ''

    if (opts.groupBy) {
      const group = String(row[opts.groupBy] ?? '')
      idPrefix = `${group}\u0000`
      parent = child(root, idPrefix, () => newBuilding(idPrefix, group, 'group', 0))
      depth = 1
    }

    // Shown levels start after the skipped prefix; a path no longer than the
    // skip still gets one node (its full path), so no row is ever dropped.
    const start = Math.min(skip, Math.max(0, segments.length - 1))
    for (let i = start; i < segments.length; i++) {
      const id = idPrefix + segments.slice(0, i + 1).join(sep)
      parent = child(parent, id, () => newBuilding(id, segments[i], 'branch', depth))
      depth++
    }
    parent.ownRow = row
  }

  const finishTop = (b: Building) => {
    finish(b, opts)
    if (b.node.kind === 'group') {
      b.node.row[opts.pathField] = ''
      if (opts.groupBy) b.node.row[opts.groupBy] = b.node.label
    }
  }
  for (const b of root.children) finishTop(b)

  // Kinds and labels are settled once the shape is known: a node with children
  // is a branch (even if it also has its own row). Any node with its own row
  // takes its label from labelField when present.
  const settle = (b: Building) => {
    for (const c of b.children) settle(c)
    if (b.node.kind === 'group') return
    if (b.children.length === 0) b.node.kind = 'leaf'
    const label = opts.labelField ? b.ownRow?.[opts.labelField] : undefined
    if (typeof label === 'string' && label !== '') b.node.label = label
    const path = b.node.id.slice(b.node.id.indexOf('\u0000') + 1)
    b.node.row[opts.pathField] = path
  }
  for (const b of root.children) settle(b)

  return root.children.map((b) => b.node)
}
