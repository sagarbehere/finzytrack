import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, nextTick } from 'vue'
import TransactionTable from '@/components/common/TransactionTable.vue'
import { makeTx } from '@/test/factories'
import type { TransactionViewModel } from '@/types/transactions'

const uploadDocument = vi.fn()

// Force the desktop table layout (the row drag-drop accelerator is desktop-only).
vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMd: ref(true) }),
}))
vi.mock('@/composables/useDocuments', () => ({
  useDocuments: () => ({ uploadDocument, openDocument: vi.fn(), isUploading: ref(false) }),
}))

function fileDragEvent(withFiles: boolean) {
  const file = new File(['x'], 'r.pdf', { type: 'application/pdf' })
  return {
    dataTransfer: {
      types: withFiles ? ['Files'] : ['text/plain'],
      files: withFiles ? [file] : [],
    },
  }
}

// Stub the editable dropdown children (they fetch accounts/commodities on
// mount) and the teleported ConfirmDialog, so the table mounts cleanly in the
// test DOM without network or teleport reconciliation noise.
const mountOpts = {
  global: {
    stubs: {
      AccountDropdown: true,
      CommodityDropdown: true,
      PriceTypeDropdown: true,
      ConfirmDialog: true,
    },
  },
}

describe('TransactionTable row drag-and-drop', () => {
  beforeEach(() => uploadDocument.mockReset())
  afterEach(() => { document.body.innerHTML = '' })

  it('highlights all rows of a multi-posting transaction on file drag-over', async () => {
    const tx = makeTx({
      id: 'tx-1',
      postings: [
        { account: 'Expenses:Food' },
        { account: 'Expenses:Tax' },
        { account: 'Assets:Bank' },
      ],
    })
    const wrapper = mount(TransactionTable, { props: { transactions: [tx] }, ...mountOpts })
    await flushPromises()

    const rows = wrapper.findAll('tr.transaction-tx-1')
    expect(rows.length).toBe(3) // one <tr> per posting

    await rows[0].trigger('dragover', fileDragEvent(true))
    // The highlight is keyed on transaction id and applied per-cell (a soft
    // background that overrides the # column's own tint), so every cell of
    // every row of the tx lights up.
    const cells = wrapper.findAll('tr.transaction-tx-1 td')
    expect(cells.length).toBeGreaterThan(0)
    for (const cell of cells) {
      expect(cell.classes()).toContain('!bg-indigo-50')
    }
  })

  it('ignores a non-file drag (types without "Files")', async () => {
    const tx = makeTx({ id: 'tx-2' })
    const wrapper = mount(TransactionTable, { props: { transactions: [tx] }, ...mountOpts })
    await flushPromises()
    await wrapper.find('tr.transaction-tx-2').trigger('dragover', fileDragEvent(false))
    for (const cell of wrapper.findAll('tr.transaction-tx-2 td')) {
      expect(cell.classes()).not.toContain('!bg-indigo-50')
    }
  })

  it('uploads the dropped file and stages the document into the transaction meta', async () => {
    uploadDocument.mockResolvedValue({
      path: '../documents/2026/dropped.pdf', full_hash: 'h', size: 1, display_name: 'dropped.pdf',
    })
    const tx = makeTx({ id: 'tx-3', date: '2026-06-15', payee: 'Vendor', meta: {} })
    const wrapper = mount(TransactionTable, { props: { transactions: [tx] }, ...mountOpts })
    await flushPromises()

    await wrapper.find('tr.transaction-tx-3').trigger('drop', fileDragEvent(true))
    await flushPromises()
    await nextTick()

    expect(uploadDocument).toHaveBeenCalledWith(
      expect.any(File),
      { date: '2026-06-15', narration: 'Vendor' },
    )
    // The dropped doc lands in that transaction's meta and the row is now
    // modified (so it persists via the normal Save flow).
    const emissions = wrapper.emitted('transactionsUpdated') as TransactionViewModel[][][] | undefined
    expect(emissions).toBeTruthy()
    const latest = emissions![emissions!.length - 1][0]
    const updated = latest.find(t => t.id === 'tx-3')!
    expect(updated.meta.document).toBe('../documents/2026/dropped.pdf')
    expect(updated.internal.isModified).toBe(true)
  })
})
