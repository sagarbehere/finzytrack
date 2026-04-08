import { h, type Component } from 'vue'
import { createColumnHelper } from '@tanstack/vue-table'
import type { PostingViewModel, TransactionViewModel, ImportContext, LedgerContext } from '@/types/transactions'
import type { useTableColumns } from '@/composables/useTableColumns'

export interface TableRowData extends PostingViewModel {
  transaction: TransactionViewModel
  postingIndex: number
  isFirstPosting: boolean
  isLastPosting: boolean
  transactionIndex: number
}

export interface TransactionColumnDef {
  id: string
  header: string
  type: 'display' | 'component' | 'text' | 'textarea' | 'date' | 'numeric' | 'dropdown' | 'tags'
  field?: string                    // dot-path into TableRowData, e.g. 'transaction.date', 'account', 'cost.amount'
  span: 'transaction' | 'posting'   // whether this column is rowspanned across postings
  placeholder?: string
  align?: 'left' | 'right' | 'center'
  colorize?: boolean                // for numeric: apply green/red/gray color based on sign
  component?: Component             // for 'component' and 'dropdown' types
  componentProps?: Record<string, unknown>  // extra static props for dropdowns
  accessor?: string | ((row: TableRowData) => any)  // custom accessor for TanStack
}

function getNestedValue(obj: any, path: string): any {
  const parts = path.split('.')
  let current = obj
  for (const part of parts) {
    if (current === undefined || current === null) return undefined
    current = current[part]
  }
  return current
}

function resolveStorePath(field: string, postingIndex: number): string {
  if (field.startsWith('transaction.')) {
    return field.slice('transaction.'.length)
  }
  return `postings.${postingIndex}.${field}`
}

export interface BuildColumnsOptions {
  editable: boolean
  updateField: (txId: string, path: string, value: unknown) => void
  numericInputProps: (
    txId: string, postingIdx: number, field: string,
    currentValue: number | null | undefined,
    updateFn: (raw: string) => void,
    extraClasses?: string
  ) => Record<string, any>
  getImportContext: (id: string) => ImportContext | undefined
  getLedgerContext: (id: string) => LedgerContext | undefined
  onDuplicateClick: (id: string) => void
  getEditableInputClasses: (extra?: string) => string
  getDisplayClasses: () => string
  getAmountColorClass: (amount: number | null | undefined) => string
  columnConfig: ReturnType<typeof useTableColumns>
}

