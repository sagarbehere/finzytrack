// The acceptance-suite harness fixtures (S0030; review:27 §1(d); PROCESS.md §4 UI testing; INV-24, BL-0024).
//
// Each Playwright worker starts its own run (scripts/acceptance/launcher.py): a fresh temp root, a fresh demo
// workspace, the main sidecar on an OS-assigned loopback port with its folders inside that root and its clock frozen,
// and an OpenAI-compatible LLM stub. The zone is the project's `timezoneId` (west of UTC by default). In every test,
// through the automatic `guard` fixture, on the test's browser context and on every context it opens with
// `browser.newContext()` or `browser.newPage()` (all their pages and popups):
//   - the clock is frozen at the run's instant (Playwright clock);
//   - a request to any origin other than the preview server and the run's sidecar is aborted and fails the test, and
//     every WebSocket is refused (the app uses none);
//   - console errors, uncaught page errors, failed requests and HTTP error responses fail the test;
//   - axe runs on every page still open at the end (call `checkA11y(page)` for pages a flow passes through);
//   - an LLM call with no scripted reply fails the test.
// The `request` fixture follows the same origin rule. Not covered: `page.request`, `context.request` and raw `fetch`
// in test code (BL-0071). Specs import `test` and `expect` from here, never from '@playwright/test' (lint).
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import path from 'node:path'
import { createInterface } from 'node:readline'

import AxeBuilder from '@axe-core/playwright'
import { test as base, expect, type Browser, type BrowserContext, type Page } from '@playwright/test'

import { parseReady, requestAllowed, type Run } from './policy'

export { expect }

const REPO = path.resolve(import.meta.dirname, '../../..')
const PYTHON = path.join(REPO, 'backend/.venv/bin/python')
const LAUNCHER = path.join(REPO, 'scripts/acceptance/launcher.py')
const READY_TIMEOUT_MS = 60_000
const REQUEST_METHODS = new Set(['delete', 'fetch', 'get', 'head', 'patch', 'post', 'put'])
const EMPTY_PAGE = '<html><head></head><body></body></html>'

interface Launched {
  run: Run
  child: ChildProcessWithoutNullStreams
}

function launch(tz: string): Promise<Launched> {
  const child = spawn(PYTHON, [LAUNCHER, '--tz', tz], { cwd: REPO, stdio: 'pipe' })
  let stderr = ''
  child.stderr.on('data', (chunk: Buffer) => (stderr += chunk.toString()))
  return new Promise((resolve, reject) => {
    const lines = createInterface({ input: child.stdout })
    // On a timeout or a bad ready line the launcher is stopped through its stdin and, failing that, by our own
    // handle, so a failed start leaves no run behind (S0030 verifier F7).
    const fail = (err: Error) => {
      child.stdin.end()
      child.kill()
      reject(err)
    }
    const timer = setTimeout(() => {
      fail(new Error(`harness: no ready line within ${String(READY_TIMEOUT_MS)} ms\n${stderr}`))
    }, READY_TIMEOUT_MS)
    lines.once('line', (line) => {
      clearTimeout(timer)
      try {
        resolve({ run: parseReady(line), child })
      } catch (err) {
        fail(err instanceof Error ? err : new Error(String(err)))
      }
    })
    child.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`harness: the launcher exited (${String(code)}) before it was ready\n${stderr}`))
    })
  })
}

/** Stop the run: closing the launcher's stdin makes it stop its own children and remove its root. */
async function finish({ child }: Launched): Promise<void> {
  if (child.exitCode !== null) return
  const exited = new Promise<void>((resolve) => {
    child.once('exit', () => {
      resolve()
    })
  })
  child.stdin.end()
  const timeout = new Promise<'timeout'>((resolve) => {
    setTimeout(() => {
      resolve('timeout')
    }, 30_000)
  })
  if ((await Promise.race([exited, timeout])) === 'timeout') {
    child.kill() // our own child, by its handle; it did not stop in time
    await exited
  }
}

interface LlmRule {
  match: string
  reply?: string
  tool_calls?: unknown[]
}

export interface Llm {
  /** Replace the stub's script; every call must match a rule (the first `match` found in the last user message). */
  script(rules: LlmRule[]): Promise<void>
  calls(): Promise<{ calls: unknown[]; unscripted: unknown[] }>
}

/** The demo workspace of this run. `key` is the registry key once workspaces exist (BL-0071). */
export interface Demo {
  key: string
  path: string
}

/** axe on the current page: no violations (INV-18). */
export async function checkA11y(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations, `axe on ${page.url()}`).toEqual([])
}

