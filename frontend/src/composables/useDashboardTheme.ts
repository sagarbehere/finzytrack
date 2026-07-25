import { ref, readonly } from 'vue'
import { DashboardThemesService } from '@/services/generated-api'
import type { DashboardTheme } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import {
  BUDGET_STATUS_KEY,
  hexToRgba,
  type BudgetStatus,
  type BudgetStatusColors,
} from '@/utils/budgetStatus'

/**
 * Dashboard color theme — the single source of truth for chart/widget colors.
 * See dev-docs/dashboard-color-system.md.
 *
 * Module-level singleton (per frontend "Composable Lifetimes"): the active theme
 * is global, loaded once at startup, and read by every visualization component.
 * Colors are referenced from recipe JSON as `{{theme.*}}` tokens and resolved
 * here at render time (mode-aware). A raw hex/CSS color passes through unchanged
 * (the value-level escape hatch).
 */

// Built-in default (Dusty Spectrum) — kept in sync with the seeded theme file
// `backend/resources/seed_config/dashboard-themes/dusty-spectrum.json`. Used as
// the initial value so the very first render already has the palette (no flash
// of the ECharts rainbow) and as a fallback if the fetch fails.
const DEFAULT_THEME: DashboardTheme = {
  id: 'dusty-spectrum',
  name: 'Dusty Spectrum',
  brand: { light: '#4f6bb0', dark: '#7b93d6' },
  baseline: { light: '#9aa5b1', dark: '#8a95a3' },
  valence: {
    good: { light: '#4e8f66', dark: '#5faf7f' },
    warn: { light: '#b8863a', dark: '#d8a24a' },
    bad: { light: '#bf5b52', dark: '#d97066' },
    complete: { light: '#3a80b0', dark: '#5aa0d8' },
  },
  series: { budget: '{{theme.baseline}}', actual: '{{theme.brand}}' },
  categorical: {
    light: ['#3f89c3', '#ce724d', '#32a281', '#cba243', '#826dc1', '#c96d97', '#47a8af', '#7db04f', '#ae8157', '#976ec1', '#ca606b', '#6b94c1'],
    dark: ['#4ba3e8', '#f5885c', '#3cc199', '#f2c150', '#9b82e6', '#ef82b4', '#54c8d0', '#95d15e', '#cf9a68', '#b483e6', '#f0727f', '#7fb0e6'],
  },
}

// Module-level singleton state.
const activeTheme = ref<DashboardTheme>(DEFAULT_THEME)
const isLoaded = ref(false)

type Mode = 'light' | 'dark'
const modeKey = (isDark: boolean): Mode => (isDark ? 'dark' : 'light')

/** Blend two `#rrggbb` colors; `t=0` → a, `t=1` → b. */
function mixHex(a: string, b: string, t: number): string {
  const pa = parseHex(a)
  const pb = parseHex(b)
  if (!pa || !pb) return a
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t))
  return `#${c.map((n) => n.toString(16).padStart(2, '0')).join('')}`
}
function parseHex(h: string): [number, number, number] | null {
  const s = h.trim().replace('#', '')
  const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s
  if (full.length !== 6) return null
  const n = [full.slice(0, 2), full.slice(2, 4), full.slice(4, 6)].map((x) => parseInt(x, 16))
  return n.some((x) => Number.isNaN(x)) ? null : (n as [number, number, number])
}

/** Look up a theme path (e.g. "brand", "valence.bad", "series.actual",
 * "categorical.3") for a mode. Returns a color string or a nested token
 * (for `series.*`, whose values may themselves be tokens). */
