import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecipeBudgetProgress, {
  type BudgetProgressFields,
} from '@/components/recipes/RecipeBudgetProgress.vue'

// The flat joinBudgetActual field mapping (defaults).
const FIELDS: BudgetProgressFields = {
  account: 'account',
  budget: 'budget',
  actual: 'actual',
  remaining: 'remaining',
  pctUsed: 'pctUsed',
  currency: 'currency',
  direction: 'direction',
}

const ROWS = [
  { account: 'Expenses:Groceries', budget: '1000', actual: '600', remaining: '400', pctUsed: 0.6, currency: 'USD', direction: 'under-good' },
  { account: 'Expenses:Travel', budget: '500', actual: '520', remaining: '-20', pctUsed: 1.04, currency: 'USD', direction: 'under-good' },
]

describe('RecipeBudgetProgress — select action (master-detail)', () => {
  it('renders select rows as buttons and emits the resolved params on click', async () => {
    const wrapper = mount(RecipeBudgetProgress, {
      props: {
        rows: ROWS,
        fields: FIELDS,
        // Renderer supplies this from a { select: { account: "{{row.account}}" } } link.
        getRowSelect: (row: Record<string, unknown>) => ({ account: String(row.account) }),
        activeParams: { account: 'Expenses:Groceries' },
      },
    })

    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2) // one per row, no RouterLink needed

    await buttons[1].trigger('click') // Travel
    expect(wrapper.emitted('select')).toEqual([[{ account: 'Expenses:Travel' }]])
  })

  it('marks the row matching the live selection as active', () => {
    const wrapper = mount(RecipeBudgetProgress, {
      props: {
        rows: ROWS,
        fields: FIELDS,
        getRowSelect: (row: Record<string, unknown>) => ({ account: String(row.account) }),
        activeParams: { account: 'Expenses:Groceries' },
      },
    })
    // Exactly the Groceries row carries the active ring class.
    const active = wrapper.findAll('button').filter((b) => b.classes().includes('ring-indigo-200'))
    expect(active).toHaveLength(1)
    expect(active[0].text()).toContain('Groceries')
  })

  it('falls back to a non-interactive row when no link or select is given', () => {
    const wrapper = mount(RecipeBudgetProgress, {
      props: { rows: ROWS, fields: FIELDS },
    })
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.emitted('select')).toBeUndefined()
  })
})
