import { toMoney } from '@/utils/money'
import { ref } from 'vue'
import { buildTanStackColumns, type TransactionColumnDef, type TableRowData, type BuildColumnsOptions } from '@/composables/useTransactionColumns'
import { makeTx } from '@/test/factories'
import AccountDropdown from '@/components/common/AccountDropdown.vue'

// Minimal mock for columnConfig (useTableColumns return value)
const mockColumnConfig = {
  allColumns: ref([
    { id: 'status', defaultWidth: 60, minWidth: 60, resizable: false },
    { id: 'index', defaultWidth: 50, minWidth: 40, resizable: true },
    { id: 'date', defaultWidth: 120, minWidth: 100, resizable: true },
    { id: 'flag', defaultWidth: 60, minWidth: 50, resizable: true },
    { id: 'payee', defaultWidth: 150, minWidth: 100, resizable: true },
    { id: 'account', defaultWidth: 180, minWidth: 120, resizable: true },
    { id: 'amount', defaultWidth: 100, minWidth: 80, resizable: true },
  ]),
  columnVisibility: ref({}),
  columnSizing: ref({}),
  toggleColumnVisibility: vi.fn(),
  resetToDefaults: vi.fn(),
  setColumnWidth: vi.fn(),
}

function makeOptions(
  overrides: Partial<Omit<BuildColumnsOptions, 'editable'>> & { editable?: boolean | (() => boolean) } = {},
): BuildColumnsOptions {
  const { editable = true, ...rest } = overrides
  return {
    editable: typeof editable === 'function' ? editable : () => editable,
    updateField: vi.fn(),
    numericInputProps: vi.fn((_txId, _postIdx, _field, currentValue, _updateFn, extraClasses = '') => ({
      type: 'text',
      inputmode: 'decimal',
      value: String(currentValue ?? ''),
      class: extraClasses,
    })),
    getImportContext: vi.fn(() => undefined),
    getLedgerContext: vi.fn(() => undefined),
    onDuplicateClick: vi.fn(),
    getEditableInputClasses: vi.fn((extra = '') => `editable-input ${extra}`),
    getDisplayClasses: vi.fn(() => 'display-text'),
    getAmountColorClass: vi.fn(() => ''),
    columnConfig: mockColumnConfig as any,
    ...rest,
  }
}

function makeRowData(overrides: Partial<TableRowData> = {}): TableRowData {
  const tx = makeTx({ id: 'tx-1' })
  return {
    account: 'Expenses:Food',
    amount: toMoney(50),
    currency: 'USD',
    transaction: tx,
    postingIndex: 0,
    isFirstPosting: true,
    isLastPosting: true,
    transactionIndex: 1,
    ...overrides,
  }
}

function makeCellContext(rowData: TableRowData, getValue?: () => any) {
  return {
    row: { original: rowData },
    getValue: getValue ?? (() => undefined),
  }
}

// Subset of column defs for testing
const TEST_DEFS: TransactionColumnDef[] = [
  { id: 'index', header: '#', type: 'display', span: 'transaction', accessor: 'transactionIndex' },
  { id: 'date', header: 'Date', type: 'date', field: 'transaction.date', span: 'transaction' },
  { id: 'flag', header: 'Flag', type: 'text', field: 'transaction.flag', span: 'transaction' },
  { id: 'payee', header: 'Payee', type: 'textarea', field: 'transaction.payee', span: 'transaction', placeholder: 'Payee' },
  { id: 'account', header: 'Account', type: 'dropdown', field: 'account', span: 'posting', component: AccountDropdown, componentProps: { 'allow-custom': false } },
  { id: 'amount', header: 'Amount', type: 'numeric', field: 'amount', span: 'posting', align: 'right', colorize: true },
]

