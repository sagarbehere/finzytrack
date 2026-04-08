import { ref, type Ref } from 'vue'
import type { AccountTreeNode } from '@/types/accounts'
import { buildTree, flattenForDisplay, filterTree, formatBalances } from './accountTreeUtils'

interface UseAccountsTreeReturn {
  // Tree state
  readonly expandedIds: Ref<Set<string>>

  // Actions
  buildTree: typeof buildTree
  flattenForDisplay: typeof flattenForDisplay
  filterTree: typeof filterTree
  toggleExpand: (id: string) => void
  expandAll: (nodes: AccountTreeNode[]) => void
  collapseAll: () => void
}

// Module-level state
const expandedIds = ref<Set<string>>(new Set())

/**
 * Toggle expand state for a node
 */
function toggleExpand(id: string): void {
  const newSet = new Set(expandedIds.value)
  if (newSet.has(id)) {
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  expandedIds.value = newSet
}

/**
 * Expand all nodes in the tree
 */
function expandAll(nodes: AccountTreeNode[]): void {
  const allIds = new Set<string>()

  function collectIds(node: AccountTreeNode): void {
    if (node.children.length > 0) {
      allIds.add(node.id)
      for (const child of node.children) {
        collectIds(child)
      }
    }
  }

  for (const node of nodes) {
    collectIds(node)
  }

  expandedIds.value = allIds
}

/**
 * Collapse all nodes
 */
function collapseAll(): void {
  expandedIds.value = new Set()
}

export function useAccountsTree(): UseAccountsTreeReturn {
  return {
    expandedIds,
    buildTree,
    flattenForDisplay,
    filterTree,
    toggleExpand,
    expandAll,
    collapseAll
  }
}

// Re-export for direct imports
export { formatBalances } from './accountTreeUtils'
