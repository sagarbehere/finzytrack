// Integrity flow A1 (review:L8 §1.8; review:27 Appendix B: M5): deep link, edit, Save sends only modified rows, and
// rows edited during Save stay dirty (L8-02). Spec: review:L8 §1.1 B3, B4, H1, H4; INV-06 (amounts typed as `-`,
// `1.`, `10.50` stay exact). Assumed, not decided: the route `/w/{ws}/transactions` keeping the v0.3 deep-link
// parameters, the grid's accessible names, and the transactions save request matching `**/transactions:save`.
import { expect, test, type Request } from '@playwright/test'

import { fixme } from '../fixme'
import { WS } from './workspace'

test.fixme(
  'A1: a deep link loads the rows; Save sends only modified rows; rows edited during Save stay dirty',
  fixme({ id: 'BL-0064', due: 'M5', reason: 'capability not built' }),
  async ({ page }) => {
    await page.goto(`/w/${WS}/transactions?accountContains=Expenses:Food&dateFrom=2026-01-01`)
    const grid = page.getByRole('grid', { name: 'Transactions' })
    await expect(grid.getByRole('row')).not.toHaveCount(0)
    for (const account of await grid.getByRole('gridcell', { name: /^Account/ }).allTextContents()) {
      expect(account).toContain('Expenses:Food')
    }

    const first = grid.getByRole('row').nth(1)
    await first.getByRole('gridcell', { name: 'Payee' }).dblclick()
    await page.keyboard.type(' (edited)')
    await page.keyboard.press('Enter')
    const amount = first.getByRole('gridcell', { name: 'Amount' }).first()
    await amount.dblclick()
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.type('-')
    await page.keyboard.type('1.')
    await page.keyboard.press('Backspace')
    await page.keyboard.press('Backspace')
    await page.keyboard.type('10.50')
    await page.keyboard.press('Enter')
    await expect(amount).toHaveText(/10\.50/)

    let release: () => void = () => undefined
    const held = new Promise<void>((r) => (release = r))
    const sent: Request[] = []
    await page.route('**/transactions:save', async (route) => {
      sent.push(route.request())
      await held
      await route.continue()
    })
    await page.getByRole('button', { name: /Save changes \(1\)/ }).click()
    const second = grid.getByRole('row').nth(2) // typed while the save is in flight
    await second.getByRole('gridcell', { name: 'Payee' }).dblclick()
    await page.keyboard.type(' (late)')
    await page.keyboard.press('Enter')
    release()

    await expect(page.getByRole('button', { name: /Save changes \(1\)/ })).toBeEnabled()
    const body = sent[0]?.postDataJSON() as { transactions: unknown[] } | undefined
    expect(body?.transactions).toHaveLength(1)
    await expect(second).toHaveAttribute('aria-describedby', /modified/)
  },
)
