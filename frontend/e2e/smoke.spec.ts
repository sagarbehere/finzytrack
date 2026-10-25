// The harness smoke flow (S0030; review:27 Appendix B, M0 row): the built bundle loads under the harness, with the
// run's frozen clock and zone in the page, the run's sidecar answering on its own loopback port, and the fixtures'
// hard failures (axe, console errors, failed or foreign requests) armed.
import { expect, test } from './harness/fixtures'

test('the built bundle loads and shows the product name', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Finzytrack')
})

test('the page runs on the frozen clock in the run zone, and the run has its own sidecar and demo', async ({
  page,
  run,
  demo,
  request,
}) => {
  await page.goto('/')
  expect(await page.evaluate('Date.now()')).toBe(new Date(run.frozenAt).getTime())
  // The page's zone is the run's: its UTC offset at the frozen instant (Chromium may name Kolkata "Asia/Calcutta").
  const [, sign, hours, minutes] = /([+-])(\d{2}):(\d{2})$/.exec(run.frozenAt) ?? []
  const offset = (sign === '-' ? 1 : -1) * (Number(hours) * 60 + Number(minutes))
  expect(await page.evaluate('new Date().getTimezoneOffset()')).toBe(offset)
  // The local date in the page is the run's "today", which differs from the UTC date in every profile.
  expect(await page.evaluate('new Date().toLocaleDateString("en-CA")')).toBe(run.today)
  expect(await page.evaluate('new Date().toISOString().slice(0, 10)')).not.toBe(run.today)
  // No routes yet: the sidecar answers 501 until the API edge exists (L10-09).
  expect((await request.get(`${run.backend}/api/boot`)).status()).toBe(501)
  expect(demo.path).toBe(run.workspace)
})
