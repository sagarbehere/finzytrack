// Integrity flow A13 (review:L8 §1.8 and §1.6 item 6; review:27 Appendix B: M7): a partial save failure keeps the
// failed drafts, and drafts survive changing "Budget as of" (the legacy view wiped them, review:L8 §1.6 item 6
// defect 1). Spec: INV-16 and the acceptance tests listed there. Assumed, not decided: the route, the grid and field
// names, and the budget write request matching `**/budgets**`.
import { expect, test } from '../harness/fixtures'

import { fixme } from '../fixme'

test.fixme(
  'A13: a partial save failure keeps failed drafts; drafts survive changing the as-of date',
  fixme({ id: 'BL-0070', due: 'M7', reason: 'capability not built' }),
  async ({ page, demo }) => {
    await page.goto(`/w/${demo.key}/budgets`)
    const grid = page.getByRole('grid', { name: 'Budgets' })
    const food = grid.getByRole('row', { name: /Expenses:Food/ })
    const rent = grid.getByRole('row', { name: /Expenses:Rent/ })
    await food.getByRole('textbox', { name: 'New budget' }).fill('9000')
    await rent.getByRole('textbox', { name: 'New budget' }).fill('25000')

    await page.getByRole('textbox', { name: 'Budget as of' }).fill('2026-02-01')
    await expect(food.getByRole('textbox', { name: 'New budget' })).toHaveValue('9000')
    await expect(rent.getByRole('textbox', { name: 'New budget' })).toHaveValue('25000')

    await page.route('**/budgets**', async (route) => {
      const body = route.request().postData() ?? ''
      if (route.request().method() !== 'GET' && body.includes('Expenses:Rent')) {
        await route.fulfill({
          status: 409,
          json: {
            error: { code: 'LEDGER_EXTERNALLY_CHANGED', class: 'conflict', message: 'changed', data_safe: true },
          },
        })
      } else {
        await route.continue()
      }
    })
    await page.getByRole('button', { name: /Save/ }).click()

    await expect(food.getByRole('textbox', { name: 'New budget' })).toHaveValue('')
    await expect(rent.getByRole('textbox', { name: 'New budget' })).toHaveValue('25000') // the failed draft stays
    await expect(rent.getByRole('img', { name: /not saved/i })).toBeVisible()
  },
)