function lookup(theme: DashboardTheme, path: string, mode: Mode): string | undefined {
  const [head, sub] = path.split('.')
  switch (head) {
    case 'brand':
      if (sub === 'muted') return mixHex(theme.brand[mode], theme.baseline[mode], 0.5)
      return theme.brand[mode]
    case 'baseline':
      return theme.baseline[mode]
    case 'complete':
      return theme.valence.complete[mode]
    case 'valence': {
      const v = theme.valence[sub as keyof DashboardTheme['valence']]
      return v ? v[mode] : undefined
    }
    case 'series':
      return theme.series?.[sub]
    case 'categorical': {
      if (sub == null) return undefined // not a single color
      const arr = theme.categorical[mode]
      const i = parseInt(sub, 10)
      return Number.isFinite(i) ? arr?.[i] : undefined
    }
    default:
      return undefined
  }
}

const THEME_TOKEN = /^\{\{\s*theme\.([a-zA-Z0-9_.]+)\s*\}\}$/

/**
 * Resolve a color reference to a concrete color for the given mode.
 * - A `{{theme.*}}` token → the theme color (recursively, for series aliases).
 * - Anything else (a raw hex/CSS color, or an unrecognized token) → returned as-is.
 */
function resolveThemeColor(token: string, isDark: boolean, depth = 0): string {
  if (typeof token !== 'string') return token
  const m = token.trim().match(THEME_TOKEN)
  if (!m) return token // raw hex/CSS passthrough (value-level escape hatch)
  const val = lookup(activeTheme.value, m[1], modeKey(isDark))
  if (val == null) return token
  if (depth < 4 && THEME_TOKEN.test(val.trim())) return resolveThemeColor(val, isDark, depth + 1)
  return val
}

/** The ordered categorical palette for the mode (identity colors). */
function categoricalPalette(isDark: boolean): string[] {
  return activeTheme.value.categorical[modeKey(isDark)] ?? []
}

/** Deep-resolve every `{{theme.*}}` token string in a value (arrays/objects
 * walked; functions and non-token strings pass through untouched). Used to
 * resolve tokens embedded in ECharts option objects (e.g. series itemStyle.color). */
function resolveTokensDeep<T>(value: T, isDark: boolean): T {
  if (typeof value === 'string') return resolveThemeColor(value, isDark) as unknown as T
  if (Array.isArray(value)) return value.map((v) => resolveTokensDeep(v, isDark)) as unknown as T
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const k of Object.keys(value as Record<string, unknown>)) {
      out[k] = resolveTokensDeep((value as Record<string, unknown>)[k], isDark)
    }
    return out as unknown as T
  }
  return value
}

// ── valence (favorability) — the single source for budget-status colors ──────

const STATUS_TO_VALENCE: Record<BudgetStatus, 'good' | 'warn' | 'bad' | 'complete'> = {
  good: 'good',
  warn: 'warn',
  exact: 'complete',
  bad: 'bad',
}

/** Color for a budget status, mode-aware. A recipe `colors` override (token or
 * hex) wins (value-level escape hatch); otherwise the theme's valence band. */
function valenceColor(status: BudgetStatus, isDark: boolean, overrides?: BudgetStatusColors): string {
  const override = overrides?.[BUDGET_STATUS_KEY[status]]
  if (override) return resolveThemeColor(override, isDark)
  return activeTheme.value.valence[STATUS_TO_VALENCE[status]][modeKey(isDark)]
}

/** The global amber-onset threshold (a recipe may still override per-widget). */
function warnAtDefault(): number {
  const w = activeTheme.value.thresholds?.warnAt
  return typeof w === 'number' && w > 0 && w <= 1 ? w : 0.85
}

/** Opacity for adherence heat-map cell fills. */
function heatmapAlpha(): number {
  const a = activeTheme.value.tints?.heatmapAlpha
  return typeof a === 'number' && a >= 0 && a <= 1 ? a : 0.3
}

// ── interaction states (derived from base colors, not new hues) ──────────────

function lighten(hex: string, amount: number): string {
  return mixHex(hex, '#ffffff', Math.max(0, Math.min(1, amount)))
}
/** Lighten a color for hover. */
function hoverColor(hex: string): string {
  return lighten(hex, activeTheme.value.states?.hoverLighten ?? 0.12)
}
/** Lighten a color for a selected element. */
function selectedColor(hex: string): string {
  return lighten(hex, activeTheme.value.states?.selectedLighten ?? 0.08)
}
/** Fade a color for a de-emphasized/muted element. */
function mutedColor(hex: string): string {
  return hexToRgba(hex, activeTheme.value.states?.muteOpacity ?? 0.35)
}

