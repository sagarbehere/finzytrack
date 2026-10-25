// Integrity flow A10 (review:L8 §1.8 and §1.5; review:27 Appendix B: M10): section saves keep other dirty sections;
// the rules editor never shows A's content under B; the unsaved guard covers the tab switch (L9.M-1 on the real
// page) and a workspace switch. Adapted: v0.4 has no ledger path in settings (INV-27), so L8's "dirty section survives
// a ledger switch" becomes "a workspace switch with a dirty section asks first, and Cancel keeps it" (review:23 §3.3
// step 1; ADR-0035). Assumed, not decided: routes, section and field names, the rule files in the demo.
import { expect, test } from '../harness/fixtures'

import { fixme } from '../fixme'

test.fixme(
  'A10: section saves keep other edits; rules A to B never show stale content; tab and workspace switches guard',
  fixme({ id: 'BL-0069', due: 'M10', reason: 'capability not built' }),
  async ({ page, demo }) => {
    await page.goto(`/w/${demo.key}/settings?tab=general`)
    const display = page.getByRole('region', { name: 'Display' })
    const ai = page.getByRole('region', { name: 'AI' })
    await ai.getByRole('textbox', { name: 'Model' }).fill('local-model-b')
    await display.getByRole('combobox', { name: 'Date format' }).selectOption('DD/MM/YYYY')
    await display.getByRole('button', { name: 'Save' }).click()
    await expect(ai.getByRole('textbox', { name: 'Model' })).toHaveValue('local-model-b') // B kept its edit
    await expect(ai.getByRole('button', { name: 'Save' })).toBeEnabled()

    await page.getByRole('tab', { name: 'Rules' }).click()
    const guard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' })
    await expect(guard.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await guard.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true')

    await page.getByRole('combobox', { name: 'Workspace' }).click()
    await page.getByRole('option').nth(1).click()
    await expect(guard).toBeVisible()
    await guard.getByRole('button', { name: 'Cancel' }).click()
    await expect(page).toHaveURL(new RegExp(`/w/${demo.key}/settings`))
    await expect(ai.getByRole('textbox', { name: 'Model' })).toHaveValue('local-model-b')

    await ai.getByRole('button', { name: 'Reset' }).click()
    await page.getByRole('tab', { name: 'Rules' }).click()
    let slow: () => void = () => undefined
    const held = new Promise<void>((r) => (slow = r))
    await page.route('**/rules/groceries*', async (route) => {
      await held
      await route.continue()
    })
    await page.getByRole('option', { name: 'groceries.yaml' }).click()
    await page.getByRole('option', { name: 'salary.yaml' }).click()
    slow() // A's response arrives after B was chosen
    await expect(page.getByRole('textbox', { name: 'Rule file' })).toContainText('salary')
    await expect(page.getByRole('textbox', { name: 'Rule file' })).not.toContainText('groceries')
  },
)
