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
  const editableColumns = [
    'date', 'flag', 'payee', 'narration', 'tags_links', 
    'account', 'amount', 'currency'
  ]

  const setCellFocus = async (position: CellPosition) => {
    currentCell.value = position
    isNavigating.value = true
    
    await nextTick()
    
    // Find the input element for this cell position
    const selector = `[data-row="${position.rowIndex}"][data-column="${position.columnId}"]${
      position.postingIndex !== undefined ? `[data-posting="${position.postingIndex}"]` : ''
    } input, [data-row="${position.rowIndex}"][data-column="${position.columnId}"]${
      position.postingIndex !== undefined ? `[data-posting="${position.postingIndex}"]` : ''
    } select, [data-row="${position.rowIndex}"][data-column="${position.columnId}"]${
      position.postingIndex !== undefined ? `[data-posting="${position.postingIndex}"]` : ''
    } textarea`
    
    const element = document.querySelector(selector) as HTMLElement
    if (element) {
      element.focus()
      
      // For account dropdowns, open them automatically
      if (position.columnId === 'account') {
        const comboboxButton = element.parentElement?.querySelector('[role="combobox"] button')
        if (comboboxButton) {
          (comboboxButton as HTMLElement).click()
        }
      }
    }
    
    isNavigating.value = false
  }

  const getNextCell = (current: CellPosition, direction: 'next' | 'prev' | 'up' | 'down', totalRows: number, getTransactionPostingCount: (rowIndex: number) => number): CellPosition | null => {
    const currentColumnIndex = editableColumns.indexOf(current.columnId)
    if (currentColumnIndex === -1) return null

    if (direction === 'next') {
      // Tab navigation: account → amount → currency → next posting's account
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
          // Move to next transaction's first editable field
          if (current.rowIndex + 1 < totalRows) {
            return { rowIndex: current.rowIndex + 1, columnId: 'date', postingIndex: undefined }
          }
        }
      } else {
        // For transaction-level fields, move to next field or first posting
        const nextColumnIndex = currentColumnIndex + 1
        if (nextColumnIndex < editableColumns.length) {
          const nextColumnId = editableColumns[nextColumnIndex]
          if (['account', 'amount', 'currency'].includes(nextColumnId)) {
            return { ...current, columnId: nextColumnId, postingIndex: 0 }
          } else {
            return { ...current, columnId: nextColumnId, postingIndex: undefined }
          }
        }
      }
    } else if (direction === 'prev') {
      // Shift+Tab navigation: reverse of above logic
      if (current.columnId === 'currency') {
        return { ...current, columnId: 'amount' }
      } else if (current.columnId === 'amount') {
        return { ...current, columnId: 'account' }
      } else if (current.columnId === 'account') {
        // Move to previous posting's currency or transaction field
        if ((current.postingIndex || 0) > 0) {
          return { ...current, columnId: 'currency', postingIndex: (current.postingIndex || 1) - 1 }
        } else {
          // Move to last transaction-level field
          return { ...current, columnId: 'tags_links', postingIndex: undefined }
        }
      } else {
        // For transaction-level fields, move to previous field
        const prevColumnIndex = currentColumnIndex - 1
        if (prevColumnIndex >= 0) {
          return { ...current, columnId: editableColumns[prevColumnIndex], postingIndex: undefined }
        } else if (current.rowIndex > 0) {
          // Move to previous transaction's last field
          const prevRowIndex = current.rowIndex - 1
          const postingCount = getTransactionPostingCount(prevRowIndex)
          return { rowIndex: prevRowIndex, columnId: 'currency', postingIndex: postingCount - 1 }
        }
      }
    } else if (direction === 'up' && current.rowIndex > 0) {
      return { ...current, rowIndex: current.rowIndex - 1 }
    } else if (direction === 'down' && current.rowIndex + 1 < totalRows) {
      return { ...current, rowIndex: current.rowIndex + 1 }
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
      case 'ArrowUp':
        event.preventDefault()
        nextCell = getNextCell(currentPosition, 'up', totalRows, getTransactionPostingCount)
        break
      case 'ArrowDown':
        event.preventDefault()
        nextCell = getNextCell(currentPosition, 'down', totalRows, getTransactionPostingCount)
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
    initializeNavigation
  }
}