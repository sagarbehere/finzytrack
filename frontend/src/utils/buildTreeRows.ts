import { add, toMoney, zero, type Money } from '@/utils/money'

/**
 * Tree logic for the `tree-table` visualization: turns flat rows carrying a
 * hierarchical path (e.g. an account name `Assets:Liquid:Checking:BofA`) into a
 * tree, and the pure operations the table offers on it (fold, filter, sort,
 * shares, flatten).
 *
 * - Every path prefix becomes a node; a row's own values attach to its node.
 * - Optional `groupBy` adds a top level per distinct value of that field (e.g.
 *   one per currency), so totals never mix values that must not be summed.
 * - Parent and group nodes aggregate the `aggregates` columns over everything
 *   beneath them: `sum` in decimal arithmetic (dev-docs/money-types.md), `max` /
 *   `min` for the latest/earliest (numbers, or ISO dates compared as text). Other
 *   fields show on a parent only when every row beneath agrees (e.g. the
 *   currency); otherwise they are empty.
 * - A node's `path` (and `row[pathField]`) is its own path prefix, so links
 *   templated on `{{row.<pathField>}}` work at every level; group nodes have an
 *   empty path.
 * - Children keep the order in which rows first mention them.
 */

export type TreeNodeKind = 'group' | 'branch' | 'leaf'
export type Aggregate = 'sum' | 'max' | 'min'

export interface TreeNode {
  id: string
  label: string
  kind: TreeNodeKind
  depth: number
  /** Full path of this node ('' for a group). */
  path: string
  /** Whether a source row sits exactly on this node (vs. a pure path prefix). */
  hasOwnRow: boolean
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
  /** Row field holding a row's display label; falls back to its last path segment. */
  labelField?: string
  /** Column key → how parent and group rows combine it. */
  aggregates: Record<string, Aggregate>
}

interface Building {
  node: TreeNode
  ownRow: Record<string, unknown> | null
  childIndex: Map<string, Building>
  children: Building[]
}

const GROUP_SEP = '\u0000'

function newBuilding(id: string, label: string, kind: TreeNodeKind, depth: number): Building {
  return {
    node: { id, label, kind, depth, path: '', hasOwnRow: false, row: {}, children: [] },
    ownRow: null,
    childIndex: new Map(),
    children: [],
  }
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

function isNumeric(v: unknown): v is number | string {
  return typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)))
}

function aggregate(values: unknown[], how: Aggregate): unknown {
  const present = values.filter((v) => v !== null && v !== undefined && v !== '')
  if (present.length === 0) return null
  if (how === 'sum') {
    let total: Money = zero()
    let any = false
    for (const v of present) {
      if (isNumeric(v)) {
        total = add(total, toMoney(v))
        any = true
      }
    }
    return any ? total : null
  }
  // max / min: numbers numerically, anything else (ISO dates) as text.
  const numeric = present.every(isNumeric)
  const cmp = (a: unknown, b: unknown) => (numeric ? Number(a) - Number(b) : String(a).localeCompare(String(b)))
  return present.reduce((best, v) => ((how === 'max' ? cmp(v, best) > 0 : cmp(v, best) < 0) ? v : best))
}

const UNSET = Symbol('unset')
const MIXED = Symbol('mixed')

/** Fill `node.row` bottom-up; returns every source row beneath (for "all agree"). */
function finish(b: Building, opts: TreeOptions): Record<string, unknown>[] {
  const below: Record<string, unknown>[] = b.ownRow ? [b.ownRow] : []
  for (const c of b.children) below.push(...finish(c, opts))
  b.node.children = b.children.map((c) => c.node)
  b.node.hasOwnRow = b.ownRow !== null

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
    if (v !== MIXED && !(k in opts.aggregates)) common[k] = v
  }
  for (const [key, how] of Object.entries(opts.aggregates)) {
    common[key] = aggregate(below.map((r) => r[key]), how)
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
      idPrefix = `${group}${GROUP_SEP}`
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

  for (const b of root.children) {
    finish(b, opts)
    if (b.node.kind === 'group') {
      b.node.row[opts.pathField] = ''
      if (opts.groupBy) b.node.row[opts.groupBy] = b.node.label
    }
  }

  // Kinds, labels and paths are settled once the shape is known: a node with
  // children is a branch (even if it also has its own row). Any node with its
  // own row takes its label from labelField when present.
  const settle = (b: Building) => {
    for (const c of b.children) settle(c)
    if (b.node.kind === 'group') return
    if (b.children.length === 0) b.node.kind = 'leaf'
    const label = opts.labelField ? b.ownRow?.[opts.labelField] : undefined
    if (typeof label === 'string' && label !== '') b.node.label = label
    b.node.path = b.node.id.slice(b.node.id.indexOf(GROUP_SEP) + 1)
    b.node.row[opts.pathField] = b.node.path
  }
  for (const b of root.children) settle(b)

  return root.children.map((b) => b.node)
}

