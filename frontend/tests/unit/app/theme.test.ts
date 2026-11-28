// @vitest-environment node
// Theme tokens (L9-11, L9-12, ADR-0035 and its amendment of 2026-11-28; INV-18; S0170): src/app/theme.css is the one
// source of the look (design/specs/visual-language.md), defined for light (`:root`) and dark (`.dark`). Text reaches
// 4.5:1 on every surface the look draws it on, and a field's border and the focus ring (as drawn) 3:1 (WCAG 2.2 AA,
// 1.4.3 and 1.4.11), in both modes. The type, density, radius and shadow tokens exist, Inter first with system-ui
// behind it. Fields are 16 px on touch screens, so iOS does not zoom on focus
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

/** A token's colour: `#rrggbb` (the board's values, S0170) or `oklch(L C H)`, as gamma-encoded sRGB. */
function toSrgb(value: string): Rgb {
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value)
  if (hex) return [1, 2, 3].map((i) => parseInt(hex[i] ?? '', 16) / 255) as Rgb
  return oklchToSrgb(value)
}

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

/** The badge tones, one per Beancount account type (the type comes from the backend, never from a name). */
const BADGES = ['assets', 'liabilities', 'equity', 'income', 'expenses'] as const

/** The colour tokens: shadcn-vue's names (the vendored components draw with them), its sidebar names, and ours. */
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
  'sidebar',
  'sidebar-foreground',
  'sidebar-accent',
  'sidebar-accent-foreground',
  'sidebar-border',
  'hairline',
  'card-outline',
  'link-underline',
  ...BADGES.flatMap((b) => [`badge-${b}`, `badge-${b}-foreground`] as const),
] as const
type Token = (typeof TOKENS)[number]

/** A colour as the page shows it: a token, white, or a token drawn at some alpha over another (the page by default). */
type Paint = Token | 'white' | { token: Token; alpha: number; over?: Token }

interface Pair {
  name: string
  fg: Paint
  bg: Paint
  min: number
  modes?: Mode[]
}

/** The surfaces text and controls sit on: the page, a card (tables, the top bar), the sidebar, a muted track. */
const SURFACES = [
  ['the page', 'background'],
  ['a card', 'card'],
  ['the sidebar', 'sidebar'],
  ['a muted surface', 'muted'],
] as const

// Each pair is a place the look draws one colour on another (design/specs/visual-language.md); the alphas are the
// classes' (`dark:bg-input/30` on fields and outline buttons, `dark:bg-destructive/60` on the destructive button,
// `ring-ring/50` and `outline-ring/50` for focus).
const PAIRS: Pair[] = [
  { name: 'text on the page', fg: 'foreground', bg: 'background', min: 4.5 },
  { name: 'text on a card', fg: 'card-foreground', bg: 'card', min: 4.5 },
  { name: 'text on a popover', fg: 'popover-foreground', bg: 'popover', min: 4.5 },
  { name: 'text on a muted surface', fg: 'foreground', bg: 'muted', min: 4.5 },
  { name: 'sidebar text', fg: 'sidebar-foreground', bg: 'sidebar', min: 4.5 },
  { name: 'active sidebar item', fg: 'sidebar-accent-foreground', bg: 'sidebar-accent', min: 4.5 },
  { name: 'primary button text', fg: 'primary-foreground', bg: 'primary', min: 4.5 },
  { name: 'link text on the page', fg: 'primary', bg: 'background', min: 4.5 },
  { name: 'link text on a card', fg: 'primary', bg: 'card', min: 4.5 },
  { name: 'secondary button text', fg: 'secondary-foreground', bg: 'secondary', min: 4.5 },
  { name: 'text on a hovered item', fg: 'accent-foreground', bg: 'accent', min: 4.5 },
  // L9-12: helper text on every surface it is drawn on (the top bar's search sits on --secondary).
  ...SURFACES.map(([where, bg]): Pair => ({ name: `helper text on ${where}`, fg: 'muted-foreground', bg, min: 4.5 })),
  { name: 'helper text on a secondary surface', fg: 'muted-foreground', bg: 'secondary', min: 4.5 },
  { name: 'error text on the page', fg: 'destructive', bg: 'background', min: 4.5 },
  { name: 'error text on a card', fg: 'destructive', bg: 'card', min: 4.5 },
  { name: 'destructive button text', fg: 'white', bg: 'destructive', min: 4.5, modes: ['light'] },
  ...(['background', 'card'] as const).map((over): Pair => ({
    name: `destructive button text over ${over}`,
    fg: 'white',
    bg: { token: 'destructive', alpha: 0.6, over },
    min: 4.5,
    modes: ['dark'],
  })),
  ...(['background', 'card'] as const).flatMap((over): Pair[] => [
    {
      name: `field text over ${over}`,
      fg: 'foreground',
      bg: { token: 'input', alpha: 0.3, over },
      min: 4.5,
      modes: ['dark'],
    },
    {
      name: `placeholder text over ${over}`,
      fg: 'muted-foreground',
      bg: { token: 'input', alpha: 0.3, over },
      min: 4.5,
      modes: ['dark'],
    },
  ]),
  ...BADGES.map((b): Pair => ({ name: `${b} badge`, fg: `badge-${b}-foreground`, bg: `badge-${b}`, min: 4.5 })),
  { name: 'field border on the page', fg: 'input', bg: 'background', min: 3 },
  { name: 'field border on a card', fg: 'input', bg: 'card', min: 3 },
  { name: 'focused field border', fg: 'ring', bg: 'background', min: 3 },
  // The focus ring as drawn (S0169 verifier round 1 F1): `focus-visible:ring-ring/50` on the controls and
  // `outline-ring/50` on everything (our base layer), so --ring at 50% over the surface it sits on. The destructive
  // button draws the same ring (BL-0325: components/app/DestructiveButton.vue overrides the vendored
  // `ring-destructive/20`, which no colour can bring to 3:1; tests/unit/components/app.test.ts).
  ...SURFACES.map(([where, bg]): Pair => ({
    name: `focus ring as drawn on ${where}`,
    fg: { token: 'ring', alpha: 0.5, over: bg },
    bg,
    min: 3,
  })),
]

