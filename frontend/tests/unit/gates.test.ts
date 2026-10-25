// @vitest-environment node
// The frontend's type gate and lint bans cannot be loosened silently (EP-22, INV-06, INV-07).
// The tsconfig and the knip config are pinned whole; each ESLint ban is checked on planted code beside code that must
// pass; ESLint and prettier agree on formatting (S0032).
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { ESLint, type Linter } from 'eslint'
import prettierOff from 'eslint-config-prettier'
import * as prettier from 'prettier'
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

  it.each([
    ['expect.soft', "expect.soft('Delete').toBe('Cancel')\n"],
    ['expect.assertions', 'expect.assertions(2)\n'],
    ['expect.hasAssertions', 'expect.hasAssertions()\n'],
    ['an indexed expect.soft', "expect['soft']('Delete').toBe('Cancel')\n"],
    ['soft taken by destructuring', "const { soft } = expect\nsoft('Delete').toBe('Cancel')\n"],
  ])('refuses %s, which defers a failure past an expected-to-fail body (S0032 verifier F1)', async (_label, body) => {
    expect(await inTest(`import { expect } from 'vitest'\n${body}`)).toContain('no-restricted-syntax')
  })

  it('accepts a plain expect', async () => {
    expect(await inTest("import { expect } from 'vitest'\nexpect('Cancel').toBe('Cancel')\n")).toEqual([])
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

describe('[EP-22] the knip config', () => {
  // Seeded plant E12: an `ignore` of src/** hid every unused file and export while knip stayed green.
  it('is exactly the reviewed one @regression-guard', () => {
    const config: unknown = JSON.parse(readFileSync(`${root}/knip.json`, 'utf8'))
    expect(config).toEqual({
      $schema: 'https://unpkg.com/knip@6/schema.json',
      entry: [
        'tests/unit/**/*.test.ts',
        'e2e/**/*.spec.ts',
        'eslint.boundaries.js',
        'e2e/**/*.selftest.ts',
        'e2e/harness/selftest/playwright.config.ts',
      ],
      project: ['src/**/*.{ts,vue}', 'tests/**/*.ts', 'e2e/**/*.ts'],
      ignoreDependencies: ['license-checker-rseidelsohn', '@tauri-apps/cli'],
    })
  })
})

describe('ESLint agrees with prettier on formatting (S0032)', () => {
  // eslint-plugin-vue's layout rules fought prettier's Vue output (the seeded-defect run's side finding):
  // `eslint --fix` and `prettier --write` undid each other. eslint-config-prettier turns every such rule off.
  const severity = (entry: Linter.RuleEntry | undefined): number => {
    const level: unknown = Array.isArray(entry) ? entry[0] : entry
    return level === 'error' || level === 2 ? 2 : level === 'warn' || level === 1 ? 1 : 0
  }
  const layout = [
    'vue/max-attributes-per-line',
    'vue/singleline-html-element-content-newline',
    'vue/multiline-html-element-content-newline',
    'vue/html-indent',
    'vue/html-self-closing',
    'vue/html-closing-bracket-newline',
  ]

  it('passes a prettier-formatted component with several attributes per element', async () => {
    const file = `${root}/src/App.vue`
    const source =
      '<script setup lang="ts">\nconst label = \'x\'\n</script>\n\n<template>\n' +
      '  <section class="a" :title="label" aria-label="probe" data-x="1"><span>{{ label }}</span></section>\n' +
      '  <p class="b" :title="label">{{ label }}</p>\n  <br/>\n</template>\n'
    const formatted = await prettier.format(source, { ...(await prettier.resolveConfig(file)), filepath: file })
    expect(formatted).toContain('<p class="b" :title="label">{{ label }}</p>') // still several attributes on a line
    expect(await ruleIds(formatted, 'App.vue')).toEqual([])
  })

  it('leaves every rule eslint-config-prettier turns off switched off, for Vue and TypeScript files', async () => {
    const off = Object.keys(prettierOff.rules).filter((name) => severity(prettierOff.rules[name]) === 0)
    expect(off).toEqual(expect.arrayContaining(layout))
    const eslint = new ESLint({ cwd: root })
    for (const file of ['src/App.vue', 'src/main.ts', 'tests/unit/gates.test.ts']) {
      const config = (await eslint.calculateConfigForFile(`${root}/${file}`)) as Linter.Config
      const on = off.filter((name) => severity(config.rules?.[name]) !== 0)
      expect(on, file).toEqual([])
    }
  })
})
