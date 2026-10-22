// The AI import hands a mutual fund CAS to Import statement (never to the model) and
// sends a PDF's password for local decryption (dev-docs/cas-import.md §3.1).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'

const m = vi.hoisted(() => ({ parse: vi.fn() }))
vi.mock('@/services/generated-api', () => ({ ImportService: { llmParseApiImportLlmParsePost: m.parse } }))
vi.mock('@/composables/useConfig', () => ({ useConfig: () => ({ config: ref({ ai: { llm: { is_configured: true } } }) }) }))

import AIImportPanel from '@/components/import/AIImportPanel.vue'

function render() {
  return mount(AIImportPanel, {
    global: { stubs: { AccountDropdown: true, CommodityDropdown: true, RouterLink: { template: '<a class="rl"><slot /></a>' } } },
    attachTo: document.body,
  })
}

async function choose(w: ReturnType<typeof render>, name: string) {
  const input = w.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [new File(['%PDF'], name)], configurable: true })
  await input.trigger('change')
  w.findAllComponents({ name: 'AccountDropdown' })[0].vm.$emit('update:modelValue', 'Assets:Bank')
  w.findAllComponents({ name: 'CommodityDropdown' })[0].vm.$emit('update:modelValue', 'INR')
  await flushPromises()
}

beforeEach(() => { m.parse.mockReset() })
afterEach(() => { document.body.innerHTML = '' })

describe('AIImportPanel', () => {
  it('sends the PDF password and shows the CAS hand-over instead of AI results', async () => {
    m.parse.mockRejectedValue({ body: { error: { code: 'STATEMENT_IS_CAS', message: 'This is a CAS' } } })
    const w = render()
    await choose(w, 'statement.pdf')
    await w.find('#ai-pdf-password').setValue('secret')
    await w.findAll('button').find(b => b.text().includes('Parse with AI'))!.trigger('click')
    await flushPromises()
    expect(m.parse.mock.calls[0][0]).toMatchObject({ pdf_password: 'secret' })
    expect(w.text()).toContain('This is a mutual fund statement (CAS).')
    expect(w.text()).toContain('Nothing was sent to the AI.')
    expect(w.emitted('proceedWithImport')).toBeUndefined()
  })

  it('asks for a password only for PDFs', async () => {
    const w = render()
    await choose(w, 'bank.csv')
    expect(w.find('#ai-pdf-password').exists()).toBe(false)
  })
})
