// Our own primitives over the vendored ones (ADR-0035). BL-0325 (owner, 2026-11-28: "App-level override
// (Recommended)"): the vendored destructive button draws its focus ring as `ring-destructive/20` (light) and `/40`
// (dark), which no colour can bring to 3:1; our destructive button draws the app's one focus ring instead
// (`ring-ring/50`, 3:1 or more on every surface: tests/unit/app/theme.test.ts), with no edit to the copy.
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DestructiveButton from '../../../src/components/app/DestructiveButton.vue'

describe('[BL-0325] the destructive button', () => {
  it("draws the app's focus ring, not the vendored faint red one, in both modes", () => {
    const classes = mount(DestructiveButton, { slots: { default: () => 'Delete' } })
      .get('button')
      .classes()
    expect(classes).toEqual(
      expect.arrayContaining(['bg-destructive', 'focus-visible:ring-ring/50', 'dark:focus-visible:ring-ring/50']),
    )
    // The only red rings left are the invalid state's, drawn beside a full-colour red border.
    expect(classes.filter((c) => c.includes('ring-destructive'))).toEqual([
      'aria-invalid:ring-destructive/20',
      'dark:aria-invalid:ring-destructive/40',
    ])
  })

  it("keeps the caller's class, its label and its clicks", async () => {
    const clicks: string[] = []
    const wrapper = mount(DestructiveButton, {
      props: { class: 'w-full' },
      attrs: { onClick: () => clicks.push('x') },
      slots: { default: () => 'Delete account' },
    })
    const el = wrapper.get('button')
    expect(el.text()).toBe('Delete account')
    expect(el.classes()).toContain('w-full')
    await el.trigger('click')
    expect(clicks).toEqual(['x'])
  })
})
