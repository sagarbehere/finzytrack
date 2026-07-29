import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BulkActionBar from '@/components/transactions/BulkActionBar.vue'
import type { BulkOperation } from '@/utils/bulkOperations'

const mountOpts = {
  props: { selectedCount: 3, accountsInSelection: ['Expenses:Fees'], editableMetaKeysInSelection: ['project'], tagsInSelection: ['trip'], linksInSelection: ['invoice-1'] },
  global: { stubs: { AccountDropdown: true } },
}

function lastApply(wrapper: ReturnType<typeof mount>): BulkOperation {
  const emitted = wrapper.emitted('apply') as BulkOperation[][]
  return emitted[emitted.length - 1][0]
}

describe('BulkActionBar', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('shows the selected count and emits clear', async () => {
    const wrapper = mount(BulkActionBar, mountOpts)
    expect(wrapper.text()).toContain('3 selected')
    const clearBtn = wrapper.findAll('button').find(b => b.text() === 'Clear selection')!
    await clearBtn.trigger('click')
    expect(wrapper.emitted('clear')).toBeTruthy()
  })

  it('emits delete', async () => {
    const wrapper = mount(BulkActionBar, mountOpts)
    const deleteBtn = wrapper.findAll('button').find(b => b.text() === 'Delete')!
    await deleteBtn.trigger('click')
    expect(wrapper.emitted('delete')).toBeTruthy()
  })

  it('emits an addTag operation from the Tag popover', async () => {
    const wrapper = mount(BulkActionBar, mountOpts)

    // Open the Tag popover (buttons are labelled by their text).
    const tagButton = wrapper.findAll('button').find(b => b.text() === 'Tag')!
    await tagButton.trigger('click')
    await flushPromises()

    await wrapper.get('input[placeholder="tag"]').setValue('q1-reclass')
    const applyBtn = wrapper.findAll('button').find(b => b.text() === 'Apply')!
    await applyBtn.trigger('click')

    expect(lastApply(wrapper)).toEqual({ type: 'addTag', tag: 'q1-reclass' })
  })

  it('emits a setFlag operation from the Flag popover', async () => {
    const wrapper = mount(BulkActionBar, mountOpts)
    const flagButton = wrapper.findAll('button').find(b => b.text() === 'Flag')!
    await flagButton.trigger('click')
    await flushPromises()

    const pendingBtn = wrapper.findAll('button').find(b => b.text().includes('Pending'))!
    await pendingBtn.trigger('click')

    expect(lastApply(wrapper)).toEqual({ type: 'setFlag', flag: '!' })
  })

  it('rejects a system-managed metadata key (Apply stays disabled)', async () => {
    const wrapper = mount(BulkActionBar, mountOpts)
    const metaButton = wrapper.findAll('button').find(b => b.text() === 'Metadata')!
    await metaButton.trigger('click')
    await flushPromises()

    await wrapper.get('input[placeholder="key"]').setValue('source_account')
    await wrapper.get('input[placeholder="value"]').setValue('x')
    await flushPromises()

    const applyBtn = wrapper.findAll('button').find(b => b.text() === 'Apply')!
    expect(applyBtn.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('system-managed')
  })
})
