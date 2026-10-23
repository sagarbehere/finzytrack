// tree-table hierarchy: rows with account-style paths become a tree, grouped by
// currency first, with exact decimal totals on every parent and group.
import { describe, it, expect } from 'vitest'
import {
  buildTreeRows, foldSingleChildChains, filterTree, sortTree, computeShares, flattenTree, maxDepth, type TreeNode,
} from '@/utils/buildTreeRows'

const rows = [
  { account: 'Assets:Liquid:Savings:AxisBank:NRE', currency: 'INR', balance: 100.1, name: 'Axis NRE' },
  { account: 'Assets:Liquid:Savings:AxisBank:NRO', currency: 'INR', balance: 200.2, name: null },
  { account: 'Assets:Liquid:Savings:AxisBank:RFC', currency: 'USD', balance: 5, name: null },
  { account: 'Assets:Liquid:Checking:TechCU', currency: 'USD', balance: '0.1', name: 'TechCU Checking' },
]

const opts = { pathField: 'account', groupBy: 'currency', skipLevels: 1, labelField: 'name', aggregates: { balance: 'sum' as const } }

/** Compact view: "label [kind] balance" lines, indented by depth. */
function outline(nodes: TreeNode[]): string[] {
  return nodes.flatMap((n) => [
    `${'  '.repeat(n.depth)}${n.label} [${n.kind}] ${String(n.row.balance)}`,
    ...outline(n.children),
  ])
}

describe('buildTreeRows', () => {
  it('groups by currency, drops skipped levels, and totals every parent exactly', () => {
    expect(outline(buildTreeRows(rows, opts))).toEqual([
      'INR [group] 300.3',
      '  Liquid [branch] 300.3',
      '    Savings [branch] 300.3',
      '      AxisBank [branch] 300.3',
      '        Axis NRE [leaf] 100.1',
      '        NRO [leaf] 200.2',
      'USD [group] 5.1',
      '  Liquid [branch] 5.1',
      '    Savings [branch] 5',
      '      AxisBank [branch] 5',
      '        RFC [leaf] 5',
      '    Checking [branch] 0.1',
      '      TechCU Checking [leaf] 0.1',
    ])
  })

  it('gives each parent its own path (for links) and the values its rows agree on', () => {
    const [inr] = buildTreeRows(rows, opts)
    expect(inr.row).toMatchObject({ account: '', currency: 'INR' })
    const axis = inr.children[0].children[0].children[0]
    expect(axis.row.account).toBe('Assets:Liquid:Savings:AxisBank')
    expect(axis.row.currency).toBe('INR')
    expect(axis.row.name).toBeUndefined() // rows beneath disagree → not shown
  })

  it('keeps every level when nothing is skipped or grouped', () => {
    const tree = buildTreeRows([{ account: 'A:B', balance: 1 }, { account: 'A:C', balance: 2 }], {
      pathField: 'account', aggregates: { balance: 'sum' as const },
    })
    expect(outline(tree)).toEqual(['A [branch] 3', '  B [leaf] 1', '  C [leaf] 2'])
  })

  it('adds an account’s own balance to its sub-accounts’ total', () => {
    const tree = buildTreeRows(
      [{ account: 'X:Broker', balance: 10, name: 'Broker cash' }, { account: 'X:Broker:Fund', balance: 5 }],
      { pathField: 'account', skipLevels: 1, labelField: 'name', aggregates: { balance: 'sum' as const } },
    )
    expect(outline(tree)).toEqual(['Broker cash [branch] 15', '  Fund [leaf] 5'])
  })

  it('aggregates max and min (dates as text) on parents', () => {
    const tree = buildTreeRows(
      [{ account: 'A:B', last: '2026-01-05' }, { account: 'A:C', last: '2026-03-01' }, { account: 'A:D', last: null }],
      { pathField: 'account', aggregates: { last: 'max' as const } },
    )
    expect(tree[0].row.last).toBe('2026-03-01')
    const earliest = buildTreeRows([{ account: 'A:B', n: 3 }, { account: 'A:C', n: 10 }], {
      pathField: 'account', aggregates: { n: 'min' as const },
    })
    expect(earliest[0].row.n).toBe(3)
  })
})

