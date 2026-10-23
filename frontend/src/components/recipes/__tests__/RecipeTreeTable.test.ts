// tree-table rendering: rows nest under their group with totals, a parent can be
// collapsed and expanded, and expandDepth controls what starts open.
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecipeTreeTable from '@/components/recipes/RecipeTreeTable.vue'
import type { TableColumn } from '@/types/recipes'

const rows = [
  { account: 'Assets:Bank:Checking', currency: 'USD', balance: 10 },
  { account: 'Assets:Bank:Savings', currency: 'USD', balance: 5 },
]
const columns: TableColumn[] = [{ key: 'balance', label: 'Balance', align: 'right' }]
const treeOptions = { pathField: 'account', groupBy: 'currency', skipLevels: 1, sumKeys: ['balance'] }

/** Visible rows as "label balance". */
function visible(w: ReturnType<typeof mount>): string[] {
  return w.findAll('tbody tr').map((tr) => tr.findAll('td').map((td) => td.text()).join(' '))
}

describe('RecipeTreeTable', () => {
  it('shows every level expanded by default, with totals on parents', () => {
    const w = mount(RecipeTreeTable, { props: { rows, columns, treeOptions }, global: { stubs: { RouterLink: true } } })
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
  })

  it('collapses and re-expands a parent', async () => {
    const w = mount(RecipeTreeTable, { props: { rows, columns, treeOptions }, global: { stubs: { RouterLink: true } } })
    await w.find('button[aria-label="Collapse Bank"]').trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15'])
    await w.find('button[aria-label="Expand Bank"]').trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
  })

  it('opens only the first expandDepth levels', () => {
    const w = mount(RecipeTreeTable, { props: { rows, columns, treeOptions, expandDepth: 1 }, global: { stubs: { RouterLink: true } } })
    expect(visible(w)).toEqual(['USD 15', 'Bank 15'])
  })

  it('shows the empty message when there are no rows', () => {
    const w = mount(RecipeTreeTable, { props: { rows: [], columns, treeOptions, emptyText: 'Nothing here' }, global: { stubs: { RouterLink: true } } })
    expect(visible(w)).toEqual(['Nothing here'])
  })
})
