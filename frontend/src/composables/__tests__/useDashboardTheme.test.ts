import { describe, it, expect } from 'vitest'
import { useDashboardTheme } from '../useDashboardTheme'

// These test the pure resolver against the built-in default theme (Dusty
// Spectrum) that is the singleton's initial value — no network needed.
const { resolveThemeColor, categoricalPalette, hashColor, familyColor } = useDashboardTheme()

describe('resolveThemeColor', () => {
  it('passes a raw hex through unchanged (value-level escape hatch)', () => {
    expect(resolveThemeColor('#123456', true)).toBe('#123456')
    expect(resolveThemeColor('rebeccapurple', false)).toBe('rebeccapurple')
  })

  it('resolves brand per mode', () => {
    expect(resolveThemeColor('{{theme.brand}}', true)).toBe('#7b93d6')
    expect(resolveThemeColor('{{theme.brand}}', false)).toBe('#4f6bb0')
  })

  it('resolves a valence color', () => {
    expect(resolveThemeColor('{{theme.valence.bad}}', true)).toBe('#d97066')
    expect(resolveThemeColor('{{theme.valence.complete}}', false)).toBe('#3a80b0')
  })

  it('resolves a series alias recursively (series.actual → brand)', () => {
    expect(resolveThemeColor('{{theme.series.actual}}', true)).toBe('#7b93d6')
    expect(resolveThemeColor('{{theme.series.budget}}', false)).toBe('#9aa5b1')
  })

  it('resolves an indexed categorical slot', () => {
    expect(resolveThemeColor('{{theme.categorical.2}}', true)).toBe('#3cc199')
  })

  it('leaves an unknown token untouched (caller falls back)', () => {
    expect(resolveThemeColor('{{theme.nope}}', true)).toBe('{{theme.nope}}')
    expect(resolveThemeColor('{{theme.categorical}}', true)).toBe('{{theme.categorical}}')
  })
})

describe('categoricalPalette', () => {
  it('returns the ordered palette for the mode', () => {
    expect(categoricalPalette(true)).toHaveLength(12)
    expect(categoricalPalette(false)[0]).toBe('#3f89c3')
  })
})

describe('stickiness (hashColor / familyColor)', () => {
  it('hashColor is stable and drawn from the palette', () => {
    const c = hashColor('Groceries', true)
    expect(hashColor('Groceries', true)).toBe(c)
    expect(categoricalPalette(true)).toContain(c)
  })

  it('familyColor ignores a leading account-type root', () => {
    // Expenses:EatingOut:Coffee and EatingOut:Coffee are the same family+depth.
    expect(familyColor('Expenses:EatingOut:Coffee', true)).toBe(familyColor('EatingOut:Coffee', true))
  })

  it('familyColor groups siblings under one family hue', () => {
    // A top-level family is its own palette color (depth 0).
    expect(familyColor('EatingOut', true)).toBe(hashColor('EatingOut', true))
  })

  it('familyColor shades by depth (child differs from its family root)', () => {
    expect(familyColor('EatingOut:Coffee', true)).not.toBe(familyColor('EatingOut', true))
  })
})
