// The first frame is in the right theme (L9-11, ADR-0035): the one inline script in index.html reads the stored
// theme (JSON-encoded "light" | "dark" | "system" under ft/v1/app/theme, ADR-0036's key scheme) and sets `.dark` on
// <html> before the first paint; anything else stored, or no storage at all, means "system" (the OS's choice).
// The script runs here in a fresh VM context holding only what a page gives it, so a global it leaks is visible.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createContext, runInContext } from 'node:vm'

import { describe, expect, it } from 'vitest'

const indexHtml = readFileSync(resolve(import.meta.dirname, '../../../index.html'), 'utf8')

/** The bodies of the inline scripts of a page (a script with a src attribute is not inline). */
function inlineScripts(html: string): string[] {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return [...doc.querySelectorAll('script')].filter((s) => !s.hasAttribute('src')).map((s) => s.textContent)
}

interface PageStub {
  stored?: string | null
  storageThrows?: boolean
  osDark: boolean
}

/** Runs a script as the page would, and returns whether <html> ended dark and which globals the script added. */
function run(script: string, page: PageStub): { dark: boolean; leaked: string[]; keysRead: string[] } {
  const root = document.createElement('html')
  const keysRead: string[] = []
  const localStorage = {
    getItem(key: string): string | null {
      if (page.storageThrows === true) throw new Error('SecurityError')
      keysRead.push(key)
      return key === 'ft/v1/app/theme' ? (page.stored ?? null) : null
    },
  }
  const matchMedia = (query: string) => ({ matches: query === '(prefers-color-scheme: dark)' && page.osDark })
  const given = { document: { documentElement: root }, localStorage, matchMedia, JSON }
  const context = createContext({ ...given })
  runInContext(script, context)
  const leaked = Object.keys(context).filter((k) => !(k in given))
  return { dark: root.classList.contains('dark'), leaked, keysRead }
}

const scripts = inlineScripts(indexHtml)
const prepaint = scripts[0] ?? ''

describe('[L9-11] the pre-paint theme script', () => {
  it('is the one inline script of index.html, in <head> before anything else runs', () => {
    expect(scripts).toHaveLength(1)
    const doc = new DOMParser().parseFromString(indexHtml, 'text/html')
    const first = doc.head.querySelector('script')
    expect(first?.hasAttribute('src')).toBe(false)
    expect(first?.getAttribute('type')).toBeNull() // classic, so it blocks the parser (a module script is deferred)
  })

  it.each([
    ['"dark" stored, light OS', '"dark"', false, true],
    ['"dark" stored, dark OS', '"dark"', true, true],
    ['"light" stored, dark OS', '"light"', true, false],
    ['"light" stored, light OS', '"light"', false, false],
    ['"system" stored, dark OS', '"system"', true, true],
    ['"system" stored, light OS', '"system"', false, false],
    ['nothing stored, dark OS', null, true, true],
    ['nothing stored, light OS', null, false, false],
    ['unknown value, dark OS', '"blue"', true, true],
    ['unknown value, light OS', '"blue"', false, false],
    ['a number, dark OS', '1', true, true],
    ['not JSON, dark OS', 'dark', true, true],
    ['not JSON, light OS', '{', false, false],
  ])('%s', (_label, stored, osDark, dark) => {
    const result = run(prepaint, { stored, osDark })
    expect(result.dark).toBe(dark)
    expect(result.keysRead).toEqual(['ft/v1/app/theme'])
  })

  it.each([true, false])('follows the OS when storage throws (OS dark: %s), without throwing', (osDark) => {
    expect(run(prepaint, { storageThrows: true, osDark }).dark).toBe(osDark)
  })

  it('leaves no global behind', () => {
    expect(run(prepaint, { stored: '"dark"', osDark: false }).leaked).toEqual([])
  })

  // Must-not-pass: the harness itself tells these apart, so the cases above can fail.
  it.each([
    [
      'a script that ignores the stored choice',
      "document.documentElement.classList.toggle('dark', matchMedia('(prefers-color-scheme: dark)').matches)",
      { stored: '"light"', osDark: true },
      true,
    ],
    [
      'a script that reads another key',
      "document.documentElement.classList.toggle('dark', JSON.parse(localStorage.getItem('theme')) === 'dark')",
      { stored: '"dark"', osDark: false },
      false,
    ],
  ])('tells apart %s', (_label, script, page, dark) => {
    expect(run(script, page).dark).toBe(dark)
  })

  it('counts every inline script, and only inline ones', () => {
    expect(
      inlineScripts('<head><script>a()</script><script src="/x.js"></script></head><body><script>b()</script>'),
    ).toEqual(['a()', 'b()'])
  })
})
