import { ref } from 'vue'
import { BudgetsService } from '@/services/generated-api'
import type { BudgetItem, BudgetWriteRequest } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

/**
 * Budgets CRUD composable over /api/budgets (the generated BudgetsService).
 * Function-scoped state — the Budgets view owns its own list and dirty tracking.
 */
export function useBudgets() {
  const budgets = ref<BudgetItem[]>([])
  const isLoading = ref(false)
  const isSaving = ref(false)
  const error = ref<string | null>(null)

  type LoadOpts = { history?: boolean; account?: string; currency?: string; asOf?: string }

  /** Fetch budget directives without mutating shared state — returns the list.
   * Use when a caller needs more than one dataset (e.g. effective + history). */
  async function fetch(opts: LoadOpts = {}): Promise<BudgetItem[]> {
    const resp = await BudgetsService.getBudgets(
      opts.account ?? null,
      opts.currency ?? null,
      opts.asOf ?? null,
      opts.history ?? false,
    )
    if (!resp.success || !resp.data) {
      throw new Error(resp.error?.message || 'Failed to load budgets')
    }
    return resp.data.budgets
  }

  /** Load budget directives into shared `budgets`. By default the effective set;
   * pass `history: true` for all raw directives. */
  async function load(opts: LoadOpts = {}): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      budgets.value = await fetch(opts)
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : 'Failed to load budgets'
      errorHandler.display(err)
    } finally {
      isLoading.value = false
    }
  }

  async function create(body: BudgetWriteRequest): Promise<BudgetItem | null> {
    isSaving.value = true
    try {
      const resp = await BudgetsService.createBudget(body)
      if (!resp.success || !resp.data) throw new Error(resp.error?.message || 'Create failed')
      return resp.data.budget ?? null
    } catch (err: unknown) {
      errorHandler.display(err)
      return null
    } finally {
      isSaving.value = false
    }
  }

  async function update(id: string, body: BudgetWriteRequest): Promise<BudgetItem | null> {
    isSaving.value = true
    try {
      const resp = await BudgetsService.updateBudget(id, body)
      if (!resp.success || !resp.data) throw new Error(resp.error?.message || 'Update failed')
      return resp.data.budget ?? null
    } catch (err: unknown) {
      errorHandler.display(err)
      return null
    } finally {
      isSaving.value = false
    }
  }

  async function remove(id: string): Promise<boolean> {
    isSaving.value = true
    try {
      const resp = await BudgetsService.deleteBudget(id)
      if (!resp.success) throw new Error(resp.error?.message || 'Delete failed')
      return true
    } catch (err: unknown) {
      errorHandler.display(err)
      return false
    } finally {
      isSaving.value = false
    }
  }

  return { budgets, isLoading, isSaving, error, fetch, load, create, update, remove }
}
