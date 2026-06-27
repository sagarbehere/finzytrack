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

  /** Load budget directives. By default all raw directives (history) for editing. */
  async function load(opts: { history?: boolean; account?: string; asOf?: string } = {}): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const resp = await BudgetsService.getBudgets(
        opts.account ?? null,
        null,
        opts.asOf ?? null,
        opts.history ?? true,
      )
      if (!resp.success || !resp.data) {
        throw new Error(resp.error?.message || 'Failed to load budgets')
      }
      budgets.value = resp.data.budgets
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

  return { budgets, isLoading, isSaving, error, load, create, update, remove }
}
