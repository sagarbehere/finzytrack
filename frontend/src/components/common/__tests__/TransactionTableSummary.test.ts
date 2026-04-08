import { mount } from '@vue/test-utils'
import TransactionTableSummary from '@/components/common/TransactionTableSummary.vue'
import { makeTx, makeImportContext } from '@/test/factories'
import type { ImportContext } from '@/types/transactions'

describe('TransactionTableSummary', () => {
  it('shows edited count', () => {
    const transactions = [
      makeTx({ internal: { isNew: false, isModified: true } }),
      makeTx({ internal: { isNew: false, isModified: false } }),
      makeTx({ internal: { isNew: false, isModified: false } }),
    ]
    const wrapper = mount(TransactionTableSummary, { props: { transactions } })
    const text = wrapper.text()
    expect(text).toContain('Edited:')
    // Find the edited count: it's after "Edited:" and should be 1
    const editedSection = wrapper.findAll('.flex.items-center.gap-2').find(el => el.text().includes('Edited:'))
    expect(editedSection?.text()).toContain('1')
  })

  it('shows unbalanced count', () => {
    const transactions = [
      makeTx({ postings: [{ amount: 100, currency: 'USD' }, { amount: -100, currency: 'USD' }] }),
      makeTx({ postings: [{ amount: 100, currency: 'USD' }, { amount: -50, currency: 'USD' }] }),
    ]
    const wrapper = mount(TransactionTableSummary, { props: { transactions } })
    const unbalancedSection = wrapper.findAll('.flex.items-center.gap-2').find(el => el.text().includes('Unbalanced:'))
    expect(unbalancedSection?.text()).toContain('1')
    // Red styling on the count
    const countSpan = unbalancedSection?.find('.font-semibold')
    expect(countSpan?.classes()).toContain('text-red-600')
  })

  it('shows zero unbalanced without red styling', () => {
    const transactions = [
      makeTx({ postings: [{ amount: 50, currency: 'USD' }, { amount: -50, currency: 'USD' }] }),
      makeTx({ postings: [{ amount: 100, currency: 'USD' }, { amount: -100, currency: 'USD' }] }),
    ]
    const wrapper = mount(TransactionTableSummary, { props: { transactions } })
    const unbalancedSection = wrapper.findAll('.flex.items-center.gap-2').find(el => el.text().includes('Unbalanced:'))
    expect(unbalancedSection?.text()).toContain('0')
    const countSpan = unbalancedSection?.find('.font-semibold')
    expect(countSpan?.classes()).not.toContain('text-red-600')
  })

  it('shows duplicate count from importContext', () => {
    const tx1 = makeTx()
    const tx2 = makeTx()
    const importContext = new Map<string, ImportContext>([
      [tx1.id, makeImportContext({ is_duplicate: true })],
      [tx2.id, makeImportContext({ is_duplicate: false })],
    ])
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx1, tx2], importContext } })
    const dupSection = wrapper.findAll('.flex.items-center.gap-2').find(el => el.text().includes('Potential duplicates:'))
    expect(dupSection?.text()).toContain('1')
  })

  it('disables duplicate button when count is zero', () => {
    const tx1 = makeTx()
    const importContext = new Map<string, ImportContext>([
      [tx1.id, makeImportContext({ is_duplicate: false })],
    ])
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx1], importContext } })
    const button = wrapper.find('button')
    expect(button.attributes('disabled')).toBeDefined()
  })

  it('emits duplicateClick when duplicate summary is clicked', async () => {
    const tx1 = makeTx()
    const tx2 = makeTx()
    const importContext = new Map<string, ImportContext>([
      [tx1.id, makeImportContext({ is_duplicate: true })],
      [tx2.id, makeImportContext({ is_duplicate: false })],
    ])
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx1, tx2], importContext } })
    const button = wrapper.find('button')
    await button.trigger('click')
    expect(wrapper.emitted('duplicateClick')).toBeTruthy()
    expect(wrapper.emitted('duplicateClick')![0]).toEqual([tx1.id])
  })

  it('shows net flow by currency when importContext has source_account', () => {
    const tx = makeTx({
      meta: { source_account: 'Assets:Bank' },
      postings: [
        { account: 'Assets:Bank', amount: -500, currency: 'USD' },
        { account: 'Expenses:Food', amount: 500, currency: 'USD' },
      ],
    })
    const importContext = new Map<string, ImportContext>([[tx.id, makeImportContext()]])
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx], importContext } })
    expect(wrapper.text()).toContain('-500.00')
    expect(wrapper.text()).toContain('USD')
  })

  it('shows account totals by top-level account', () => {
    const tx = makeTx({
      postings: [
        { account: 'Expenses:Food', amount: 50, currency: 'USD' },
        { account: 'Assets:Bank', amount: -50, currency: 'USD' },
      ],
    })
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx] } })
    const text = wrapper.text()
    expect(text).toContain('Expenses')
    expect(text).toContain('Assets')
    expect(text).toContain('50.00')
  })

  it('hides net flow section when no importContext', () => {
    const tx = makeTx()
    const wrapper = mount(TransactionTableSummary, { props: { transactions: [tx] } })
    expect(wrapper.text()).not.toContain('Net Flow:')
    expect(wrapper.text()).not.toContain('Potential duplicates:')
  })
})
