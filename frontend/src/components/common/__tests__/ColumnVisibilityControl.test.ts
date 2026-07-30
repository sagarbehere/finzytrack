import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ColumnVisibilityControl from '@/components/common/ColumnVisibilityControl.vue'

const allColumns = [
  { id: 'status', label: 'Info' },                               // required (not toggleable)
  { id: 'date', label: 'Date' },
  { id: 'amount', label: 'Amount' },
  { id: 'balance', label: 'Balance', disabled: true, disabledReason: 'Coming Soon' }, // disabled
  { id: 'actions', label: 'Actions' },                           // required (not toggleable)
]

function setup() {
  const toggleColumnVisibility = vi.fn()
  const resetToDefaults = vi.fn()
  const wrapper = mount(ColumnVisibilityControl, {
    props: {
      columnVisibility: { status: true, date: true, amount: true, actions: true },
      allColumns,
      toggleColumnVisibility,
      resetToDefaults,
    },
    attachTo: document.body,
  })
  return { wrapper, toggleColumnVisibility, resetToDefaults }
}

async function open(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('button[title="Show/hide columns"]').trigger('click')
  await flushPromises()
}

function colButton(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('button').find(b => b.text() === label)
}

describe('ColumnVisibilityControl', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('lists only toggleable columns — hides required and disabled entries', async () => {
    const { wrapper } = setup()
    await open(wrapper)
    const text = wrapper.text()
    expect(text).toContain('Date')
    expect(text).toContain('Amount')
    // Required (Info/Actions), disabled (Balance/Coming Soon), and the "Required"
    // label are all gone.
    expect(text).not.toContain('Info')
    expect(text).not.toContain('Actions')
    expect(text).not.toContain('Balance')
    expect(text).not.toContain('Coming Soon')
    expect(text).not.toContain('Required')
  })

  it('toggles a column and stays open (does not close on selection)', async () => {
    const { wrapper, toggleColumnVisibility } = setup()
    await open(wrapper)

    await colButton(wrapper, 'Date')!.trigger('click')
    expect(toggleColumnVisibility).toHaveBeenCalledWith('date')

    // The panel is still open — a second column is still clickable.
    await flushPromises()
    expect(colButton(wrapper, 'Amount')).toBeTruthy()
    await colButton(wrapper, 'Amount')!.trigger('click')
    expect(toggleColumnVisibility).toHaveBeenCalledWith('amount')
    expect(toggleColumnVisibility).toHaveBeenCalledTimes(2)
  })

  it('reset calls resetToDefaults', async () => {
    const { wrapper, resetToDefaults } = setup()
    await open(wrapper)
    await colButton(wrapper, 'Reset to defaults')!.trigger('click')
    expect(resetToDefaults).toHaveBeenCalled()
  })
})
