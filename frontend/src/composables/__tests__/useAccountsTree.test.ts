import { useAccountsTree, formatBalances } from '@/composables/useAccountsTree'
import { toMoney } from '@/utils/money'
import type { AccountDetails } from '@/services/generated-api'
import type { AccountTreeNode, AccountFilters } from '@/types/accounts'

// ---------------------------------------------------------------------------
// Factory helpers — build inputs from domain knowledge, not implementation
// ---------------------------------------------------------------------------

function makeAccount(
  name: string,
  overrides: Partial<Omit<AccountDetails, 'name'>> = {},
): AccountDetails {
  return {
    name,
    open_date: '2024-01-01',
    close_date: overrides.close_date ?? null,
    currencies: overrides.currencies ?? [
      { currency: 'USD', transaction_count: 5, last_transaction_date: '2024-06-01', balance: toMoney(100)},
    ],
    metadata: overrides.metadata ?? {},
  }
}

// ---------------------------------------------------------------------------
// buildTree
// ---------------------------------------------------------------------------

describe('buildTree', () => {
  const { buildTree } = useAccountsTree()

  it('creates a single root from one account', () => {
    const roots = buildTree([makeAccount('Assets:Bank:Checking')])

    // Should have an "Assets" root
    const assets = roots.find(r => r.name === 'Assets')
    expect(assets).toBeDefined()
    expect(assets!.type).toBe('Assets')
    expect(assets!.depth).toBe(0)
  })

  it('creates virtual intermediate nodes when there are gaps in the hierarchy', () => {
    // Only "Assets:Bank:Checking" is a real account — "Assets" and "Assets:Bank" have
    // no open directives in Beancount, so they should be virtual.
    const roots = buildTree([makeAccount('Assets:Bank:Checking')])
    const assets = roots.find(r => r.name === 'Assets')!

    expect(assets.isVirtual).toBe(true)
    expect(assets.fullPath).toBe('Assets')

    const bank = assets.children.find(c => c.name === 'Bank')
    expect(bank).toBeDefined()
    expect(bank!.isVirtual).toBe(true)
    expect(bank!.fullPath).toBe('Assets:Bank')

    const checking = bank!.children.find(c => c.name === 'Checking')
    expect(checking).toBeDefined()
    expect(checking!.isVirtual).toBe(false)
  })

  it('sets correct depth for each level', () => {
    const roots = buildTree([makeAccount('Expenses:Food:Groceries')])
    const expenses = roots.find(r => r.name === 'Expenses')!
    const food = expenses.children[0]
    const groceries = food.children[0]

    expect(expenses.depth).toBe(0)
    expect(food.depth).toBe(1)
    expect(groceries.depth).toBe(2)
  })

  it('sorts roots in Beancount type order: Assets, Liabilities, Equity, Income, Expenses', () => {
    const roots = buildTree([
      makeAccount('Expenses:Food'),
      makeAccount('Assets:Cash'),
      makeAccount('Income:Salary'),
      makeAccount('Liabilities:CreditCard'),
      makeAccount('Equity:Opening'),
    ])

    const names = roots.map(r => r.name)
    expect(names).toEqual(['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses'])
  })

  it('sorts children alphabetically within each parent', () => {
    const roots = buildTree([
      makeAccount('Assets:Zelle'),
      makeAccount('Assets:Bank'),
      makeAccount('Assets:Cash'),
    ])
    const assets = roots.find(r => r.name === 'Assets')!
    const childNames = assets.children.map(c => c.name)

    expect(childNames).toEqual(['Bank', 'Cash', 'Zelle'])
  })

  it('aggregates balances from children up to parents', () => {
    const roots = buildTree([
      makeAccount('Assets:Bank:Checking', {
        currencies: [{ currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(500)}],
      }),
      makeAccount('Assets:Bank:Savings', {
        currencies: [{ currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(300)}],
      }),
    ])

    const assets = roots.find(r => r.name === 'Assets')!
    const bank = assets.children.find(c => c.name === 'Bank')!

    // Bank should aggregate Checking + Savings = 800 USD
    const bankUsd = bank.aggregatedBalances.find(b => b.currency === 'USD')
    expect(bankUsd).toBeDefined()
    expect(bankUsd!.balance).toBe(toMoney(800))

    // Assets should also aggregate to 800 USD
    const assetsUsd = assets.aggregatedBalances.find(b => b.currency === 'USD')
    expect(assetsUsd!.balance).toBe(toMoney(800))
  })

  it('aggregates multiple currencies separately', () => {
    const roots = buildTree([
      makeAccount('Assets:US', {
        currencies: [{ currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(100)}],
      }),
      makeAccount('Assets:EU', {
        currencies: [{ currency: 'EUR', transaction_count: 1, last_transaction_date: null, balance: toMoney(200)}],
      }),
    ])

    const assets = roots.find(r => r.name === 'Assets')!
    const usd = assets.aggregatedBalances.find(b => b.currency === 'USD')
    const eur = assets.aggregatedBalances.find(b => b.currency === 'EUR')

    expect(usd!.balance).toBe(toMoney(100))
    expect(eur!.balance).toBe(toMoney(200))
  })

  it('marks closed accounts correctly', () => {
    const roots = buildTree([
      makeAccount('Assets:OldBank', { close_date: '2023-12-31' }),
    ])
    const oldBank = roots.find(r => r.name === 'Assets')!.children[0]

    expect(oldBank.status).toBe('closed')
    expect(oldBank.closeDate).toBe('2023-12-31')
  })

  it('marks open accounts correctly', () => {
    const roots = buildTree([makeAccount('Assets:Bank')])
    const bank = roots.find(r => r.name === 'Assets')!.children[0]

    expect(bank.status).toBe('open')
    expect(bank.closeDate).toBeNull()
  })

  it('populates metadata, excluding beancount internal keys (filename, lineno)', () => {
    const roots = buildTree([
      makeAccount('Assets:Bank', {
        metadata: { description: 'Main bank', filename: '/ledger.beancount', lineno: '42', custom_field: 'value' },
      }),
    ])
    const bank = roots.find(r => r.name === 'Assets')!.children[0]

    expect(bank.metadata).toHaveProperty('description', 'Main bank')
    expect(bank.metadata).toHaveProperty('custom_field', 'value')
    expect(bank.metadata).not.toHaveProperty('filename')
    expect(bank.metadata).not.toHaveProperty('lineno')
  })

  it('extracts notes from metadata.description', () => {
    const roots = buildTree([
      makeAccount('Assets:Bank', { metadata: { description: 'My checking account' } }),
    ])
    const bank = roots.find(r => r.name === 'Assets')!.children[0]

    expect(bank.notes).toBe('My checking account')
  })

  it('builds currency badges from account currencies', () => {
    const roots = buildTree([
      makeAccount('Assets:Multi', {
        currencies: [
          { currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(100)},
          { currency: 'EUR', transaction_count: 1, last_transaction_date: null, balance: toMoney(50)},
        ],
      }),
    ])
    const multi = roots.find(r => r.name === 'Assets')!.children[0]

    expect(multi.currencyBadges).toContain('USD')
    expect(multi.currencyBadges).toContain('EUR')
  })

  it('returns an empty array when given no accounts', () => {
    expect(buildTree([])).toEqual([])
  })

  it('does not duplicate a node that is both a real account and a parent', () => {
    // "Assets:Bank" exists as a real account AND is parent of "Assets:Bank:Checking"
    const roots = buildTree([
      makeAccount('Assets:Bank', {
        currencies: [{ currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(50)}],
      }),
      makeAccount('Assets:Bank:Checking', {
        currencies: [{ currency: 'USD', transaction_count: 1, last_transaction_date: null, balance: toMoney(200)}],
      }),
    ])
    const assets = roots.find(r => r.name === 'Assets')!

    // Assets should have exactly one "Bank" child, not two
    const bankChildren = assets.children.filter(c => c.name === 'Bank')
    expect(bankChildren).toHaveLength(1)

    const bank = bankChildren[0]
    expect(bank.isVirtual).toBe(false)
    expect(bank.children).toHaveLength(1)
    expect(bank.children[0].name).toBe('Checking')

    // Bank's aggregated balance = own 50 + child 200 = 250
    const bankUsd = bank.aggregatedBalances.find(b => b.currency === 'USD')
    expect(bankUsd!.balance).toBe(toMoney(250))
  })
})

