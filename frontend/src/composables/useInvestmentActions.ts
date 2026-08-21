/**
 * Investment Quick Actions — assemble typed events into review-table drafts.
 *
 * Thin wrapper over the backend assembler (`POST /api/investments/assemble`): sends
 * events, surfaces per-event warnings/errors, and converts the returned drafts to
 * `TransactionViewModel`s ready to stage into the shared review table. Register then
 * goes through the existing commit path — this never writes (dev-docs/investment-view.md §6).
 *
 * Function-scoped (composable-lifetimes rule): staging is local to the Investments view.
 */
import { ref } from 'vue'
import { InvestmentsService } from '@/services/generated-api'
import type { AssembleRequest, AssembleIssue } from '@/services/generated-api'
import type { TransactionImportBundle } from '@/types/transactions'
import { convertAssembledDraftsToViewModels } from '@/utils/investmentDrafts'
import { errorHandler } from '@/utils/ErrorHandler'

type InvestmentEvent = AssembleRequest['events'][number]

export interface AssembleOutcome {
  bundle: TransactionImportBundle
  warnings: AssembleIssue[]
  errors: AssembleIssue[]
}

export function useInvestmentActions() {
  const isLoading = ref(false)

  /**
   * Assemble one or more events and convert the resulting drafts to view models.
   * Partial success is preserved: `bundle` holds whatever drafts came back, and
   * `warnings`/`errors` carry the per-event issues for inline display.
   */
  async function assembleAndConvert(events: InvestmentEvent[]): Promise<AssembleOutcome> {
    isLoading.value = true
    try {
      const request: AssembleRequest = { events }
      const response = await InvestmentsService.assembleInvestmentEvents(request)
      if (!response.data) {
        throw new Error('No data received from the assemble endpoint')
      }
      const bundle = convertAssembledDraftsToViewModels(response.data.drafts ?? [])
      return {
        bundle,
        warnings: response.data.warnings ?? [],
        errors: response.data.errors ?? [],
      }
    } catch (error) {
      errorHandler.display(error)
      throw error
    } finally {
      isLoading.value = false
    }
  }

  return { isLoading, assembleAndConvert }
}
