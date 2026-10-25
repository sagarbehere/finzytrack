// @vitest-environment node
// The frontend's type gate and lint bans cannot be loosened silently (EP-22, INV-06, INV-07).
// The tsconfig is pinned whole; each ESLint ban is checked on planted code beside code that must pass.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

const root = fileURLToPath(new URL('../..', import.meta.url))

describe('[EP-22] the TypeScript config', () => {
  it('is exactly the strict one', () => {
    const config: unknown = JSON.parse(readFileSync(`${root}/tsconfig.json`, 'utf8'))
    expect(config).toEqual({
      compilerOptions: {
        target: 'ES2022',
        lib: ['ES2022', 'DOM', 'DOM.Iterable'],
        module: 'ESNext',
        moduleResolution: 'Bundler',
        types: ['vite/client'],
        strict: true,
        noUncheckedIndexedAccess: true,
        exactOptionalPropertyTypes: true,
        noImplicitOverride: true,
        noImplicitReturns: true,
        noFallthroughCasesInSwitch: true,
        noPropertyAccessFromIndexSignature: true,
        noUnusedLocals: true,
        noUnusedParameters: true,
        useUnknownInCatchVariables: true,
        verbatimModuleSyntax: true,
        isolatedModules: true,
        skipLibCheck: true,
        noEmit: true,
      },
      include: ['src', 'tests', 'e2e', '*.config.ts'],
      vueCompilerOptions: { strictTemplates: true },
    })
  })
})

async function ruleIds(code: string, file = 'main.ts', dir = 'src'): Promise<string[]> {
  // The text replaces an existing file's content in memory; nothing is written to disk.
  const eslint = new ESLint({ cwd: root })
  const [result] = await eslint.lintText(code, { filePath: `${root}/${dir}/${file}` })
  return (result?.messages ?? []).map((m) => m.ruleId ?? 'parse-error')
}

describe('[INV-06][INV-07] lint bans', () => {
  it.each([
    ['a system "today"', 'export const d = new Date()\n', 'no-restricted-syntax'],
    ['Date.now', 'export const t = Date.now()\n', 'no-restricted-syntax'],
    ['float money formatting', 'export const s = (1.5).toFixed(2)\n', 'no-restricted-properties'],
    ['Date() without new', 'export const s = Date()\n', 'no-restricted-syntax'],
    ['globalThis.Date', 'export const d = new globalThis.Date()\n', 'no-restricted-syntax'],
    ['window.Date.now', 'export const t = window.Date.now()\n', 'no-restricted-syntax'],
  ])('refuses %s', async (_label, code, rule) => {
    expect(await ruleIds(code)).toContain(rule)
  })

  it('accepts a date built from given parts', async () => {
    expect(await ruleIds('export const d = new Date(2026, 9, 25)\n')).toEqual([])
    expect(await ruleIds('export const d = new globalThis.Date(2026, 9, 25)\n')).toEqual([])
  })

  const sfc = (expr: string) =>
    `<script setup lang="ts">\nconst amount = 1.5\n</script>\n\n<template>\n  <p>{{ amount }} {{ ${expr} }}</p>\n</template>\n`

  it.each(['amount.toFixed(2)', 'new Date()', 'Date.now()', 'Date()'])('refuses %s in a template', async (expr) => {
    expect(await ruleIds(sfc(expr), 'App.vue')).toContain('vue/no-restricted-syntax')
  })

  it('accepts a plain template expression', async () => {
    expect(await ruleIds(sfc('amount'), 'App.vue')).toEqual([])
  })
})

describe('[L9.M-7] tests stub components by import, not by name', () => {
  const inTest = (code: string) => ruleIds(code, 'App.test.ts', 'tests/unit')

  it.each([
    ['a stubs map in mount options', 'mount(App, { global: { stubs: { Child: true } } })\n'],
    ['a quoted stubs key', "mount(App, { global: { 'stubs': { Child: true } } })\n"],
    ['stubs set on the global config', 'config.global.stubs = { Child: true }\n'],
    ['stubs set by a computed key', "config.global['stubs'] = {}\n"],
    ['findComponent by name', "wrapper.findComponent({ name: 'Child' })\n"],
    ['findAllComponents by name', "wrapper.findAllComponents({ name: 'Child' })\n"],
    ['getComponent by name', "wrapper.getComponent({ name: 'Child' })\n"],
    ['findComponent by a quoted name key', "wrapper.findComponent({ 'name': 'Child' })\n"],
    ['stubs by a template-literal member', 'config.global[`stubs`] = {}\n'],
    ['stubs by a template-literal key', 'mount(App, { global: { [`stubs`]: {} } })\n'],
  ])('refuses %s', async (_label, code) => {
    expect(await inTest(code)).toContain('no-restricted-syntax')
  })

  it('accepts stubbing and finding by import', async () => {
    const ok = "vi.mock('../../src/Child.vue')\nwrapper.findComponent(Child)\nwrapper.findComponent({ ref: 'x' })\n"
    expect(await inTest(ok)).not.toContain('no-restricted-syntax')
  })

  it('keeps the INV-07 bans in test files', async () => {
    expect(await inTest('export const d = new Date()\n')).toContain('no-restricted-syntax')
  })

  it('does not touch app code', async () => {
    expect(await ruleIds("export const o = { stubs: 1, name: 'x' }\n")).toEqual([])
  })
})

describe('[INV-24] Playwright specs run through the acceptance harness (S0030)', () => {
  const inSpec = (code: string) => ruleIds(code, 'smoke.spec.ts', 'e2e')

  it.each([
    ['test from @playwright/test', "import { test } from '@playwright/test'\ntest('x', () => undefined)\n"],
    ['expect from @playwright/test', "import { expect } from '@playwright/test'\nexpect(1).toBe(1)\n"],
    ['a namespace import', "import * as pw from '@playwright/test'\npw.test('x', () => undefined)\n"],
  ])('refuses %s in a spec', async (_label, code) => {
    expect(await inSpec(code)).toContain('@typescript-eslint/no-restricted-imports')
  })

  it('refuses @playwright/test in an e2e helper module too, so no helper re-exports an unguarded test', async () => {
    const helper = "import { test } from '@playwright/test'\nexport { test }\n"
    expect(await ruleIds(helper, 'policy.ts', 'e2e/harness')).toContain('@typescript-eslint/no-restricted-imports')
  })

  it.each([
    [
      'browser.newContext',
      "import { test } from './harness/fixtures'\ntest('x', async ({ browser }) => {\n  await browser.newContext()\n})\n",
    ],
    [
      'browser.newPage',
      "import { test } from './harness/fixtures'\ntest('x', async ({ browser }) => {\n  await browser.newPage()\n})\n",
    ],
  ])('refuses %s in a spec', async (_label, code) => {
    expect(await inSpec(code)).toContain('no-restricted-properties')
  })

  it('accepts the harness fixtures and type-only imports', async () => {
    const ok =
      "import type { Page } from '@playwright/test'\n\nimport { expect, test } from './harness/fixtures'\n\n" +
      "test('x', async ({ page }: { page: Page }) => {\n  await expect(page).toHaveTitle('x')\n})\n"
    expect(await inSpec(ok)).not.toContain('@typescript-eslint/no-restricted-imports')
  })
})
