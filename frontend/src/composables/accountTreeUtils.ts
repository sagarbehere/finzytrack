import type { AccountDetails } from '@/services/generated-api'
import { ACCOUNT_TYPES, type AccountTreeNode, type AccountFilters, type AccountType, type AggregatedBalance } from '@/types/accounts'
import { getLocale } from '@/utils/currencyFormat'

/**
 * Extract the account type from the full path
 */
function getAccountType(fullPath: string): AccountType {
  const firstPart = fullPath.split(':')[0]
  if ((ACCOUNT_TYPES as readonly string[]).includes(firstPart)) {
    return firstPart as AccountType
  }
  return 'Assets' // Default fallback
}

/**
 * Build a hierarchical tree from flat account list
 */
export function buildTree(accounts: AccountDetails[]): AccountTreeNode[] {
  // Map to store all nodes by their full path
  const nodeMap = new Map<string, AccountTreeNode>()

  // First pass: create nodes for all accounts
  for (const account of accounts) {
    const parts = account.name.split(':')
    const depth = parts.length - 1
    const name = parts[parts.length - 1]

    // Calculate balances from currencies
    const aggregatedBalances: AggregatedBalance[] = account.currencies.map(c => ({
      currency: c.currency,
      balance: c.balance
    }))

    // Extract metadata, filtering out beancount internal keys
    const INTERNAL_KEYS = new Set(['filename', 'lineno'])
    const metadata: Record<string, string> = {}
    if (account.metadata) {
      for (const [k, v] of Object.entries(account.metadata)) {
        if (!INTERNAL_KEYS.has(k)) {
          metadata[k] = String(v)
        }
      }
    }

    const node: AccountTreeNode = {
      id: account.name,
      name,
      fullPath: account.name,
      depth,
      isVirtual: false,
      children: [],
      type: getAccountType(account.name),
      status: account.close_date ? 'closed' : 'open',
      openDate: account.open_date,
      closeDate: account.close_date || null,
      aggregatedBalances,
      notes: account.metadata?.description || null,
      currencyBadges: account.currencies.map(c => c.currency),
      declaredCurrencies: account.declared_currencies ?? [],
      metadata,
    }

    nodeMap.set(account.name, node)
  }

  // Second pass: create virtual parent nodes and build hierarchy
  for (const account of accounts) {
    const parts = account.name.split(':')

    // Create virtual parents if they don't exist
    for (let i = 1; i < parts.length; i++) {
      const parentPath = parts.slice(0, i).join(':')

      if (!nodeMap.has(parentPath)) {
        // Create virtual node
        const parentParts = parentPath.split(':')
        const virtualNode: AccountTreeNode = {
          id: parentPath,
          name: parentParts[parentParts.length - 1],
          fullPath: parentPath,
          depth: parentParts.length - 1,
          isVirtual: true,
          children: [],
          type: getAccountType(parentPath),
          status: 'open',
          openDate: null,
          closeDate: null,
          aggregatedBalances: [],
          notes: null,
          currencyBadges: [],
          declaredCurrencies: [],
          metadata: {},
        }
        nodeMap.set(parentPath, virtualNode)
      }
    }
  }

  // Third pass: link children to parents
  for (const [path, node] of nodeMap) {
    const parts = path.split(':')
    if (parts.length > 1) {
      const parentPath = parts.slice(0, -1).join(':')
      const parent = nodeMap.get(parentPath)
      if (parent) {
        parent.children.push(node)
      }
    }
  }

  // Sort children alphabetically at each level
  for (const node of nodeMap.values()) {
    node.children.sort((a, b) => a.name.localeCompare(b.name))
  }

  // Fourth pass: aggregate balances from children (post-order)
  function aggregateBalances(node: AccountTreeNode): void {
    // First aggregate all children
    for (const child of node.children) {
      aggregateBalances(child)
    }

    // Then aggregate children's balances into this node
    if (node.children.length > 0) {
      const balanceMap = new Map<string, number>()

      // Add own balances first
      for (const bal of node.aggregatedBalances) {
        balanceMap.set(bal.currency, (balanceMap.get(bal.currency) || 0) + bal.balance)
      }

      // Add children's balances
      for (const child of node.children) {
        for (const bal of child.aggregatedBalances) {
          balanceMap.set(bal.currency, (balanceMap.get(bal.currency) || 0) + bal.balance)
        }
      }

      // Convert back to array
      node.aggregatedBalances = Array.from(balanceMap.entries()).map(([currency, balance]) => ({
        currency,
        balance
      }))

      // Aggregate currency badges from children
      const allCurrencies = new Set(node.currencyBadges)
      for (const child of node.children) {
        for (const currency of child.currencyBadges) {
          allCurrencies.add(currency)
        }
      }
      node.currencyBadges = Array.from(allCurrencies)
    }
  }

  // Get root nodes and aggregate
  const roots: AccountTreeNode[] = []
  for (const [path, node] of nodeMap) {
    if (!path.includes(':') || path.split(':').length === 1) {
      // This is a root node (top-level account type)
      roots.push(node)
      aggregateBalances(node)
    }
  }

  // Sort roots by type order
  const typeOrder: readonly string[] = ACCOUNT_TYPES
  roots.sort((a, b) => typeOrder.indexOf(a.name) - typeOrder.indexOf(b.name))

  return roots
}

