// @vitest-environment node
// Theme tokens (L9-11, ADR-0035; INV-18): one set of CSS variables in src/app/theme.css, defined for light (`:root`)
// and dark (`.dark`). Text on its surface reaches 4.5:1 and a field's border and the focus ring (as drawn) 3:1 against the page
// (WCAG 2.2 AA, 1.4.3 and 1.4.11), in both modes. Fields are 16 px on touch screens, so iOS does not zoom on focus
// (L9-16): an unlayered rule, which beats Tailwind's layered utilities such as the vendored input's `md:text-sm`.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const themeCss = readFileSync(fileURLToPath(new URL('../../../src/app/theme.css', import.meta.url)), 'utf8')

interface Block {
  prelude: string
  body: string
}

/** The top-level blocks of a stylesheet (comments removed), each with its prelude and the text inside its braces. */
function topLevelBlocks(css: string): Block[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out: Block[] = []
  let depth = 0
  let start = 0
  let open = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '{') {
      if (depth === 0) open = i
      depth++
    } else if (c === '}') {
      depth--
      if (depth === 0) {
        out.push({
          prelude: text
            .slice(start, open)
            .replace(/^[\s\S]*;/, '')
            .trim(),
          body: text.slice(open + 1, i),
        })
        start = i + 1
      }
    } else if (c === ';' && depth === 0) {
      start = i + 1
    }
  }
  return out
}

function declarations(body: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const part of body.split(';')) {
    const at = part.indexOf(':')
    if (at > 0) out.set(part.slice(0, at).trim(), part.slice(at + 1).trim())
  }
  return out
}

type Mode = 'light' | 'dark'
const MODE_SELECTOR: Record<Mode, string> = { light: ':root', dark: '.dark' }

function tokens(css: string, mode: Mode): Map<string, string> {
  const block = topLevelBlocks(css).find((b) => b.prelude === MODE_SELECTOR[mode])
  return block ? declarations(block.body) : new Map<string, string>()
}

type Rgb = [number, number, number] // gamma-encoded sRGB, 0..1

/** CSS `oklch(L C H)` (L as a number or a percentage) to gamma-encoded sRGB, clamped to the gamut. */
function oklchToSrgb(value: string): Rgb {
  const m = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(value)
  if (!m) throw new Error(`not an oklch() colour: ${value}`)
  const l = Number(m[1]) / (m[2] === '%' ? 100 : 1)
  const c = Number(m[3])
  const h = (Number(m[4]) * Math.PI) / 180
  const a = c * Math.cos(h)
  const b = c * Math.sin(h)
  const l1 = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m1 = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s1 = (l - 0.0894841775 * a - 1.291485548 * b) ** 3
  const linear = [
    4.0767416621 * l1 - 3.3077115913 * m1 + 0.2309699292 * s1,
    -1.2684380046 * l1 + 2.6097574011 * m1 - 0.3413193965 * s1,
    -0.0041960863 * l1 - 0.7034186147 * m1 + 1.707614701 * s1,
  ]
  const encode = (x: number) => {
    const v = Math.min(1, Math.max(0, x))
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
  }
  return [encode(linear[0] ?? 0), encode(linear[1] ?? 0), encode(linear[2] ?? 0)]
}

function luminance([r, g, b]: Rgb): number {
  const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(x: Rgb, y: Rgb): number {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p)
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05)
}

/** A colour drawn at `alpha` over another, as the browser composites (in gamma-encoded sRGB). */
function over(top: Rgb, alpha: number, bottom: Rgb): Rgb {
  return [0, 1, 2].map((i) => alpha * (top[i] ?? 0) + (1 - alpha) * (bottom[i] ?? 0)) as Rgb
}

const WHITE: Rgb = [1, 1, 1]

/** The tokens the vendored components and our pages use (shadcn-vue's names). */
const TOKENS = [
  'background',
  'foreground',
  'card',
  'card-foreground',
  'popover',
  'popover-foreground',
  'primary',
  'primary-foreground',
  'secondary',
  'secondary-foreground',
  'muted',
  'muted-foreground',
  'accent',
  'accent-foreground',
  'destructive',
  'border',
  'input',
  'ring',
] as const
type Token = (typeof TOKENS)[number]

