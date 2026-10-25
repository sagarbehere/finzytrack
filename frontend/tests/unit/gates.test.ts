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

async function ruleIds(code: string, file = 'main.ts'): Promise<string[]> {
  // The text replaces an existing file's content in memory; nothing is written to disk.
  const eslint = new ESLint({ cwd: root })
  const [result] = await eslint.lintText(code, { filePath: `${root}/src/${file}` })
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
