// @vitest-environment node
// The frontend's type gate and lint bans cannot be loosened silently (EP-22, INV-06, INV-07).
// The tsconfig and the knip config are pinned whole; each ESLint ban is checked on planted code beside code that must
// pass; ESLint and prettier agree on formatting (S0032).
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { ESLint, type Linter } from 'eslint'
import prettierOff from 'eslint-config-prettier'
import * as prettier from 'prettier'
import { beforeAll, describe, expect, it } from 'vitest'

const root = fileURLToPath(new URL('../..', import.meta.url))

// One ESLint for the file, warmed before any test: its cold start (loading the config and every plugin) took 1-4 s
// and timed out the first lint test under load (S0071 round 2). Paid here, it is outside every test's own time.
let eslint: ESLint
beforeAll(async () => {
  eslint = new ESLint({ cwd: root })
  await eslint.lintText('export {}\n', { filePath: `${root}/src/main.ts` })
})

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

  // BL-0007: outside src/platform the class itself is refused, so no alias of it can read the system clock
  // (`const D = Date; new D()`), and Temporal's system clock (`Temporal.Now`) is refused with it (S0092).
  it.each([
    ['an alias of Date', 'const D = Date\nexport const d = new D()\n', 'no-restricted-globals'],
    [
      'Date passed as a value',
      'export const make = (f: () => unknown) => f\nmake(() => Date)\n',
      'no-restricted-globals',
    ],
    ['a date from parts in app code', 'export const d = new Date(2026, 9, 25)\n', 'no-restricted-globals'],
    ['Date.UTC', 'export const t = Date.UTC(2026, 9, 25)\n', 'no-restricted-globals'],
    ['globalThis.Date', 'export const D = globalThis.Date\n', 'no-restricted-properties'],
    ["globalThis['Date']", "export const D = globalThis['Date']\n", 'no-restricted-properties'],
    ['window.Date', 'export const D = window.Date\n', 'no-restricted-properties'],
    ['self.Date', 'export const D = self.Date\n', 'no-restricted-properties'],
    [
      'Date taken by destructuring',
      'const { Date: D } = globalThis\nexport const d = new D()\n',
      'no-restricted-properties',
    ],
    [
      'Temporal.Now',
      'declare const Temporal: { Now: { plainDateISO(): unknown } }\nexport const d = Temporal.Now.plainDateISO()\n',
      'no-restricted-properties',
    ],
    [
      'an alias of Temporal.Now',
      'declare const T: { Now: unknown }\nexport const n = T.Now\n',
      'no-restricted-properties',
    ],
    // S0092 verifier V3: a date formatter with no date formats the system clock's now.
    [
      'Intl.DateTimeFormat().format() (now, here in UTC)',
      "export const t = new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC' }).format()\n",
      'no-restricted-syntax',
    ],
    ['formatToParts() of now', 'export const t = Intl.DateTimeFormat().formatToParts()\n', 'no-restricted-syntax'],
    [
      'format() of now through a variable',
      'const f = new Intl.DateTimeFormat()\nexport const t = f.format()\n',
      'no-restricted-syntax',
    ],
    [
      "Reflect.get(globalThis, 'Date')",
      "export const D: unknown = Reflect.get(globalThis, 'Date')\n",
      'no-restricted-syntax',
    ],
  ])('refuses %s in src', async (_label, code, rule) => {
    expect(await ruleIds(code)).toContain(rule)
  })

  it.each([
    ['parseFloat', "export const n = parseFloat('10.50')\n", 'no-restricted-globals'],
    ['Number.parseFloat', "export const n = Number.parseFloat('10.50')\n", 'no-restricted-properties'],
    ['an alias of parseFloat', 'const p = parseFloat\nexport const n = p("1.5")\n', 'no-restricted-globals'],
  ])('refuses %s, a float parse of money, in src', async (_label, code, rule) => {
    expect(await ruleIds(code)).toContain(rule)
  })

  // src/platform has no file yet, and typed linting needs one: its rules are read from the resolved config.
  async function rulesFor(file: string): Promise<Partial<Linter.RulesRecord>> {
    const config = (await eslint.calculateConfigForFile(`${root}/${file}`)) as Linter.Config
    return config.rules ?? {}
  }

  it('lets src/platform (the home of the clock adapter) name Date, and still refuses the system clock there', async () => {
    const platform = await rulesFor('src/platform/clock.ts')
    const app = await rulesFor('src/main.ts')
    expect(JSON.stringify(app['no-restricted-globals'])).toContain('"Date"')
    expect(JSON.stringify(platform['no-restricted-globals'] ?? 'off')).not.toContain('"Date"')
    expect(JSON.stringify(platform['no-restricted-syntax'])).toContain(
      "callee.object.name='Date'][callee.property.name='now']",
    )
    expect(JSON.stringify(platform['no-restricted-syntax'])).toContain(
      "NewExpression[callee.name='Date'][arguments.length=0]",
    )
  })

  // @regression-guard (S0092): the must-pass beside the app-code ban; tests and e2e keep S0009's selectors.
  it('accepts a date built from given parts outside src (tests, the acceptance harness)', async () => {
    expect(await ruleIds('export const d = new Date(2026, 9, 25)\n', 'App.test.ts', 'tests/unit')).toEqual([])
  })

  // @regression-guard (S0092): the must-pass beside the bans above (a field `.now`, a shadowing parameter).
  // @regression-guard (S0092 round 1): the must-pass beside the format-of-now ban.
  it('accepts formatting a given date', async () => {
    const code = "declare const when: number\nexport const t = new Intl.DateTimeFormat('en-CA').format(when)\n"
    expect(await ruleIds(code)).toEqual([])
  })

  it('accepts a field or a local of the same name', async () => {
    expect(await ruleIds('export const o = { date: 1, now: 2 }\nexport const n = o.now + o.date\n')).toEqual([])
    expect(await ruleIds('export function f(Date: number): number {\n  return Date\n}\n')).toEqual([])
  })

  const sfc = (expr: string) =>
    `<script setup lang="ts">\nconst amount = 1.5\n</script>\n\n<template>\n  <p>{{ amount }} {{ ${expr} }}</p>\n</template>\n`

  it.each([
    'amount.toFixed(2)',
    'new Date()',
    'Date.now()',
    'Date()',
    'new Date(2026, 9, 25)',
    "parseFloat('1.5')",
    'Intl.DateTimeFormat().format()',
  ])('refuses %s in a template', async (expr) => {
    expect(await ruleIds(sfc(expr), 'App.vue')).toContain('vue/no-restricted-syntax')
  })

  it('accepts a plain template expression', async () => {
    expect(await ruleIds(sfc('amount'), 'App.vue')).toEqual([])
  })

  it('accepts a field named Date or parseFloat in a template (S0092 verifier V6)', async () => {
    const code =
      '<script setup lang="ts">\nconst row = { Date: "2026-01-01", parseFloat: 1 }\n</script>\n\n<template>\n  <p>{{ row.Date }} {{ row.parseFloat }}</p>\n</template>\n'
    expect(await ruleIds(code, 'App.vue')).toEqual([])
  })
})

