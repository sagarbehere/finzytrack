// @vitest-environment node
// One source for the look (ADR-0035 amendment of 2026-11-28; S0170): outside src/app/theme.css no file under src
// names a raw colour (hex, rgb()/hsl()/oklch() and the other colour functions) or a Tailwind palette class
// (`bg-indigo-600`, `text-gray-500`, `text-white`), nor fades a text token (`text-muted-foreground/70`, L9-12).
// The vendored copies in src/components/ui are exempt: they are the CLI's bytes (ADR-0035 amendment of the same day)
// and use `text-white` on the destructive button. ESLint runs the rule on .vue and .ts (`ft/no-raw-colour`, from
// scripts/raw-colours.mjs); .css files, which ESLint does not read, are scanned here with the same function.
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { ESLint, type Linter } from 'eslint'
import { beforeAll, describe, expect, it } from 'vitest'

import { isExempt, rawColourProblems } from '../../scripts/raw-colours.mjs'

const root = fileURLToPath(new URL('../..', import.meta.url))

let eslint: ESLint
beforeAll(async () => {
  eslint = new ESLint({ cwd: root })
  await eslint.lintText('export {}\n', { filePath: `${root}/src/main.ts` })
})

async function ruleIds(code: string, path: string): Promise<string[]> {
  // The text replaces an existing (or a new) file's content in memory; nothing is written to disk.
  const [result] = await eslint.lintText(code, { filePath: `${root}/${path}` })
  return (result?.messages ?? []).map((m) => m.ruleId ?? 'parse-error')
}

const vue = (cls: string) => `<template>\n  <p class="${cls}">x</p>\n</template>\n`
const RULE = 'ft/no-raw-colour'

describe('[S0170] raw colours are refused outside theme.css', () => {
  it.each([
    ['a palette background', vue('bg-indigo-600')],
    ['a palette text colour', vue('text-gray-500')],
    ['white text', vue('text-white')],
    ['a palette class behind variants and an opacity', vue('dark:hover:border-slate-700/50')],
    ['a new Tailwind 4 palette', vue('bg-mauve-100')],
    ['a hex arbitrary value', vue('bg-[#fff]')],
    ['an rgb arbitrary value', vue('text-[rgb(1,2,3)]')],
    ['a faded text token', vue('text-muted-foreground/70')],
    ['a faded text token by arbitrary alpha', vue('text-foreground/[0.6]')],
    ['a hex colour in a style block', '<template><p>x</p></template>\n<style>\np { color: #1e293b; }\n</style>\n'],
    ['oklch in a style block', '<template><p>x</p></template>\n<style>\np { color: oklch(50% 0.1 250); }\n</style>\n'],
    ['a bound class string', "<template>\n  <p :class=\"on ? 'text-red-600' : ''\">x</p>\n</template>\n"],
  ])('refuses %s in a .vue file', async (_label, code) => {
    expect(await ruleIds(code, 'src/App.vue')).toContain(RULE)
  })

  it.each([
    ['a hex colour', "export const c = '#4338ca'\n"],
    ['a short hex colour', "export const c = '#fff'\n"],
    ['an 8-digit hex colour', "export const c = '#4338caff'\n"],
    ['rgba()', "export const c = 'rgba(0, 0, 0, 0.5)'\n"],
    ['hsl()', "export const c = 'hsl(220 10% 50%)'\n"],
    ['hwb()', "export const c = 'hwb(220 10% 50%)'\n"],
    ['oklab()', "export const c = 'oklab(0.5 0.1 0.1)'\n"],
    ['lab()', "export const c = 'lab(50% 40 59)'\n"],
    ['lch()', "export const c = 'lch(50% 40 59)'\n"],
    ['color()', "export const c = 'color(display-p3 1 0 0)'\n"],
    ['upper-case RGB()', "export const c = 'RGB(1 2 3)'\n"],
    ['a palette class in a class list', "export const cls = ['px-2', 'bg-emerald-50']\n"],
    ['black background', "export const cls = 'bg-black/40'\n"],
  ])('refuses %s in a .ts file', async (_label, code) => {
    expect(await ruleIds(code, 'src/main.ts')).toContain(RULE)
  })

  it.each([
    [
      'token classes',
      vue('bg-primary text-primary-foreground text-muted-foreground border-input ring-ring/50 bg-badge-assets'),
    ],
    ['an opacity on a surface token', vue('bg-destructive/60 dark:bg-input/30 hover:bg-accent/50')],
    [
      'decoration and sidebar tokens',
      vue('decoration-link-underline bg-sidebar-accent text-sidebar-accent-foreground'),
    ],
    ['text sizes named like colours are not colours', vue('text-sm text-body text-table text-page-title')],
    ['inherited and current colour', vue('text-inherit text-current bg-transparent')],
  ])('accepts %s in a .vue file', async (_label, code) => {
    expect(await ruleIds(code, 'src/App.vue')).not.toContain(RULE)
  })

  it.each([
    ['a mount selector', "export const el = '#app'\n"],
    ['the word colour in prose and names', "export const colorScheme = 'dark' // the color of the page\n"],
    ['a fragment link', "export const href = '#transactions'\n"],
    ['a method named rgb', 'export const o = { rgb: 1 }\n'],
  ])('accepts %s in a .ts file', async (_label, code) => {
    expect(await ruleIds(code, 'src/main.ts')).not.toContain(RULE)
  })

  it('exempts the theme and the vendored copies, and only them', async () => {
    expect(await ruleIds(vue('text-white bg-[#fff]'), 'src/components/ui/button/Button.vue')).not.toContain(RULE)
    const ruleIn = async (file: string) =>
      ((await eslint.calculateConfigForFile(`${root}/${file}`)) as Linter.Config).rules?.[RULE]
    expect(await ruleIn('src/components/app/Probe.vue')).toEqual([2])
    expect(await ruleIn('src/features/x/Probe.ts')).toEqual([2])
    expect(await ruleIn('src/components/ui/button/Button.vue')).toBeUndefined()
    expect(isExempt('src/app/theme.css')).toBe(true)
    expect(isExempt('src/components/ui/input/Input.vue')).toBe(true)
    expect(isExempt('src/app/other.css')).toBe(false)
    expect(isExempt('src/components/uix/A.vue')).toBe(false)
  })

  it('reports each problem with its line', () => {
    expect(rawColourProblems('a {}\np { color: #abcdef; }\n.x { @apply text-zinc-400; }\n')).toEqual([
      { line: 2, text: '#abcdef' },
      { line: 3, text: 'text-zinc-400' },
    ])
  })

  it('finds no raw colour in any .css file under src but the theme', () => {
    const files = (function walk(dir: string): string[] {
      return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
      )
    })(join(root, 'src'))
      .map((f) => relative(root, f))
      .filter((f) => f.endsWith('.css') && !isExempt(f))
    const found = files.flatMap((f) =>
      rawColourProblems(readFileSync(join(root, f), 'utf8')).map((p) => `${f}:${String(p.line)} ${p.text}`),
    )
    expect(found).toEqual([])
  })

  it('would see a raw colour in the theme if it were not exempt (the scan reads CSS)', () => {
    const theme = readFileSync(join(root, 'src/app/theme.css'), 'utf8')
    expect(rawColourProblems(theme).length).toBeGreaterThan(10)
  })
})
