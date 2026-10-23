// A date parameter with a templated default ("$gen:today") can always return to
// that live default: a button labelled with the template ("Today") appears once a
// specific date is picked, and clearing the field restores the default instead
// of binding an empty value.
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecipeParameters from '@/components/recipes/RecipeParameters.vue'
import type { RecipeParameter } from '@/types/recipes'

const asOf: RecipeParameter = { name: 'asOf', label: 'As of', type: 'date', default: '$gen:today' } as RecipeParameter

function mountWith(value: string, param: RecipeParameter = asOf) {
  return mount(RecipeParameters, { props: { parameters: [param], modelValue: { asOf: value } } })
}

const lastEmitted = (w: ReturnType<typeof mountWith>) =>
  (w.emitted('update:modelValue')?.at(-1)?.[0] as Record<string, unknown>)?.asOf

describe('date parameter with a templated default', () => {
  it('shows no button while the live default is selected', () => {
    expect(mountWith('$gen:today').find('button').exists()).toBe(false)
  })

  it('offers the template by name once a specific date is picked, and restores it', async () => {
    const w = mountWith('2025-12-31')
    const button = w.find('button')
    expect(button.text()).toBe('Today')
    await button.trigger('click')
    expect(lastEmitted(w)).toBe('$gen:today')
  })

  it('restores the default when the date is cleared', async () => {
    const w = mountWith('2025-12-31')
    await w.find('input[type="date"]').setValue('')
    expect(lastEmitted(w)).toBe('$gen:today')
  })

  it('shows no button for a literal default', () => {
    const literal = { ...asOf, default: '2024-01-01' } as RecipeParameter
    expect(mountWith('2025-12-31', literal).find('button').exists()).toBe(false)
  })
})
