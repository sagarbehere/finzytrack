import { ref } from 'vue'
import type {
  WidgetRecipe,
  JsonWidgetRecipe,
  ValueFormat,
  Step,
  StepKind,
  TransformConfig,
  TransformContext,
} from '@/types/recipes'
import { LedgerService, ComputeService } from '@/services/generated-api'
import type { QueryRequest } from '@/services/generated-api'
import { formatAmount, formatSignedAmount } from '@/utils/currencyFormat'
import { errorHandler } from '@/utils/ErrorHandler'
import { applyTransform } from '@/composables/useRecipeTransforms'
import {
  WHOLE_TOKEN_RE,
  resolvePath,
  interpolateString,
  stepDependencies,
} from '@/recipes/templating'

/**
 * Union type for any widget recipe (TypeScript or JSON). Both are now
 * step/DAG-shaped (the standalone single-query form was removed).
 */
export type AnyWidgetRecipe = WidgetRecipe | JsonWidgetRecipe

/**
 * Structured execution error. A failing step short-circuits its dependents;
 * the widget renders its error state from `stepId`/`message` (which names the
 * failed step). See refactored-dashboard-recipes.md §3.5.
 */
export interface StepError {
  stepId: string
  kind: StepKind | 'graph'
  message: string
}

export function isStepError(e: unknown): e is StepError {
  return (
    typeof e === 'object' &&
    e !== null &&
    'stepId' in e &&
    'kind' in e &&
    'message' in e
  )
}

// ============================================================================
// Predefined Formats
// ============================================================================

/**
 * Predefined format functions for JSON recipes.
 * These can format both numbers and strings depending on the format type.
 * Uses USD as the default currency — use getFormats(currency) for locale-aware currency formatting.
 */
export const predefinedFormats: Record<ValueFormat, (value: unknown) => string> = getFormats()

/** Coerce a display value to a number (the sanctioned float boundary — the
 * value reaching a formatter may be a number or a decimal string). */
function toNum(value: unknown): number {
  return typeof value === 'number' ? value : parseFloat(String(value)) || 0
}

/**
 * Create a set of format functions with the given currency for locale-aware formatting.
 * Currency-dependent formats (currency, signedCurrency) use the specified currency code
 * to determine locale and symbol. Non-currency formats are unaffected.
 *
 * @param currency - ISO 4217 currency code (e.g., 'USD', 'INR'). Defaults to 'USD'.
 */
export function getFormats(currency?: string): Record<ValueFormat, (value: unknown) => string> {
  const curr = currency || 'USD'
  return {
    // Number formats
    currency: (value) => {
      const num = toNum(value)
      return formatAmount(num, curr)
    },

    signedCurrency: (value) => {
      const num = toNum(value)
      return formatSignedAmount(num, curr)
    },

    percent: (value) => {
      const num = toNum(value)
      return num.toLocaleString('en-US', {
        style: 'percent',
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      })
    },

    number: (value) => {
      const num = toNum(value)
      return num.toLocaleString('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
      })
    },

    compact: (value) => {
      const num = toNum(value)
      if (Math.abs(num) >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M'
      }
      if (Math.abs(num) >= 1000) {
        return (num / 1000).toFixed(1) + 'k'
      }
      return num.toFixed(0)
    },

    // Date formats
    date: (value) => {
      const str = String(value)
      const date = new Date(str)
      if (isNaN(date.getTime())) return str
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    },

    dateShort: (value) => {
      const str = String(value)
      const date = new Date(str)
      if (isNaN(date.getTime())) return str
      return date.toLocaleDateString('en-US', {
        year: '2-digit',
        month: 'numeric',
        day: 'numeric',
      })
    },

    // Account name formats
    accountName: (value) => {
      const str = String(value)
      const segments = str.split(':')
      return segments[segments.length - 1] || str
    },

    accountName2: (value) => {
      const str = String(value)
      const segments = str.split(':')
      if (segments.length >= 2) {
        return segments.slice(-2).join(':')
      }
      return str
    },
  }
}

/**
 * Interpolate parameter values into a query string (escaped substitution).
 *
 * Used ONLY for the `beanquery` engine, which takes a complete query string and
 * has no parameter-binding API. The `sqlite` engine does NOT use this — it sends
 * the query with :name placeholders plus a `parameters` map that the database
 * binds, so values can never be parsed as SQL (§3.6 G9). Exported for the
 * beanquery path and tests.
 */
export function interpolateParameters(
  sql: string,
  params: Record<string, string | number>
): string {
  let result = sql
  for (const [key, value] of Object.entries(params)) {
    const placeholder = `:${key}`
    // Numbers don't need quotes, strings need escaped quotes
    const escaped =
      typeof value === 'number'
        ? String(value)
        : `'${String(value).replace(/'/g, "''")}'`
    result = result.replaceAll(placeholder, escaped)
  }
  return result
}

