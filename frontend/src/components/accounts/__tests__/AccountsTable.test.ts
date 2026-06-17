import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AccountsTable from '@/components/accounts/AccountsTable.vue'
import type { AccountTreeNode } from '@/types/accounts'

function node(overrides: Partial<AccountTreeNode> = {}): AccountTreeNode {
  return {
    id: 'Assets:Bank:Checking',
    name: 'Checking',
    fullPath: 'Assets:Bank:Checking',
    depth: 2,
    isVirtual: false,
    children: [],
    type: 'Assets',
    status: 'open',
    openDate: '2020-01-01',
    closeDate: null,
    aggregatedBalances: [],
    notes: null,
    currencyBadges: ['USD'],
    declaredCurrencies: ['USD'],
    metadata: {},
    ...overrides,
  }
}

describe('AccountsTable account name rendering', () => {
  it('renders a real account name exactly once (no duplicate from the badge slot)', () => {
    const wrapper = mount(AccountsTable, {
      props: { displayNodes: [node()], expandedIds: new Set<string>() },
    })
    // The bold clickable button is the only name element for a real account.
    const nameCell = wrapper.find('td')
    expect(nameCell.text()).toBe('Checking') // not "CheckingChecking"
    expect(wrapper.findAll('button').filter(b => b.text() === 'Checking').length).toBe(1)
  })

  it('renders a virtual (no-open) account name once, italicized', () => {
    const wrapper = mount(AccountsTable, {
      props: { displayNodes: [node({ isVirtual: true, name: 'Bank' })], expandedIds: new Set<string>() },
    })
    expect(wrapper.find('td').text()).toBe('Bank')
    expect(wrapper.findAll('button').filter(b => b.text() === 'Bank').length).toBe(0)
  })

  it('shows the paperclip badge with a count when the account has documents', () => {
    const wrapper = mount(AccountsTable, {
      props: {
        displayNodes: [node()],
        expandedIds: new Set<string>(),
        documentCounts: { 'Assets:Bank:Checking': 3 },
      },
    })
    // Name once + the badge count; never the name twice.
    expect(wrapper.find('td').text()).toContain('Checking')
    expect(wrapper.find('td').text()).not.toContain('CheckingChecking')
    expect(wrapper.find('td').text()).toContain('3')
  })

  it('clicking the paperclip badge opens the detail drawer (same as the name)', async () => {
    const wrapper = mount(AccountsTable, {
      props: {
        displayNodes: [node()],
        expandedIds: new Set<string>(),
        documentCounts: { 'Assets:Bank:Checking': 3 },
      },
    })
    const badge = wrapper.findAll('button').find(b => b.text() === '3')!
    await badge.trigger('click')
    const emitted = wrapper.emitted('show-detail')
    expect(emitted).toBeTruthy()
    expect((emitted![0][0] as AccountTreeNode).fullPath).toBe('Assets:Bank:Checking')
  })

  it('omits the badge when the account has no documents', () => {
    const wrapper = mount(AccountsTable, {
      props: {
        displayNodes: [node()],
        expandedIds: new Set<string>(),
        documentCounts: { 'Assets:Bank:Checking': 0 },
      },
    })
    expect(wrapper.find('td').text()).toBe('Checking')
  })
})