// ---------------------------------------------------------------------------
// flattenForDisplay
// ---------------------------------------------------------------------------

describe('flattenForDisplay', () => {
  const { buildTree, flattenForDisplay } = useAccountsTree()

  function buildSimpleTree(): AccountTreeNode[] {
    return buildTree([
      makeAccount('Assets:Bank:Checking'),
      makeAccount('Assets:Cash'),
      makeAccount('Expenses:Food'),
    ])
  }

  it('shows only root nodes when nothing is expanded', () => {
    const roots = buildSimpleTree()
    const flat = flattenForDisplay(roots, new Set())

    // Only roots visible
    const names = flat.map(n => n.name)
    expect(names).toEqual(['Assets', 'Expenses'])
  })

  it('shows children of expanded nodes', () => {
    const roots = buildSimpleTree()
    const flat = flattenForDisplay(roots, new Set(['Assets']))

    const names = flat.map(n => n.name)
    expect(names).toContain('Bank')
    expect(names).toContain('Cash')
    // Checking should NOT appear because "Assets:Bank" is not expanded
    expect(names).not.toContain('Checking')
  })

  it('shows deeply nested children when all ancestors are expanded', () => {
    const roots = buildSimpleTree()
    const flat = flattenForDisplay(roots, new Set(['Assets', 'Assets:Bank']))

    const names = flat.map(n => n.name)
    expect(names).toContain('Checking')
  })

  it('preserves parent-before-children order', () => {
    const roots = buildSimpleTree()
    const flat = flattenForDisplay(roots, new Set(['Assets', 'Assets:Bank']))

    const assetsIdx = flat.findIndex(n => n.fullPath === 'Assets')
    const bankIdx = flat.findIndex(n => n.fullPath === 'Assets:Bank')
    const checkingIdx = flat.findIndex(n => n.fullPath === 'Assets:Bank:Checking')

    expect(assetsIdx).toBeLessThan(bankIdx)
    expect(bankIdx).toBeLessThan(checkingIdx)
  })
})

