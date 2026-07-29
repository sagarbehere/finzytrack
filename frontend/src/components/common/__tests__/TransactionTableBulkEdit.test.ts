import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import TransactionTable from '@/components/common/TransactionTable.vue'
import BulkActionBar from '@/components/transactions/BulkActionBar.vue'
import OperationSummary from '@/components/transactions/OperationSummary.vue'
import DetailsBadge from '@/components/documents/DetailsBadge.vue'
import { makeTx } from '@/test/factories'
import { toMoney } from '@/utils/money'
import type { TransactionViewModel } from '@/types/transactions'

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMd: ref(true) }),
}))
vi.mock('@/composables/useDocuments', () => ({
  useDocuments: () => ({ uploadDocument: vi.fn(), openDocument: vi.fn(), isUploading: ref(false) }),
}))
// Auto-confirm destructive dialogs and stub the ledger delete API for the bulk
// delete test (harmless for the other tests, which don't delete).
const deleteTransactions = vi.fn().mockResolvedValue({})
vi.mock('@/composables/useTransactionDeleter', () => ({
  useTransactionDeleter: () => ({ deleteTransactions }),
}))
vi.mock('@/composables/useConfirmDialog', () => ({
  useConfirmDialog: () => ({
    isOpen: ref(false),
    dialogOptions: ref({ title: '', message: '', confirmText: '', cancelText: '', variant: 'warning' }),
    showConfirm: vi.fn().mockResolvedValue(true),
    handleConfirm: vi.fn(),
    handleCancel: vi.fn(),
    handleClose: vi.fn(),
  }),
}))

const mountOpts = {
  global: {
    stubs: { AccountDropdown: true, CommodityDropdown: true, PriceTypeDropdown: true, ConfirmDialog: true },
  },
}

function threeTxns(): TransactionViewModel[] {
  return [
    makeTx({ id: 'tx-1', tags: [], postings: [
      { account: 'Expenses:Fees', amount: toMoney(10), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-10), currency: 'USD' },
    ] }),
    makeTx({ id: 'tx-2', tags: [], postings: [
      { account: 'Expenses:Fees', amount: toMoney(20), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-20), currency: 'USD' },
    ] }),
    makeTx({ id: 'tx-3', tags: [], postings: [
      { account: 'Expenses:Food', amount: toMoney(30), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney(-30), currency: 'USD' },
    ] }),
  ]
}

function latestEmitted(wrapper: ReturnType<typeof mount>): TransactionViewModel[] {
  const emissions = wrapper.emitted('transactionsUpdated') as TransactionViewModel[][][]
  return emissions[emissions.length - 1][0]
}