describe('[EP-23] ledger semantics have one home, in the backend (INV-02, ADR-0024; S0104)', () => {
  // The shapes scripts/concept_counters.py refuses in Python, as lint bans in src (no frontend home yet).
  it.each([
    ['an account regex', 'export const a = /^(Assets|Income)(:[A-Z][\\w-]*)+$/\n'],
    ['an account regex with a Unicode class', 'export const a = /^\\p{Lu}[^:]*(:\\p{Lu}[^:]*)+$/u\n'],
    ['an account regex built from a string', "export const a = new RegExp('^[A-Z][a-z]*:[A-Z].*$')\n"],
    ['RegExp() without new', "export const a = RegExp('[A-Z]+:.*')\n"],
    ["a name split at ':'", "export const leaf = (n: string) => n.split(':').at(-1)\n"],
    ["a name joined with ':'", "export const join = (p: string[]) => p.join(':')\n"],
    ["a name's root found by indexOf", "export const root = (n: string) => n.slice(0, n.indexOf(':'))\n"],
    ['a currency regex', 'export const c = /^[A-Z]{3}$/\n'],
    ['a root name compared', "export const isIncome = (r: string) => r === 'Income'\n"],
    ['a root prefix tested', "export const asset = (n: string) => n.startsWith('Assets:')\n"],
    ['a list of root names', "export const holding = ['Assets', 'Liabilities']\n"],
    [
      'a root name in a switch',
      "export const k = (r: string) => {\n  switch (r) {\n    case 'Expenses':\n      return 1\n  }\n  return 0\n}\n",
    ],
    ['a root option read', "export const r = (o: Record<string, string>) => o['name_assets']\n"],
    ['a tolerance option read', "export const t = (o: Record<string, string>) => o['inferred_tolerance_default']\n"],
    ['a power of ten from a scale', 'export const tol = (e: number) => 10 ** e / 2\n'],
    ['Math.pow of ten from a scale', 'export const tol = (e: number) => Math.pow(10, -e)\n'],
    ['a magnitude compared', 'export const ok = (r: number, t: number) => Math.abs(r) <= t\n'],
    [
      'a decimal magnitude compared',
      'declare const r: { abs(): { lte(x: number): boolean } }\nexport const ok = r.abs().lte(1)\n',
    ],
    ['a value compared with a tolerance', 'export const ok = (r: number, tolerance: number) => r <= tolerance\n'],
    ['a value compared with a fixed tolerance', 'export const ok = (r: number) => r < 0.005\n'],
    ['a number regex', 'export const n = /^-?\\d+(\\.\\d+)?$/\n'],
    ['a number regex with a class', 'export const n = /[0-9]+[.][0-9]*/\n'],
    ['grouping removed by hand', "export const n = (t: string) => t.replace(',', '')\n"],
    ['grouping removed everywhere', "export const n = (t: string) => t.replaceAll(',', '')\n"],
    // S0104 verifier round 1 (F6): decimal.js forms
    ['grouping removed by a regex', "export const n = (t: string) => t.replace(/,/g, '')\n"],
    [
      'a decimal tolerance from a scale',
      'declare const D: new (x: number) => { pow(e: number): unknown }\ndeclare const n: { decimalPlaces(): number }\nexport const tol = new D(10).pow(-n.decimalPlaces())\n',
    ],
    [
      'Decimal.pow of ten from a scale',
      'declare const D: { pow(b: number, e: number): unknown }\nexport const tol = (e: number) => D.pow(10, e)\n',
    ],
    [
      'a scale shifted into a tolerance',
      'declare const one: { shiftedBy(e: number): unknown }\ndeclare const n: { dp(): number }\nexport const tol = one.shiftedBy(-n.dp())\n',
    ],
    [
      'a posting sum tested with isZero',
      'declare const ps: { reduce(f: unknown, s: unknown): { isZero(): boolean } }\nexport const ok = ps.reduce(0, 0).isZero()\n',
    ],
    [
      'a posting sum compared with zero',
      'declare const ps: number[]\nexport const ok = ps.reduce((a, b) => a + b, 0) === 0\n',
    ],
    ['a currency regex with no count', 'export const c = /^[A-Z][A-Z][A-Z]$/\n'],
    [
      'an object keyed by root names',
      "export const k: Record<string, string> = { Assets: 'asset', Income: 'income' }\n",
    ],
    ['an object keyed by quoted root names', "export const k: Record<string, string> = { 'Expenses': 'expense' }\n"],
    ['an object of root names', "export const k = { asset: 'Assets' }\n"],
    ['an enum of root names', "export enum Root {\n  Assets = 'Assets',\n}\n"],
  ])('refuses %s in src', async (_label, code) => {
    expect(await ruleIds(code)).toContain('no-restricted-syntax')
  })

  // S0104 verifier F5: the bans cover all of src, src/platform and src's own tests included, and every script
  // suffix the counter scans. Typed linting needs an existing file, so the resolved config is read.
  it.each([
    'src/main.ts',
    'src/platform/clock.ts',
    'src/domain/x.tsx',
    'src/domain/x.mts',
    'src/domain/x.js',
    'src/domain/x.test.ts',
    'src/App.vue',
  ])('applies to %s', async (file) => {
    const config = (await eslint.calculateConfigForFile(`${root}/${file}`)) as Linter.Config
    expect(JSON.stringify(config.rules?.['no-restricted-syntax'])).toContain('EP-23: a second implementation')
  })

  it('refuses a root name compared in a template', async () => {
    const code =
      '<script setup lang="ts">\nconst root = \'Income\' as string\n</script>\n\n' +
      "<template>\n  <p>{{ root === 'Assets' }}</p>\n</template>\n"
    expect(await ruleIds(code, 'App.vue')).toContain('vue/no-restricted-syntax')
  })

  it.each([
    ['a sha regex', 'export const s = /^[0-9a-f]{64}$/\n'],
    ['a non-capturing group', 'export const s = /(?:-[0-9]{1,3})?/\n'],
    ['a URL', 'export const u = /^https?:\\/\\/[a-z.]+$/\n'],
    ['an account name as content', "export const n = 'Assets:Bank:Checking'\n"],
    ["a test for ':'", "export const has = (t: string) => t.includes(':')\n"],
    ['a comparison of counts', 'export const ok = (n: number, limit: number) => n > limit\n'],
    ['a fixed power of ten', 'export const m = 10 ** 6\n'],
    ['a ratio above one', 'export const ok = (r: number) => r > 1.5\n'],
    ['help text naming a root', "export const help = 'Assets are what you own'\n"],
    ['a power of a value', 'export const sq = (n: { pow(e: number): number }) => n.pow(2)\n'],
    ['a letters regex', 'export const w = /^[A-Za-z]+$/\n'],
    ['an object of kinds', "export const k = { asset: 'asset' }\n"],
    [
      'a sum compared with a limit',
      'declare const ps: number[]\nexport const big = ps.reduce((a, b) => a + b, 0) > 10\n',
    ],
  ])('accepts %s', async (_label, code) => {
    expect(await ruleIds(code)).not.toContain('no-restricted-syntax')
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
        'scripts/**/*.mjs', // S0046: Node build tooling (the API type generator) and its dependencies
      ],
      project: ['src/**/*.{ts,vue}', 'tests/**/*.ts', 'e2e/**/*.ts', 'scripts/**/*.mjs'],
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
    for (const file of ['src/App.vue', 'src/main.ts', 'tests/unit/gates.test.ts']) {
      const config = (await eslint.calculateConfigForFile(`${root}/${file}`)) as Linter.Config
      const on = off.filter((name) => severity(config.rules?.[name]) !== 0)
      expect(on, file).toEqual([])
    }
  })
})
