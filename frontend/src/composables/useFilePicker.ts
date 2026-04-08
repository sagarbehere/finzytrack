import { ref, computed, watch, onMounted, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAccounts } from '@/composables/useAccounts'
import { useToast } from '@/composables/useNotifications'
import type { InvalidRuleSummary } from '@/services/generated-api'
import type { CsvFileDetails } from '@/types/csv'

/**
 * Minimal shape shared by CsvRuleSummary and XlsRuleSummary.
 */
interface RuleSummary {
  filename: string
  name: string
  default_account: string
  default_currency?: string
}

/**
 * Minimal shape shared by CsvRule and XlsRule (the full rule object).
 */
interface RuleFull {
  name: string
  default_account: string
  default_currency?: string
}

/**
 * The shape returned by both useCsvParser() and useXlsParser().
 */
interface FileParser<TRule> {
  selectedFile: Ref<File | null>
  fileDetails: Ref<CsvFileDetails | null>
  parseError: Ref<string | null>
  isParsing: Ref<boolean>
  processFile: (file: File, rule: TRule) => Promise<void>
  clearFile: () => void
}

/**
 * API service callbacks for listing and fetching rules.
 */
interface RuleService<TRuleSummary, TRule> {
  listRules: () => Promise<{
    success: boolean
    data?: { rules?: TRuleSummary[]; invalid_rules?: InvalidRuleSummary[] } | null
  }>
  getRule: (filename: string) => Promise<{
    success: boolean
    data?: TRule | null
  }>
}

interface FilePickerOptions<TRuleSummary extends RuleSummary, TRule extends RuleFull> {
  /** The format-specific parser composable instance */
  parser: FileParser<TRule>
  /** API service callbacks for listing and fetching rules */
  service: RuleService<TRuleSummary, TRule>
  /** Display label, e.g. "CSV" or "XLS" */
  formatLabel: string
  /** Rule type for settings navigation, e.g. "csv" or "xls" */
  ruleType: string
}

export function useFilePicker<
  TRuleSummary extends RuleSummary,
  TRule extends RuleFull,