describe('buildTanStackColumns', () => {
  it('produces a column for each definition', () => {
    const columns = buildTanStackColumns(TEST_DEFS, makeOptions())
    expect(columns.length).toBe(TEST_DEFS.length)
  })

  it('text column renders input when editable', () => {
    const options = makeOptions({ editable: true })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const flagCol = columns.find(c => c.id === 'flag')!
    const rowData = makeRowData()
    const vnode = (flagCol as any).cell(makeCellContext(rowData, () => '*'))
    expect(vnode.type).toBe('input')
  })

  it('text column renders span when not editable', () => {
    const options = makeOptions({ editable: false })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const flagCol = columns.find(c => c.id === 'flag')!
    const vnode = (flagCol as any).cell(makeCellContext(makeRowData(), () => '*'))
    expect(vnode.type).toBe('span')
  })

  it('textarea column renders textarea when editable', () => {
    const options = makeOptions({ editable: true })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const payeeCol = columns.find(c => c.id === 'payee')!
    const vnode = (payeeCol as any).cell(makeCellContext(makeRowData(), () => 'Store'))
    expect(vnode.type).toBe('textarea')
  })

  it('textarea column renders div when not editable', () => {
    const options = makeOptions({ editable: false })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const payeeCol = columns.find(c => c.id === 'payee')!
    const vnode = (payeeCol as any).cell(makeCellContext(makeRowData(), () => 'Store'))
    expect(vnode.type).toBe('div')
  })

  it('date column renders date input when editable', () => {
    const options = makeOptions({ editable: true })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const dateCol = columns.find(c => c.id === 'date')!
    const vnode = (dateCol as any).cell(makeCellContext(makeRowData(), () => '2025-01-15'))
    expect(vnode.type).toBe('input')
    expect(vnode.props.type).toBe('date')
  })

  it('numeric column calls numericInputProps when editable', () => {
    const options = makeOptions({ editable: true })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const amountCol = columns.find(c => c.id === 'amount')!;
    (amountCol as any).cell(makeCellContext(makeRowData(), () => 50))
    expect(options.numericInputProps).toHaveBeenCalled()
  })

  it('numeric column renders span with toFixed(2) when not editable', () => {
    const options = makeOptions({ editable: false })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const amountCol = columns.find(c => c.id === 'amount')!
    const vnode = (amountCol as any).cell(makeCellContext(makeRowData(), () => 123.456))
    expect(vnode.type).toBe('span')
    expect(vnode.children).toBe('123.46')
  })

  it('dropdown column renders component when editable', () => {
    const options = makeOptions({ editable: true })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const accountCol = columns.find(c => c.id === 'account')!
    const vnode = (accountCol as any).cell(makeCellContext(makeRowData(), () => 'Expenses:Food'))
    expect(vnode.type).toBe(AccountDropdown)
  })

  it('dropdown column renders span when not editable', () => {
    const options = makeOptions({ editable: false })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const accountCol = columns.find(c => c.id === 'account')!
    const vnode = (accountCol as any).cell(makeCellContext(makeRowData(), () => 'Expenses:Food'))
    expect(vnode.type).toBe('span')
  })

  it('text column input calls updateField with correct path on change event', () => {
    const mockUpdateField = vi.fn()
    const options = makeOptions({ editable: true, updateField: mockUpdateField })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const flagCol = columns.find(c => c.id === 'flag')!
    const vnode = (flagCol as any).cell(makeCellContext(makeRowData(), () => '*'))
    // Simulate onChange
    vnode.props.onChange({ target: { value: '!' } })
    expect(mockUpdateField).toHaveBeenCalledWith('tx-1', 'flag', '!')
  })

  it('posting-level column calls updateField with postings.N path', () => {
    const mockUpdateField = vi.fn()
    const options = makeOptions({ editable: true, updateField: mockUpdateField })
    const columns = buildTanStackColumns(TEST_DEFS, options)
    const accountCol = columns.find(c => c.id === 'account')!
    const rowData = makeRowData({ postingIndex: 1 })
    const vnode = (accountCol as any).cell(makeCellContext(rowData, () => 'Expenses:Food'))
    // Simulate dropdown update
    vnode.props['onUpdate:modelValue']('Expenses:Dining')
    expect(mockUpdateField).toHaveBeenCalledWith('tx-1', 'postings.1.account', 'Expenses:Dining')
  })
})
