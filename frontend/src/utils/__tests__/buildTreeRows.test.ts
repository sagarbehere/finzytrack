// tree-table hierarchy: rows with account-style paths become a tree, grouped by
// currency first, with exact decimal totals on every parent and group.
import { describe, it, expect } from 'vitest'
import { buildTreeRows, type TreeNode } from '@/utils/buildTreeRows'

const rows = [
  { account: 'Assets:Liquid:Savings:AxisBank:NRE', currency: 'INR', balance: 100.1, name: 'Axis NRE' },
  { account: 'Assets:Liquid:Savings:AxisBank:NRO', currency: 'INR', balance: 200.2, name: null },
  { account: 'Assets:Liquid:Savings:AxisBank:RFC', currency: 'USD', balance: 5, name: null },
  { account: 'Assets:Liquid:Checking:TechCU', currency: 'USD', balance: '0.1', name: 'TechCU Checking' },
]

const opts = { pathField: 'account', groupBy: 'currency', skipLevels: 1, labelField: 'name', sumKeys: ['balance'] }

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
      pathField: 'account', sumKeys: ['balance'],
    })
    expect(outline(tree)).toEqual(['A [branch] 3', '  B [leaf] 1', '  C [leaf] 2'])
  })

  it('adds an account’s own balance to its sub-accounts’ total', () => {
    const tree = buildTreeRows(
      [{ account: 'X:Broker', balance: 10, name: 'Broker cash' }, { account: 'X:Broker:Fund', balance: 5 }],
      { pathField: 'account', skipLevels: 1, labelField: 'name', sumKeys: ['balance'] },
    )
    expect(outline(tree)).toEqual(['Broker cash [branch] 15', '  Fund [leaf] 5'])
  })
})
