// INV-24: the harness's own hard failures, each provoked on purpose (S0030; EP-29: the cases that must not pass). A provoked
// problem is asserted to be recorded and then cleared, so the test itself passes only when the guard caught it.
import { spawnSync } from 'node:child_process'
import path from 'node:path'

import { checkA11y, expect, test } from './harness/fixtures'
import { OWNER_PORT } from './harness/policy'

const OWNER = `http://127.0.0.1:${String(OWNER_PORT)}`

test("a page request to the owner's app is aborted and recorded", async ({ page, problems }) => {
  await page.goto('/')
  const reached = await page.evaluate(`fetch('${OWNER}/api/boot').then(() => true, () => false)`)
  expect(reached).toBe(false)
  expect(problems).toContainEqual(expect.stringContaining(`request to a foreign origin, aborted: ${OWNER}/api/boot`))
  problems.length = 0
})

test('a page request to any other origin is aborted and recorded', async ({ page, problems }) => {
  await page.goto('/')
  expect(await page.evaluate(`fetch('http://localhost:9999/x').then(() => true, () => false)`)).toBe(false)
  expect(problems).toContainEqual(
    expect.stringContaining('request to a foreign origin, aborted: http://localhost:9999/x'),
  )
  problems.length = 0
})

test('console errors and uncaught page errors are recorded', async ({ page, problems }) => {
  await page.goto('/')
  await page.evaluate("console.error('provoked')")
  await page.addScriptTag({ content: "throw new Error('provoked uncaught')" }).catch(() => undefined)
  await expect.poll(() => problems).toContainEqual('uncaught page error: provoked uncaught')
  expect(problems).toContainEqual('console error: provoked')
  problems.length = 0
})

test('an HTTP error response is recorded', async ({ page, problems }) => {
  await page.route('**/gone.js', (route) => route.fulfill({ status: 404, body: '' }))
  await page.goto('/')
  expect(await page.evaluate("fetch('/gone.js').then((r) => r.text().then(() => r.status))")).toBe(404)
  await expect.poll(() => problems).toContainEqual('HTTP 404: http://localhost:4173/gone.js')
  problems.length = 0
})

test('axe fails on a page with a violation', async ({ page }) => {
  await page.goto('/')
  await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<img id=provoked src=\"data:,\">')")
  await expect(checkA11y(page)).rejects.toThrow(/axe/)
  await page.evaluate("document.getElementById('provoked').remove()")
})

test('an LLM call with no scripted reply is recorded; a scripted one is answered', async ({ llm, run }) => {
  const ask = (content: string) =>
    fetch(`${run.llm}/chat/completions`, {
      method: 'POST',
      body: JSON.stringify({ model: 'stub-model', messages: [{ role: 'user', content }] }),
    })
  expect((await ask('unscripted')).status).toBe(500)
  expect((await llm.calls()).unscripted).toHaveLength(1)
  await llm.script([{ match: 'categorize', reply: 'Expenses:Food' }]) // a new script starts a clean record
  const reply = (await (await ask('categorize Corner Bakery')).json()) as {
    choices: { message: { content: string } }[]
  }
  expect(reply.choices[0]?.message.content).toBe('Expenses:Food')
  expect((await llm.calls()).unscripted).toEqual([])
})

test("the request fixture refuses the owner's app and foreign origins", async ({ request, run }) => {
  await expect(request.get(`${OWNER}/api/boot`)).rejects.toThrow(/not ours/)
  await expect(request.get('http://example.com/')).rejects.toThrow(/not ours/)
  expect((await request.get(`${run.backend}/api/boot`)).status()).toBe(501)
})

test('a problem left at the end of a test fails it', ({}, testInfo) => {
  const frontend = path.resolve(import.meta.dirname, '..')
  const nested = spawnSync(
    path.join(frontend, 'node_modules/.bin/playwright'),
    ['test', '--config', 'e2e/harness/selftest/playwright.config.ts'],
    { cwd: frontend, encoding: 'utf8', env: { ...process.env, FT_SELFTEST_OUT: testInfo.outputPath('nested') } },
  )
  expect(nested.status, nested.stdout).toBe(1)
  expect(nested.stdout).toContain('provoked leak')
  expect(nested.stdout).toContain('LLM calls with no scripted reply')
  expect(nested.stdout).toContain('2 failed')
})
