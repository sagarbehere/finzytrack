// The acceptance-suite harness fixtures (S0030; review:27 §1(d); PROCESS.md §4 UI testing; INV-24, BL-0024).
//
// Each Playwright worker starts its own run (scripts/acceptance/launcher.py): a fresh temp root, a fresh demo
// workspace, the main sidecar on an OS-assigned loopback port with its folders inside that root and its clock frozen,
// and an OpenAI-compatible LLM stub. The zone is the project's `timezoneId` (west of UTC by default). In every test:
//   - the page's clock is frozen at the run's instant (Playwright clock);
//   - a request to any origin other than the preview server and the run's sidecar is aborted and fails the test;
//   - console errors, uncaught page errors, failed requests and HTTP error responses fail the test;
//   - axe runs on the page the test ends on (call `checkA11y(page)` for pages a flow passes through);
//   - an LLM call with no scripted reply fails the test.
// Specs import `test` and `expect` from here, never from '@playwright/test'.
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import path from 'node:path'
import { createInterface } from 'node:readline'

import AxeBuilder from '@axe-core/playwright'
import { test as base, expect, type Page } from '@playwright/test'

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
    const timer = setTimeout(() => {
      reject(new Error(`harness: no ready line within ${String(READY_TIMEOUT_MS)} ms\n${stderr}`))
    }, READY_TIMEOUT_MS)
    lines.once('line', (line) => {
      clearTimeout(timer)
      try {
        resolve({ run: parseReady(line), child })
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)))
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

export const test = base.extend<{ demo: Demo; llm: Llm; problems: string[] }, { run: Run; launched: Launched }>({
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
  llm: async ({ run }, use) => {
    const admin = run.llm.replace(/\/v1$/, '/__harness')
    const llm: Llm = {
      async script(rules) {
        const r = await fetch(`${admin}/script`, { method: 'PUT', body: JSON.stringify({ rules }) })
        expect(r.status, 'LLM stub script').toBe(200)
      },
      async calls() {
        return (await (await fetch(`${admin}/calls`)).json()) as { calls: unknown[]; unscripted: unknown[] }
      },
    }
    await llm.script([])
    await use(llm)
    expect((await llm.calls()).unscripted, 'LLM calls with no scripted reply').toEqual([])
  },
  problems: async ({}, use) => use([]),
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
  page: async ({ page, run, baseURL, problems }, use) => {
    const ours = [baseURL ?? '', run.backend].filter(Boolean)
    await page.clock.setFixedTime(new Date(run.frozenAt))
    await page.context().route('**/*', async (route) => {
      const url = route.request().url()
      if (requestAllowed(url, ours)) {
        await route.fallback()
      } else {
        problems.push(`request to a foreign origin, aborted: ${url}`)
        await route.abort('blockedbyclient')
      }
    })
    page.on('console', (msg) => {
      if (msg.type() === 'error') problems.push(`console error: ${msg.text()}`)
    })
    page.on('pageerror', (err) => problems.push(`uncaught page error: ${err.message}`))
    page.on('requestfailed', (req) => {
      if (requestAllowed(req.url(), ours))
        problems.push(`request failed: ${req.url()} ${req.failure()?.errorText ?? ''}`)
    })
    page.on('response', (res) => {
      if (res.status() >= 400) problems.push(`HTTP ${String(res.status())}: ${res.url()}`)
    })
    await use(page)
    if (!page.isClosed() && (page.url() !== 'about:blank' || (await page.content()) !== EMPTY_PAGE)) {
      await checkA11y(page)
    }
    expect(problems, 'console errors, page errors, failed or foreign requests').toEqual([])
  },
})
