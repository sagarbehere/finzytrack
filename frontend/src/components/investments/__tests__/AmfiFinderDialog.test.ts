// Finding an Indian mutual fund's AMFI scheme code by name, and what the user
// sees when the search service fails.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const m = vi.hoisted(() => ({ search: vi.fn(), display: vi.fn() }))
vi.mock('@/services/generated-api', () => ({ PricesService: { searchAmfiSchemes: m.search } }))
vi.mock('@/utils/ErrorHandler', () => ({ errorHandler: { display: m.display } }))
vi.mock('@/utils/date', () => ({ todayLocal: () => '2026-10-21' }))

import AmfiFinderDialog from '@/components/investments/AmfiFinderDialog.vue'

const schemes = [
  { scheme_code: '101539', scheme_name: 'Sundaram Mid Cap Fund - Regular Plan - GROWTH', nav: '1386.6013', nav_date: '2026-10-19' },
  { scheme_code: '119581', scheme_name: 'Sundaram Mid Cap Fund - Direct Plan - GROWTH', nav: '1529.8227', nav_date: '2026-10-19' },
  { scheme_code: '111941', scheme_name: 'Sundaram Mid Cap Fund Institutional Plan - Growth', nav: '767.7664', nav_date: '2021-12-31' },
]

function render(initialQuery = 'Sundaram Mid Cap') {
  return mount(AmfiFinderDialog, {
    props: { open: true, commodity: 'SUNDMID', initialQuery },
    attachTo: document.body,
  })
}
const text = () => (document.body.textContent ?? '').replace(/\s+/g, ' ')
const button = (label: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === label) as HTMLButtonElement

beforeEach(() => {
  m.search.mockReset()
  m.display.mockReset()
})
afterEach(() => { document.body.innerHTML = '' })

describe('AmfiFinderDialog', () => {
  it("searches for the holding's name on open and lists schemes with their NAV", async () => {
    m.search.mockResolvedValue({ data: { schemes, total: 3 } })
    render()
    await flushPromises()
    expect(m.search).toHaveBeenCalledWith('Sundaram Mid Cap')
    expect(text()).toContain('Sundaram Mid Cap Fund - Regular Plan - GROWTH')
    expect(text()).toContain('Code 101539')
    expect(text()).toContain('Last NAV Dec 31, 2021')   // a closed scheme says how old its NAV is
  })

  it('emits the chosen scheme code and closes', async () => {
    m.search.mockResolvedValue({ data: { schemes, total: 3 } })
    const w = render()
    await flushPromises()
    const rows = document.body.querySelectorAll('tbody tr')
    ;(rows[1].querySelector('button') as HTMLButtonElement).click()
    expect(w.emitted('select')).toEqual([['119581']])
    expect(w.emitted('update:open')).toEqual([[false]])
  })

  it('explains a failed search inline and shows the reason as a toast', async () => {
    const err = new Error('502')
    m.search.mockRejectedValue(err)
    render()
    await flushPromises()
    expect(document.body.querySelector('[data-test="amfi-error"]')?.textContent).toContain('type amfi: and the code')
    expect(m.display).toHaveBeenCalledWith(err, { PRICE_SOURCE_UNAVAILABLE: 'Toast', PRICE_SOURCE_CHANGED: 'Toast' })
  })

  it('shows the NAV warning while still listing the schemes', async () => {
    m.search.mockResolvedValue({ data: {
      schemes: schemes.map((s) => ({ ...s, nav: null, nav_date: null })), total: 3,
      nav_warning: 'The latest NAVs could not be loaded.',
    } })
    render()
    await flushPromises()
    expect(text()).toContain('The latest NAVs could not be loaded.')
    expect(document.body.querySelectorAll('tbody tr').length).toBe(3)
  })

  it('says when nothing matches, and asks for 3 letters before searching', async () => {
    m.search.mockResolvedValue({ data: { schemes: [], total: 0 } })
    render('zzqq nothing')
    await flushPromises()
    expect(text()).toContain('No funds match “zzqq nothing”')

    document.body.innerHTML = ''
    m.search.mockClear()
    render('ab')
    await flushPromises()
    expect(m.search).not.toHaveBeenCalled()
    button('Search').click()
    await flushPromises()
    expect(text()).toContain('Type at least 3 letters')
  })

  it('tells the user when only the first matches are listed', async () => {
    m.search.mockResolvedValue({ data: { schemes, total: 13 } })
    render()
    await flushPromises()
    expect(text()).toContain('Showing 3 of 13 matches')
  })
})