>(options: FilePickerOptions<TRuleSummary, TRule>) {
  const { parser, service, formatLabel, ruleType } = options
  const { selectedFile, fileDetails, parseError, isParsing, processFile, clearFile } = parser

  const router = useRouter()
  const toast = useToast()
  const { accountDetails, hasBeenFetched } = useAccounts()

  // Rule state
  const availableRules = ref<TRuleSummary[]>([]) as Ref<TRuleSummary[]>
  const invalidRules = ref<InvalidRuleSummary[]>([])
  const selectedRuleFilename = ref<string>('')
  const selectedRule = ref<TRule | null>(null) as Ref<TRule | null>
  const isLoadingRules = ref<boolean>(false)
  const isReloadingRules = ref<boolean>(false)
  const rulesLoadError = ref<string | null>(null)

  const ruleOptions = computed(() => [
    { value: '', label: 'Select a rule...' },
    ...availableRules.value.map(rule => ({
      value: rule.filename,
      label: `${rule.name} (${rule.default_account})`,
    })),
  ])

  // Account/currency state
  const selectedAccount = ref<string>('')
  const selectedCurrency = ref<string>('')

  const accountNotInLedger = computed(() => {
    if (!selectedAccount.value || !hasBeenFetched.value) return false
    const openAccounts = accountDetails.value.filter(a => !a.close_date).map(a => a.name)
    return !openAccounts.includes(selectedAccount.value)
  })

  // UI state
  const fileInput = ref<HTMLInputElement | null>(null)
  const isDragOver = ref<boolean>(false)

  const previewTransactions = computed(() => {
    if (!fileDetails.value) return []
    return fileDetails.value.rawTransactions.slice(0, 5)
  })

  // Load rules on mount
  onMounted(async () => {
    isLoadingRules.value = true
    try {
      const response = await service.listRules()
      if (response.success && response.data) {
        availableRules.value = response.data.rules || []
        invalidRules.value = response.data.invalid_rules || []
      }
    } catch (err: any) {
      rulesLoadError.value = `Failed to load ${formatLabel} rules.`
      console.error(`Failed to load ${formatLabel} rules:`, err)
    } finally {
      isLoadingRules.value = false
    }
  })

  watch(selectedRuleFilename, () => handleRuleChange())

  const handleRuleChange = async () => {
    if (!selectedRuleFilename.value) {
      selectedRule.value = null
      return
    }

    try {
      const response = await service.getRule(selectedRuleFilename.value)
      if (response.success && response.data) {
        selectedRule.value = response.data
        selectedAccount.value = response.data.default_account
        selectedCurrency.value = response.data.default_currency || 'USD'

        // Re-parse if file already selected
        if (selectedFile.value) {
          processFile(selectedFile.value, response.data)
        }
      }
    } catch (err: any) {
      console.error(`Failed to load ${formatLabel} rule:`, err)
      parseError.value = 'Failed to load the selected rule.'
    }
  }

  const handleReloadRules = async () => {
    isReloadingRules.value = true
    try {
      const response = await service.listRules()
      if (response.success && response.data) {
        availableRules.value = response.data.rules || []
        invalidRules.value = response.data.invalid_rules || []

        // Re-fetch the selected rule if it still exists, then re-parse if file is loaded
        if (selectedRuleFilename.value) {
          const stillExists = availableRules.value.some(r => r.filename === selectedRuleFilename.value)
          if (stillExists) {
            const ruleResponse = await service.getRule(selectedRuleFilename.value)
            if (ruleResponse.success && ruleResponse.data) {
              selectedRule.value = ruleResponse.data
              selectedAccount.value = ruleResponse.data.default_account
              selectedCurrency.value = ruleResponse.data.default_currency || 'USD'
              if (selectedFile.value) {
                processFile(selectedFile.value, ruleResponse.data)
              }
            }
          } else {
            selectedRuleFilename.value = ''
            selectedRule.value = null
          }
        }

        toast.success('Rules reloaded', `${availableRules.value.length} rule${availableRules.value.length === 1 ? '' : 's'} loaded.`)
      }
    } catch {
      toast.error('Reload failed', `Could not reload ${formatLabel} rules.`)
    } finally {
      isReloadingRules.value = false
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    isDragOver.value = false
    const files = e.dataTransfer?.files
    if (files && files.length > 0 && selectedRule.value) {
      processFile(files[0], selectedRule.value)
    }
  }

  const handleDragEnter = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true }
  const handleDragOver = (e: DragEvent) => { e.preventDefault(); isDragOver.value = true }
  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault()
    if (e.currentTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget as Node)) {
      isDragOver.value = false
    }
  }

  const openFilePicker = () => fileInput.value?.click()

  const handleFileSelect = (e: Event) => {
    const target = e.target as HTMLInputElement
    const files = target.files
    if (files && files.length > 0 && selectedRule.value) {
      processFile(files[0], selectedRule.value)
    }
  }

  const handleClearFile = (emitCleared: () => void) => {
    clearFile()
    if (fileInput.value) fileInput.value.value = ''
    emitCleared()
  }

  const proceedWithImport = (emitProceed: (payload: {
    file: File
    details: CsvFileDetails
    account: string
    currency: string
  }) => void) => {
    if (selectedFile.value && fileDetails.value && selectedAccount.value && selectedCurrency.value) {
      emitProceed({
        file: selectedFile.value,
        details: fileDetails.value,
        account: selectedAccount.value,
        currency: selectedCurrency.value,
      })
    }
  }

  const navigateToRuleSettings = () => {
    router.push({ path: '/settings', query: { tab: 'rules', type: ruleType } })
  }

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  const formatDateRange = (startDate: string | null, endDate: string | null): string => {
    if (!startDate || !endDate) return 'Unknown'
    const formatDate = (date: string) =>
      new Date(date).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    return `${formatDate(startDate)} - ${formatDate(endDate)}`
  }

  return {
    // Parser pass-through
    selectedFile,
    fileDetails,
    parseError,
    isParsing,

    // Rule state
    availableRules,
    invalidRules,
    selectedRuleFilename,
    selectedRule,
    isLoadingRules,
    isReloadingRules,
    rulesLoadError,
    ruleOptions,

    // Account/currency
    selectedAccount,
    selectedCurrency,
    accountNotInLedger,

    // UI state
    fileInput,
    isDragOver,
    previewTransactions,

    // Actions
    handleReloadRules,
    handleDrop,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    openFilePicker,
    handleFileSelect,
    handleClearFile,
    proceedWithImport,
    navigateToRuleSettings,

    // Formatters
    formatFileSize,
    formatDateRange,
  }
}