export function buildTanStackColumns(
  defs: TransactionColumnDef[],
  options: BuildColumnsOptions,
) {
  const columnHelper = createColumnHelper<TableRowData>()
  const { editable, updateField, numericInputProps, getEditableInputClasses, getDisplayClasses, getAmountColorClass, columnConfig } = options
  const getColumnConfig = (columnId: string) => columnConfig.allColumns.value.find((col: any) => col.id === columnId)

  return defs.map(def => {
    const colConfig = getColumnConfig(def.id)
    const size = colConfig?.defaultWidth || 100
    const minSize = colConfig?.minWidth || 60
    const enableResizing = colConfig?.resizable ?? true

    const accessorFn = def.accessor
      ? (typeof def.accessor === 'function' ? def.accessor : (row: TableRowData) => getNestedValue(row, def.accessor as string))
      : def.field
        ? (row: TableRowData) => getNestedValue(row, def.field!)
        : undefined

    const cellFn = ({ row, getValue }: { row: any; getValue: () => any }) => {
      const rowData: TableRowData = row.original
      const tx = rowData.transaction
      const postingIndex = rowData.postingIndex

      switch (def.type) {
        case 'display': {
          return h('span', { class: getDisplayClasses() }, getValue())
        }

        case 'component': {
          if (!def.component) return null
          return h(def.component, {
            transaction: tx,
            importContext: options.getImportContext(tx.id),
            ledgerContext: options.getLedgerContext(tx.id),
            onDuplicateClick: (transactionId: string) => options.onDuplicateClick(transactionId),
            ...(def.componentProps || {}),
          })
        }

        case 'text': {
          const storePath = resolveStorePath(def.field!, postingIndex)
          if (editable) {
            return h('input', {
              type: 'text',
              value: getValue() ?? '',
              onChange: (e: any) => updateField(tx.id, storePath, e.target.value),
              class: getEditableInputClasses(),
              ...(def.placeholder ? { placeholder: def.placeholder } : {}),
              autocomplete: 'off',
            })
          }
          return h('span', { class: getDisplayClasses() }, getValue() ?? '')
        }

        case 'textarea': {
          const storePath = resolveStorePath(def.field!, postingIndex)
          if (editable) {
            return h('textarea', {
              value: getValue() || '',
              onInput: (e: any) => updateField(tx.id, storePath, e.target.value),
              class: `${getEditableInputClasses()} resize-none overflow-y-auto h-full`,
              style: { width: '100%', minHeight: '2.5rem', boxSizing: 'border-box', display: 'block' },
              ...(def.placeholder ? { placeholder: def.placeholder } : {}),
            })
          }
          return h('div', {
            class: `${getDisplayClasses()} break-words overflow-hidden`,
          }, getValue() ?? '')
        }

        case 'date': {
          const storePath = resolveStorePath(def.field!, postingIndex)
          if (editable) {
            return h('input', {
              type: 'date',
              value: getValue() || '',
              onInput: (e: any) => updateField(tx.id, storePath, e.target.value),
              class: getEditableInputClasses(),
              autocomplete: 'off',
            })
          }
          return h('span', { class: getDisplayClasses() }, getValue() || '')
        }

        case 'numeric': {
          const value = getValue()
          const amountColorClass = def.colorize ? getAmountColorClass(value) : ''

          if (editable) {
            const storePath = resolveStorePath(def.field!, postingIndex)
            return h('input', numericInputProps(
              tx.id, postingIndex, def.id,
              value,
              (raw) => {
                const parsed = raw ? parseFloat(raw) : null
                updateField(tx.id, storePath, parsed)
              },
              `${getEditableInputClasses('text-right')} ${amountColorClass}`,
            ))
          }
          return h('span', {
            class: `${getDisplayClasses()} font-mono text-right block ${amountColorClass}`,
          }, value !== undefined && value !== null ? value.toFixed(2) : '')
        }

        case 'dropdown': {
          const storePath = resolveStorePath(def.field!, postingIndex)
          if (editable && def.component) {
            return h(def.component, {
              modelValue: getValue() || '',
              'onUpdate:modelValue': (value: string) => updateField(tx.id, storePath, value),
              'custom-class': '!text-sm !py-0.5 !px-1.5 !pr-8 !outline-0',
              ...(def.componentProps || {}),
              ...(def.placeholder ? { placeholder: def.placeholder } : {}),
            })
          }
          return h('span', { class: getDisplayClasses() }, getValue() || '')
        }

        case 'tags': {
          if (editable) {
            return h('input', {
              type: 'text',
              value: getValue() ?? '',
              onInput: (e: any) => updateField(tx.id, 'tags_links', e.target.value),
              class: getEditableInputClasses(),
              placeholder: def.placeholder || '#tag ^link',
              autocomplete: 'off',
            })
          }
          return h('span', { class: getDisplayClasses() }, getValue() ?? '')
        }

        default:
          return null
      }
    }

    if ((def.type === 'display' || def.type === 'component') && !accessorFn) {
      return columnHelper.display({
        id: def.id,
        header: def.header,
        cell: cellFn,
        size,
        minSize,
        enableResizing,
      })
    }

    return columnHelper.accessor(
      accessorFn!,
      {
        id: def.id,
        header: def.header,
        cell: cellFn,
        size,
        minSize,
        enableResizing,
      },
    )
  })
}
