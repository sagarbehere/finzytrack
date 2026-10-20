// Contract for the Setup & Metadata helpers. See dev-docs/investment-view.md §10.3.

import { describe, it, expect } from 'vitest'
import type { MetadataFieldSpec } from '@/services/generated-api'
import {
  canonicalValue,
  currencyRoleAfterEdit,
  diffMetadata,
  fieldApplies,
  fieldsFor,
  isRecognizedValue,
} from '@/utils/metadataVocabulary'

const assetClass: MetadataFieldSpec = {
  key: 'asset-class', scope: 'account', kind: 'enum', label: 'Account type', adds: '',
  values: ['checking', 'savings', 'cd', 'bond', 'cash'], review: true,
}
const maturity: MetadataFieldSpec = {
  key: 'maturity_date', scope: 'account', kind: 'date', label: 'Maturity date', adds: '',
  review: true, requires_key: 'asset-class', requires_values: ['cd', 'bond'],
}
const incomeType: MetadataFieldSpec = {
  key: 'income-type', scope: 'income-account', kind: 'enum', label: 'Income type', adds: '',
  values: ['interest', 'dividend'], review: true,
}

describe('fieldsFor', () => {
  it('keeps only the given scope, in order', () => {
    expect(fieldsFor([assetClass, incomeType, maturity], 'account')).toEqual([assetClass, maturity])
  })
})

describe('fieldApplies', () => {
  it('applies when the field has no requirement', () => {
    expect(fieldApplies(assetClass, {})).toBe(true)
  })
  it('applies only when the required key has a listed value (case-insensitive)', () => {
    expect(fieldApplies(maturity, {})).toBe(false)
    expect(fieldApplies(maturity, { 'asset-class': 'savings' })).toBe(false)
    expect(fieldApplies(maturity, { 'asset-class': ' CD ' })).toBe(true)
  })
})

describe('canonicalValue / isRecognizedValue', () => {
  it('maps a recognized value to its listed spelling', () => {
    expect(canonicalValue(assetClass, ' Savings ')).toBe('savings')
    expect(isRecognizedValue(assetClass, 'SAVINGS')).toBe(true)
  })
  it('keeps a custom value as typed (trimmed)', () => {
    expect(canonicalValue(assetClass, ' Brokerage-Sweep ')).toBe('Brokerage-Sweep')
    expect(isRecognizedValue(assetClass, 'brokerage-sweep')).toBe(false)
  })
})

describe('diffMetadata', () => {
  const original = { 'asset-class': 'cd', interest_rate: '4.5%' }

  it('returns null when nothing changed', () => {
    expect(diffMetadata(original, { 'asset-class': ' cd ' }, ['asset-class', 'interest_rate'])).toBeNull()
  })
  it('sets new and changed keys', () => {
    expect(diffMetadata(original, { interest_rate: '5%', maturity_date: '2027-01-14' }, ['interest_rate', 'maturity_date']))
      .toEqual({ set: { interest_rate: '5%', maturity_date: '2027-01-14' }, remove: [] })
  })
  it('removes a key that was emptied', () => {
    expect(diffMetadata(original, { interest_rate: '' }, ['interest_rate']))
      .toEqual({ set: {}, remove: ['interest_rate'] })
  })
  it('ignores an emptied key that was never set', () => {
    expect(diffMetadata(original, { maturity_date: '' }, ['maturity_date'])).toBeNull()
  })
  it('ignores keys outside the given list', () => {
    expect(diffMetadata(original, { note: 'x' }, ['asset-class'])).toBeNull()
  })
})

describe('currencyRoleAfterEdit', () => {
  const classes = ['cash', 'currency']

  it('is null when operating currencies decide the role', () => {
    expect(currencyRoleAfterEdit(true, 'etf', true, classes)).toBeNull()
  })
  it('reports an untagged currency becoming a holding', () => {
    expect(currencyRoleAfterEdit(true, 'etf', false, classes)).toBe('holding')
  })
  it('reports a holding becoming a currency when tagged cash or cleared', () => {
    expect(currencyRoleAfterEdit(false, 'Cash', false, classes)).toBe('currency')
    expect(currencyRoleAfterEdit(false, '', false, classes)).toBe('currency')
  })
  it('is null when the role does not change', () => {
    expect(currencyRoleAfterEdit(false, 'stock', false, classes)).toBeNull()
    expect(currencyRoleAfterEdit(true, 'currency', false, classes)).toBeNull()
  })
})
