/* Auto-generated from recipe.schema.json — do not edit by hand. Regenerate via npm run generate-recipe-types. */

// Runtime mirrors of the schema's enums/discriminators. Import these instead of
// re-declaring the value lists by hand.

export const STEP_KINDS = ['query', 'compute', 'transform'] as const
export const VIZ_TYPES = ['chart', 'kpi', 'table', 'pivot', 'budget-progress'] as const
export const SUPPORTED_CHART_TYPES = ['bar', 'line', 'pie', 'area', 'scatter', 'treemap', 'funnel', 'gauge', 'calendar', 'sankey', 'radar', 'sunburst'] as const
export const VALID_VALUE_FORMATS = ['currency', 'percent', 'number', 'compact', 'signedCurrency', 'date', 'dateShort', 'accountName', 'accountName2'] as const
export const VALID_PARAM_TYPES = ['date', 'select', 'number', 'boolean'] as const
export const QUERY_ENGINES = ['sqlite', 'beanquery'] as const
