/**
 * Shared budget-status *logic* (which band a usage fraction falls in), so the
 * `budget-progress` bars and the `pivot` heat-map classify identically. The
 * scale: good (under) → warn (approaching, ≥ warnAt) → exact (on budget, = 1)
 * → bad (over, > 1); flipped for income (`overGood`), where hitting the target
 * is good. **Colors are NOT here** — they come from the active theme's valence
 * band (`useDashboardTheme().valenceColor`), the single source of truth. See
 * dev-docs/dashboard-color-system.md.
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
