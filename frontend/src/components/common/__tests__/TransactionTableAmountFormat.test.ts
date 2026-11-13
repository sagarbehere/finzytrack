// Amounts read with thousands separators (issue #5) but edit as plain numbers.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import TransactionTable from '@/components/common/TransactionTable.vue'
import { makeTx } from '@/test/factories'
import { toMoney } from '@/utils/money'
import type { TransactionViewModel } from '@/types/transactions'

vi.mock('@/composables/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMd: ref(true) }),
}))
vi.mock('@/composables/useDocuments', () => ({
  useDocuments: () => ({ uploadDocument: vi.fn(), openDocument: vi.fn(), isUploading: ref(false) }),
}))

const mountOpts = {
  global: {
    stubs: { AccountDropdown: true, CommodityDropdown: true, PriceTypeDropdown: true, ConfirmDialog: true },
  },
}

function txns(): TransactionViewModel[] {
  return [
    makeTx({ id: 'tx-usd', tags: [], postings: [
      { account: 'Expenses:Rent', amount: toMoney('1129.41'), currency: 'USD' },
      { account: 'Assets:Bank', amount: toMoney('-1129.41'), currency: 'USD' },
    ] }),
    makeTx({ id: 'tx-inr', tags: [], postings: [
      { account: 'Assets:Broker', amount: toMoney('2814969.9'), currency: 'INR' },
      { account: 'Assets:Bank', amount: toMoney('-2814969.9'), currency: 'INR' },
    ] }),
  ]
}

const amountInputs = (w: ReturnType<typeof mount>) =>
  w.findAll('td[data-column-id="amount"] input').map((i) => i.element as HTMLInputElement)

describe('TransactionTable amount formatting', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it("groups amounts at rest by each posting's currency", async () => {
    const w = mount(TransactionTable, { props: { transactions: txns() }, ...mountOpts })
    await flushPromises()
    expect(amountInputs(w).map((i) => i.value)).toEqual(['1,129.41', '-1,129.41', '28,14,969.90', '-28,14,969.90'])
  })

  it('shows the plain number while focused and groups it again on blur', async () => {
    const w = mount(TransactionTable, { props: { transactions: txns() }, ...mountOpts })
    await flushPromises()
    const input = w.findAll('td[data-column-id="amount"] input')[0]
    await input.trigger('focus')
    expect((input.element as HTMLInputElement).value).toBe('1129.41')
    await input.trigger('blur')
    expect((input.element as HTMLInputElement).value).toBe('1,129.41')
  })

  it('accepts a pasted amount with grouping commas', async () => {
    const w = mount(TransactionTable, { props: { transactions: txns() }, ...mountOpts })
    await flushPromises()
    const input = w.findAll('td[data-column-id="amount"] input')[0]
    await input.trigger('focus')
    await input.setValue('2,500.5')
    const emissions = w.emitted('transactionsUpdated') as TransactionViewModel[][][]
    const latest = emissions[emissions.length - 1][0]
    expect(String(latest[0].postings[0].amount)).toBe('2500.5')
  })

  it('groups read-only amounts', async () => {
    const w = mount(TransactionTable, { props: { transactions: txns(), editable: false }, ...mountOpts })
    await flushPromises()
    const cells = w.findAll('td[data-column-id="amount"] span').map((s) => s.text())
    expect(cells).toEqual(['1,129.41', '-1,129.41', '28,14,969.90', '-28,14,969.90'])
  })
})
