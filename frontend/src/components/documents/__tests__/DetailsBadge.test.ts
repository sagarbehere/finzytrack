import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import DetailsBadge from '@/components/documents/DetailsBadge.vue'
import { makeTx } from '@/test/factories'

describe('DetailsBadge', () => {
  it('shows the document count when there are documents', () => {
    const transaction = makeTx({ meta: { document: 'a.pdf', document2: 'b.pdf' } })
    const wrapper = mount(DetailsBadge, { props: { transaction } })
    expect(wrapper.text()).toContain('2')
  })

  it('renders no count when there are no documents', () => {
    const transaction = makeTx({ meta: {} })
    const wrapper = mount(DetailsBadge, { props: { transaction } })
    expect(wrapper.text().trim()).toBe('')
  })

  it('emits documentClick with the transaction id', async () => {
    const transaction = makeTx({ id: 'tx-1', meta: { document: 'a.pdf' } })
    const wrapper = mount(DetailsBadge, { props: { transaction } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('documentClick')?.[0]).toEqual(['tx-1'])
  })
})
