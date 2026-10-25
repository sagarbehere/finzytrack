// P1 integrity test for the sale form's units (review:27 §1(d), Appendix B: green in M8): L3-01.
//
// Spec: review:L3 L3-01 (the sale form submitted `toFixed(units, 2)`: "sell all 10.123" sold 10.12 and left a
// 0.003-unit lot; "sell all 123.456" rounded up and was refused; its regression test "submits 10.123 and asserts
// units == '10.123'"), INV-06 (money and quantities are exact; display formatting never feeds a write), the
// frontend rule "Money is the branded `Money` (decimal.js)" (frontend/CLAUDE.md).
//
// Interface: the sale form in `features/investments/components/` (review:L8 §1.6 item 7 keeps `SaleForm`). Assumed,
// not decided: the file `SaleForm.vue`, its `lots` prop (units as decimal strings), the buttons "Sell all" and
// "Review sale", and the `submit` event carrying `{ units, lots: [{ id, units }] }` as decimal strings.
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect } from 'vitest'
import type { Component } from 'vue'

import { fixme, planned } from '../../fixme'

interface SaleRequest {
  units: string
  lots: { id: string; units: string }[]
}

describe('sale form (P1)', () => {
  fixme(
    { id: 'L3-01', due: 'M8', reason: 'capability not built' },
    'selling all submits the exact units, not a 2-decimal rounding',
    async () => {
      const SaleForm = (await planned<{ default: Component }>('/src/features/investments/components/SaleForm.vue'))
        .default
      const wrapper = mount(SaleForm, {
        props: {
          commodity: 'MF-GROWTH',
          lots: [
            { id: 'lot-1', units: '10.123', cost: '45.10', currency: 'INR' },
            { id: 'lot-2', units: '123.456', cost: '51.75', currency: 'INR' },
          ],
        },
      })

      await wrapper.get('button[data-action="sell-all"]').trigger('click')
      await wrapper.get('button[data-action="review-sale"]').trigger('click')
      await flushPromises()

      const submitted = wrapper.emitted<[SaleRequest]>('submit')?.[0]?.[0]
      expect(submitted?.units).toBe('133.579') // 10.123 + 123.456, exact
      expect(submitted?.lots).toEqual([
        { id: 'lot-1', units: '10.123' },
        { id: 'lot-2', units: '123.456' },
      ])
    },
  )
})