/** Separator shown between folded labels. */
export const FOLD_SEPARATOR = ' › '

/**
 * Fold chains of single-child parents into one row: `Bonds › TreasuryDirect ›
 * IBonds` instead of three rows repeating the same total. A parent folds only
 * when it has exactly one child and no row of its own (so no value is hidden);
 * group rows never fold. The folded row keeps the deepest node's identity
 * (path, link, values); depths are renumbered.
 */
export function foldSingleChildChains(nodes: TreeNode[]): TreeNode[] {
  const fold = (n: TreeNode, depth: number): TreeNode => {
    let current = n
    const labels = [n.label]
    while (current.kind !== 'group' && current.children.length === 1 && !current.hasOwnRow) {
      current = current.children[0]
      labels.push(current.label)
    }
    return {
      ...current,
      label: labels.join(FOLD_SEPARATOR),
      depth,
      children: current.children.map((c) => fold(c, depth + 1)),
    }
  }
  return nodes.map((n) => fold(n, n.depth))
}

/**
 * Keep the nodes whose label or path contains `query` (case-insensitive), with
 * their ancestors and — for a matching parent — everything beneath it. Totals
 * are left as they are (they describe the real accounts, not the filter).
 */
export function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  const q = query.trim().toLowerCase()
  if (!q) return nodes
  const matches = (n: TreeNode) => n.label.toLowerCase().includes(q) || n.path.toLowerCase().includes(q)
  const walk = (list: TreeNode[]): TreeNode[] =>
    list.flatMap((n) => {
      if (n.kind !== 'group' && matches(n)) return [n]
      const kept = walk(n.children)
      return kept.length ? [{ ...n, children: kept }] : []
    })
  return walk(nodes)
}

/**
 * Sort siblings at every level by `key` (numbers numerically, others as text),
 * keeping the hierarchy. Top-level group rows keep their order: their values
 * may not be comparable (e.g. totals in different currencies).
 */
export function sortTree(nodes: TreeNode[], key: string, dir: 'asc' | 'desc'): TreeNode[] {
  const sign = dir === 'asc' ? 1 : -1
  const value = (n: TreeNode) => n.row[key]
  const compare = (a: TreeNode, b: TreeNode) => {
    const va = value(a)
    const vb = value(b)
    const ea = va === null || va === undefined || va === ''
    const eb = vb === null || vb === undefined || vb === ''
    if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1 // blanks last either way
    if (isNumeric(va) && isNumeric(vb)) return sign * (Number(va) - Number(vb))
    return sign * String(va).localeCompare(String(vb))
  }
  const walk = (list: TreeNode[]): TreeNode[] =>
    [...list]
      .sort((a, b) => (a.kind === 'group' && b.kind === 'group' ? 0 : compare(a, b)))
      .map((n) => ({ ...n, children: walk(n.children) }))
  return walk(nodes)
}

/**
 * Each node's share of `key`: of its top-level total (`total` — the group, or the
 * sum of all top-level rows when there are no groups) or of its parent
 * (`parent`). Returns node id → fraction, or null where the base is zero/blank.
 * Shares are display values, so ordinary numbers are fine here.
 */
export function computeShares(
  nodes: TreeNode[],
  key: string,
  base: 'total' | 'parent',
): Map<string, number | null> {
  const out = new Map<string, number | null>()
  const num = (n: TreeNode) => (isNumeric(n.row[key]) ? Number(n.row[key]) : null)
  const ratio = (v: number | null, of: number | null) => (v === null || !of ? null : v / of)
  const hasGroups = nodes.some((n) => n.kind === 'group')
  const grand = nodes.reduce((s, n) => s + (num(n) ?? 0), 0)

  const walk = (list: TreeNode[], parent: number | null, top: number | null) => {
    for (const n of list) {
      const v = num(n)
      const topHere = top ?? (hasGroups ? v : grand)
      out.set(n.id, ratio(v, base === 'parent' ? parent ?? (hasGroups ? null : grand) : topHere))
      walk(n.children, v, topHere)
    }
  }
  walk(nodes, null, null)
  return out
}

/** All nodes in display order (depth-first), optionally only the expanded ones. */
export function flattenTree(nodes: TreeNode[], isExpanded: (n: TreeNode) => boolean = () => true): TreeNode[] {
  return nodes.flatMap((n) => [n, ...(n.children.length && isExpanded(n) ? flattenTree(n.children, isExpanded) : [])])
}

/** Deepest level in the tree (0 = only top-level rows). */
export function maxDepth(nodes: TreeNode[]): number {
  return nodes.reduce((m, n) => Math.max(m, n.depth, maxDepth(n.children)), 0)
}
