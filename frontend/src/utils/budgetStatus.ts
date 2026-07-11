/**
 * Shared budget status → colour logic, so the `budget-progress` bars and the
 * `pivot` colour-by-value heat-map stay identical (no drift). The scale:
 * green (under) → amber (approaching, ≥ warnAt) → blue (exactly on budget, = 1)
 * → red (over, > 1); flipped for income (`overGood`), where hitting the target
 * is good. See dev-docs/budget-dashboards.md §2a.
 */

export type BudgetStatus = 'good' | 'warn' | 'exact' | 'bad'

/** Per-status colour overrides (any CSS/hex colour); omitted → default palette. */
export interface BudgetStatusColors {
  under?: string
  approaching?: string
  exact?: string
  over?: string
}

/** Config key (recipe-facing name) for each internal status. */
export const BUDGET_STATUS_KEY: Record<BudgetStatus, keyof BudgetStatusColors> = {
  good: 'under',
  warn: 'approaching',
  exact: 'exact',
  bad: 'over',
}

/** Default palette as hex — the exact Tailwind *-500 shades the class palette
 * uses, so inline (pivot cells / custom overrides) and class-based (bars) match. */
export const BUDGET_STATUS_HEX: Record<BudgetStatus, string> = {
  good: '#10b981', // emerald-500
  warn: '#f59e0b', // amber-500
  exact: '#3b82f6', // blue-500
  bad: '#ef4444', // red-500
}

/** The status of a usage fraction. `warnAt` (default 0.85) is the amber onset. */
export function budgetStatusOf(
  pctUsed: number,
  opts?: { overGood?: boolean; warnAt?: number },
): BudgetStatus {
  const warnAt = opts?.warnAt && opts.warnAt > 0 && opts.warnAt <= 1 ? opts.warnAt : 0.85
  if (opts?.overGood) {
    if (pctUsed >= 1) return 'good' // hit/beat the income target
    if (pctUsed >= warnAt) return 'warn'
    return 'bad' // well short of target
  }
  if (pctUsed > 1) return 'bad' // over an expense budget
  if (pctUsed === 1) return 'exact' // exactly on budget (e.g. rent paid in full)
  if (pctUsed >= warnAt) return 'warn'
  return 'good'
}

/** Resolve a status to a CSS colour: a custom override for its role, else the
 * default hex. Use with `rgba()` for a fill/tint. */
export function budgetStatusColor(status: BudgetStatus, colors?: BudgetStatusColors): string {
  return colors?.[BUDGET_STATUS_KEY[status]] ?? BUDGET_STATUS_HEX[status]
}

/** `#rgb`/`#rrggbb` → `rgba(...)`; non-hex (e.g. "rebeccapurple") passes through. */
export function hexToRgba(color: string, alpha: number): string {
  const h = color.trim().replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  if ([r, g, b].some((n) => Number.isNaN(n))) return color
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