/** A colour as the page shows it: a token, white, or a token drawn at some alpha over the page background. */
type Paint = Token | 'white' | { token: Token; alpha: number; over?: Token }

interface Pair {
  name: string
  fg: Paint
  bg: Paint
  min: number
  modes?: Mode[]
}

// Each pair is a place the components draw one colour on another; the alphas are the vendored classes'
// (`dark:bg-input/30` on fields and outline buttons, `dark:bg-destructive/60` on the destructive button).
const PAIRS: Pair[] = [
  { name: 'text on the page', fg: 'foreground', bg: 'background', min: 4.5 },
  { name: 'text on a card', fg: 'card-foreground', bg: 'card', min: 4.5 },
  { name: 'text on a popover', fg: 'popover-foreground', bg: 'popover', min: 4.5 },
  { name: 'primary button text', fg: 'primary-foreground', bg: 'primary', min: 4.5 },
  { name: 'link text (primary on the page)', fg: 'primary', bg: 'background', min: 4.5 },
  { name: 'secondary button text', fg: 'secondary-foreground', bg: 'secondary', min: 4.5 },
  { name: 'helper text on the page', fg: 'muted-foreground', bg: 'background', min: 4.5 },
  { name: 'helper text on a card', fg: 'muted-foreground', bg: 'card', min: 4.5 },
  { name: 'helper text on a muted surface', fg: 'muted-foreground', bg: 'muted', min: 4.5 },
  { name: 'text on a hovered item', fg: 'accent-foreground', bg: 'accent', min: 4.5 },
  { name: 'error text on the page', fg: 'destructive', bg: 'background', min: 4.5 },
  { name: 'destructive button text', fg: 'white', bg: 'destructive', min: 4.5, modes: ['light'] },
  { name: 'destructive button text', fg: 'white', bg: { token: 'destructive', alpha: 0.6 }, min: 4.5, modes: ['dark'] },
  { name: 'field text', fg: 'foreground', bg: { token: 'input', alpha: 0.3 }, min: 4.5, modes: ['dark'] },
  { name: 'placeholder text', fg: 'muted-foreground', bg: { token: 'input', alpha: 0.3 }, min: 4.5, modes: ['dark'] },
  { name: 'field border', fg: 'input', bg: 'background', min: 3 },
  { name: 'focused field border', fg: 'ring', bg: 'background', min: 3 },
  // The focus ring as drawn (verifier round 1 F1): `focus-visible:ring-ring/50` on the button and input, and
  // `outline-ring/50` on everything (our base layer), so --ring at 50% over the surface it sits on.
  { name: 'focus ring as drawn', fg: { token: 'ring', alpha: 0.5 }, bg: 'background', min: 3 },
  { name: 'focus ring as drawn on a card', fg: { token: 'ring', alpha: 0.5, over: 'card' }, bg: 'card', min: 3 },
  // Stated limit (backlog BL-0325): the destructive button's ring is `ring-destructive/20` (light) and `/40`
  // (dark); at 20% no colour reaches 3:1 over white (black gives 1.6:1), so no token can fix it: a patch is the
  // owner's decision. A field's invalid ring is drawn beside its `border-destructive` edge at full colour.
]

function paint(p: Paint, t: Map<string, string>): Rgb {
  const of = (token: Token) => oklchToSrgb(t.get(`--${token}`) ?? 'missing')
  if (p === 'white') return WHITE
  if (typeof p === 'string') return of(p)
  return over(of(p.token), p.alpha, of(p.over ?? 'background'))
}

/** Every problem of a stylesheet's tokens: a token missing in a mode, or a pair under its ratio. */
function tokenProblems(css: string): string[] {
  const out: string[] = []
  for (const mode of ['light', 'dark'] as const) {
    const t = tokens(css, mode)
    const missing = TOKENS.filter((name) => !t.has(`--${name}`))
    if (missing.length) {
      out.push(`${mode}: missing ${missing.join(', ')}`)
      continue
    }
    for (const pair of PAIRS) {
      if (pair.modes && !pair.modes.includes(mode)) continue
      const ratio = contrast(paint(pair.fg, t), paint(pair.bg, t))
      if (ratio < pair.min)
        out.push(`${mode}: ${pair.name} ${String(Math.round(ratio * 100) / 100)} < ${String(pair.min)}`)
    }
  }
  return out
}

