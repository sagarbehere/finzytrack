import { ref, nextTick } from 'vue'

export interface CellPosition {
  rowIndex: number
  columnId: string
  postingIndex?: number
}

export function useTableKeyboardNavigation() {
  const currentCell = ref<CellPosition | null>(null)
  const isNavigating = ref(false)

  // Define the logical tab order for editable columns
  const allEditableColumns = [
    'date', 'flag', 'payee', 'narration', 'tags_links',
    'account', 'amount', 'currency'
  ]

  // Function to get only visible editable columns
  const getVisibleEditableColumns = (): string[] => {
    return allEditableColumns.filter(columnId => {
      const cell = document.querySelector(`td[data-column-id="${columnId}"]`)
      return cell !== null
    })
  }

  const setCellFocus = async (position: CellPosition) => {
    currentCell.value = position
    isNavigating.value = true

    await nextTick()

    // Build the correct selector based on the DOM structure
    // For posting-level cells, we need both transaction index and posting index
    // For transaction-level cells, we only need transaction index
    let cellSelector = ''

    if (['account', 'amount', 'currency'].includes(position.columnId)) {
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
      isNavigating.value = false
      return
    }

    // Find the appropriate input element within the cell
    let targetElement: HTMLElement | null = null

    // Try different element types based on the column
    if (position.columnId === 'payee' || position.columnId === 'narration') {
      targetElement = cell.querySelector('[contenteditable="true"]') as HTMLElement
    } else if (position.columnId === 'account' || position.columnId === 'currency') {
      // For dropdowns, find the ComboboxInput element
      targetElement = cell.querySelector('input') as HTMLElement
    } else {
      // For regular inputs
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

    isNavigating.value = false
  }

  const getNextCell = (current: CellPosition, direction: 'next' | 'prev', totalRows: number, getTransactionPostingCount: (rowIndex: number) => number): CellPosition | null => {
    const visibleEditableColumns = getVisibleEditableColumns()
    const currentColumnIndex = visibleEditableColumns.indexOf(current.columnId)
    if (currentColumnIndex === -1) return null

    if (direction === 'next') {
      // Tab navigation follows logical flow: date → flag → payee → narration → tags_links → account(0) → amount(0) → currency(0) → account(1) → ...
      if (['account', 'amount', 'currency'].includes(current.columnId)) {
        // Posting-level navigation
        if (current.columnId === 'account') {
          return { ...current, columnId: 'amount' }
        } else if (current.columnId === 'amount') {
          return { ...current, columnId: 'currency' }
        } else if (current.columnId === 'currency') {
          // Move to next posting's account in same transaction
          const postingCount = getTransactionPostingCount(current.rowIndex)
          const nextPostingIndex = (current.postingIndex || 0) + 1

          if (nextPostingIndex < postingCount) {
            return { ...current, columnId: 'account', postingIndex: nextPostingIndex }
          } else {
            // Move to next transaction's first field
            if (current.rowIndex + 1 < totalRows) {
              return { rowIndex: current.rowIndex + 1, columnId: 'date', postingIndex: undefined }
            }
          }
        }
      } else {
        // Transaction-level field navigation
        const nextColumnIndex = currentColumnIndex + 1
        if (nextColumnIndex < visibleEditableColumns.length) {
          const nextColumnId = visibleEditableColumns[nextColumnIndex]
          if (['account', 'amount', 'currency'].includes(nextColumnId)) {
            // Moving to first posting-level field
            return { ...current, columnId: nextColumnId, postingIndex: 0 }
          } else {
            // Moving to next transaction-level field
            return { ...current, columnId: nextColumnId, postingIndex: undefined }
          }
        }
      }
    } else if (direction === 'prev') {
      // Shift+Tab navigation: reverse of above logic
      if (['account', 'amount', 'currency'].includes(current.columnId)) {
        // Posting-level navigation (reverse)
        if (current.columnId === 'currency') {
          return { ...current, columnId: 'amount' }
        } else if (current.columnId === 'amount') {
          return { ...current, columnId: 'account' }
        } else if (current.columnId === 'account') {
          const currentPostingIndex = current.postingIndex || 0
          if (currentPostingIndex > 0) {
            // Move to previous posting's currency
            return { ...current, columnId: 'currency', postingIndex: currentPostingIndex - 1 }
          } else {
            // Move to last visible transaction-level field
            const transactionColumns = visibleEditableColumns.filter(col => !['account', 'amount', 'currency'].includes(col))
            const lastTransactionColumn = transactionColumns[transactionColumns.length - 1]
            return { ...current, columnId: lastTransactionColumn, postingIndex: undefined }
          }
        }
      } else {
        // Transaction-level field navigation (reverse)
        const prevColumnIndex = currentColumnIndex - 1
        if (prevColumnIndex >= 0) {
          return { ...current, columnId: visibleEditableColumns[prevColumnIndex], postingIndex: undefined }
        } else if (current.rowIndex > 0) {
          // Move to previous transaction's last posting's currency
          const prevRowIndex = current.rowIndex - 1
          const postingCount = getTransactionPostingCount(prevRowIndex)
          return { rowIndex: prevRowIndex, columnId: 'currency', postingIndex: postingCount - 1 }
        }
      }
    }

    return null
  }

  const handleKeyNavigation = (
    event: KeyboardEvent,
    currentPosition: CellPosition,
    totalRows: number,
    getTransactionPostingCount: (rowIndex: number) => number
  ) => {
    let nextCell: CellPosition | null = null

    switch (event.key) {
      case 'Tab':
        event.preventDefault()
        nextCell = getNextCell(
          currentPosition,
          event.shiftKey ? 'prev' : 'next',
          totalRows,
          getTransactionPostingCount
        )
        break
      case 'Enter':
        event.preventDefault()
        // Enter should focus/edit the current cell if not already focused
        nextCell = currentPosition
        break
      case 'Escape':
        // Clear current focus
        currentCell.value = null
        ;(document.activeElement as HTMLElement)?.blur()
        break
    }

    if (nextCell) {
      setCellFocus(nextCell)
    }
  }

  const initializeNavigation = (initialPosition: CellPosition) => {
    setCellFocus(initialPosition)
  }

  return {
    currentCell,
    isNavigating,
    setCellFocus,
    handleKeyNavigation,
    initializeNavigation,
    getNextCell
  }
}