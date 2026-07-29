import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import TransactionCardList from '@/components/common/TransactionCardList.vue'
import DetailsBadge from '@/components/documents/DetailsBadge.vue'
import { makeTx } from '@/test/factories'

const stubs = { AccountDropdown: true, CommodityDropdown: true, PriceTypeDropdown: true }

describe('TransactionCardList details affordance (mobile)', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('renders a "Details" row with the badge, gated on the status/Info column', () => {
    const tx = makeTx({ meta: { project: 'q1' } })
    const wrapper = mount(TransactionCardList, {
      props: { transactions: [tx], columnVisibility: { status: true }, enableBulkEdit: true },
      global: { stubs },
    })
    expect(wrapper.text()).toContain('Details')
    const badge = wrapper.findComponent(DetailsBadge)
    expect(badge.exists()).toBe(true)
    expect(badge.props('detailsMode')).toBe(true) // metadata-aware in the Transactions view
  })

  it('hides the Details row when the Info column is hidden', () => {
    const tx = makeTx({ meta: {} })
    const wrapper = mount(TransactionCardList, {
      props: { transactions: [tx], columnVisibility: { status: false }, enableBulkEdit: true },
      global: { stubs },
    })
    expect(wrapper.findComponent(DetailsBadge).exists()).toBe(false)
  })
})