// ── identity: stickiness + overflow ──────────────────────────────────────────

/** FNV-1a hash → unsigned 32-bit; stable and well-distributed. */
function hashString(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** A categorical color by index, with overflow handling: past the palette
 * length it wraps and shifts lightness so repeats stay distinct. */
function categoricalAt(i: number, isDark: boolean): string {
  const pal = categoricalPalette(isDark)
  if (pal.length === 0) return isDark ? '#7fb0e6' : '#3f89c3'
  if (i < pal.length) return pal[i]
  const wraps = Math.floor(i / pal.length)
  const step = activeTheme.value.overflow?.lightenStep ?? 0.15
  return mixHex(pal[i % pal.length], isDark ? '#ffffff' : '#000000', Math.min(wraps * step, 0.6))
}

/** Flat stickiness: same label → same color across charts (hash → slot). */
function hashColor(label: string, isDark: boolean): string {
  const pal = categoricalPalette(isDark)
  if (pal.length === 0) return categoricalAt(0, isDark)
  return categoricalAt(hashString(label) % pal.length, isDark)
}

// Beancount's five account roots. Stripped so "family" is the meaningful top
// category whether the label is a full path or type-stripped.
const ACCOUNT_TYPES = new Set(['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses'])

/** Segments of an account with any leading account-type root removed, so
 * `Expenses:EatingOut:Restaurants` and `EatingOut:Restaurants` both start at
 * `EatingOut`. */
function strippedSegments(account: string): string[] {
  const parts = account.split(':')
  return parts.length > 1 && ACCOUNT_TYPES.has(parts[0]) ? parts.slice(1) : parts
}

/** The top-level "family" of a label — the first segment after any account-type
 * root. `Expenses:EatingOut:Restaurants` → `EatingOut`; `EatingOut:Coffee` →
 * `EatingOut`; `HouseRent` → `HouseRent`. */
function familyOf(account: string): string {
  return strippedSegments(account)[0] || account
}

/** Hierarchical stickiness: hue by top-level family (hashed), lightness by depth
 * — so all `EatingOut:*` share a hue, shaded by level. A top-level label is its
 * own hashed color (depth 0). */
function familyColor(account: string, isDark: boolean): string {
  const seg = strippedSegments(account)
  const base = hashColor(seg[0] || account, isDark)
  const depth = Math.max(0, seg.length - 1)
  if (depth === 0) return base
  const step = activeTheme.value.stickiness?.depthLightenStep ?? 0.18
  return mixHex(base, isDark ? '#ffffff' : '#000000', Math.min(depth * step, 0.6))
}

/** Load the active theme from the backend (once). Falls back to the built-in
 * default on any failure so charts always have a theme. */
async function loadTheme(): Promise<void> {
  try {
    const resp = await DashboardThemesService.getActiveDashboardThemeApiDashboardThemeGet()
    if (resp.success && resp.data) {
      activeTheme.value = resp.data
    }
  } catch (err: unknown) {
    // Non-fatal: keep the built-in default; surface for visibility.
    errorHandler.display(err)
  } finally {
    isLoaded.value = true
  }
}

export function useDashboardTheme() {
  return {
    theme: readonly(activeTheme),
    isLoaded: readonly(isLoaded),
    loadTheme,
    resolveThemeColor,
    resolveTokensDeep,
    categoricalPalette,
    categoricalAt,
    // valence / thresholds / tints
    valenceColor,
    warnAtDefault,
    heatmapAlpha,
    // interaction states
    hoverColor,
    selectedColor,
    mutedColor,
    // identity stickiness
    hashColor,
    familyColor,
  }
}
