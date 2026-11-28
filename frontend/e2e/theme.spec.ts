// The first frame is in the right theme (L9-11, ADR-0035), in real Chromium and WebKit under the preview's CSP
// (BL-0032): the inline pre-paint script runs (a blocked script would be a console error, which fails the test) and
// sets `.dark` before the page's own code. The script's cases are unit-tested (tests/unit/app/prepaint.test.ts).
import { expect, test } from './harness/fixtures'

const store = (value: string) => {
  // Runs before every page script, on every navigation; storage may be absent on a blank page.
  try {
    localStorage.setItem('ft/v1/app/theme', value)
  } catch {
    // nothing to store on a page without storage
  }
}

test('with nothing stored, the page follows a dark OS from the first frame', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await expect(page.locator('html')).toHaveClass(/(^|\s)dark(\s|$)/)
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(background).not.toBe('rgb(255, 255, 255)')
})

test('a stored "light" wins over a dark OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(store, '"light"')
  await page.goto('/')
  await expect(page.locator('html')).not.toHaveClass(/(^|\s)dark(\s|$)/)
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(255, 255, 255)')
})

test('a stored "dark" wins over a light OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.addInitScript(store, '"dark"')
  await page.goto('/')
  await expect(page.locator('html')).toHaveClass(/(^|\s)dark(\s|$)/)
})
