// P1 integrity tests for dashboard widget runs (review:27 §1(d), Appendix B: green in M7): I7, I8.
//
// Spec: ADR-0037 ("Recipe widgets are one query per widget with a run token, so a stale response is ignored.
// Generators resolve only after the commodities load ('not ready' until then)"), ARCHITECTURE.md §3.2 (the widget
// query key carries the params), review:10 I7 (2024 data shown under a 2025 selector) and I8 (`$gen:defaultCurrency`
// first resolved to USD, then INR, so every widget ran twice, first in the wrong currency), ADR-0052 and ADR-0024 (the
// default currency is the ledger's operating currency, served by the Ledger Profile).
//
// Interface: the per-widget recipe-run query in `features/dashboards/` (ARCHITECTURE.md §3.1, §3.2). Assumed, not
// decided: `useWidgetRun({ params, profile, run })` in `features/dashboards/composables/useWidgetRun`, returning `data`
// and `status` refs, with `run(params, signal)` standing in for the generated `recipes:run` call; the test mounts it
// in a bare component (the landing slice adds the app's query-client provider if the composable needs one).
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect } from 'vitest'
import { defineComponent, h, ref, type Ref } from 'vue'

import { fixme, planned } from '../../fixme'

type Params = Record<string, string>
interface Profile {
  operatingCurrencies: string[]
}
interface WidgetRun {
  data: Ref<string | undefined>
  status: Ref<string>
}
type RunModule = {
  useWidgetRun: (deps: {
    params: Ref<Params>
    profile: Ref<Profile | null>
    run: (params: Params, signal: AbortSignal) => Promise<string>
  }) => WidgetRun
}

const PATH = '/src/features/dashboards/composables/useWidgetRun'

function deferred(): { promise: Promise<string>; resolve: (v: string) => void } {
  let resolve: (v: string) => void = () => undefined
  const promise = new Promise<string>((r) => (resolve = r))
  return { promise, resolve }
}

function mountRun(useWidgetRun: RunModule['useWidgetRun'], deps: Parameters<RunModule['useWidgetRun']>[0]) {
  let result: WidgetRun | undefined
  mount(defineComponent({ setup: () => ((result = useWidgetRun(deps)), () => h('div')) }))
  if (!result) throw new Error('not mounted')
  return result
}

describe('dashboard widget runs (P1)', () => {
  fixme(
    { id: 'I7', due: 'M7', reason: 'capability not built' },
    'a late response for old parameters never replaces the current one',
    async () => {
      const { useWidgetRun } = await planned<RunModule>(PATH)
      const calls = new Map<string, ReturnType<typeof deferred>>()
      const params = ref<Params>({ year: '2024' })
      const widget = mountRun(useWidgetRun, {
        params,
        profile: ref({ operatingCurrencies: ['INR'] }),
        run: (p) => {
          const d = deferred()
          calls.set(p['year'] ?? '', d)
          return d.promise
        },
      })
      await flushPromises()
      params.value = { year: '2025' }
      await flushPromises()

      calls.get('2025')?.resolve('result for 2025')
      await flushPromises()
      calls.get('2024')?.resolve('result for 2024') // the stale run answers last
      await flushPromises()

      expect(widget.data.value).toBe('result for 2025')
    },
  )

  fixme(
    { id: 'I8', due: 'M7', reason: 'capability not built' },
    'generators wait for the profile, and the widget runs once in the ledger currency',
    async () => {
      const { useWidgetRun } = await planned<RunModule>(PATH)
      const runs: Params[] = []
      const profile = ref<Profile | null>(null)
      const widget = mountRun(useWidgetRun, {
        params: ref({ currency: '$gen:defaultCurrency' }),
        profile,
        run: (p) => {
          runs.push(p)
          return Promise.resolve(`total in ${p['currency'] ?? '?'}`)
        },
      })
      await flushPromises()
      expect(runs).toEqual([]) // not ready: no run with a guessed currency
      expect(widget.status.value).toBe('not-ready')

      profile.value = { operatingCurrencies: ['INR'] }
      await flushPromises()

      expect(runs).toEqual([{ currency: 'INR' }]) // exactly one run, never a USD one first
      expect(widget.data.value).toBe('total in INR')
    },
  )
})
