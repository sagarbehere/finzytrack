import { expect, test } from '@playwright/test'

test('the built bundle loads and shows the product name', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Finzytrack')
})
