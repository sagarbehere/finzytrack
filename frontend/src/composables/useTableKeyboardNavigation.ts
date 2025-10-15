import { nextTick } from 'vue'

export interface CellPosition {
  rowIndex: number
  columnId: string
  postingIndex?: number
}

export function useTableKeyboardNavigation() {
  // Columns that span across all postings
  const spannedColumns = ['date', 'flag', 'payee', 'memo', 'narration', 'tags_links']

  // Columns that are per-posting
  const postingColumns = [
    'account',
    'amount',
    'currency',
    'cost_amount',
    'cost_currency',
    'cost_date',
    'price_amount',
    'price_currency',
    'price_type',
    'actions'
  ]

  // Complete column order (for horizontal navigation)
  // This defines the left-to-right order of all columns
  const allColumnsInOrder = [
    'status',
    'index',
    'date',
    'flag',
    'payee',
    'memo',
    'narration',
    'tags_links',
    'account',
    'amount',
    'currency',
    'cost_amount',
    'cost_currency',
    'cost_date',
    'price_amount',
    'price_currency',
    'price_type',
    'balance',
    'actions'
  ]

  const setCellFocus = async (position: CellPosition) => {
    await nextTick()

    // Build the correct selector based on the DOM structure
    // For posting-level cells, we need both transaction index and posting index
    // For transaction-level cells, we only need transaction index
    let cellSelector = ''

    if (postingColumns.includes(position.columnId)) {
      // Posting-level fields need posting index
      cellSelector = `td[data-column-id="${position.columnId}"][data-row="${position.rowIndex + 1}"][data-posting="${position.postingIndex || 0}"]`
    } else {
      // Transaction-level fields only need row index
      cellSelector = `td[data-column-id="${position.columnId}"][data-row="${position.rowIndex + 1}"]`
    }

    // Look for focusable elements within the cell
    const cell = document.querySelector(cellSelector)
    if (!cell) {
      console.warn('Cell not found:', cellSelector)
      return
    }

    // Find the appropriate input element within the cell
    let targetElement: HTMLElement | null = null

    // Try different element types based on the column
    if (position.columnId === 'payee' || position.columnId === 'memo' || position.columnId === 'narration') {
      targetElement = cell.querySelector('[contenteditable="true"]') as HTMLElement
    } else if (['account', 'currency', 'cost_currency', 'price_currency', 'price_type'].includes(position.columnId)) {
      // For dropdowns (AccountDropdown/CommodityDropdown/PriceTypeDropdown), find the ComboboxInput element
      targetElement = cell.querySelector('input') as HTMLElement
    } else if (position.columnId === 'actions') {
      // For actions column, focus the first button
      targetElement = cell.querySelector('button') as HTMLElement
    } else {
      // For regular inputs (amount, cost_amount, cost_date, price_amount, date, etc.)
      targetElement = cell.querySelector('input, select, textarea') as HTMLElement
    }

    if (targetElement) {
      targetElement.focus()

      // For contenteditable elements, place cursor at end
      if (targetElement.contentEditable === 'true') {
        const range = document.createRange()
        const selection = window.getSelection()
        range.selectNodeContents(targetElement)
        range.collapse(false)
        selection?.removeAllRanges()
        selection?.addRange(range)
      }

      // For input elements, select all text
      if (targetElement.tagName === 'INPUT') {
        (targetElement as HTMLInputElement).select()
      }
    } else {
      console.warn('No focusable element found in cell:', cellSelector)
    }
  }

  // Vertical navigation (ArrowUp/ArrowDown)
  const getVerticalCell = (
    current: CellPosition,
    direction: 'up' | 'down',
    totalRows: number,
    getTransactionPostingCount: (rowIndex: number) => number
  ): CellPosition | null => {
    if (spannedColumns.includes(current.columnId)) {
      // For spanned columns, move to next/previous transaction
      const targetRowIndex = direction === 'down' ? current.rowIndex + 1 : current.rowIndex - 1
      if (targetRowIndex < 0 || targetRowIndex >= totalRows) return null
      return { rowIndex: targetRowIndex, columnId: current.columnId, postingIndex: undefined }
    } else if (postingColumns.includes(current.columnId)) {
      // For posting columns, move to next/previous posting
      const postingCount = getTransactionPostingCount(current.rowIndex)
      const currentPostingIndex = current.postingIndex ?? 0

      if (direction === 'down') {
        const nextPostingIndex = currentPostingIndex + 1
        if (nextPostingIndex < postingCount) {
          // Move to next posting in same transaction
          return { rowIndex: current.rowIndex, columnId: current.columnId, postingIndex: nextPostingIndex }
        } else {
          // Move to first posting of next transaction
          const nextRowIndex = current.rowIndex + 1
          if (nextRowIndex >= totalRows) return null
          return { rowIndex: nextRowIndex, columnId: current.columnId, postingIndex: 0 }
        }
      } else {
        // Moving up
        if (currentPostingIndex > 0) {
          // Move to previous posting in same transaction
          return { rowIndex: current.rowIndex, columnId: current.columnId, postingIndex: currentPostingIndex - 1 }
        } else {
          // Move to last posting of previous transaction
          const prevRowIndex = current.rowIndex - 1
          if (prevRowIndex < 0) return null
          const prevPostingCount = getTransactionPostingCount(prevRowIndex)
          return { rowIndex: prevRowIndex, columnId: current.columnId, postingIndex: prevPostingCount - 1 }
        }
      }
    }

    return null
  }

  // Horizontal navigation (Alt+ArrowLeft/Alt+ArrowRight)
  const getHorizontalCell = (
    current: CellPosition,
    direction: 'left' | 'right',
    visibleColumns: string[]
  ): CellPosition | null => {
    // Filter allColumnsInOrder to only include visible columns
    const orderedVisibleColumns = allColumnsInOrder.filter(col => visibleColumns.includes(col))

    if (orderedVisibleColumns.length === 0) return null

    // Find current column index in the visible columns
    const currentIndex = orderedVisibleColumns.indexOf(current.columnId)
    if (currentIndex === -1) return null

    // Calculate target column index
    const targetIndex = direction === 'right' ? currentIndex + 1 : currentIndex - 1
    if (targetIndex < 0 || targetIndex >= orderedVisibleColumns.length) return null

    const targetColumnId = orderedVisibleColumns[targetIndex]

    // Determine the posting index for the target cell
    if (spannedColumns.includes(targetColumnId)) {
      // Moving to a transaction-level column - no posting index needed
      return {
        rowIndex: current.rowIndex,
        columnId: targetColumnId,
        postingIndex: undefined
      }
    } else if (postingColumns.includes(targetColumnId)) {
      // Moving to a posting-level column
      if (spannedColumns.includes(current.columnId)) {
        // Coming from a transaction-level column - go to posting 0
        return {
          rowIndex: current.rowIndex,
          columnId: targetColumnId,
          postingIndex: 0
        }
      } else {
        // Coming from another posting-level column - preserve posting index
        return {
          rowIndex: current.rowIndex,
          columnId: targetColumnId,
          postingIndex: current.postingIndex ?? 0
        }
      }
    }

    return null
  }

  const handleKeyNavigation = (
    event: KeyboardEvent,
    currentPosition: CellPosition,
    totalRows: number,
    getTransactionPostingCount: (rowIndex: number) => number,
    visibleColumns?: string[]
  ) => {
    let nextCell: CellPosition | null = null

    switch (event.key) {
      case 'ArrowUp':
        nextCell = getVerticalCell(currentPosition, 'up', totalRows, getTransactionPostingCount)
        break
      case 'ArrowDown':
        nextCell = getVerticalCell(currentPosition, 'down', totalRows, getTransactionPostingCount)
        break
      case 'ArrowLeft':
        if (event.altKey && visibleColumns) {
          nextCell = getHorizontalCell(currentPosition, 'left', visibleColumns)
        }
        break
      case 'ArrowRight':
        if (event.altKey && visibleColumns) {
          nextCell = getHorizontalCell(currentPosition, 'right', visibleColumns)
        }
        break
    }

    if (nextCell) {
      event.preventDefault()
      setCellFocus(nextCell)
    }
  }

  return {
    setCellFocus,
    handleKeyNavigation
  }
}