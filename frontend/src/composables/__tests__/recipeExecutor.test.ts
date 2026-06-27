/**
 * DAG executor end-to-end (§7.1): topological execution, sql + compute +
 * transform wiring, {{...}} interpolation, and the StepError contract.
 *
 * LedgerService.executeQuery and ComputeService.executeCompute are mocked so the
 * test exercises the executor's graph logic without a backend.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const executeQuery = vi.fn()
const executeCompute = vi.fn()

vi.mock('@/services/generated-api', () => ({
  LedgerService: { executeQuery: (...a: unknown[]) => executeQuery(...a) },
  ComputeService: { executeCompute: (...a: unknown[]) => executeCompute(...a) },
  ApiError: class ApiError extends Error {},
}))

import { useRecipeExecutor, type StepError } from '@/composables/useRecipeExecutor'
import type { AnyWidgetRecipe } from '@/composables/useRecipeExecutor'

function ok<T>(data: T) {
  return { success: true, data, error: null }
}

beforeEach(() => {
  executeQuery.mockReset()
  executeCompute.mockReset()
})

/** A budget-vs-actual widget: sql actuals + compute budgets + joinBudgetActual. */
const budgetWidget: AnyWidgetRecipe = {
  id: 'budget-vs-actual',
  title: 'Budget vs Actual',
  steps: [
    { id: 'actuals', kind: 'sql', query: "SELECT account, currency, SUM(CAST(amount AS REAL)) AS actual FROM postings WHERE date BETWEEN :monthStart AND :monthEnd GROUP BY account, currency" },
    { id: 'budgets', kind: 'compute', fn: 'budget_for_range', args: { from: '{{params.monthStart}}', to: '{{params.monthEnd}}', currency: '{{params.currency}}' } },
    { id: 'variance', kind: 'transform', fn: 'joinBudgetActual', inputs: ['{{steps.budgets}}', '{{steps.actuals}}'] },
  ],
  output: 'variance',
  visualization: { type: 'table', columns: [] },
}

describe('DAG executor', () => {
  it('runs sql + compute + transform and feeds the output step', async () => {
    executeQuery.mockResolvedValue(ok({ rows: [{ account: 'Expenses:Food', currency: 'USD', actual: 312 }] }))
    executeCompute.mockResolvedValue(ok({ result: [{ account: 'Expenses:Food', currency: 'USD', budget: '600' }] }))

    const { executeRecipe } = useRecipeExecutor()
    const out = (await executeRecipe(budgetWidget, { monthStart: '2026-06-01', monthEnd: '2026-06-30', currency: 'USD' })) as Record<string, unknown>[]

    expect(out).toHaveLength(1)
    expect(out[0].budget).toBe('600')
    expect(out[0].actual).toBe('312')
    expect(out[0].remaining).toBe('288')
  })

  it('interpolates :params into SQL and {{params}} into compute args', async () => {
    executeQuery.mockResolvedValue(ok({ rows: [] }))
    executeCompute.mockResolvedValue(ok({ result: [] }))

    const { executeRecipe } = useRecipeExecutor()
    await executeRecipe(budgetWidget, { monthStart: '2026-06-01', monthEnd: '2026-06-30', currency: 'USD' })

    // SQL :name interpolation (quoted strings).
    const sql = executeQuery.mock.calls[0][0].query as string
    expect(sql).toContain("'2026-06-01'")
    expect(sql).toContain("'2026-06-30'")
    // Compute args resolved from {{params}}.
    expect(executeCompute.mock.calls[0][0]).toEqual({
      function: 'budget_for_range',
      args: { from: '2026-06-01', to: '2026-06-30', currency: 'USD' },
    })
  })

  it('surfaces a StepError naming the failed step and skips dependents', async () => {
    executeQuery.mockResolvedValue(ok({ rows: [] }))
    executeCompute.mockResolvedValue({ success: false, data: null, error: { message: 'boom' } })

    const { executeRecipe } = useRecipeExecutor()
    await expect(executeRecipe(budgetWidget, { monthStart: '2026-06-01', monthEnd: '2026-06-30', currency: 'USD' }))
      .rejects.toMatchObject({ stepId: 'budgets', kind: 'compute' } as Partial<StepError>)
  })

  it('rejects a cyclic graph with a graph-level StepError', async () => {
    const cyclic: AnyWidgetRecipe = {
      id: 'c', title: 'C',
      steps: [
        { id: 'a', kind: 'transform', fn: 'none', inputs: ['{{steps.b}}'] },
        { id: 'b', kind: 'transform', fn: 'none', inputs: ['{{steps.a}}'] },
      ],
      output: 'a',
      visualization: { type: 'table', columns: [] },
    }
    const { executeRecipe } = useRecipeExecutor()
    await expect(executeRecipe(cyclic, {})).rejects.toMatchObject({ kind: 'graph' })
  })
})
