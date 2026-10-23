// tree-table rendering and interaction: totals on parents, expand/collapse
// (per row, all, to a level), search, sort, column visibility, share column,
// remembered view state, and keyboard navigation.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import RecipeTreeTable from '@/components/recipes/RecipeTreeTable.vue'
import type { TableColumn } from '@/types/recipes'

const rows = [
  { account: 'Assets:Bank:Checking', currency: 'USD', balance: 10 },
  { account: 'Assets:Bank:Savings', currency: 'USD', balance: 5 },
]
const columns: TableColumn[] = [{ key: 'balance', label: 'Balance', align: 'right' }]
const treeOptions = { pathField: 'account', groupBy: 'currency', skipLevels: 1, aggregates: { balance: 'sum' as const } }

function mountTree(extra: Record<string, unknown> = {}) {
  return mount(RecipeTreeTable, {
    props: { rows, columns, treeOptions, ...extra },
    global: { stubs: { RouterLink: true } },
    attachTo: document.body,
  })
}

/** Visible rows as their cell texts joined by spaces. */
function visible(w: ReturnType<typeof mount>): string[] {
  return w.findAll('tbody tr').map((tr) => tr.findAll('td').map((td) => td.text()).join(' ').replace(/\s+/g, ' ').trim())
}

const headerButton = (w: ReturnType<typeof mount>, text: string) =>
  w.findAll('thead button').find((b) => b.text().includes(text))!

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
})

describe('RecipeTreeTable', () => {
  it('shows every level expanded by default, with totals on parents', () => {
    expect(visible(mountTree())).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
  })

  it('collapses and re-expands a parent', async () => {
    const w = mountTree()
    await w.find('button[aria-label="Collapse Bank"]').trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15'])
    await w.find('button[aria-label="Expand Bank"]').trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
  })

  it('collapses and expands every level from the header toggle', async () => {
    const w = mountTree()
    await headerButton(w, 'Collapse all').trigger('click')
    expect(visible(w)).toEqual(['USD 15'])
    await headerButton(w, 'Expand all').trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
  })

  it('expands to a chosen level from the toolbar', async () => {
    const w = mountTree()
    const level = (n: number) => w.findAll('[aria-label="Expand to level"] button').find((b) => b.text() === String(n))!
    await level(2).trigger('click')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15'])
    await level(1).trigger('click')
    expect(visible(w)).toEqual(['USD 15'])
  })

  it('opens only the first expandDepth levels', () => {
    expect(visible(mountTree({ expandDepth: 1 }))).toEqual(['USD 15', 'Bank 15'])
  })

  it('searches by name, showing matches with their parents', async () => {
    const w = mountTree({ expandDepth: 0 })
    await w.find('input[type="search"]').setValue('sav')
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Savings 5'])
    await w.find('input[type="search"]').setValue('zzz')
    expect(visible(w)).toEqual(['Nothing matches “zzz”.'])
    await w.find('input[type="search"]').setValue('')
    expect(visible(w)).toEqual(['USD 15']) // back to the remembered expansion
  })

  it('sorts siblings by a column: largest first, smallest first, original order', async () => {
    const w = mountTree()
    await headerButton(w, 'Balance').trigger('click')
    expect(visible(w).slice(2)).toEqual(['Checking 10', 'Savings 5'])
    await headerButton(w, 'Balance').trigger('click')
    expect(visible(w).slice(2)).toEqual(['Savings 5', 'Checking 10'])
  })

  it('starts a column hidden when asked, and shows the share column', () => {
    const w = mountTree({ share: { column: 'balance' }, hiddenColumns: ['balance'] })
    expect(w.findAll('thead th').map((th) => th.text())).toEqual(['NameCollapse all', '% of total'])
    expect(visible(w)).toEqual(['USD 100.0%', 'Bank 100.0%', 'Checking 66.7%', 'Savings 33.3%'])
  })

  it('remembers expansion and sort across mounts', async () => {
    const w = mountTree({ stateKey: 'k' })
    await w.find('button[aria-label="Collapse Bank"]').trigger('click')
    await headerButton(w, 'Balance').trigger('click')
    w.unmount()
    const again = mountTree({ stateKey: 'k' })
    expect(visible(again)).toEqual(['USD 15', 'Bank 15'])
    expect(again.find('thead th[aria-sort="descending"]').exists()).toBe(true)
  })

  it('moves and expands with the keyboard', async () => {
    const w = mountTree()
    const focused = () => (document.activeElement as HTMLElement)?.dataset.rowId
    const press = async (key: string) => {
      document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
      await nextTick(); await nextTick()
    }
    ;(w.find('tbody tr').element as HTMLElement).focus()
    await press('ArrowDown')
    expect(focused()).toContain('Assets:Bank')
    await press('ArrowLeft') // expanded → collapse
    expect(visible(w)).toEqual(['USD 15', 'Bank 15'])
    await press('ArrowRight') // collapsed → expand
    expect(visible(w)).toEqual(['USD 15', 'Bank 15', 'Checking 10', 'Savings 5'])
    await press('End')
    expect(focused()).toContain('Savings')
  })

  it('copies the visible rows tab-separated, with raw values', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const w = mountTree({ expandDepth: 1 })
    await w.findAll('button').find((b) => b.text() === 'Copy')!.trigger('click')
    expect(writeText).toHaveBeenCalledWith('Name\tPath\tCurrency\tBalance\nUSD\t\tUSD\t15\n  Bank\tAssets:Bank\tUSD\t15')
  })

  it('downloads the visible rows as CSV, quoting values that need it', async () => {
    let blob: Blob | null = null
    const create = vi.spyOn(URL, 'createObjectURL').mockImplementation((b) => { blob = b as Blob; return 'blob:x' })
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const w = mountTree({
      rows: [{ account: 'Assets:Bank:Checking', currency: 'USD', balance: 10, name: 'Checking, joint' }],
      treeOptions: { ...treeOptions, labelField: 'name' },
      exportName: 'Account Balances',
    })
    await w.findAll('button').find((b) => b.text() === 'CSV')!.trigger('click')
    expect(create).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
    const text = await blob!.text()
    expect(text.replace(/^\ufeff/, '').split('\r\n')).toEqual([
      'Name,Path,Currency,Balance',
      'USD,,USD,10',
      '"  Bank › Checking, joint",Assets:Bank:Checking,USD,10',
    ])
  })

  it('explains a column from an info icon in its heading', () => {
    const w = mountTree({ columnDescriptions: { balance: 'Sum of the account postings.' } })
    const icon = w.find('thead [role="img"]')
    expect(icon.attributes('aria-label')).toBe('About Balance: Sum of the account postings.')
    expect(icon.attributes('tabindex')).toBe('0') // reachable by keyboard
  })

  it('shows the empty message when there are no rows', () => {
    expect(visible(mountTree({ rows: [], emptyText: 'Nothing here' }))).toEqual(['Nothing here'])
  })
})
