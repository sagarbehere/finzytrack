// The design gallery (S0170; design/specs/visual-language.md): one dev-only page that draws the visual language with
// the theme's tokens, for the owner's review in both themes. It shows the shell (sidebar, top bar, page header), the
// accounts table in the board's style (a multi-currency row, neutral balance links), the filters, badges, buttons,
// links and fields; `?theme=dark` and `?theme=light` force a theme for screenshots. Its absence from the production
// build is proved in galleryBuild.test.ts.
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import GalleryPage from '../../../src/app/gallery/GalleryPage.vue'

function open(search = ''): VueWrapper {
  window.history.replaceState({}, '', `/dev/gallery${search}`)
  return mount(GalleryPage, { attachTo: document.body })
}

let wrapper: VueWrapper | undefined
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.documentElement.classList.remove('dark')
})

describe('[S0170] the design gallery', () => {
  it('draws the shell: the main navigation, the top bar and the page header', () => {
    wrapper = open()
    const nav = wrapper.get('nav[aria-label="Main"]')
    const items = nav.findAll('a').map((a) => a.text())
    expect(items).toEqual([
      'Dashboards',
      'Import',
      'Transactions',
      'Accounts',
      'Budgets',
      'Investments',
      'Query',
      'AI Assistant',
    ])
    expect(nav.get('a[aria-current="page"]').text()).toBe('Accounts')
    const header = wrapper.get('header')
    for (const label of ['Back', 'Forward', 'Notifications', 'Theme'])
      expect(header.find(`button[aria-label="${label}"]`).exists(), label).toBe(true)
    expect(header.find('input[type="search"][aria-label="Search everything"]').exists()).toBe(true)
    expect(wrapper.get('h1').text()).toBe('Accounts')
  })

  it('draws the table with sentence-case headers, one line per currency and neutral balance links', () => {
    wrapper = open()
    const table = wrapper.get('section[aria-label="Accounts"] table')
    expect(table.findAll('th').map((th) => th.text())).toEqual(['Account', 'Type', 'Currencies', 'Balance'])
    const rows = table.findAll('tbody tr')
    expect(rows.length).toBeGreaterThanOrEqual(9)
    const multi = rows.find((r) => r.findAll('td').at(-1)?.findAll('a').length === 2)
    expect(multi, 'a row with two currencies').toBeDefined()
    const links = multi?.findAll('td').at(-1)?.findAll('a') ?? []
    expect(links.map((a) => a.text().split(' ').at(-1))).toEqual(['INR', 'USD'])
    for (const a of links) {
      expect(a.classes()).toEqual(expect.arrayContaining(['decoration-dotted', 'decoration-link-underline']))
      expect(a.classes()).toContain('hover:text-primary')
    }
    expect(table.get('th:last-child').classes()).toContain('text-right')
  })

  it('draws the filters: a segmented period, a filter field, a select and a toggle', () => {
    wrapper = open()
    const period = wrapper.get('[role="group"][aria-label="Period"]')
    const segments = period.findAll('button')
    expect(segments.map((b) => b.text())).toEqual(['All time', 'YTD', 'This month', 'More'])
    expect(segments.filter((b) => b.attributes('aria-pressed') === 'true').map((b) => b.text())).toEqual(['YTD'])
    expect(wrapper.find('input[type="search"][aria-label="Filter accounts"]').exists()).toBe(true)
    expect(
      wrapper
        .get('select')
        .findAll('option')
        .map((o) => o.text()),
    ).toEqual(['All', 'Assets', 'Liabilities', 'Equity', 'Income', 'Expenses'])
    const toggle = wrapper.get('[role="checkbox"]')
    const label = wrapper.get(`label[for="${toggle.attributes('id') ?? 'missing'}"]`)
    expect(label.text()).toBe('Show closed')
  })

  it('draws a badge per account type, every button kind, links and fields', () => {
    wrapper = open()
    const badges = wrapper
      .findAll('section[aria-labelledby="g-badges"] span')
      .map((b) => `${b.text()} ${b.classes().find((c) => c.startsWith('bg-badge-')) ?? 'no tone'}`)
    expect(badges).toEqual([
      'Assets bg-badge-assets',
      'Liabilities bg-badge-liabilities',
      'Equity bg-badge-equity',
      'Income bg-badge-income',
      'Expenses bg-badge-expenses',
    ])
    const variants = wrapper.findAll('button[data-slot="button"]').map((b) => b.attributes('data-variant'))
    for (const v of ['default', 'secondary', 'outline', 'ghost', 'link', 'destructive']) expect(variants).toContain(v)
    const invalid = wrapper.get('input[aria-invalid="true"]')
    const error = wrapper.get(`#${invalid.attributes('aria-describedby') ?? 'missing'}`)
    expect(error.classes()).toContain('text-destructive')
    expect(wrapper.find('a.text-primary').exists()).toBe(true)
  })

  it.each([
    ['?theme=dark', true],
    ['?theme=light', false],
  ])('forces the theme with %s', (search, dark) => {
    document.documentElement.classList.toggle('dark', !dark)
    wrapper = open(search)
    expect(document.documentElement.classList.contains('dark')).toBe(dark)
  })

  it('leaves the theme to the pre-paint script without a parameter', () => {
    document.documentElement.classList.add('dark')
    wrapper = open()
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('links to each theme, so a reviewer can switch', () => {
    wrapper = open()
    const hrefs = wrapper.findAll('a').map((a) => a.attributes('href'))
    expect(hrefs).toEqual(expect.arrayContaining(['?theme=light', '?theme=dark']))
  })
})
