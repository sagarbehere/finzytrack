/**
 * Recipe Validation
 *
 * Pure validation functions for JSON widget and dashboard recipes.
 * Returns structured error lists — no side effects.
 */

import { SUPPORTED_CHART_TYPES, VALID_VALUE_FORMATS } from '@/types/recipes'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface RecipeValidationError {
  /** Path to the offending field, e.g. "visualization.chartType" */
  field: string
  message: string
}

export interface RecipeFileError {
  /** Path as it appears in manifest.json, e.g. "widgets/expense-treemap.json" */
  file: string
  /** 'parse' = JSON.parse failed; 'schema' = content failed validation */
  kind: 'parse' | 'schema'
  errors: RecipeValidationError[]
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

const VALID_VIZ_TYPES = ['kpi', 'chart', 'table', 'pivot'] as const
const VALID_TRANSFORM_TYPES = ['sortBy', 'limit', 'pluck', 'pivot'] as const
const VALID_SIMPLE_TRANSFORMS = ['none', 'firstRow', 'firstValue'] as const
const VALID_PARAM_TYPES = ['date', 'select', 'number'] as const
const VALID_FORMAT_COLUMNS = ['monthYear', 'yearMonth'] as const
const VALID_SORT_ROWS_BY = ['total_desc', 'total_asc', 'label_asc', 'label_desc'] as const

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

// ─── Transform validation ─────────────────────────────────────────────────────

function validateTransform(transform: unknown, prefix: string): RecipeValidationError[] {
  if (transform === undefined) return []
  const errors: RecipeValidationError[] = []

  // Simple string transforms
  if (typeof transform === 'string') {
    if (!VALID_SIMPLE_TRANSFORMS.includes(transform as never)) {
      errors.push({
        field: prefix,
        message: `unknown simple transform "${transform}"; must be one of: ${VALID_SIMPLE_TRANSFORMS.join(', ')}`,
      })
    }
    return errors
  }

  if (!isPlainObject(transform)) {
    errors.push({ field: prefix, message: 'must be a string or object' })
    return errors
  }

  if (!VALID_TRANSFORM_TYPES.includes(transform.type as never)) {
    errors.push({
      field: `${prefix}.type`,
      message: `must be one of: ${VALID_TRANSFORM_TYPES.join(', ')}`,
    })
    return errors
  }

  if (transform.type === 'sortBy') {
    if (transform.field !== undefined && !isString(transform.field)) {
      errors.push({ field: `${prefix}.field`, message: 'must be a non-empty string' })
    }
    if (transform.order !== undefined && !['asc', 'desc'].includes(String(transform.order))) {
      errors.push({ field: `${prefix}.order`, message: 'must be "asc" or "desc"' })
    }
  }

  if (transform.type === 'limit') {
    if (transform.count !== undefined && (typeof transform.count !== 'number' || transform.count < 1)) {
      errors.push({ field: `${prefix}.count`, message: 'must be a positive number' })
    }
  }

  if (transform.type === 'pluck') {
    if (!isString(transform.field)) {
      errors.push({ field: `${prefix}.field`, message: 'required for pluck transform' })
    }
  }

  if (transform.type === 'pivot') {
    if (!isString(transform.rowField)) errors.push({ field: `${prefix}.rowField`, message: 'required for pivot transform' })
    if (!isString(transform.columnField)) errors.push({ field: `${prefix}.columnField`, message: 'required for pivot transform' })
    if (!isString(transform.valueField)) errors.push({ field: `${prefix}.valueField`, message: 'required for pivot transform' })
    if (transform.formatColumn !== undefined && !VALID_FORMAT_COLUMNS.includes(transform.formatColumn as never)) {
      errors.push({ field: `${prefix}.formatColumn`, message: `must be one of: ${VALID_FORMAT_COLUMNS.join(', ')}` })
    }
    if (transform.sortRowsBy !== undefined && !VALID_SORT_ROWS_BY.includes(transform.sortRowsBy as never)) {
      errors.push({ field: `${prefix}.sortRowsBy`, message: `must be one of: ${VALID_SORT_ROWS_BY.join(', ')}` })
    }
  }

  return errors
}

// ─── Visualization validation ─────────────────────────────────────────────────

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

// ─── Widget validation ────────────────────────────────────────────────────────

/**
 * Validate the content of a parsed JSON widget recipe.
 * Returns an array of validation errors (empty = valid).
 */
export function validateJsonWidgetRecipe(recipe: unknown): RecipeValidationError[] {
  const errors: RecipeValidationError[] = []

  if (!isPlainObject(recipe)) {
    errors.push({ field: '(root)', message: 'recipe must be a JSON object' })
    return errors
  }

  if (!isString(recipe.id)) errors.push({ field: 'id', message: 'required, must be a non-empty string' })
  if (!isString(recipe.title)) errors.push({ field: 'title', message: 'required, must be a non-empty string' })
  if (!isString(recipe.query)) errors.push({ field: 'query', message: 'required, must be a non-empty string' })

  errors.push(...validateVisualization(recipe.visualization, 'visualization'))
  errors.push(...validateTransform(recipe.transform, 'transform'))
  errors.push(...validateParameters(recipe.parameters, 'parameters'))

  return errors
}

// ─── Dashboard validation ─────────────────────────────────────────────────────

/**
 * Validate the content of a parsed JSON dashboard recipe.
 * Also validates any inline widgets in the "widgets" array.
 * Returns an array of validation errors (empty = valid).
 */
export function validateJsonDashboardRecipe(dashboard: unknown): RecipeValidationError[] {
  const errors: RecipeValidationError[] = []

  if (!isPlainObject(dashboard)) {
    errors.push({ field: '(root)', message: 'dashboard must be a JSON object' })
    return errors
  }

  if (!isString(dashboard.id)) errors.push({ field: 'id', message: 'required, must be a non-empty string' })
  if (!isString(dashboard.title)) errors.push({ field: 'title', message: 'required, must be a non-empty string' })
  errors.push(...validateParameters(dashboard.parameters, 'parameters'))

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

  // Inline widgets
  if (dashboard.widgets !== undefined) {
    if (!Array.isArray(dashboard.widgets)) {
      errors.push({ field: 'widgets', message: 'must be an array' })
    } else {
      dashboard.widgets.forEach((w: unknown, i: number) => {
        const widgetErrors = validateJsonWidgetRecipe(w)
        widgetErrors.forEach((e) =>
          errors.push({ field: `widgets[${i}].${e.field}`, message: e.message })
        )
      })
    }
  }

  return errors
}