/**
 * Flatten tree for display based on expanded state
 */
export function flattenForDisplay(roots: AccountTreeNode[], expandedIds: Set<string>): AccountTreeNode[] {
  const result: AccountTreeNode[] = []

  function traverse(node: AccountTreeNode): void {
    result.push(node)

    if (node.children.length > 0 && expandedIds.has(node.id)) {
      for (const child of node.children) {
        traverse(child)
      }
    }
  }

  for (const root of roots) {
    traverse(root)
  }

  return result
}

/**
 * Filter tree nodes based on search and filter criteria
 */
export function filterTree(nodes: AccountTreeNode[], filters: AccountFilters): AccountTreeNode[] {
  const { search, type, status } = filters
  const searchLower = search.toLowerCase()

  function nodeMatches(node: AccountTreeNode): boolean {
    // Check search filter
    if (search && !node.fullPath.toLowerCase().includes(searchLower)) {
      return false
    }

    // Check type filter
    if (type !== 'All' && node.type !== type) {
      return false
    }

    // Check status filter
    if (status !== 'All' && node.status !== status) {
      return false
    }

    return true
  }

  function filterNode(node: AccountTreeNode): AccountTreeNode | null {
    // Filter children first
    const filteredChildren = node.children
      .map(filterNode)
      .filter((n): n is AccountTreeNode => n !== null)

    // Virtual nodes only appear if they have matching children — their own
    // status is synthetic and shouldn't influence filtering.
    if (node.isVirtual) {
      if (filteredChildren.length > 0) {
        return { ...node, children: filteredChildren }
      }
      return null
    }

    // Real nodes: include if they match or have matching children
    if (nodeMatches(node) || filteredChildren.length > 0) {
      return {
        ...node,
        children: filteredChildren
      }
    }

    return null
  }

  return nodes
    .map(filterNode)
    .filter((n): n is AccountTreeNode => n !== null)
}

/**
 * Format balances for display, showing top currencies
 */
export function formatBalances(balances: AggregatedBalance[], maxShow = 2): { display: string; overflow: number } {
  if (balances.length === 0) {
    return { display: '—', overflow: 0 }
  }

  // Sort by absolute balance value
  const sorted = [...balances].sort((a, b) => Math.abs(b.balance) - Math.abs(a.balance))
  const shown = sorted.slice(0, maxShow)

  const display = shown
    .map(b => `${b.balance.toLocaleString(getLocale(b.currency), { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${b.currency}`)
    .join(', ')

  return { display, overflow: Math.max(0, sorted.length - maxShow) }
}
