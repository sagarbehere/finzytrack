// The vendored primitives work under our build (L9-16, ADR-0035): shadcn-vue's button and input on Reka UI, with
// `cn` merging a caller's classes over the component's own; and Lucide is the icon set (L9-17), its icons hidden
// from assistive technology unless given a label.
import { Sun } from '@lucide/vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { h } from 'vue'

import { Button } from '../../../src/components/ui/button'
import { Input } from '../../../src/components/ui/input'

describe('[L9-16] the button primitive', () => {
  it('renders a native button with its variant and size', () => {
    const wrapper = mount(Button, { props: { variant: 'outline', size: 'sm' }, slots: { default: () => 'Save' } })
    const el = wrapper.get('button')
    expect(el.text()).toBe('Save')
    expect(el.attributes('data-slot')).toBe('button')
    expect(el.classes()).toEqual(expect.arrayContaining(['border', 'bg-background', 'h-8']))
  })

  it("lets a caller's class win a conflict, and keeps the rest", () => {
    const wrapper = mount(Button, { props: { class: 'h-12 w-full' } })
    const classes = wrapper.get('button').classes()
    expect(classes).toEqual(expect.arrayContaining(['h-12', 'w-full', 'bg-primary']))
    expect(classes).not.toContain('h-9')
  })

  it('passes clicks and disabled through', async () => {
    const clicks: string[] = []
    const wrapper = mount(Button, { attrs: { onClick: () => clicks.push('x'), disabled: true } })
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.get('button').trigger('click')
    expect(clicks).toEqual([]) // a disabled native button fires no click
  })
})

describe('[L9-16] the input primitive', () => {
  it('round-trips v-model', async () => {
    const wrapper = mount(Input, { props: { modelValue: 'a' } })
    const el = wrapper.get('input')
    expect((el.element as HTMLInputElement).value).toBe('a')
    await el.setValue('b')
    expect(wrapper.emitted('update:modelValue')).toEqual([['b']])
  })

  it('keeps its own value without v-model, starting from defaultValue', async () => {
    const wrapper = mount(Input, { props: { defaultValue: 'start' } })
    const el = wrapper.get('input')
    expect((el.element as HTMLInputElement).value).toBe('start')
    await el.setValue('typed')
    expect((el.element as HTMLInputElement).value).toBe('typed')
  })

  it('starts empty with no value and no default (the recorded patch keeps that behaviour)', () => {
    const wrapper = mount(Input)
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
  })

  it('is 16 px on small screens (the vendored text-base) before our touch rule applies', () => {
    expect(mount(Input).get('input').classes()).toContain('text-base')
  })
})

describe('[L9-17] Lucide icons', () => {
  it('renders an svg hidden from assistive technology', () => {
    const svg = mount(Sun).get('svg')
    expect(svg.attributes('aria-hidden')).toBe('true')
  })

  it('exposes an icon that carries a label', () => {
    const svg = mount(() => h(Sun, { 'aria-label': 'Light' })).get('svg')
    expect(svg.attributes('aria-hidden')).toBeUndefined()
    expect(svg.attributes('aria-label')).toBe('Light')
  })
})
