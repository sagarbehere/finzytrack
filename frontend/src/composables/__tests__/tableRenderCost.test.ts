// Behaviours that keep large transaction tables cheap to render:
// - useBreakpoint reports the real width on the very first render, so a
//   component never mounts its mobile variant first and then swaps it out.
// - useDropdownPosition listens for scroll/resize only while its dropdown is in
//   use (after updatePosition, until focus leaves the trigger).
// - Keyboard focus asks a virtualized table to render a missing row, then retries.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDropdownPosition } from '@/composables/useDropdownPosition'
import { useTableKeyboardNavigation } from '@/composables/useTableKeyboardNavigation'

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('useBreakpoint', () => {
  it('reports the real breakpoint during the first render', () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1280)
    const seenDuringRender: boolean[] = []
    const Probe = defineComponent({
      setup() {
        const { isMd } = useBreakpoint()
        return () => {
          seenDuringRender.push(isMd.value)
          return h('div')
        }
      },
    })
    mount(Probe)
    expect(seenDuringRender[0]).toBe(true)
  })
})

describe('useDropdownPosition', () => {
  function mountDropdown() {
    const api: { updatePosition?: () => void } = {}
    const Dropdown = defineComponent({
      setup() {
        const trigger = ref<HTMLElement | null>(null)
        api.updatePosition = useDropdownPosition(trigger).updatePosition
        return () => h('div', { class: 'scroller' }, [
          h('div', { ref: trigger, class: 'trigger' }, [h('input'), h('button')]),
        ])
      },
    })
    const wrapper = mount(Dropdown, { attachTo: document.body })
    return { wrapper, updatePosition: () => api.updatePosition!() }
  }

  // Spies on the trigger itself and on window — two of the targets it listens on.
  function spyListeners(el: Element) {
    const spies = [el, window].map(t => ({
      add: vi.spyOn(t, 'addEventListener'),
      remove: vi.spyOn(t, 'removeEventListener'),
    }))
    const count = (kind: 'add' | 'remove') =>
      spies.reduce((n, s) => n + s[kind].mock.calls.filter(c => c[0] === 'scroll').length, 0)
    return { added: () => count('add'), removed: () => count('remove') }
  }

  it('adds no scroll listeners when mounted', () => {
    const add = vi.spyOn(window, 'addEventListener')
    mountDropdown()
    expect(add.mock.calls.filter(c => c[0] === 'scroll')).toEqual([])
  })

  it('listens while in use and stops when focus leaves the trigger', () => {
    const { wrapper, updatePosition } = mountDropdown()
    const listeners = spyListeners(wrapper.find('.trigger').element)

    updatePosition()
    expect(listeners.added()).toBe(2) // the trigger + window

    // Focus moving within the trigger (input → its chevron button) keeps listening.
    const [input, button] = [wrapper.find('input').element, wrapper.find('button').element]
    input.dispatchEvent(new FocusEvent('focusout', { relatedTarget: button, bubbles: true }))
    expect(listeners.removed()).toBe(0)

    // Focus leaving the trigger stops listening.
    input.dispatchEvent(new FocusEvent('focusout', { relatedTarget: document.body, bubbles: true }))
    expect(listeners.removed()).toBe(2)
  })
})

describe('useTableKeyboardNavigation', () => {
  it('asks for a missing row to be rendered, then focuses it', async () => {
    const ensureRowRendered = vi.fn(async (rowIndex: number) => {
      document.body.innerHTML =
        `<table><tr><td data-column-id="payee" data-row="${rowIndex + 1}"><textarea></textarea></td></tr></table>`
    })
    const { setCellFocus } = useTableKeyboardNavigation({ ensureRowRendered })

    await setCellFocus({ rowIndex: 499, columnId: 'payee' })

    expect(ensureRowRendered).toHaveBeenCalledWith(499)
    expect(document.activeElement).toBe(document.querySelector('td[data-row="500"] textarea'))
  })

  it('does not ask for rendering when the row is already in the DOM', async () => {
    document.body.innerHTML =
      '<table><tr><td data-column-id="payee" data-row="1"><textarea></textarea></td></tr></table>'
    const ensureRowRendered = vi.fn(async () => {})
    const { setCellFocus } = useTableKeyboardNavigation({ ensureRowRendered })

    await setCellFocus({ rowIndex: 0, columnId: 'payee' })

    expect(ensureRowRendered).not.toHaveBeenCalled()
    expect(document.activeElement?.tagName).toBe('TEXTAREA')
  })
})
