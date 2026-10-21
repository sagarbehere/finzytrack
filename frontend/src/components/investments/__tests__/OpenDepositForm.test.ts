// Open Deposit form: live dry-run preview, Create only for what was previewed,
// preview errors shown inline, and a reset after creating.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'

const openDeposit = vi.fn()
vi.mock('@/services/generated-api', async (orig) => ({
  ...(await orig<typeof import('@/services/generated-api')>()),
  InvestmentsService: { openDeposit: (body: unknown) => openDeposit(body) },
}))
const fetchAccounts = vi.fn()
vi.mock('@/composables/useAccounts', () => ({
  useAccounts: () => ({ accountDetails: ref([]), fetchAccounts }),
}))
const success = vi.fn()
vi.mock('@/composables/useNotifications', () => ({
  useToast: () => ({ success, error: vi.fn() }),
  useNotifications: () => ({ addNotification: vi.fn() }),
}))
const display = vi.fn()
vi.mock('@/utils/ErrorHandler', () => ({
  errorHandler: { display: (e: unknown) => display(e), messageOf: () => 'This account already exists.' },
}))

import OpenDepositForm from '@/components/investments/OpenDepositForm.vue'

function mountForm() {
  return mount(OpenDepositForm, {
    props: { date: '2026-01-15', currency: 'USD' },
    global: { stubs: { AccountDropdown: true } },
  })
}

async function typeAccount(w: ReturnType<typeof mountForm>, name: string) {
  await w.find('#open-deposit-account').setValue(name)
  await vi.advanceTimersByTimeAsync(450)
  await flushPromises()
}

const createButton = (w: ReturnType<typeof mountForm>) => w.findAll('button').find(b => b.text() === 'Create')!

describe('OpenDepositForm', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    openDeposit.mockReset(); fetchAccounts.mockReset(); success.mockReset(); display.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('previews with a dry run, then creates what was previewed', async () => {
    openDeposit.mockImplementation(async (body: { dry_run: boolean }) => ({
      data: { preview: '2026-01-15 open Assets:Bank:CD-2026 USD', created: !body.dry_run,
              accounts_created: body.dry_run ? [] : ['Assets:Bank:CD-2026'], warnings: [] },
    }))
    const w = mountForm()
    expect(createButton(w).attributes('disabled')).toBeDefined()
    await typeAccount(w, 'Assets:Bank:CD-2026')
    expect(openDeposit).toHaveBeenLastCalledWith(expect.objectContaining({ account: 'Assets:Bank:CD-2026', dry_run: true }))
    expect(w.find('pre').text()).toBe('2026-01-15 open Assets:Bank:CD-2026 USD')
    expect(createButton(w).attributes('disabled')).toBeUndefined()

    await createButton(w).trigger('click')
    await flushPromises()
    expect(openDeposit).toHaveBeenLastCalledWith(expect.objectContaining({ dry_run: false }))
    expect(success).toHaveBeenCalledWith('Created', 'Opened Assets:Bank:CD-2026.')
    expect(fetchAccounts).toHaveBeenCalledWith(true)
    expect(w.emitted('created')).toEqual([[['Assets:Bank:CD-2026']]])
    expect((w.find('#open-deposit-account').element as HTMLInputElement).value).toBe('')
  })

  it('shows a preview error inline and does not allow Create', async () => {
    openDeposit.mockRejectedValue(new Error('409'))
    const w = mountForm()
    await typeAccount(w, 'Assets:Bank:Savings')
    expect(w.text()).toContain('This account already exists.')
    expect(display).not.toHaveBeenCalled()
    expect(createButton(w).attributes('disabled')).toBeDefined()
  })

  it('asks for the missing fields instead of previewing', async () => {
    const w = mountForm()
    await vi.advanceTimersByTimeAsync(450)
    expect(openDeposit).not.toHaveBeenCalled()
    expect(w.text()).toContain('Enter the account name to see what will be written.')
  })
})
