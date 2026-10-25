// Each test leaks on purpose; the harness's automatic teardown must fail all four (e2e/harness.spec.ts): a recorded
// page problem, an unscripted LLM call in a test that does not take the `llm` fixture, an axe violation on the page a
// test ends on, and a failed request to an allowed origin.
import type { Page } from '@playwright/test'

import { test } from '../fixtures'

const PAGE = '<html lang="en"><head><title>leak</title></head><body><main><h1>leak</h1></main></body></html>'

async function servedPage(page: Page, body = PAGE): Promise<void> {
  await page.route('http://localhost:4173/', (route) => route.fulfill({ body, contentType: 'text/html' }))
  await page.goto('/')
}

test('a recorded problem left at the end', async ({ page, problems }) => {
  await page.setContent(PAGE)
  problems.push('provoked leak')
})

test('an unscripted LLM call left at the end', async ({ run }) => {
  await fetch(`${run.llm}/chat/completions`, {
    method: 'POST',
    body: JSON.stringify({ model: 'stub-model', messages: [{ role: 'user', content: 'provoked unscripted' }] }),
  })
})

test('an axe violation left at the end', async ({ page }) => {
  await servedPage(page, PAGE.replace('<h1>leak</h1>', '<h1>leak</h1><img src="data:,">'))
})

test('a failed request to an allowed origin left at the end', async ({ page }) => {
  await servedPage(page)
  await page.route('http://localhost:4173/fail.js', (route) => route.abort())
  await Promise.all([page.waitForEvent('requestfailed'), page.evaluate("fetch('/fail.js').catch(() => 0)")])
})