async function arm(context: BrowserContext, ours: string[], frozenAt: string, problems: string[]): Promise<void> {
  await context.clock.setFixedTime(new Date(frozenAt))
  await context.route('**/*', async (route) => {
    const url = route.request().url()
    if (requestAllowed(url, ours)) {
      await route.fallback()
    } else {
      problems.push(`request to a foreign origin, aborted: ${url}`)
      await route.abort('blockedbyclient')
    }
  })
  await context.routeWebSocket(
    () => true,
    async (ws) => {
      // The same rule as for requests; a ws: URL never matches an http origin, so every WebSocket is refused.
      if (requestAllowed(ws.url(), ours)) {
        ws.connectToServer()
      } else {
        problems.push(`WebSocket refused: ${ws.url()}`)
        await ws.close()
      }
    },
  )
  context.on('console', (msg) => {
    if (msg.type() === 'error') problems.push(`console error: ${msg.text()}`)
  })
  context.on('weberror', (err) => problems.push(`uncaught page error: ${err.error().message}`))
  context.on('requestfailed', (req) => {
    if (requestAllowed(req.url(), ours)) problems.push(`request failed: ${req.url()} ${req.failure()?.errorText ?? ''}`)
  })
  context.on('response', (res) => {
    if (res.status() >= 400) problems.push(`HTTP ${String(res.status())}: ${res.url()}`)
  })
}

async function llmCalls(run: Run): Promise<{ calls: unknown[]; unscripted: unknown[] }> {
  return (await (await fetch(`${run.llm.replace(/\/v1$/, '/__harness')}/calls`)).json()) as {
    calls: unknown[]
    unscripted: unknown[]
  }
}

async function setScript(run: Run, rules: LlmRule[]): Promise<void> {
  const r = await fetch(`${run.llm.replace(/\/v1$/, '/__harness')}/script`, {
    method: 'PUT',
    body: JSON.stringify({ rules }),
  })
  expect(r.status, 'LLM stub script').toBe(200)
}

type NewContext = Browser['newContext']

export const test = base.extend<
  { demo: Demo; llm: Llm; problems: string[]; guard: undefined },
  { run: Run; launched: Launched }
>({
  launched: [
    async ({}, use, workerInfo) => {
      const tz = workerInfo.project.use.timezoneId ?? 'America/Los_Angeles'
      const launched = await launch(tz)
      try {
        await use(launched)
      } finally {
        await finish(launched)
      }
    },
    { scope: 'worker' },
  ],
  run: [async ({ launched }, use) => use(launched.run), { scope: 'worker' }],
  demo: async ({ run }, use) => use({ key: 'demo', path: run.workspace }),
  llm: async ({ run }, use) => use({ script: (rules) => setScript(run, rules), calls: () => llmCalls(run) }),
  problems: async ({}, use) => use([]),
  guard: [
    async ({ context, browser, run, baseURL, problems }, use) => {
      const ours = [baseURL ?? '', run.backend].filter(Boolean)
      const armed = [context]
      await arm(context, ours, run.frozenAt, problems)
      // A context the test opens itself is armed too; `browser.newPage()` opens its context through this method.
      const original: NewContext = browser.newContext.bind(browser)
      const guarded: NewContext = async (options) => {
        const c = await original(options)
        await arm(c, ours, run.frozenAt, problems)
        armed.push(c)
        return c
      }
      browser.newContext = guarded
      await setScript(run, [])
      try {
        await use(undefined)
      } finally {
        browser.newContext = original
      }
      for (const page of armed.flatMap((c) => c.pages())) {
        if (!page.isClosed() && (page.url() !== 'about:blank' || (await page.content()) !== EMPTY_PAGE)) {
          await checkA11y(page)
        }
      }
      expect((await llmCalls(run)).unscripted, 'LLM calls with no scripted reply').toEqual([])
      expect(problems, 'console errors, page errors, failed or foreign requests').toEqual([])
    },
    { auto: true },
  ],
  request: async ({ request, baseURL, run }, use) => {
    const ours = [baseURL ?? '', run.backend].filter(Boolean)
    const guarded = new Proxy(request, {
      get(target, prop, receiver): unknown {
        const value: unknown = Reflect.get(target, prop, receiver)
        if (typeof value !== 'function' || !REQUEST_METHODS.has(String(prop))) return value
        const method = value as (...args: unknown[]) => Promise<unknown>
        return async (target_: string | { url(): string }, ...rest: unknown[]) => {
          const url = typeof target_ === 'string' ? new URL(target_, baseURL).href : target_.url()
          if (!requestAllowed(url, ours)) {
            throw new Error(`harness: ${url} is not ours; tests reach only the preview server and the run's sidecar`)
          }
          return method.apply(target, [target_, ...rest])
        }
      },
    })
    await use(guarded)
  },
})
