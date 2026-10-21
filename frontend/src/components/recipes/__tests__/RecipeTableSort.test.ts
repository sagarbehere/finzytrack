// Clicking a recipe-table header sorts it; row actions keep pointing at the right row.
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecipeTable from '@/components/recipes/RecipeTable.vue'
import type { TableColumn } from '@/types/recipes'

const data = [
  { holding: 'VOO', value: 300 },
  { holding: 'BND', value: 900 },
  { holding: 'VTI', value: 100 },
]
const columns: TableColumn[] = [
  { key: 'holding', label: 'Holding', getSelect: ({ row, rowIndex }) => ({ holding: String(row.holding), i: String(rowIndex) }) },
  { key: 'value', label: 'Value', align: 'right' },
]

function render(props: Record<string, unknown> = {}) {
  return mount(RecipeTable, { props: { data, columns, ...props }, global: { stubs: { RouterLink: true } } })
}
const holdings = (w: ReturnType<typeof render>) => w.findAll('tbody tr').map((tr) => tr.find('td').text())
const header = (w: ReturnType<typeof render>, i: number) => w.findAll('th')[i]

describe('RecipeTable sorting', () => {
  it('cycles ascending → descending → original order', async () => {
    const w = render()
    await header(w, 1).find('button').trigger('click')
    expect(holdings(w)).toEqual(['VTI', 'VOO', 'BND'])
    expect(header(w, 1).attributes('aria-sort')).toBe('ascending')
    await header(w, 1).find('button').trigger('click')
    expect(holdings(w)).toEqual(['BND', 'VOO', 'VTI'])
    expect(header(w, 1).attributes('aria-sort')).toBe('descending')
    await header(w, 1).find('button').trigger('click')
    expect(holdings(w)).toEqual(['VOO', 'BND', 'VTI'])
    expect(header(w, 1).attributes('aria-sort')).toBeUndefined()
  })

  it("passes each row's original index to row actions", async () => {
    const w = render()
    await header(w, 1).find('button').trigger('click')   // VTI, VOO, BND
    await w.findAll('tbody tr')[0].find('button').trigger('click')
    expect(w.emitted('select')).toEqual([[{ holding: 'VTI', i: '2' }]])
  })

  it('highlights the active row wherever it sorts to', async () => {
    const w = render({ activeParams: { holding: 'BND', i: '1' } })
    await header(w, 1).find('button').trigger('click')
    const active = w.findAll('tbody tr').findIndex((tr) => tr.classes().includes('bg-indigo-50'))
    expect(active).toBe(2)
  })

  it('keeps the sort across re-mounts with the same sortStateKey', async () => {
    const first = render({ sortStateKey: 'k1' })
    await header(first, 1).find('button').trigger('click')
    first.unmount()
    expect(holdings(render({ sortStateKey: 'k1' }))).toEqual(['VTI', 'VOO', 'BND'])
    expect(holdings(render({ sortStateKey: 'other' }))).toEqual(['VOO', 'BND', 'VTI'])
    expect(holdings(render())).toEqual(['VOO', 'BND', 'VTI'])
  })
})
