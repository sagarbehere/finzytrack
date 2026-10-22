// Investments → Import statement (dev-docs/cas-import.md §5, §9a, §13).
//
// Function-scoped (view-local) state: the parsed statement's token, the user's
// account choices, their row corrections, and the latest plan. The plan is recomputed
// on the backend whenever choices or corrections change (debounced; stale replies are
// dropped), so units checks, lots and gains always reflect what Register would write.

import { reactive, ref, watch } from 'vue'
import {
  ApiError,
  InvestmentsService,
  type RowCorrection,
  type StatementCommitData,
  type StatementImportRecord,
  type StatementMapping,
  type StatementParseData,
  type StatementPlanData,
} from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { clearCorrection, importFacts, setCorrection, type ImportFacts } from '@/utils/statementImport'

// Expected, user-fixable outcomes: shown next to the form, not as notifications.
const INLINE = {
  CAS_WRONG_PASSWORD: 'Inline',
  CAS_UNRECOGNIZED: 'Inline',
  CAS_PARSE_FAILED: 'Inline',
  CAS_INTEGRITY: 'Inline',
  STATEMENT_EXPIRED: 'Inline',
  PREVIEW_STALE: 'Inline',
  STATEMENT_NOT_READY: 'Inline',
} as const

const codeOf = (err: unknown) => (err instanceof ApiError ? err.body?.error?.code : undefined)

// Account choices are '' until picked (the backend reads '' as "not chosen").
type AccountRole = 'cash_fallback_account' | 'cash_matching_account' | 'cash_before_cutoff_account'
  | 'dividend_account' | 'taxes_account' | 'gains_account'
type UiMapping = Omit<StatementMapping, AccountRole> & Record<AccountRole, string>
  & Required<Pick<StatementMapping, 'holding_accounts' | 'commodity_codes' | 'hand_entries' | 'excluded_funds'>>

function emptyMapping(): UiMapping {
  return {
    holding_accounts: {},
    cash_fallback_account: '',
    cash_matching_account: '',
    match_window_days: 7,
    cutoff_date: null,
    cash_before_cutoff_account: '',
    history_start: null,
    dividend_account: '',
    taxes_account: '',
    gains_account: '',
    commodity_codes: {},
    hand_entries: {},
    target_file: null,
    tag_dividend_account: false,
    excluded_funds: [],
  }
}

export function useStatementImport(previewDelayMs = 400) {
  const parsed = ref<StatementParseData | null>(null)
  const mapping = reactive(emptyMapping())
  const corrections = ref<RowCorrection[]>([])
  const plan = ref<StatementPlanData | null>(null)
  const result = ref<StatementCommitData | null>(null)
  const resultFacts = ref<ImportFacts | null>(null)
  const imports = ref<StatementImportRecord[]>([])

  const reading = ref(false)
  const previewing = ref(false)
  const committing = ref(false)
  const readError = ref<string | null>(null)
  const previewError = ref<string | null>(null)
  const commitError = ref<string | null>(null)

  let previewSeq = 0
  let timer: ReturnType<typeof setTimeout> | null = null

  function applyPreselection(p: StatementParseData) {
    Object.assign(mapping, emptyMapping())
    const pre = p.preselected
    for (const d of p.distributors) {
      const acct = pre.holding_accounts?.[d.key]
      if (acct) mapping.holding_accounts[d.key] = acct
    }
    mapping.cash_fallback_account = pre.cash_fallback_account ?? ''
    mapping.cash_matching_account = pre.cash_matching_account ?? ''
    mapping.dividend_account = pre.dividend_account ?? ''
    mapping.taxes_account = pre.taxes_account ?? ''
    mapping.gains_account = pre.gains_account ?? ''
  }

  async function read(file: File, password: string) {
    reading.value = true
    readError.value = null
    result.value = null
    try {
      const res = await InvestmentsService.parseStatement({ file, password })
      parsed.value = res.data ?? null
      corrections.value = []
      plan.value = null
      if (parsed.value) {
        applyPreselection(parsed.value)
        await preview()
        if (timer) { clearTimeout(timer); timer = null } // the preselection's own change is covered
      }
    } catch (err) {
      readError.value = errorHandler.messageOf(err)
      errorHandler.display(err, INLINE)
    } finally {
      reading.value = false
    }
  }

  async function preview() {
    if (!parsed.value) return
    const seq = ++previewSeq
    previewing.value = true
    try {
      const res = await InvestmentsService.previewStatement({
        token: parsed.value.token,
        mapping: { ...mapping },
        corrections: corrections.value,
      })
      if (seq !== previewSeq) return // a newer preview is on its way
      plan.value = res.data ?? null
      previewError.value = null
    } catch (err) {
      if (seq !== previewSeq) return
      previewError.value = errorHandler.messageOf(err)
      errorHandler.display(err, INLINE)
      if (codeOf(err) === 'STATEMENT_EXPIRED') reset()
    } finally {
      if (seq === previewSeq) previewing.value = false
    }
  }

  function schedulePreview() {
    if (!parsed.value) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { timer = null; void preview() }, previewDelayMs)
  }

  watch(() => JSON.stringify(mapping), schedulePreview)
  watch(corrections, schedulePreview, { deep: true })

  function correct(change: RowCorrection) {
    corrections.value = setCorrection(corrections.value, change)
  }

  function undoCorrection(rowId: string) {
    corrections.value = clearCorrection(corrections.value, rowId)
  }

  async function commit(): Promise<boolean> {
    if (!parsed.value || !plan.value) return false
    committing.value = true
    commitError.value = null
    try {
      const res = await InvestmentsService.commitStatement({
        token: parsed.value.token,
        mapping: { ...mapping },
        corrections: corrections.value,
        expected_preview_hash: plan.value.preview_hash,
      })
      result.value = res.data ?? null
      resultFacts.value = importFacts(plan.value)
      parsed.value = null
      plan.value = null
      corrections.value = []
      await loadImports()
      return true
    } catch (err) {
      commitError.value = errorHandler.messageOf(err)
      errorHandler.display(err, INLINE)
      if (codeOf(err) === 'PREVIEW_STALE') await preview()
      if (codeOf(err) === 'STATEMENT_EXPIRED') reset()
      return false
    } finally {
      committing.value = false
    }
  }

  async function loadImports() {
    try {
      const res = await InvestmentsService.listStatementImports()
      imports.value = res.data?.imports ?? []
    } catch (err) {
      errorHandler.display(err)
    }
  }

  async function undo(importId: string): Promise<boolean> {
    try {
      await InvestmentsService.undoStatementImport(importId)
      await loadImports()
      if (parsed.value) await preview()
      return true
    } catch (err) {
      errorHandler.display(err)
      return false
    }
  }

  function reset() {
    if (timer) clearTimeout(timer)
    previewSeq++
    parsed.value = null
    plan.value = null
    corrections.value = []
    previewing.value = false
    Object.assign(mapping, emptyMapping())
  }

  return {
    parsed, mapping, corrections, plan, result, resultFacts, imports,
    reading, previewing, committing, readError, previewError, commitError,
    read, preview, correct, undoCorrection, commit, loadImports, undo, reset,
  }
}
