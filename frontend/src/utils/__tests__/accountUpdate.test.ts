// The account edit form sends metadata as a change (set + remove), never a
// snapshot. See dev-docs/investment-view.md §10.3 (Q5).

import { describe, it, expect } from 'vitest'
import type { AccountTreeNode, AccountFormSubmitData } from '@/types/accounts'
import { buildAccountUpdateRequest } from '@/utils/accountUpdate'

const node = (metadata: Record<string, string>): AccountTreeNode => ({
  id: 'Assets:Bank:CD1', name: 'CD1', fullPath: 'Assets:Bank:CD1', depth: 2, isVirtual: false,
  children: [], type: 'Assets', status: 'open', openDate: '2020-01-01', closeDate: null,
  aggregatedBalances: [], notes: metadata.description ?? null, currencyBadges: ['USD'],
  declaredCurrencies: ['USD'], metadata,
})

const form = (metadata: Record<string, string>, extra: Partial<AccountFormSubmitData> = {}): AccountFormSubmitData => ({
  name: 'Assets:Bank:CD1', openDate: '2020-01-01', currencies: ['USD'], description: '', metadata, ...extra,
})

describe('buildAccountUpdateRequest', () => {
  it('sends nothing for unchanged metadata', () => {
    const req = buildAccountUpdateRequest(node({ 'asset-class': 'cd' }), form({ 'asset-class': 'cd' }))
    expect(req.metadata).toBeUndefined()
    expect(req.remove_metadata).toBeUndefined()
  })

  it('sends only changed and new keys', () => {
    const req = buildAccountUpdateRequest(
      node({ 'asset-class': 'cd', interest_rate: '4%' }),
      form({ 'asset-class': 'cd', interest_rate: '4.5%', maturity_date: '2027-01-14' }),
    )
    expect(req.metadata).toEqual({ interest_rate: '4.5%', maturity_date: '2027-01-14' })
    expect(req.remove_metadata).toBeUndefined()
  })

  it('removes keys the form dropped, including description', () => {
    const req = buildAccountUpdateRequest(
      node({ 'asset-class': 'cd', branch: 'Main', description: 'old' }),
      form({ 'asset-class': 'cd' }),
    )
    expect(req.metadata).toBeUndefined()
    expect(req.remove_metadata).toEqual(['branch', 'description'])
  })

  it('carries description as a metadata key', () => {
    const req = buildAccountUpdateRequest(node({}), form({}, { description: 'My CD' }))
    expect(req.metadata).toEqual({ description: 'My CD' })
  })

  it('sets new_name only when renamed', () => {
    expect(buildAccountUpdateRequest(node({}), form({})).new_name).toBeUndefined()
    expect(buildAccountUpdateRequest(node({}), form({}, { name: 'Assets:Bank:CD2' })).new_name).toBe('Assets:Bank:CD2')
  })

  it('does not send currencies when the declared constraint is unchanged', () => {
    expect(buildAccountUpdateRequest(node({}), form({}, { currencies: ['USD'] })).currencies).toBeUndefined()
  })

  it('does not add a constraint to an unconstrained open from the pre-filled held currencies', () => {
    const unconstrained = { ...node({}), declaredCurrencies: [], currencyBadges: ['USD', 'EUR'] }
    const req = buildAccountUpdateRequest(unconstrained, form({ 'asset-class': 'savings' }, { currencies: ['EUR', 'USD'] }))
    expect(req.currencies).toBeUndefined()
    expect(req.metadata).toEqual({ 'asset-class': 'savings' })
  })

  it('sends currencies when the user changed them', () => {
    expect(buildAccountUpdateRequest(node({}), form({}, { currencies: ['USD', 'EUR'] })).currencies).toEqual(['USD', 'EUR'])
    const unconstrained = { ...node({}), declaredCurrencies: [], currencyBadges: ['USD'] }
    expect(buildAccountUpdateRequest(unconstrained, form({}, { currencies: ['USD', 'INR'] })).currencies).toEqual(['USD', 'INR'])
  })
})