// ── Step-reference interpolation ({{params|steps|dashboard.steps}}) ──────────
//
// The `{{...}}` walker + step-dependency scan live in @/recipes/templating (one
// source of truth shared with the client validator and the renderer — G6).

interface RefScope {
  params: Record<string, string | number>
  steps: Record<string, unknown>
  dashboardSteps: Record<string, unknown>
}

/** Flatten a RefScope into the nested object the dotted-path resolver walks. */
function scopeObject(scope: RefScope): Record<string, unknown> {
  return { params: scope.params, steps: scope.steps, dashboard: { steps: scope.dashboardSteps } }
}

/**
 * Resolve `{{...}}` references in a value.
 * - Whole-value mode: a string that is exactly one token resolves to the actual
 *   value (object/array preserved).
 * - String-interpolation mode: tokens embedded in a larger string resolve to
 *   String(value).
 * Objects/arrays are resolved recursively (for nested compute args).
 */
function interpolateValue(raw: unknown, scope: RefScope): unknown {
  if (typeof raw === 'string') {
    const obj = scopeObject(scope)
    const whole = raw.match(WHOLE_TOKEN_RE)
    if (whole) return resolvePath(whole[1], obj)
    return interpolateString(raw, (path) => resolvePath(path, obj))
  }
  if (Array.isArray(raw)) return raw.map((v) => interpolateValue(v, scope))
  if (raw && typeof raw === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(raw)) out[k] = interpolateValue(v, scope)
    return out
  }
  return raw
}

// ── DAG ordering ─────────────────────────────────────────────────────────────

/**
 * Topologically sort steps by their `{{steps.*}}` references. Throws a
 * graph-level StepError on a cycle or a dangling reference.
 */
function topoSort(steps: Step[]): Step[] {
  const byId = new Map(steps.map((s) => [s.id, s]))
  const deps = new Map(steps.map((s) => [s.id, stepDependencies(s)]))
  const ordered: Step[] = []
  const state = new Map<string, 'visiting' | 'done'>()

  const visit = (id: string, trail: string[]) => {
    if (state.get(id) === 'done') return
    if (state.get(id) === 'visiting') {
      throw { stepId: id, kind: 'graph', message: `Cyclic step reference: ${[...trail, id].join(' → ')}` } as StepError
    }
    const step = byId.get(id)
    if (!step) {
      throw { stepId: id, kind: 'graph', message: `Step references unknown step "${id}"` } as StepError
    }
    state.set(id, 'visiting')
    for (const dep of deps.get(id) ?? []) {
      if (!byId.has(dep)) {
        throw { stepId: id, kind: 'graph', message: `Step "${id}" references unknown step "${dep}"` } as StepError
      }
      visit(dep, [...trail, id])
    }
    state.set(id, 'done')
    ordered.push(step)
  }

  for (const s of steps) visit(s.id, [])
  return ordered
}

/**
 * Composable for executing recipe DAGs.
 *
 * A widget is a graph of `sql` / `compute` / `transform` steps feeding a
 * visualization. The executor is stateless and re-runs the whole graph on each
 * call (no memoization — §3.5). Steps run as soon as their inputs are ready;
 * independent steps run concurrently.
 */