describe('leafOnly', () => {
  it('keeps a per-row field off parent rows even when every row beneath agrees', () => {
    const tree = buildTreeRows(
      [{ account: 'A:B', status: 'Passed' }, { account: 'A:C', status: 'Passed' }],
      { pathField: 'account', aggregates: {}, leafOnly: ['status'] },
    )
    expect(tree[0].row.status).toBeUndefined()
    expect(tree[0].children.map((c) => c.row.status)).toEqual(['Passed', 'Passed'])
  })
})

describe('foldSingleChildChains', () => {
  it('folds single-child parents into one row and renumbers depths', () => {
    const tree = buildTreeRows(
      [{ account: 'Assets:Bonds:TD:IBonds', currency: 'USD', balance: 60 }, { account: 'Assets:Bank:A', currency: 'USD', balance: 1 }, { account: 'Assets:Bank:B', currency: 'USD', balance: 2 }],
      { ...opts, labelField: undefined },
    )
    expect(outline(foldSingleChildChains(tree))).toEqual([
      'USD [group] 63',
      '  Bonds › TD › IBonds [leaf] 60',
      '  Bank [branch] 3',
      '    A [leaf] 1',
      '    B [leaf] 2',
    ])
  })

  it('never folds a parent that has its own row (its value would be hidden)', () => {
    const tree = buildTreeRows(
      [{ account: 'X:Broker', balance: 10 }, { account: 'X:Broker:Fund', balance: 5 }],
      { pathField: 'account', skipLevels: 1, aggregates: { balance: 'sum' as const } },
    )
    expect(outline(foldSingleChildChains(tree))).toEqual(['Broker [branch] 15', '  Fund [leaf] 5'])
  })
})

describe('filterTree', () => {
  const tree = buildTreeRows(rows, opts)
  it('keeps matches with their ancestors; totals stay those of the real accounts', () => {
    expect(outline(filterTree(tree, 'techcu'))).toEqual([
      'USD [group] 5.1',
      '  Liquid [branch] 5.1',
      '    Checking [branch] 0.1',
      '      TechCU Checking [leaf] 0.1',
    ])
  })
  it('keeps everything beneath a matching parent', () => {
    expect(outline(filterTree(tree, 'axisbank')).filter((l) => l.includes('[leaf]'))).toEqual([
      '        Axis NRE [leaf] 100.1',
      '        NRO [leaf] 200.2',
      '        RFC [leaf] 5',
    ])
  })
  it('returns the tree unchanged for an empty query', () => {
    expect(filterTree(tree, '  ')).toBe(tree)
  })
})

describe('sortTree', () => {
  it('sorts siblings at every level and keeps groups in place', () => {
    const tree = buildTreeRows(
      [
        { account: 'R:Small', currency: 'USD', balance: 1 },
        { account: 'R:Big', currency: 'USD', balance: 9 },
        { account: 'R:Small', currency: 'EUR', balance: 5 },
      ],
      { pathField: 'account', groupBy: 'currency', skipLevels: 1, aggregates: { balance: 'sum' as const } },
    )
    expect(outline(sortTree(tree, 'balance', 'desc'))).toEqual([
      'USD [group] 10', '  Big [leaf] 9', '  Small [leaf] 1',
      'EUR [group] 5', '  Small [leaf] 5',
    ])
    expect(outline(sortTree(tree, 'balance', 'asc')).slice(0, 3)).toEqual(['USD [group] 10', '  Small [leaf] 1', '  Big [leaf] 9'])
  })
})

describe('computeShares', () => {
  const tree = buildTreeRows(
    [{ account: 'R:A:X', currency: 'USD', balance: 30 }, { account: 'R:A:Y', currency: 'USD', balance: 10 }, { account: 'R:B', currency: 'USD', balance: 60 }],
    { pathField: 'account', groupBy: 'currency', skipLevels: 1, aggregates: { balance: 'sum' as const } },
  )
  const byLabel = (shares: Map<string, number | null>) =>
    Object.fromEntries(flattenTree(tree).map((n) => [n.label, shares.get(n.id)]))

  it('shares of the group total', () => {
    expect(byLabel(computeShares(tree, 'balance', 'total'))).toEqual({ USD: 1, A: 0.4, X: 0.3, Y: 0.1, B: 0.6 })
  })
  it('shares of the parent', () => {
    expect(byLabel(computeShares(tree, 'balance', 'parent'))).toEqual({ USD: null, A: 0.4, X: 0.75, Y: 0.25, B: 0.6 })
  })
})

describe('maxDepth', () => {
  it('is the deepest level', () => {
    expect(maxDepth(buildTreeRows(rows, opts))).toBe(4)
  })
})
