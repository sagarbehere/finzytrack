// The first frame is in the right theme (L9-11, ADR-0035), in real Chromium and WebKit under the preview's CSP
// (BL-0032): the inline pre-paint script runs (a blocked script would be a console error, which fails the test) and
// sets `.dark` before the page's own code. The script's cases are unit-tested (tests/unit/app/prepaint.test.ts).
import type { Page } from '@playwright/test'

import { expect, test } from './harness/fixtures'

const store = (value: string) => {
  // Runs before every page script, on every navigation; storage may be absent on a blank page.
  try {
    localStorage.setItem('ft/v1/app/theme', value)
  } catch {
    // nothing to store on a page without storage
  }
}

/** The page background as sRGB bytes: the computed value may be oklch() (Tailwind 4), so a 1x1 canvas converts it. */
async function background(page: Page): Promise<number[]> {
  return page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('no 2d context')
    ctx.fillStyle = getComputedStyle(document.body).backgroundColor
    ctx.fillRect(0, 0, 1, 1)
    return Array.from(ctx.getImageData(0, 0, 1, 1).data.slice(0, 3))
  })
}
const WHITE = [255, 255, 255]
const isDark = (rgb: number[]) => rgb.every((v) => v < 40) // gray-950 is about rgb(3, 7, 18)

test('with nothing stored, the page follows a dark OS from the first frame', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await expect(page.locator('html')).toHaveClass(/(^|\s)dark(\s|$)/)
  expect(isDark(await background(page))).toBe(true)
})

test('a stored "light" wins over a dark OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(store, '"light"')
  await page.goto('/')
  await expect(page.locator('html')).not.toHaveClass(/(^|\s)dark(\s|$)/)
  expect(await background(page)).toEqual(WHITE)
})

test('a stored "dark" wins over a light OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.addInitScript(store, '"dark"')
  await page.goto('/')
  await expect(page.locator('html')).toHaveClass(/(^|\s)dark(\s|$)/)
  expect(isDark(await background(page))).toBe(true)
})