describe('TransactionTable bulk edit', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('renders one selection checkbox per transaction and no action bar until a row is selected', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    const boxes = wrapper.findAll('td[data-column-id="select"] input[type="checkbox"]')
    expect(boxes.length).toBe(3)
    expect(wrapper.findComponent(BulkActionBar).exists()).toBe(false)
  })

  it('shows the action bar once a row is selected', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    await wrapper.find('td[data-column-id="select"] input[type="checkbox"]').trigger('click')
    const bar = wrapper.findComponent(BulkActionBar)
    expect(bar.exists()).toBe(true)
    expect(bar.props('selectedCount')).toBe(1)
  })

  it('applies a bulk op to selected rows, marks them modified, and tints their cells', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    // Select tx-1 and tx-2 (first two checkboxes).
    const boxes = wrapper.findAll('td[data-column-id="select"] input[type="checkbox"]')
    await boxes[0].trigger('click')
    await boxes[1].trigger('click')

    // Apply via the action bar's emit (the popover UI is covered separately).
    wrapper.findComponent(BulkActionBar).vm.$emit('apply', { type: 'addTag', tag: 'q1' })
    await flushPromises()

    const latest = latestEmitted(wrapper)
    expect(latest.find(t => t.id === 'tx-1')!.tags).toEqual(['q1'])
    expect(latest.find(t => t.id === 'tx-2')!.tags).toEqual(['q1'])
    expect(latest.find(t => t.id === 'tx-1')!.internal.isModified).toBe(true)
    expect(latest.find(t => t.id === 'tx-3')!.tags).toEqual([])

    // The in-table diff signal: a modified transaction's scrollable cells are tinted.
    const payeeCell = wrapper.find('tr.transaction-tx-1 td[data-column-id="payee"]')
    expect(payeeCell.classes()).toContain('bg-amber-50')
    const unchangedCell = wrapper.find('tr.transaction-tx-3 td[data-column-id="payee"]')
    expect(unchangedCell.classes()).not.toContain('bg-amber-50')
  })

  it('shift-clicking selects a contiguous range from the anchor', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    const boxes = wrapper.findAll('td[data-column-id="select"] input[type="checkbox"]')
    await boxes[0].trigger('click') // anchor on tx-1
    await boxes[2].trigger('click', { shiftKey: true }) // extend to tx-3

    expect(wrapper.findComponent(BulkActionBar).props('selectedCount')).toBe(3)
  })

  it('shows a readable old → new diff when "Show changes" is toggled on', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    await wrapper.find('td[data-column-id="select"] input[type="checkbox"]').trigger('click')
    wrapper.findComponent(BulkActionBar).vm.$emit('apply', { type: 'setPayee', payee: 'Renamed' })
    await flushPromises()

    // The toggle appears once there are modifications.
    const toggle = wrapper.findAll('button').find(b => b.text() === 'Show changes')!
    expect(toggle).toBeTruthy()
    await toggle.trigger('click')

    const payeeCell = wrapper.find('tr.transaction-tx-1 td[data-column-id="payee"]')
    expect(payeeCell.text()).toContain('Test Payee') // old (struck)
    expect(payeeCell.text()).toContain('Renamed')    // new
    // An unchanged row shows no diff, just its current value.
    expect(wrapper.find('tr.transaction-tx-3 td[data-column-id="payee"]').text()).not.toContain('Renamed')
  })

  it('lists the applied operation in the summary and undoes it', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    await wrapper.find('td[data-column-id="select"] input[type="checkbox"]').trigger('click')
    wrapper.findComponent(BulkActionBar).vm.$emit('apply', { type: 'addTag', tag: 'q1' })
    await flushPromises()

    const summary = wrapper.findComponent(OperationSummary)
    expect(summary.props('operations')).toHaveLength(1)
    const opId = (summary.props('operations') as { id: number }[])[0].id

    summary.vm.$emit('undo', opId)
    await flushPromises()

    expect(latestEmitted(wrapper).find(t => t.id === 'tx-1')!.tags).toEqual([])
    expect(wrapper.findComponent(OperationSummary).props('operations')).toHaveLength(0)
  })

  it('only affects rows the op changes (selected-but-unaffected subset stays clean)', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    // Select all three, then replace an account only tx-1 and tx-2 use.
    await wrapper.find('th[data-column-id="select"] input[type="checkbox"]').setValue(true)
    wrapper.findComponent(BulkActionBar).vm.$emit('apply', {
      type: 'replaceAccount', from: 'Expenses:Fees', to: 'Expenses:SpecialFees',
    })
    await flushPromises()

    const latest = latestEmitted(wrapper)
    expect(latest.find(t => t.id === 'tx-1')!.postings[0].account).toBe('Expenses:SpecialFees')
    expect(latest.find(t => t.id === 'tx-3')!.postings[0].account).toBe('Expenses:Food')
    expect(latest.find(t => t.id === 'tx-3')!.internal.isModified).toBe(false)
  })

  it('bulk-deletes the selected transactions (immediate, after confirm)', async () => {
    deleteTransactions.mockClear()
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    const boxes = wrapper.findAll('td[data-column-id="select"] input[type="checkbox"]')
    await boxes[0].trigger('click')
    await boxes[1].trigger('click')

    wrapper.findComponent(BulkActionBar).vm.$emit('delete')
    await flushPromises()

    expect(deleteTransactions).toHaveBeenCalledWith(['tx-1', 'tx-2'])
    expect(latestEmitted(wrapper).map(t => t.id)).toEqual(['tx-3'])
    const deleted = (wrapper.emitted('transactionDeleted') as string[][] | undefined)?.flat()
    expect(deleted).toEqual(['tx-1', 'tx-2'])
  })

  it('folds the details paperclip into the Status/Info column (no separate documents column)', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: true }, ...mountOpts })
    await flushPromises()

    // No dedicated documents column any more.
    expect(wrapper.find('td[data-column-id="documents"]').exists()).toBe(false)
    // The header is relabelled "Info".
    expect(wrapper.find('th[data-column-id="status"]').text()).toContain('Info')
    // The paperclip (DetailsBadge) now lives inside the status cell.
    expect(wrapper.findComponent(DetailsBadge).exists()).toBe(true)
    expect(wrapper.find('td[data-column-id="status"] button').exists()).toBe(true)
  })

  it('does not render selection UI when enableBulkEdit is off (Import parity)', async () => {
    const wrapper = mount(TransactionTable, { props: { transactions: threeTxns(), enableBulkEdit: false }, ...mountOpts })
    await flushPromises()
    expect(wrapper.find('td[data-column-id="select"]').exists()).toBe(false)
    expect(wrapper.findComponent(BulkActionBar).exists()).toBe(false)
  })
})
