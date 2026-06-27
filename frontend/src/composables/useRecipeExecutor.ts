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
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return formatAmount(num, curr)
    },

    signedCurrency: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return formatSignedAmount(num, curr)
    },

    percent: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return num.toLocaleString('en-US', {
        style: 'percent',
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      })
    },

    number: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
      return num.toLocaleString('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
      })
    },

    compact: (value) => {
      const num = typeof value === 'number' ? value : parseFloat(String(value)) || 0
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
 * Interpolate parameter values into a SQL string.
 * Replaces :paramName placeholders with escaped values (string substitution,
 * not bound params — inherited behaviour; see §3.6 G9). Exported for reuse and
 * tests.
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

const WHOLE_TOKEN_RE = /^\{\{\s*([^}]+?)\s*\}\}$/
const ANY_TOKEN_RE = /\{\{\s*([^}]+?)\s*\}\}/g

interface RefScope {
  params: Record<string, string | number>
  steps: Record<string, unknown>
  dashboardSteps: Record<string, unknown>
}

/** Resolve a dotted reference path (`steps.actuals`, `params.x`, `dashboard.steps.y`). */
function resolveRef(path: string, scope: RefScope): unknown {
  const parts = path.split('.')
  if (parts[0] === 'params') return scope.params[parts[1]]
  if (parts[0] === 'steps') return scope.steps[parts[1]]
  if (parts[0] === 'dashboard' && parts[1] === 'steps') return scope.dashboardSteps[parts[2]]
  return undefined
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
    const whole = raw.match(WHOLE_TOKEN_RE)
    if (whole) return resolveRef(whole[1], scope)
    if (ANY_TOKEN_RE.test(raw)) {
      return raw.replace(ANY_TOKEN_RE, (_m, p1: string) => String(resolveRef(p1.trim(), scope) ?? ''))
    }
    return raw
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

/** Collect the step ids this step references via `{{steps.<id>}}`. */
function stepDependencies(step: Step): string[] {
  const deps = new Set<string>()
  const scan = (s: string) => {
    let m: RegExpExecArray | null
    const re = new RegExp(ANY_TOKEN_RE.source, 'g')
    while ((m = re.exec(s)) !== null) {
      const path = m[1].trim()
      if (path.startsWith('steps.')) deps.add(path.slice('steps.'.length).split('.')[0])
    }
  }
  if (step.kind === 'compute' && step.args) scan(JSON.stringify(step.args))
  if (step.kind === 'transform') step.inputs.forEach(scan)
  return [...deps]
}

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

  async function runSqlStep(
    step: Extract<Step, { kind: 'sql' }>,
    params: Record<string, string | number>,
  ): Promise<unknown> {
    const sql = interpolateParameters(step.query, params)
    const queryRequest: QueryRequest = { query: sql }
    const response = await LedgerService.executeQuery(queryRequest, step.dbType ?? 'sqlite')
    if (!response.success || !response.data) {
      throw { stepId: step.id, kind: 'sql', message: response.error?.message || 'Query failed: no data returned' } as StepError
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
    try {
      return applyTransform(step.fn, inputs, step.config as TransformConfig | undefined, ctx)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : `Transform "${step.fn}" failed`
      throw { stepId: step.id, kind: 'transform', message } as StepError
    }
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
      const ordered = topoSort(recipe.steps)
      const stepOutputs: Record<string, unknown> = {}
      const ctx: TransformContext = { params: parameters }

      // Execute in dependency layers so independent steps run concurrently.
      const remaining = new Set(ordered.map((s) => s.id))
      const deps = new Map(ordered.map((s) => [s.id, stepDependencies(s)]))
      while (remaining.size > 0) {
        const ready = ordered.filter(
          (s) => remaining.has(s.id) && (deps.get(s.id) ?? []).every((d) => !remaining.has(d)),
        )
        await Promise.all(
          ready.map(async (step) => {
            const scope: RefScope = { params: parameters, steps: stepOutputs, dashboardSteps }
            if (step.kind === 'sql') stepOutputs[step.id] = await runSqlStep(step, parameters)
            else if (step.kind === 'compute') stepOutputs[step.id] = await runComputeStep(step, scope)
            else stepOutputs[step.id] = runTransformStep(step, scope, ctx)
            remaining.delete(step.id)
          }),
        )
      }

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
    getDefaultParameters,
    getFormatFunction,
    isLoading,
    error,
    stepError,
  }
}