// ---------------------------------------------------------------------------
// filterTree
// ---------------------------------------------------------------------------

describe('filterTree', () => {
  const { buildTree, filterTree } = useAccountsTree()

  function buildMixedTree(): AccountTreeNode[] {
    return buildTree([
      makeAccount('Assets:Bank:Checking'),
      makeAccount('Assets:OldAccount', { close_date: '2023-06-01' }),
      makeAccount('Expenses:Food:Groceries'),
      makeAccount('Income:Salary'),
    ])
  }

  const allFilters: AccountFilters = { search: '', type: 'All', status: 'All' }

  it('returns all roots when no filters are active', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, allFilters)
    const rootNames = filtered.map(r => r.name)

    expect(rootNames).toContain('Assets')
    expect(rootNames).toContain('Expenses')
    expect(rootNames).toContain('Income')
  })

  it('filters by account type', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, { search: '', type: 'Expenses', status: 'All' })

    // Only Expenses root should remain
    expect(filtered).toHaveLength(1)
    expect(filtered[0].name).toBe('Expenses')
  })

  it('filters by status (closed)', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, { search: '', type: 'All', status: 'closed' })

    // Should find the closed account somewhere in the tree
    function findAll(nodes: AccountTreeNode[]): AccountTreeNode[] {
      return nodes.flatMap(n => [n, ...findAll(n.children)])
    }
    const allNodes = findAll(filtered)
    const realNodes = allNodes.filter(n => !n.isVirtual)

    expect(realNodes.every(n => n.status === 'closed')).toBe(true)
    expect(realNodes.some(n => n.name === 'OldAccount')).toBe(true)
  })

  it('filters by search string (case-insensitive)', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, { search: 'grocer', type: 'All', status: 'All' })

    function findAll(nodes: AccountTreeNode[]): AccountTreeNode[] {
      return nodes.flatMap(n => [n, ...findAll(n.children)])
    }
    const allNodes = findAll(filtered)
    const realNodes = allNodes.filter(n => !n.isVirtual)

    expect(realNodes).toHaveLength(1)
    expect(realNodes[0].name).toBe('Groceries')
  })

  it('keeps virtual parents of matching children', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, { search: 'Checking', type: 'All', status: 'All' })

    // Assets root and Bank intermediate should be kept as ancestors
    expect(filtered).toHaveLength(1)
    expect(filtered[0].name).toBe('Assets')

    const bank = filtered[0].children.find(c => c.name === 'Bank')
    expect(bank).toBeDefined()
    expect(bank!.children.some(c => c.name === 'Checking')).toBe(true)
  })

  it('returns empty array when nothing matches', () => {
    const roots = buildMixedTree()
    const filtered = filterTree(roots, { search: 'nonexistent', type: 'All', status: 'All' })

    expect(filtered).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// expand/collapse state
// ---------------------------------------------------------------------------

describe('expand/collapse', () => {
  const { expandedIds, buildTree, toggleExpand, expandAll, collapseAll } = useAccountsTree()

  beforeEach(() => {
    collapseAll()
  })

  it('toggleExpand adds an id then removes it on second call', () => {
    toggleExpand('Assets')
    expect(expandedIds.value.has('Assets')).toBe(true)

    toggleExpand('Assets')
    expect(expandedIds.value.has('Assets')).toBe(false)
  })

  it('expandAll expands every node that has children', () => {
    const roots = buildTree([
      makeAccount('Assets:Bank:Checking'),
      makeAccount('Expenses:Food'),
    ])
    expandAll(roots)

    expect(expandedIds.value.has('Assets')).toBe(true)
    expect(expandedIds.value.has('Assets:Bank')).toBe(true)
    expect(expandedIds.value.has('Expenses')).toBe(true)
    // Leaf nodes should NOT be in expanded set (they have no children to show)
    expect(expandedIds.value.has('Assets:Bank:Checking')).toBe(false)
    expect(expandedIds.value.has('Expenses:Food')).toBe(false)
  })

  it('collapseAll removes all expanded ids', () => {
    toggleExpand('Assets')
    toggleExpand('Expenses')
    collapseAll()

    expect(expandedIds.value.size).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// formatBalances
// ---------------------------------------------------------------------------

describe('formatBalances', () => {
  it('returns a dash for empty balances', () => {
    const result = formatBalances([])
    expect(result.display).toBe('—')
    expect(result.overflow).toBe(0)
  })

  it('formats a single currency with 2 decimal places', () => {
    const result = formatBalances([{ currency: 'USD', balance: toMoney(1234.5) }])
    // Should contain the formatted number and currency code
    expect(result.display).toContain('USD')
    expect(result.display).toContain('1,234.50') // or locale-appropriate
    expect(result.overflow).toBe(0)
  })

  it('shows at most maxShow currencies and reports overflow', () => {
    const balances = [
      { currency: 'USD', balance: toMoney(100)},
      { currency: 'EUR', balance: toMoney(200)},
      { currency: 'GBP', balance: toMoney(50)},
    ]
    const result = formatBalances(balances, 2)

    expect(result.overflow).toBe(1)
    // The two largest by absolute value should be shown (EUR=200, USD=100)
    expect(result.display).toContain('EUR')
    expect(result.display).toContain('USD')
    expect(result.display).not.toContain('GBP')
  })

  it('sorts by absolute balance value (negative balances ranked correctly)', () => {
    const balances = [
      { currency: 'USD', balance: toMoney(10) },
      { currency: 'EUR', balance: toMoney(-500) },
    ]
    const result = formatBalances(balances, 1)

    // EUR has higher absolute value, should be the one shown
    expect(result.display).toContain('EUR')
    expect(result.display).not.toContain('USD')
    expect(result.overflow).toBe(1)
  })
})
