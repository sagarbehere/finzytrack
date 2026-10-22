// The dashboard metadata hint is an offer: shown only when the review has unset
// items in its scope, and stays dismissed per dashboard. See
// dev-docs/investment-view.md §10.3 (Q8).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, RouterLinkStub } from '@vue/test-utils'

const getMetadataReview = vi.fn()
vi.mock('@/services/generated-api', () => ({
  MetadataService: { getMetadataReview: () => getMetadataReview() },
}))

import MetadataHintBanner from '@/components/recipes/MetadataHintBanner.vue'

const hint = { scope: 'account' as const, text: 'Want yield per account? Add an account type.' }

function mountBanner(dashboardId = 'cash-deposits') {
  return mount(MetadataHintBanner, {
    props: { dashboardId, hint },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

function review(summary: Record<string, number>) {
  return { data: { items: [], summary, has_operating_currencies: true } }
}

describe('MetadataHintBanner', () => {
  beforeEach(() => {
    localStorage.clear()
    getMetadataReview.mockReset()
  })

  it.each([
    ['income-account', 'https://docs.finzytrack.com/reference/metadata/#on-an-income-accounts-open'],
    ['account', 'https://docs.finzytrack.com/reference/metadata/#on-an-assets-accounts-open'],
    ['commodity', 'https://docs.finzytrack.com/reference/metadata/#on-a-commodity'],
  ] as const)('links "Learn more" to the Metadata reference section for %s', async (scope, url) => {
    getMetadataReview.mockResolvedValue(review({ commodity: 1, account: 1, 'income-account': 1 }))
    const wrapper = mount(MetadataHintBanner, {
      props: { dashboardId: `d-${scope}`, hint: { scope, text: 'Offer' } },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    await flushPromises()
    const link = wrapper.findAll('a').find(a => a.text() === 'Learn more')!
    expect(link.attributes('href')).toBe(url)
    expect(link.attributes('target')).toBe('_blank')
  })

  it('shows the offer when its scope has unset items, linking to the Setup tab', async () => {
    getMetadataReview.mockResolvedValue(review({ commodity: 0, account: 3, 'income-account': 0 }))
    const wrapper = mountBanner()
    await flushPromises()
    expect(wrapper.text()).toContain(hint.text)
    expect(wrapper.findComponent(RouterLinkStub).props('to')).toEqual({
      path: '/investments', query: { tab: 'metadata' },
    })
  })

  it('stays hidden when nothing in its scope is unset', async () => {
    getMetadataReview.mockResolvedValue(review({ commodity: 5, account: 0, 'income-account': 0 }))
    const wrapper = mountBanner()
    await flushPromises()
    expect(wrapper.html()).not.toContain(hint.text)
  })

  it('stays hidden when the review cannot load', async () => {
    getMetadataReview.mockRejectedValue(new Error('offline'))
    const wrapper = mountBanner()
    await flushPromises()
    expect(wrapper.html()).not.toContain(hint.text)
  })

  it('stays dismissed for that dashboard only', async () => {
    getMetadataReview.mockResolvedValue(review({ commodity: 0, account: 3, 'income-account': 0 }))
    const first = mountBanner()
    await flushPromises()
    await first.find('button').trigger('click')
    expect(first.html()).not.toContain(hint.text)

    const again = mountBanner()
    await flushPromises()
    expect(again.html()).not.toContain(hint.text)
    expect(getMetadataReview).toHaveBeenCalledTimes(1)

    const other = mountBanner('another-dashboard')
    await flushPromises()
    expect(other.text()).toContain(hint.text)
  })
})
