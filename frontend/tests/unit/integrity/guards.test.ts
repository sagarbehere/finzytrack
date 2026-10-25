// P1 integrity tests for confirm dialogs and the unsaved-change guard (review:27 §1(d), Appendix B: green in M2,
// "on a fixture dialog"): L9-01, L9.M-1, L9.M-3.
//
// Spec: INV-16 (a destructive action has safe focus: Cancel), ADR-0035 ("confirm dialogs focus Cancel"; `useConfirm`
// + one `ConfirmHost`; `DialogShell` with `dirty`; one `useUnsavedGuard(dirty)` covering route leave, in-view tab
// switch, reload and dialog close), review:L9 L9-01 (Enter or Space right after opening "Delete Transactions?"
// deleted, because the destructive button had focus), review:L9-verif M-1 (switching Settings tabs discarded unsaved
// Rules and Dashboards edits) and M-3 (five guards with five coverages; dialogs guarded nothing).
// L9.M-1 is due by M2 in the backlog (Appendix B says M10 for the full Settings page; the backlog's triage wins), so
// it runs on a fixture tab set here; A10 re-checks it on the real page in M10.
//
// Interface: `components/app/` primitives named by ADR-0035 and ARCHITECTURE.md §3.1. Assumed, not decided: one file
// per primitive (`ConfirmHost.vue`, `useConfirm.ts`, `useUnsavedGuard.ts`, `DialogShell.vue`, `PageTabs.vue`), the
// option names `title`/`confirmLabel`/`tone`, the guard's `confirmLeave()` (what the router's leave hook calls), and
// the props of `DialogShell` (`open`, `dirty`) and `PageTabs` (`tabs`, `modelValue`).
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect } from 'vitest'
import { type Component, defineComponent, h, ref, type Ref, type VNodeChild } from 'vue'

import { fixme, planned } from '../../fixme'
import { rulesTabFixture } from './rulesTabFixture'

interface ConfirmOptions {
  title: string
  confirmLabel: string
  tone?: 'danger' | 'neutral'
}
type Confirm = (options: ConfirmOptions) => Promise<boolean>
interface Guard {
  confirmLeave(): Promise<boolean>
}
type Default = { default: Component }

const APP = '/src/components/app'
const focusedText = (): string => document.activeElement?.textContent.trim() ?? ''
const button = (label: string): HTMLElement => {
  const found = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
  if (!found) throw new Error(`no button ${label}`)
  return found
}
// One host component per test, set up by the test (one defineComponent call site in this file).
const mountHost = (setup: () => () => VNodeChild) => mount(defineComponent({ setup }), { attachTo: document.body })
const confirmHost = async (): Promise<Component> => (await planned<Default>(`${APP}/ConfirmHost.vue`)).default

describe('safe confirm and unsaved guards (P1)', () => {
  fixme(
    { id: 'L9-01', due: 'M2', reason: 'capability not built' },
    'a destructive confirm opens with Cancel focused, so Enter does not delete',
    async () => {
      const ConfirmHost = await confirmHost()
      const { useConfirm } = await planned<{ useConfirm: () => Confirm }>(`${APP}/useConfirm`)
      let confirm: Confirm | undefined
      const wrapper = mountHost(() => {
        confirm = useConfirm()
        return () => h(ConfirmHost)
      })
      const answer = confirm?.({ title: 'Delete 3 transactions?', confirmLabel: 'Delete', tone: 'danger' })
      await flushPromises()

      expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
      expect(focusedText()).toBe('Cancel')
      ;(document.activeElement as HTMLElement).click() // what Enter or Space does to the focused button
      expect(await answer).toBe(false)
      wrapper.unmount()
    },
  )

  fixme(
    { id: 'L9.M-3', due: 'M2', reason: 'capability not built' },
    'one guard covers reload, route leave and dialog close, and only while dirty',
    async () => {
      const ConfirmHost = await confirmHost()
      const { useUnsavedGuard } = await planned<{ useUnsavedGuard: (dirty: Ref<boolean>) => Guard }>(
        `${APP}/useUnsavedGuard`,
      )
      const DialogShell = (await planned<Default>(`${APP}/DialogShell.vue`)).default
      const dirty = ref(true)
      const open = ref(true)
      let guard: Guard | undefined
      const wrapper = mountHost(() => {
        guard = useUnsavedGuard(dirty)
        return () => [
          h(ConfirmHost),
          h(
            DialogShell,
            { open: open.value, dirty: dirty.value, 'onUpdate:open': (v: boolean) => (open.value = v) },
            () => h('p', 'draft budget'),
          ),
        ]
      })
      const reload = (): boolean => {
        const event = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(event)
        return event.defaultPrevented
      }

      expect(reload()).toBe(true) // reload and window close
      const leave = guard?.confirmLeave() // route leave
      await flushPromises()
      expect(focusedText()).toBe('Cancel')
      button('Cancel').click()
      expect(await leave).toBe(false)
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) // dialog close
      await flushPromises()
      button('Cancel').click()
      await flushPromises()
      expect(open.value).toBe(true) // the draft dialog stays open

      dirty.value = false
      await flushPromises()
      expect(reload()).toBe(false)
      expect(await guard?.confirmLeave()).toBe(true) // clean: no question asked
      wrapper.unmount()
    },
  )

  fixme(
    { id: 'L9.M-1', due: 'M2', reason: 'capability not built' },
    'switching tabs with unsaved edits asks first, and Cancel keeps the edits',
    async () => {
      const ConfirmHost = await confirmHost()
      const PageTabs = (await planned<Default>(`${APP}/PageTabs.vue`)).default
      const { useUnsavedGuard } = await planned<{ useUnsavedGuard: (dirty: Ref<boolean>) => Guard }>(
        `${APP}/useUnsavedGuard`,
      )
      const RulesTab = rulesTabFixture(useUnsavedGuard) // a dirty editor that registers the guard itself
      const active = ref('rules')
      const wrapper = mountHost(() => () => [
        h(ConfirmHost),
        h(
          PageTabs,
          {
            tabs: [
              { id: 'rules', label: 'Rules' },
              { id: 'general', label: 'General' },
            ],
            modelValue: active.value,
            'onUpdate:modelValue': (v: string) => (active.value = v),
          },
          () => (active.value === 'rules' ? h(RulesTab) : h('p', 'general')),
        ),
      ])

      const area = document.querySelector<HTMLTextAreaElement>('textarea')
      if (!area) throw new Error('no editor')
      area.value = 'rule: groceries and rent' // the unsaved edit
      area.dispatchEvent(new Event('input'))
      await flushPromises()
      button('General').click()
      await flushPromises()
      expect(focusedText()).toBe('Cancel')
      button('Cancel').click()
      await flushPromises()

      expect(active.value).toBe('rules')
      // the typed edit survives: a tab that was unmounted and remounted would show its initial text again
      expect(document.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe('rule: groceries and rent')
      wrapper.unmount()
    },
  )
})