/** Whether the stylesheet sets fields to at least 16 px on coarse pointers, outside every cascade layer. */
function fieldsAreTouchSized(css: string): boolean {
  return topLevelBlocks(css).some((media) => {
    if (!/^@media\s*\(\s*pointer\s*:\s*coarse\s*\)$/.test(media.prelude)) return false
    return topLevelBlocks(media.body).some((rule) => {
      const selectors = rule.prelude.split(',').map((s) => s.trim())
      const size = /^(\d+(?:\.\d+)?)px$/.exec(declarations(rule.body).get('font-size') ?? '')
      return (
        ['input', 'textarea', 'select'].every((s) => selectors.includes(s)) && size !== null && Number(size[1]) >= 16
      )
    })
  })
}

describe('[L9-11][INV-18] theme tokens', () => {
  it('defines every token in light and dark, each pair at its contrast', () => {
    expect(tokenProblems(themeCss)).toEqual([])
  })

  it('declares the colour scheme per mode, so native controls and scrollbars match', () => {
    expect(tokens(themeCss, 'light').get('color-scheme')).toBe('light')
    expect(tokens(themeCss, 'dark').get('color-scheme')).toBe('dark')
  })

  it('makes `dark:` match inside `.dark` (the variant the vendored components use)', () => {
    expect(themeCss).toMatch(/@custom-variant dark \(&:is\(\.dark \*\)\);/)
  })

  // Must-not-pass: planted variants of the real stylesheet.
  it('refuses a token missing in dark', () => {
    const planted = themeCss.replace(/(\.dark\s*\{[^}]*?)--muted-foreground:[^;]*;/, '$1')
    expect(tokenProblems(planted)).toEqual(['dark: missing muted-foreground'])
  })

  it('refuses helper text under 4.5:1 (gray-400 on white)', () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--muted-foreground:)[^;]*;/, '$1 oklch(70.7% 0.022 261.325);')
    expect(tokenProblems(planted).some((p) => p.startsWith('light: helper text on the page'))).toBe(true)
  })

  it('refuses helper text just under 4.5:1 (gray-500 on gray-100, 4.4:1)', () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--muted-foreground:)[^;]*;/, '$1 oklch(55.1% 0.027 264.364);')
    expect(tokenProblems(planted).filter((p) => p.startsWith('light: '))).toEqual([
      expect.stringMatching(/^light: helper text on a muted surface 4\.\d+ < 4\.5$/),
    ])
  })

  it('refuses a field border under 3:1 (gray-200 on white)', () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--input:)[^;]*;/, '$1 oklch(92.8% 0.006 264.531);')
    expect(tokenProblems(planted).some((p) => p.startsWith('light: field border'))).toBe(true)
  })

  it('computes known contrasts (black on white 21:1, white on white 1:1)', () => {
    expect(contrast(oklchToSrgb('oklch(0 0 0)'), WHITE)).toBeCloseTo(21, 1)
    expect(contrast(oklchToSrgb('oklch(1 0 0)'), WHITE)).toBeCloseTo(1, 3)
    // Tailwind's gray-500 (#6b7280) on white is 4.83:1.
    expect(contrast(oklchToSrgb('oklch(55.1% 0.027 264.364)'), WHITE)).toBeCloseTo(4.83, 1)
  })
})

describe('[L9-16] no iOS zoom on fields', () => {
  it('sets fields to 16 px on coarse pointers, outside every layer', () => {
    expect(fieldsAreTouchSized(themeCss)).toBe(true)
  })

  it.each([
    ['inside a layer', '@layer base { @media (pointer: coarse) { input, textarea, select { font-size: 16px; } } }'],
    ['under 16 px', '@media (pointer: coarse) { input, textarea, select { font-size: 15px; } }'],
    ['without select', '@media (pointer: coarse) { input, textarea { font-size: 16px; } }'],
    ['for fine pointers', '@media (pointer: fine) { input, textarea, select { font-size: 16px; } }'],
  ])('refuses the rule %s', (_label, css) => {
    expect(fieldsAreTouchSized(css)).toBe(false)
  })

  it('accepts the rule written plainly', () => {
    expect(fieldsAreTouchSized('@media (pointer: coarse) { input, textarea, select { font-size: 16px; } }')).toBe(true)
  })
})
