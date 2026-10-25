// Integrity flow A2 (review:L8 §1.8; review:27 Appendix B: M5): unsaved edits + Apply filter, global search, route
// leave or reload all ask first (L8-01; review:L8 §1.1 H5; ADR-0035 one `useUnsavedGuard`; INV-16 Cancel focused).
// Assumed, not decided: the route, the filter panel's "Apply" button, the global search box name, the guard's dialog
// title "Discard unsaved changes?" with "Discard" / "Cancel".
import { expect, test } from '../harness/fixtures'
import type { Page } from '@playwright/test'

import { fixme } from '../fixme'

async function editOnePayee(page: Page): Promise<void> {
  const grid = page.getByRole('grid', { name: 'Transactions' })
  await grid.getByRole('row').nth(1).getByRole('gridcell', { name: 'Payee' }).dblclick()
  await page.keyboard.type(' (edited)')
  await page.keyboard.press('Enter')
}

async function expectGuardThenCancel(page: Page): Promise<void> {
  const dialog = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('button', { name: /Save changes \(1\)/ })).toBeVisible()
}

test.fixme(
  'A2: Apply filter, global search, route leave and reload all guard unsaved edits',
  fixme({ id: 'BL-0065', due: 'M5', reason: 'capability not built' }),
  async ({ page, demo }) => {
    await page.goto(`/w/${demo.key}/transactions`)
    await editOnePayee(page)

    await page.getByRole('textbox', { name: 'Account contains' }).fill('Income')
    await page.getByRole('button', { name: 'Apply' }).click()
    await expectGuardThenCancel(page)

    await page.getByRole('searchbox', { name: 'Search' }).fill('Cafe')
    await page.keyboard.press('Enter')
    await expectGuardThenCancel(page)

    await page.getByRole('link', { name: 'Accounts' }).click()
    await expectGuardThenCancel(page)
    await expect(page).toHaveURL(/\/transactions/)

    let asked = false
    page.once('dialog', (dialog) => {
      asked = dialog.type() === 'beforeunload'
      void dialog.dismiss()
    })
    await page.reload()
    expect(asked).toBe(true)
  },
)