export function useRecipeExecutor() {
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const stepError = ref<StepError | null>(null)

  async function runQueryStep(
    step: Extract<Step, { kind: 'query' }>,
    params: Record<string, string | number>,
  ): Promise<unknown> {
    const engine = step.engine ?? 'sqlite'
    // sqlite: send :name placeholders + a parameters map the DB binds (injection-safe).
    // beanquery: no binding API — interpolate into a complete query string.
    const queryRequest: QueryRequest =
      engine === 'beanquery'
        ? { query: interpolateParameters(step.query, params) }
        : { query: step.query, parameters: params }
    const response = await LedgerService.executeQuery(queryRequest, engine)
    if (!response.success || !response.data) {
      throw { stepId: step.id, kind: 'query', message: response.error?.message || 'Query failed: no data returned' } as StepError
    }
    return response.data.rows as Record<string, unknown>[]
  }

  async function runComputeStep(
    step: Extract<Step, { kind: 'compute' }>,
    scope: RefScope,
  ): Promise<unknown> {
    // Resolve {{params/steps/dashboard.steps}} references in the args, then call
    // the server-side compute function. Args should be small scalars (G4).
    const args = (interpolateValue(step.args ?? {}, scope) ?? {}) as Record<string, unknown>
    const response = await ComputeService.executeCompute({ function: step.fn, args })
    if (!response.success || !response.data) {
      throw { stepId: step.id, kind: 'compute', message: response.error?.message || `Compute "${step.fn}" failed` } as StepError
    }
    // Unwrap the ApiResponse[ComputeData] envelope → the function's result (G7).
    return response.data.result
  }

  function runTransformStep(
    step: Extract<Step, { kind: 'transform' }>,
    scope: RefScope,
    ctx: TransformContext,
  ): unknown {
    const inputs = step.inputs.map((token) => interpolateValue(token, scope))
    // Resolve {{params/steps/...}} inside config too (e.g. pace period bounds).
    const config = step.config ? (interpolateValue(step.config, scope) as TransformConfig) : undefined
    try {
      return applyTransform(step.fn, inputs, config, ctx)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : `Transform "${step.fn}" failed`
      throw { stepId: step.id, kind: 'transform', message } as StepError
    }
  }

  /**
   * Run a step graph to completion and return every step's output. Steps run as
   * soon as their inputs are ready; independent steps run concurrently. Throws a
   * StepError on cycle / dangling ref / step failure.
   */
  async function runStepGraph(
    steps: Step[],
    parameters: Record<string, string | number>,
    dashboardSteps: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const ordered = topoSort(steps)
    const stepOutputs: Record<string, unknown> = {}
    const ctx: TransformContext = { params: parameters }
    const remaining = new Set(ordered.map((s) => s.id))
    const deps = new Map(ordered.map((s) => [s.id, stepDependencies(s)]))
    while (remaining.size > 0) {
      const ready = ordered.filter(
        (s) => remaining.has(s.id) && (deps.get(s.id) ?? []).every((d) => !remaining.has(d)),
      )
      await Promise.all(
        ready.map(async (step) => {
          const scope: RefScope = { params: parameters, steps: stepOutputs, dashboardSteps }
          if (step.kind === 'query') stepOutputs[step.id] = await runQueryStep(step, parameters)
          else if (step.kind === 'compute') stepOutputs[step.id] = await runComputeStep(step, scope)
          else stepOutputs[step.id] = runTransformStep(step, scope, ctx)
          remaining.delete(step.id)
        }),
      )
    }
    return stepOutputs
  }

  /**
   * Execute dashboard shared steps once and return their outputs, keyed by id —
   * consumed by widgets via {{dashboard.steps.<id>}}. (§3.3)
   */
  async function executeSharedSteps(
    steps: Step[] | undefined,
    parameters: Record<string, string | number>,
  ): Promise<Record<string, unknown>> {
    if (!steps || steps.length === 0) return {}
    return runStepGraph(steps, parameters, {})
  }

  /**
   * Execute a widget recipe with given parameters.
   * Returns the `output` step's result, ready for visualization.
   *
   * @param dashboardSteps - resolved outputs of dashboard shared steps,
   *   referenced from widget steps via {{dashboard.steps.<id>}}.
   */
  async function executeRecipe(
    recipe: AnyWidgetRecipe,
    parameters: Record<string, string | number>,
    dashboardSteps: Record<string, unknown> = {},
  ): Promise<unknown> {
    isLoading.value = true
    error.value = null
    stepError.value = null

    try {
      const stepOutputs = await runStepGraph(recipe.steps, parameters, dashboardSteps)
      if (!(recipe.output in stepOutputs)) {
        throw { stepId: recipe.output, kind: 'graph', message: `output names unknown step "${recipe.output}"` } as StepError
      }
      return stepOutputs[recipe.output]
    } catch (err: unknown) {
      const se: StepError = isStepError(err)
        ? err
        : { stepId: recipe.output, kind: 'graph', message: err instanceof Error ? err.message : 'Failed to execute recipe' }
      stepError.value = se
      error.value = se.message
      errorHandler.display(err)
      throw se
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Build initial parameter values from recipe defaults.
   *
   * After `resolveRecipeGenerators` runs at load time, `param.default` is
   * always a scalar — either a literal `string`/`number`, or a sentinel
   * string like `"$gen:currentMonth"` for no-arg generator defaults. We
   * preserve the sentinel here so the UI can offer it as a templated option;
   * call `resolveParameterValue` at the consumer point (query interpolation,
   * widget rendering) to get the runtime scalar.
   */
  function getDefaultParameters(
    recipe: AnyWidgetRecipe
  ): Record<string, string | number> {
    const params: Record<string, string | number> = {}
    if (recipe.parameters) {
      for (const param of recipe.parameters) {
        params[param.name] = param.default as string | number
      }
    }
    return params
  }

  /**
   * Get format function for a value format type, with optional currency for locale-aware formatting.
   */
  function getFormatFunction(format?: ValueFormat, currency?: string): ((value: number) => string) | undefined {
    if (!format) return undefined
    return getFormats(currency)[format]
  }

  return {
    executeRecipe,
    executeSharedSteps,
    getDefaultParameters,
    getFormatFunction,
    isLoading,
    error,
    stepError,
  }
}
