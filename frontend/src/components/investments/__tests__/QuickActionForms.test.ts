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

describe('QuickActionForms — Merger', () => {
  beforeEach(() => { assembleAndConvert.mockReset() })

  it('assembles a conversion event from the form', async () => {
    assembleAndConvert.mockResolvedValue({ bundle: { transactions: [{ id: 't1' }] }, warnings: [], errors: [] })
    const wrapper = mountForms()
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === 'Merger')!.trigger('click')
    const accounts = wrapper.findAllComponents({ name: 'AccountDropdown' })
    const commodities = wrapper.findAllComponents({ name: 'CommodityDropdown' })
    // [currency, fund merged away, new fund]
    commodities[1].vm.$emit('update:modelValue', 'OLD')
    commodities[2].vm.$emit('update:modelValue', 'NEW')
    // [holding, gains]
    accounts[0].vm.$emit('update:modelValue', 'Assets:Broker')
    accounts[1].vm.$emit('update:modelValue', 'Income:Gains')
    const amountInputs = wrapper.findAll('input[inputmode="decimal"]')
    await amountInputs[0].setValue('696.767')
    await amountInputs[1].setValue('1875.574')
    await wrapper.findAll('button').find(b => b.text() === 'Add to review')!.trigger('click')
    await flushPromises()
    expect(assembleAndConvert.mock.calls[0][0][0]).toMatchObject({
      type: 'conversion', currency: 'USD', holding_account: 'Assets:Broker', security: 'OLD', from_units: '696.767',
      to_security: 'NEW', to_units: '1875.574', gains_account: 'Income:Gains',
    })
  })
})
