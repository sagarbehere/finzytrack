// After a sale is staged, the Sell form is reset (as the other forms reset their
// amounts), so the same sale can't be staged twice by accident.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'

const assembleAndConvert = vi.fn()
vi.mock('@/composables/useInvestmentActions', () => ({
  useInvestmentActions: () => ({ assembleAndConvert, isLoading: ref(false) }),
}))
vi.mock('@/composables/useAccounts', () => ({
  useAccounts: () => ({ accountDetails: ref([]), fetchAccounts: vi.fn() }),
}))
vi.mock('@/composables/useNotifications', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
  useNotifications: () => ({ addNotification: vi.fn() }),
}))
vi.mock('@/services/generated-api', () => ({
  CommoditiesService: { getOperatingCurrencies: () => Promise.resolve({ data: { currencies: ['USD'] } }) },
}))

const reset = vi.fn()
const SaleFormStub = defineComponent({
  name: 'SaleForm',
  emits: ['submit'],
  setup(_props, { expose }) { expose({ reset }); return {} },
  template: '<button class="sale-submit" @click="$emit(\'submit\', { type: \'sell\' })">Add to review</button>',
})

import QuickActionForms from '@/components/investments/QuickActionForms.vue'

function mountForms() {
  return mount(QuickActionForms, {
    global: { stubs: { SaleForm: SaleFormStub, AccountDropdown: true, CommodityDropdown: true } },
  })
}

async function stageSale(wrapper: ReturnType<typeof mountForms>) {
  await wrapper.findAll('button').find(b => b.text() === 'Sell')!.trigger('click')
  await wrapper.find('.sale-submit').trigger('click')
  await flushPromises()
}

describe('QuickActionForms — Sell', () => {
  beforeEach(() => { reset.mockReset(); assembleAndConvert.mockReset() })

  it('resets the Sell form after a successful stage', async () => {
    assembleAndConvert.mockResolvedValue({ bundle: { transactions: [{ id: 't1' }] }, warnings: [], errors: [] })
    const wrapper = mountForms()
    await stageSale(wrapper)
    expect(wrapper.emitted('staged')).toHaveLength(1)
    expect(reset).toHaveBeenCalledTimes(1)
  })

  it('keeps the Sell form filled in when the sale could not be staged', async () => {
    assembleAndConvert.mockResolvedValue({
      bundle: { transactions: [] }, warnings: [],
      errors: [{ event_index: 0, code: 'INSUFFICIENT_LOTS', message: 'Only 5 units held' }],
    })
    const wrapper = mountForms()
    await stageSale(wrapper)
    expect(wrapper.emitted('staged')).toBeUndefined()
    expect(reset).not.toHaveBeenCalled()
  })
})