function paint(p: Paint, t: Map<string, string>): Rgb {
  const of = (token: Token) => toSrgb(t.get(`--${token}`) ?? 'missing')
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
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--muted-foreground:)[^;]*;/, '$1 #9ca3af;')
    expect(tokenProblems(planted).some((p) => p.startsWith('light: helper text on the page'))).toBe(true)
  })

  it('refuses helper text just under 4.5:1 on a muted surface while it passes on the page', () => {
    // #6f6f6f is about 4.45:1 on the muted surface and 4.7:1 on the page.
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--muted-foreground:)[^;]*;/, '$1 #6f6f6f;')
    const light = tokenProblems(planted).filter((p) => p.startsWith('light: '))
    expect(light).toContainEqual(expect.stringMatching(/^light: helper text on a muted surface 4\.\d+ < 4\.5$/))
    expect(light.some((p) => p.startsWith('light: helper text on the page'))).toBe(false)
  })

  it("refuses the board's own field border (#9aa1ad, 2.4:1 on the page)", () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--input:)[^;]*;/, '$1 #9aa1ad;')
    expect(tokenProblems(planted).some((p) => p.startsWith('light: field border on the page'))).toBe(true)
  })

  it("refuses the board's dark field border (#596075, 2.8:1 on a card)", () => {
    const planted = themeCss.replace(/(\.dark\s*\{[^}]*?--input:)[^;]*;/, '$1 #596075;')
    expect(tokenProblems(planted)).toContainEqual(expect.stringMatching(/^dark: field border on a card 2\.\d+ < 3$/))
  })

  it('refuses a badge whose text is under 4.5:1 on its tone', () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--badge-assets-foreground:)[^;]*;/, '$1 #6fbf8f;')
    expect(tokenProblems(planted)).toContainEqual(expect.stringMatching(/^light: assets badge \d\.\d+ < 4\.5$/))
  })

  it('refuses a focus ring that is too faint on the sidebar', () => {
    const planted = themeCss.replace(/(:root\s*\{[^}]*?--ring:)[^;]*;/, '$1 #4338ca;')
    expect(tokenProblems(planted)).toContainEqual(expect.stringMatching(/^light: focus ring as drawn on the sidebar/))
  })

  it('computes known contrasts (black on white 21:1, white on white 1:1) @regression-guard', () => {
    expect(contrast(toSrgb('#000000'), WHITE)).toBeCloseTo(21, 1)
    expect(contrast(toSrgb('#6b7280'), WHITE)).toBeCloseTo(4.83, 1)
    expect(contrast(oklchToSrgb('oklch(0 0 0)'), WHITE)).toBeCloseTo(21, 1)
    expect(contrast(oklchToSrgb('oklch(1 0 0)'), WHITE)).toBeCloseTo(1, 3)
    // Tailwind's gray-500 (#6b7280) on white is 4.83:1.
    expect(contrast(oklchToSrgb('oklch(55.1% 0.027 264.364)'), WHITE)).toBeCloseTo(4.83, 1)
  })
})

/** The declarations of every top-level `@theme` block (Tailwind's theme variables). */
function themeVariables(css: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const block of topLevelBlocks(css).filter((b) => /^@theme\b/.test(b.prelude)))
    for (const [k, v] of declarations(block.body)) out.set(k, v)
  return out
}

describe('[S0170] type, density, radius and shadow tokens (design/specs/visual-language.md)', () => {
  const vars = themeVariables(themeCss)
  const root = tokens(themeCss, 'light')

  it('sets Inter first and falls back to the system font', () => {
    const stack = (vars.get('--font-sans') ?? '').split(',').map((f) => f.trim().replace(/^['"]|['"]$/g, ''))
    expect(stack[0]).toBe('Inter Variable')
    expect(stack).toContain('Inter')
    expect(stack).toContain('system-ui')
    expect(stack.at(-1)).toBe('sans-serif')
  })

  it('holds density comfortable as on the board (owner, 2026-11-28: "Density comfortable")', () => {
    expect(Object.fromEntries([...root].filter(([k]) => k.startsWith('--density-')))).toEqual({
      '--density-text': '15px',
      '--density-table-text': '14px',
      '--density-page-x': '32px',
      '--density-page-y': '28px',
      '--density-gap': '20px',
      '--density-cell-y': '11px',
    })
  })

  it('maps every colour token and the type, density, radius and shadow tokens into Tailwind', () => {
    const names = [
      '--spacing-page-x',
      '--spacing-page-y',
      '--spacing-section',
      '--spacing-cell-y',
      '--text-body',
      '--text-table',
      '--text-table-head',
      '--text-caption',
      '--text-badge',
      '--text-page-title',
      '--text-brand',
      '--tracking-title',
      '--radius-md',
      '--radius-xl',
      '--shadow-card',
      '--shadow-segment',
      ...TOKENS.map((t) => `--color-${t}`),
    ]
    expect(names.filter((name) => !vars.has(name))).toEqual([])
  })

  it("draws the card's outline with its token (a faint outline, not a border)", () => {
    expect(vars.get('--shadow-card')).toMatch(/^0 0 0 1px var\(--card-outline\)/)
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
