/**
 * Recipe templating + step-graph primitives — the single source of truth for
 * `{{...}}` reference handling, shared by the executor (execution-time step
 * refs), the client validator (shape/acyclicity checks), and the renderer
 * (click-time link templates).
 *
 * Before this module these lived as three divergent copies (different regexes,
 * different missing-value semantics, and dependency scans that missed
 * `transform.config` — a latent DAG-ordering bug). Keeping one walker here is
 * what refactored-dashboard-recipes.md §4.3 / G6 asks for: the same mechanics,
 * even though the resolution *scopes* differ by phase (step refs vs click vars).
 */

/** A string that is exactly one `{{...}}` token (whole-value substitution). */
export const WHOLE_TOKEN_RE = /^\{\{\s*([^}]+?)\s*\}\}$/
/** Any `{{...}}` token embedded in a larger string (string interpolation). */
export const ANY_TOKEN_RE = /\{\{\s*([^}]+?)\s*\}\}/g

/**
 * Walk a dotted path (`steps.actuals`, `row.account`, `dashboard.steps.x`)
 * against a scope object. Returns `undefined` on any missing/non-object hop.
 */
export function resolvePath(path: string, scope: unknown): unknown {
  let current = scope
  for (const part of path.split('.')) {
    if (current === null || current === undefined || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

/**
 * Replace every embedded `{{...}}` token in `str` with `String(resolve(path))`
 * (nullish → empty string). Used for scalar/string interpolation.
 */
export function interpolateString(str: string, resolve: (path: string) => unknown): string {
  return str.replace(new RegExp(ANY_TOKEN_RE.source, 'g'), (_m, p1: string) => {
    const v = resolve(p1.trim())
    return v === null || v === undefined ? '' : String(v)
  })
}

/** True if the string contains any `{{...}}` token. (Stateless — safe to reuse.) */
export function hasTokens(str: string): boolean {
  return /\{\{\s*[^}]+?\s*\}\}/.test(str)
}

/** Every raw token path in a string (`["steps.a", "params.x"]`). */
export function extractTokenPaths(str: string): string[] {
  const out: string[] = []
  let m: RegExpExecArray | null
  const re = new RegExp(ANY_TOKEN_RE.source, 'g')
  while ((m = re.exec(str)) !== null) out.push(m[1].trim())
  return out
}

// ── Step references ───────────────────────────────────────────────────────────

export type RefScopeName = 'params' | 'steps' | 'dashboard.steps' | 'unknown'
export interface TokenRef {
  scope: RefScopeName
  id: string
}

/** Classify a token path into its reference scope + the referenced id. */
export function classifyRef(path: string): TokenRef {
  if (path.startsWith('dashboard.steps.')) return { scope: 'dashboard.steps', id: path.slice('dashboard.steps.'.length).split('.')[0] }
  if (path.startsWith('steps.')) return { scope: 'steps', id: path.slice('steps.'.length).split('.')[0] }
  if (path.startsWith('params.')) return { scope: 'params', id: path.slice('params.'.length).split('.')[0] }
  return { scope: 'unknown', id: path }
}

/** All `{{...}}` refs a string carries, classified by scope. */
export function extractRefs(str: string): TokenRef[] {
  return extractTokenPaths(str).map(classifyRef)
}

/**
 * A step's `{{...}}`-bearing fields, as raw strings to scan. Accepts both the
 * typed `Step` (executor) and the unvalidated object shape (client validator).
 *
 * Scans `compute.args`, `transform.inputs`, AND `transform.config` — the last
 * was previously missed, so a transform whose `config` referenced `{{steps.x}}`
 * was neither ordered after `x` nor validated. `query` steps use `:name` only.
 */
function refBearingStrings(step: unknown): string[] {
  if (typeof step !== 'object' || step === null) return []
  const s = step as Record<string, unknown>
  const out: string[] = []
  if (s.kind === 'compute' && s.args !== undefined) out.push(JSON.stringify(s.args))
  if (s.kind === 'transform') {
    if (Array.isArray(s.inputs)) for (const inp of s.inputs) if (typeof inp === 'string') out.push(inp)
    if (s.config !== undefined) out.push(JSON.stringify(s.config))
  }
  return out
}

/** All classified refs a step carries (args / inputs / config). */
export function stepRefs(step: unknown): TokenRef[] {
  return refBearingStrings(step).flatMap(extractRefs)
}

/**
 * The ids of sibling steps this step depends on via `{{steps.<id>}}` — the
 * edges of the widget's DAG. Excludes `dashboard.steps.*` (pre-resolved inputs)
 * and `params.*`. Shared by the executor's topo-sort and the validator's cycle
 * check so ordering and validation can never disagree.
 */
export function stepDependencies(step: unknown): string[] {
  const deps = new Set<string>()
  for (const ref of stepRefs(step)) if (ref.scope === 'steps') deps.add(ref.id)
  return [...deps]
}
