// INV-24: the harness's own hard failures, each provoked on purpose (S0030; EP-29: the cases that must not pass). A
// provoked problem is asserted to be recorded and then cleared, so the test itself passes only when the guard caught
// it. The foreign origin is a server each test starts on an OS-assigned loopback port, never the owner's port, so a
// guard regression can never send a real request to the owner's app (S0030 verifier F10).
import { spawnSync } from 'node:child_process'
import { createServer as httpServer } from 'node:http'
import { createServer as netServer, type AddressInfo, type Server } from 'node:net'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { checkA11y, expect, test } from './harness/fixtures'

const PAGE = '<html lang="en"><head><title>t</title></head><body><main><h1>t</h1></main></body></html>'

interface Foreign {
  origin: string
  hits: () => number
  close: () => void
}

async function listen(s: Server): Promise<string> {
  await new Promise<void>((resolve) => s.listen(0, '127.0.0.1', resolve))
  return `127.0.0.1:${String((s.address() as AddressInfo).port)}`
}

async function foreignHttp(): Promise<Foreign> {
  let n = 0
  const s = httpServer((_req, res) => {
    n++
    res.end(PAGE)
  })
  const host = await listen(s)
  return { origin: `http://${host}`, hits: () => n, close: () => s.close() }
}

async function foreignSocket(): Promise<Foreign> {
  let n = 0
  const s = netServer((sock) => {
    n++
    sock.destroy()
  })
  const host = await listen(s)
  return { origin: `ws://${host}`, hits: () => n, close: () => s.close() }
}

/** Navigate *page* to the foreign origin, then close it (an aborted navigation leaves an error page behind). */
async function visitForeign(page: Page, f: Foreign): Promise<void> {
  await page.goto(`${f.origin}/x`).catch(() => undefined)
  await page.close()
}

function expectAbortedAndClear(problems: string[], f: Foreign): void {
  expect(f.hits(), 'requests that reached the foreign server').toBe(0)
  expect(problems).toContainEqual(`request to a foreign origin, aborted: ${f.origin}/x`)
  problems.length = 0
}

test('a page request to a foreign origin is aborted and recorded', async ({ page, problems }) => {
  const f = await foreignHttp()
  try {
    await page.goto('/')
    expect(await page.evaluate(`fetch('${f.origin}/x').then(() => true, () => false)`)).toBe(false)
    expectAbortedAndClear(problems, f)
  } finally {
    f.close()
  }
})

test('a second page, a popup and a test that uses only the context are guarded too', async ({
  page,
  context,
  problems,
}) => {
  const f = await foreignHttp()
  try {
    await visitForeign(await context.newPage(), f)
    expectAbortedAndClear(problems, f)
    await page.setContent(PAGE)
    const [popup] = await Promise.all([page.waitForEvent('popup'), page.evaluate("window.open('about:blank')")])
    await popup.evaluate("console.error('popup provoked')")
    await expect.poll(() => problems).toContainEqual('console error: popup provoked')
    problems.length = 0
    await popup.close()
  } finally {
    f.close()
  }
})

test('a context-only test is guarded', async ({ context, problems }) => {
  const f = await foreignHttp()
  try {
    await visitForeign(await context.newPage(), f)
    expectAbortedAndClear(problems, f)
    const p = await context.newPage()
    await p.setContent(PAGE)
    await p.evaluate("console.error('context only')")
    await expect.poll(() => problems).toContainEqual('console error: context only')
    problems.length = 0
  } finally {
    f.close()
  }
})

test('contexts and pages the test opens from the browser are guarded', async ({ browser, problems }) => {
  const f = await foreignHttp()
  try {
    const ctx = await browser.newContext()
    await visitForeign(await ctx.newPage(), f)
    expectAbortedAndClear(problems, f)
    await ctx.close()
    await visitForeign(await browser.newPage(), f)
    expectAbortedAndClear(problems, f)
  } finally {
    f.close()
  }
})

test('a WebSocket is refused and recorded', async ({ page, problems }) => {
  const f = await foreignSocket()
  try {
    await page.setContent(PAGE)
    await page.evaluate(
      `new Promise((r) => { const w = new WebSocket('${f.origin}/'); w.onerror = w.onclose = () => r(null) })`,
    )
    expect(f.hits(), 'TCP connections that reached the WebSocket server').toBe(0)
    expect(problems).toContainEqual(`WebSocket refused: ${f.origin}/`)
    problems.length = 0
  } finally {
    f.close()
  }
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

test('the request fixture refuses foreign origins', async ({ request, run }) => {
  const f = await foreignHttp()
  try {
    await expect(request.get(`${f.origin}/x`)).rejects.toThrow(/not ours/)
    await expect(request.get('http://example.com/')).rejects.toThrow(/not ours/)
    expect(f.hits()).toBe(0)
    expect((await request.get(`${run.backend}/health`)).status()).toBe(200)
  } finally {
    f.close()
  }
})

test('a problem left at the end of a test fails it', ({}, testInfo) => {
  const frontend = path.resolve(import.meta.dirname, '..')
  const nested = spawnSync(
    path.join(frontend, 'node_modules/.bin/playwright'),
    ['test', '--config', 'e2e/harness/selftest/playwright.config.ts'],
    { cwd: frontend, encoding: 'utf8', env: { ...process.env, FT_SELFTEST_OUT: testInfo.outputPath('nested') } },
  )
  expect(nested.status, nested.stdout).toBe(1)
  for (const marker of [
    'provoked leak',
    'LLM calls with no scripted reply',
    'axe on',
    'request failed: http://localhost:4173/fail.js',
    '4 failed',
  ]) {
    expect(nested.stdout, nested.stdout).toContain(marker)
  }
})
