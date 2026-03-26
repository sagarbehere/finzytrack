/**
 * Composable for email import.
 *
 * Uses the generated API client for regular endpoints (profiles, reload,
 * test-connection). Uses fetch() + ReadableStream for POST /fetch — NOT the
 * generated client and NOT EventSource — because it is an SSE endpoint that
 * requires a POST with a JSON body.
 *
 * After the email service was merged into the main backend, all endpoints
 * live under /api/import/email/ on the same origin.
 */
import { ref, computed } from 'vue'
import { OpenAPI, ImportService } from '@/services/generated-api'
import type { ProfileInfo, InvalidProfileInfo, TestConnectionResponse } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'
import { errorHandler } from '@/utils/ErrorHandler'

// ─── Types ───────────────────────────────────────────────────────────────────

export type EmailProfileInfo = ProfileInfo
export type InvalidEmailProfileInfo = InvalidProfileInfo
export type { TestConnectionResponse }

export interface EmailParsedTransaction {
  date: string
  amount: string        // string Decimal — convert to Number for postings
  payee: string
  external_id: string | null
  external_id_type: string | null
  masked_account: string | null
  source_rule: string
  raw_email_from: string
  raw_email_subject: string
  raw_email_date: string
  message_id: string
}

export interface UnmatchedEmailInfo {
  from_address: string
  subject: string
  date: string
  reason: string
}

export interface ExtractionErrorInfo {
  from_address: string
  subject: string
  date: string
  rule_matched: string
  reason: string
}

export interface FetchStats {
  emails_fetched: number
  transactions_parsed: number
  unmatched: number
  extraction_errors: number
  truncated: boolean
}

export interface EmailFetchResult {
  stats: FetchStats
  transactions: EmailParsedTransaction[]
  unmatched_emails: UnmatchedEmailInfo[]
  extraction_errors: ExtractionErrorInfo[]
}

export interface ProgressState {
  phase: 'idle' | 'connecting' | 'fetching' | 'parsing' | 'complete' | 'error'
  message: string
  total: number | null
  current: number | null
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Build the full URL for an email import endpoint (used only for SSE /fetch). */
function emailUrl(path: string): string {
  return `${OpenAPI.BASE}/api/import/email${path}`
}

// ─── Composable ──────────────────────────────────────────────────────────────

export function useEmailImporter() {
  const { config } = useConfig()

  const emailImportEnabled = computed(() => config.value?.email_import?.enabled ?? false)
  const profiles = ref<EmailProfileInfo[]>([])
  const invalidProfiles = ref<InvalidEmailProfileInfo[]>([])
  const profilesError = ref<string | null>(null)
  const isLoadingProfiles = ref(false)
  const isFetching = ref(false)
  const fetchResult = ref<EmailFetchResult | null>(null)
  const fetchError = ref<string | null>(null)
  const progressState = ref<ProgressState>({
    phase: 'idle', message: '', total: null, current: null,
  })

  async function loadProfiles(): Promise<void> {
    if (!emailImportEnabled.value) return
    isLoadingProfiles.value = true
    profilesError.value = null
    try {
      const response = await ImportService.listEmailProfilesApiImportEmailProfilesGet()
      profiles.value = response.data?.profiles || []
      invalidProfiles.value = response.data?.invalid_profiles || []
    } catch (e) {
      profilesError.value = e instanceof Error ? e.message : String(e)
      errorHandler.display(e)
    } finally {
      isLoadingProfiles.value = false
    }
  }

  async function testConnection(
    profileId: string,
  ): Promise<TestConnectionResponse> {
    if (!emailImportEnabled.value) return { success: false, error: 'Email import not enabled' }
    const response = await ImportService.testEmailConnectionApiImportEmailTestConnectionPost({ profile_id: profileId })
    if (!response.data) throw new Error('No data in test-connection response')
    return response.data
  }

  async function reloadProfiles(): Promise<void> {
    if (!emailImportEnabled.value) return
    await ImportService.reloadEmailProfilesApiImportEmailReloadPost()
    await loadProfiles()
  }

  async function fetchTransactions(
    profileId: string,
    sinceDate?: string,
    untilDate?: string,
  ): Promise<EmailFetchResult> {
    if (!emailImportEnabled.value) throw new Error('Email import not enabled')

    isFetching.value = true
    fetchError.value = null
    fetchResult.value = null
    progressState.value = {
      phase: 'connecting', message: 'Connecting…',
      total: null, current: null,
    }

    return new Promise((resolve, reject) => {
      fetch(emailUrl('/fetch'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id: profileId,
          since_date: sinceDate ?? null,
          until_date: untilDate ?? null,
        }),
      })
        .then(async (resp) => {
          if (!resp.ok) {
            const body = await resp.text()
            const msg = `Email import error ${resp.status}: ${body}`
            fetchError.value = msg
            isFetching.value = false
            reject(new Error(msg))
            return
          }

          const reader = resp.body!.getReader()
          const decoder = new TextDecoder()
          let buffer = ''

          const readLoop = async () => {
            // eslint-disable-next-line no-constant-condition
            while (true) {
              const { done, value } = await reader.read()
              if (done) break

              buffer += decoder.decode(value, { stream: true })

              // SSE events are separated by \n\n
              const parts = buffer.split('\n\n')
              buffer = parts.pop() ?? ''   // last chunk may be incomplete

              for (const part of parts) {
                const dataLine = part.split('\n').find(l => l.startsWith('data: '))
                if (!dataLine) continue

                let event: any
                try {
                  event = JSON.parse(dataLine.slice(6))
                } catch (e) {
                  console.warn('Failed to parse SSE event:', part, e)
                  continue
                }

                // Update reactive progress state
                progressState.value = {
                  phase: event.phase,
                  message: event.message,
                  total: event.total ?? null,
                  current: event.current ?? null,
                }

                if (event.phase === 'complete') {
                  const result: EmailFetchResult = event.result
                  fetchResult.value = result
                  isFetching.value = false
                  resolve(result)
                  return
                }

                if (event.phase === 'error') {
                  fetchError.value = event.message
                  isFetching.value = false
                  reject(new Error(event.message))
                  return
                }
              }
            }

            // Stream ended without receiving a 'complete' event
            isFetching.value = false
            const msg = 'Stream ended unexpectedly'
            fetchError.value = msg
            reject(new Error(msg))
          }

          readLoop().catch(e => {
            isFetching.value = false
            fetchError.value = String(e)
            reject(e)
          })
        })
        .catch(e => {
          isFetching.value = false
          fetchError.value = String(e)
          reject(e)
        })
    })
  }

  return {
    emailImportEnabled,
    profiles,
    invalidProfiles,
    profilesError,
    isLoadingProfiles,
    isFetching,
    fetchResult,
    fetchError,
    progressState,
    loadProfiles,
    reloadProfiles,
    testConnection,
    fetchTransactions,
  }
}
