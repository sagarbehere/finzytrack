/**
 * Recipe Validation (client)
 *
 * Pure validation for JSON dashboard recipes (the only recipe type). Validates
 * the steps/DAG shape: per-kind step fields, {{...}} reference resolution,
 * acyclicity, unique step ids, no {{...}} inside sql.query, output presence,
 * and schemaVersion. `fn`-name validity (compute/transform) is checked
 * server-side (refactored-dashboard-recipes.md §3.6 G8 / §4.8). Returns
 * structured error lists — no side effects.
 */

import {
  SUPPORTED_CHART_TYPES,
  VALID_VALUE_FORMATS,
  STEP_KINDS,
  VIZ_TYPES,
  VALID_PARAM_TYPES,
} from '@/types/recipes'
import { WHOLE_TOKEN_RE, hasTokens, stepRefs } from '@/recipes/templating'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface RecipeValidationError {
  /** Path to the offending field, e.g. "widgets[0].steps[1].fn" */
  field: string
  message: string
}

export interface RecipeFileError {
  file: string
  kind: 'parse' | 'schema'
  errors: RecipeValidationError[]
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

// Viz types + param types are the schema-derived generated enums (no hand list).
const VALID_VIZ_TYPES = VIZ_TYPES
const CURRENT_SCHEMA_VERSION = 2

function isString(v: unknown): v is string {
  return typeof v === 'string' && v.trim() !== ''
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

// ─── Parameter validation ─────────────────────────────────────────────────────

function validateParameters(params: unknown, prefix: string): RecipeValidationError[] {
  if (params === undefined) return []
  const errors: RecipeValidationError[] = []
  if (!Array.isArray(params)) {
    errors.push({ field: prefix, message: 'must be an array' })
    return errors
  }
  params.forEach((p, i) => {
    const path = `${prefix}[${i}]`
    if (!isPlainObject(p)) {
      errors.push({ field: path, message: 'must be an object' })
      return
    }
    if (!isString(p.name)) errors.push({ field: `${path}.name`, message: 'required, must be a non-empty string' })
    if (!isString(p.label)) errors.push({ field: `${path}.label`, message: 'required, must be a non-empty string' })
    if (!VALID_PARAM_TYPES.includes(p.type as never)) {
      errors.push({ field: `${path}.type`, message: `must be one of: ${VALID_PARAM_TYPES.join(', ')}` })
    }
  })
  return errors
}

// ─── Steps validation ─────────────────────────────────────────────────────────

interface StepsValidation {
  errors: RecipeValidationError[]
  stepIds: string[]
}

/**
 * Validate a steps array: per-kind fields, unique ids, references resolve,
 * acyclicity, no {{...}} in sql.query.
 *
 * @param knownDashboardStepIds - ids of dashboard shared steps a widget step may
 *   reference via {{dashboard.steps.x}}. Empty for dashboard-level shared steps
 *   (they may only reference each other).
 */
function validateSteps(
  steps: unknown,
  prefix: string,
  knownDashboardStepIds: string[],
): StepsValidation {
  const errors: RecipeValidationError[] = []
  if (!Array.isArray(steps) || steps.length === 0) {
    errors.push({ field: prefix, message: 'required, must be a non-empty array of steps' })
    return { errors, stepIds: [] }
  }

  const ids: string[] = []
  const seen = new Set<string>()
  steps.forEach((s, i) => {
    const path = `${prefix}[${i}]`
    if (!isPlainObject(s)) {
      errors.push({ field: path, message: 'must be an object' })
      return
    }
    if (!isString(s.id)) {
      errors.push({ field: `${path}.id`, message: 'required, must be a non-empty string' })
    } else {
      if (seen.has(s.id)) errors.push({ field: `${path}.id`, message: `duplicate step id "${s.id}"` })
      seen.add(s.id)
      ids.push(s.id)
    }
    if (!STEP_KINDS.includes(s.kind as never)) {
      errors.push({ field: `${path}.kind`, message: `required, must be one of: ${STEP_KINDS.join(', ')}` })
      return
    }
    if (s.kind === 'query') {
      if (!isString(s.query)) errors.push({ field: `${path}.query`, message: 'required, must be a non-empty query string' })
      else if (hasTokens(s.query)) errors.push({ field: `${path}.query`, message: 'query steps cannot use {{...}} references — use :paramName for parameters; combine other steps in a transform' })
    } else if (s.kind === 'compute') {
      if (!isString(s.fn)) errors.push({ field: `${path}.fn`, message: 'required, must be a non-empty string' })
      if (s.args !== undefined && !isPlainObject(s.args)) errors.push({ field: `${path}.args`, message: 'must be an object' })
    } else if (s.kind === 'transform') {
      if (!isString(s.fn)) errors.push({ field: `${path}.fn`, message: 'required, must be a non-empty string' })
      if (!Array.isArray(s.inputs) || s.inputs.length === 0) {
        errors.push({ field: `${path}.inputs`, message: 'required, must be a non-empty array of {{steps.x}} references' })
      } else {
        s.inputs.forEach((inp, j) => {
          if (typeof inp !== 'string' || !WHOLE_TOKEN_RE.test(inp)) {
            errors.push({ field: `${path}.inputs[${j}]`, message: 'must be a {{steps.x}} or {{dashboard.steps.x}} reference' })
          }
        })
      }
      if (s.config !== undefined && !isPlainObject(s.config)) errors.push({ field: `${path}.config`, message: 'must be an object' })
    }
  })

  // Reference resolution + acyclicity (only meaningful when ids are well-formed)
  const idSet = new Set(ids)
  const dashSet = new Set(knownDashboardStepIds)
  const adjacency = new Map<string, string[]>()
  steps.forEach((s, i) => {
    if (!isPlainObject(s) || !isString(s.id)) return
    const path = `${prefix}[${i}]`
    const localDeps: string[] = []
    for (const ref of stepRefs(s)) {
      if (ref.scope === 'steps') {
        if (!idSet.has(ref.id)) errors.push({ field: path, message: `references unknown step "${ref.id}"` })
        else localDeps.push(ref.id)
      } else if (ref.scope === 'dashboard.steps') {
        if (!dashSet.has(ref.id)) errors.push({ field: path, message: `references unknown dashboard shared step "${ref.id}"` })
      } else if (ref.scope === 'unknown') {
        errors.push({ field: path, message: `unknown reference scope in "{{${ref.id}}}" (use params / steps / dashboard.steps)` })
      }
    }
    adjacency.set(s.id, localDeps)
  })

  // Cycle detection
  const state = new Map<string, 'visiting' | 'done'>()
  const visit = (id: string): boolean => {
    if (state.get(id) === 'done') return false
    if (state.get(id) === 'visiting') return true
    state.set(id, 'visiting')
    for (const dep of adjacency.get(id) ?? []) {
      if (visit(dep)) return true
    }
    state.set(id, 'done')
    return false
  }
  for (const id of ids) {
    if (visit(id)) {
      errors.push({ field: prefix, message: `cyclic step reference involving "${id}"` })
      break
    }
  }

  return { errors, stepIds: ids }
}

// ─── Visualization validation ──────────────────────────────────────────────────

function validateValueFormat(value: unknown, field: string): RecipeValidationError[] {
  if (value === undefined) return []
  if (!VALID_VALUE_FORMATS.includes(value as never)) {
    return [{ field, message: `must be one of: ${VALID_VALUE_FORMATS.join(', ')}` }]
  }
  return []
}

function validateVisualization(viz: unknown, prefix: string): RecipeValidationError[] {
  const errors: RecipeValidationError[] = []
  if (!isPlainObject(viz)) {
    errors.push({ field: prefix, message: 'required, must be an object' })
    return errors
  }

  if (!VALID_VIZ_TYPES.includes(viz.type as never)) {
    errors.push({
      field: `${prefix}.type`,
      message: `required, must be one of: ${VALID_VIZ_TYPES.join(', ')}`,
    })
    return errors
  }

  if (viz.type === 'chart') {
    if (!isString(viz.chartType)) {
      errors.push({ field: `${prefix}.chartType`, message: 'required for chart visualization' })
    } else if (!SUPPORTED_CHART_TYPES.includes(viz.chartType as never)) {
      errors.push({
        field: `${prefix}.chartType`,
        message: `must be one of: ${SUPPORTED_CHART_TYPES.join(', ')}`,
      })
    }
    errors.push(...validateValueFormat(viz.seriesLabelFormat, `${prefix}.seriesLabelFormat`))
    errors.push(...validateValueFormat(viz.yAxisLabelFormat, `${prefix}.yAxisLabelFormat`))
    errors.push(...validateValueFormat(viz.xAxisLabelFormat, `${prefix}.xAxisLabelFormat`))
    if (viz.options !== undefined && !isPlainObject(viz.options)) {
      errors.push({ field: `${prefix}.options`, message: 'must be an object' })
    }
  }

  if (viz.type === 'kpi') {
    if (viz.format !== undefined) {
      errors.push(...validateValueFormat(viz.format, `${prefix}.format`))
    }
    if (viz.iconColor !== undefined) {
      const validColors = ['blue', 'green', 'red', 'purple', 'amber']
      if (!validColors.includes(String(viz.iconColor))) {
        errors.push({ field: `${prefix}.iconColor`, message: `must be one of: ${validColors.join(', ')}` })
      }
    }
  }

  if (viz.type === 'pivot') {
    if (viz.valueLink !== undefined) {
      if (!isPlainObject(viz.valueLink)) {
        errors.push({ field: `${prefix}.valueLink`, message: 'must be an object' })
      } else {
        if (!isString(viz.valueLink.name)) {
          errors.push({ field: `${prefix}.valueLink.name`, message: 'required, must be a non-empty string' })
        }
        if (!isPlainObject(viz.valueLink.query)) {
          errors.push({ field: `${prefix}.valueLink.query`, message: 'required, must be an object' })
        }
      }
    }
  }

  return errors
}

// ─── Widget validation (inline only) ────────────────────────────────────────────

function validateWidget(
  widget: unknown,
  prefix: string,
  knownDashboardStepIds: string[],
): RecipeValidationError[] {
  const errors: RecipeValidationError[] = []
  if (!isPlainObject(widget)) {
    errors.push({ field: prefix, message: 'must be an object' })
    return errors
  }

  if (!isString(widget.id)) errors.push({ field: `${prefix}.id`, message: 'required, must be a non-empty string' })
  if (!isString(widget.title)) errors.push({ field: `${prefix}.title`, message: 'required, must be a non-empty string' })

  // Legacy single-query form — reject with a migration hint.
  if (widget.query !== undefined || widget.transform !== undefined) {
    errors.push({ field: `${prefix}`, message: 'legacy single-query widget (query/transform) is no longer supported — run the recipe migration to convert it to steps/output' })
  }

  const { errors: stepErrors, stepIds } = validateSteps(widget.steps, `${prefix}.steps`, knownDashboardStepIds)
  errors.push(...stepErrors)

  if (!isString(widget.output)) {
    errors.push({ field: `${prefix}.output`, message: 'required, must name a step in this widget' })
  } else if (stepIds.length > 0 && !stepIds.includes(widget.output)) {
    errors.push({ field: `${prefix}.output`, message: `names unknown step "${widget.output}"` })
  }

  errors.push(...validateVisualization(widget.visualization, `${prefix}.visualization`))
  errors.push(...validateParameters(widget.parameters, `${prefix}.parameters`))

  return errors
}

// ─── Dashboard validation ─────────────────────────────────────────────────────

/**
 * Validate the content of a parsed JSON dashboard recipe (the only recipe type).
 * Returns an array of validation errors (empty = valid).
 */
export function validateJsonDashboardRecipe(dashboard: unknown): RecipeValidationError[] {
  const errors: RecipeValidationError[] = []

  if (!isPlainObject(dashboard)) {
    errors.push({ field: '(root)', message: 'dashboard must be a JSON object' })
    return errors
  }

  // Format version gate — files without v2 are stale (legacy form / pre-migration).
  if (dashboard.schemaVersion !== CURRENT_SCHEMA_VERSION) {
    errors.push({ field: 'schemaVersion', message: `must be ${CURRENT_SCHEMA_VERSION} (run the recipe migration to upgrade legacy recipes)` })
  }

  if (!isString(dashboard.id)) errors.push({ field: 'id', message: 'required, must be a non-empty string' })
  if (!isString(dashboard.title)) errors.push({ field: 'title', message: 'required, must be a non-empty string' })
  errors.push(...validateParameters(dashboard.parameters, 'parameters'))

  // Dashboard shared steps (optional). They may reference only each other.
  let dashboardStepIds: string[] = []
  if (dashboard.steps !== undefined) {
    const { errors: sharedErrors, stepIds } = validateSteps(dashboard.steps, 'steps', [])
    errors.push(...sharedErrors)
    dashboardStepIds = stepIds
  }

  // Layout
  if (!isPlainObject(dashboard.layout)) {
    errors.push({ field: 'layout', message: 'required, must be an object' })
  } else {
    if (!Array.isArray(dashboard.layout.widgets)) {
      errors.push({ field: 'layout.widgets', message: 'required, must be an array' })
    } else {
      dashboard.layout.widgets.forEach((w: unknown, i: number) => {
        const path = `layout.widgets[${i}]`
        if (!isPlainObject(w)) {
          errors.push({ field: path, message: 'must be an object' })
          return
        }
        if (!isString(w.widgetId)) errors.push({ field: `${path}.widgetId`, message: 'required, must be a non-empty string' })
        if (!isString(w.gridArea)) errors.push({ field: `${path}.gridArea`, message: 'required, must be a non-empty string' })
      })
    }
  }

  // Inline widgets (non-empty)
  if (!Array.isArray(dashboard.widgets) || dashboard.widgets.length === 0) {
    errors.push({ field: 'widgets', message: 'required, must be a non-empty array of inline widgets' })
  } else {
    dashboard.widgets.forEach((w: unknown, i: number) => {
      errors.push(...validateWidget(w, `widgets[${i}]`, dashboardStepIds))
    })
  }

  return errors
}
