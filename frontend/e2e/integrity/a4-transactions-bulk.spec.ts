// Integrity flow A4 (review:L8 §1.8; review:27 Appendix B: M5, its autocategorize part M6): bulk edits on the grid.
// Spec: review:L8 §1.1 C1-C8, A9-A10; review:L8-verif M-1 (undo is per operation); L4-01 (autocategorize fills only
// a row's single unknown leg); INV-16 (bulk delete confirms with Cancel focused). Assumed, not decided: the route,
// the selection checkboxes, the bulk toolbar's button names, the operation log's list name, and the demo rows used.
import { expect, test } from '../harness/fixtures'
import type { Page } from '@playwright/test'

import { fixme } from '../fixme'

const grid = (page: Page) => page.getByRole('grid', { name: 'Transactions' })
const rowCheckbox = (page: Page, i: number) => grid(page).getByRole('row').nth(i).getByRole('checkbox')

test.fixme(
  'A4: range select, replace account, tag add then remove leaves nothing staged, undo, bulk delete confirms',
  fixme({ id: 'BL-0066', due: 'M5', reason: 'capability not built' }),
  async ({ page, demo }) => {
    await page.goto(`/w/${demo.key}/transactions?accountContains=Expenses:Food`)
    await rowCheckbox(page, 1).click()
    await rowCheckbox(page, 3).click({ modifiers: ['Shift'] })
    await expect(page.getByText('3 selected')).toBeVisible()

    await page.getByRole('button', { name: 'Replace account' }).click()
    await page.getByRole('combobox', { name: 'New account' }).fill('Expenses:Groceries')
    await page.getByRole('option', { name: 'Expenses:Groceries' }).click()
    await page.getByRole('button', { name: 'Replace' }).click()
    const log = page.getByRole('list', { name: 'Staged changes' })
    await expect(log.getByRole('listitem')).toHaveCount(1)

    await page.getByRole('button', { name: 'Add tag' }).click()
    await page.getByRole('textbox', { name: 'Tag' }).fill('trip')
    await page.keyboard.press('Enter')
    await page.getByRole('button', { name: 'Remove tag' }).click()
    await page.getByRole('textbox', { name: 'Tag' }).fill('trip')
    await page.keyboard.press('Enter')
    await expect(log.getByRole('listitem')).toHaveCount(1) // tag added then removed: no phantom staged change

    await log.getByRole('listitem').first().getByRole('button', { name: 'Undo' }).click()
    await expect(log.getByRole('listitem')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Save changes/ })).toBeDisabled()

    await page.getByRole('button', { name: 'Delete selected' }).click()
    const confirm = page.getByRole('alertdialog', { name: /Delete 3 transactions/ })
    await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await page.keyboard.press('Enter') // Enter on the focused Cancel deletes nothing
    await expect(confirm).toBeHidden()
    await expect(grid(page).getByRole('row')).not.toHaveCount(1)
    await expect(page.getByText('3 selected')).toBeVisible()
  },
)

test.fixme(
  'A4: autocategorize changes only rows whose single other leg is the unknown account',
  fixme({ id: 'BL-0067', due: 'M6', reason: 'capability not built' }),
  async ({ page, demo, request }) => {
    // The demo's unknown account comes from the Ledger Profile (L3-14); the demo generator writes rows with one
    // unknown leg, a split row with an unknown leg among three, and a categorized row.
    // The unknown account is whatever the Ledger Profile serves (ADR-0024, L3-14), never a literal name.
    const profile = (await (await request.get(`/api/w/${demo.key}/profile`)).json()) as {
      data: { unknown_account: string }
    }
    const unknown = profile.data.unknown_account
    await page.goto(`/w/${demo.key}/transactions?accountContains=Assets:Bank`)
    const rows = grid(page).getByRole('row')
    const before = await rows.allTextContents()
    await page.getByRole('checkbox', { name: 'Select all' }).check()
    await page.getByRole('button', { name: 'Autocategorize' }).click()
    await expect(page.getByRole('status')).toContainText(/categorized/i)

    const after = await rows.allTextContents()
    const changed = after.filter((text, i) => text !== before[i])
    expect(changed.length).toBeGreaterThan(0)
    for (const [i, text] of before.entries()) {
      const unknownLegs = text.split(unknown).length - 1
      if (unknownLegs !== 1) expect(after[i]).toBe(text) // categorized and multi-unknown rows are untouched
    }
  },
)
