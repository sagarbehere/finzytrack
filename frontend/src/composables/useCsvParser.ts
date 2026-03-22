import { ref } from 'vue'
import Papa from 'papaparse'
import type { CsvRule } from '@/services/generated-api'
import type { CsvParsedTransaction, CsvFileDetails } from '@/types/csv'

export function useCsvParser() {
  const selectedFile = ref<File | null>(null)
  const fileDetails = ref<CsvFileDetails | null>(null)
  const parseError = ref<string | null>(null)
  const isParsing = ref<boolean>(false)

  const processFile = async (file: File, rule: CsvRule) => {
    if (!file.name.toLowerCase().endsWith('.csv')) {
      parseError.value = 'Invalid file type. Please select a CSV file.'
      return
    }

    clearFile()
    selectedFile.value = file
    isParsing.value = true

    try {
      const content = await readFileAsText(file, rule.encoding || 'utf-8')
      const transactions = parseCsvContent(content, rule)

      if (transactions.length === 0) {
        parseError.value = 'No transactions found in the CSV file with the selected rule.'
        return
      }

      const dates = transactions
        .map(t => t.date)
        .filter(d => !!d)
        .sort()

      fileDetails.value = {
        filename: file.name,
        ruleName: rule.name,
        transactionCount: transactions.length,
        startDate: dates.length > 0 ? dates[0] : null,
        endDate: dates.length > 0 ? dates[dates.length - 1] : null,
        rawTransactions: transactions,
      }
    } catch (err: any) {
      console.error('Error processing CSV file:', err)
      parseError.value = err.message || 'Failed to parse CSV file.'
    } finally {
      isParsing.value = false
    }
  }

  const clearFile = () => {
    selectedFile.value = null
    fileDetails.value = null
    parseError.value = null
    isParsing.value = false
  }

  return {
    selectedFile,
    fileDetails,
    parseError,
    isParsing,
    processFile,
    clearFile,
  }
}

function readFileAsText(file: File, encoding: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target?.result as string)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsText(file, encoding)
  })
}

export function parseCsvContent(content: string, rule: CsvRule): CsvParsedTransaction[] {
  const separator = rule.separator || ','
  const skipStart = rule.skip_lines_start ?? 0
  const skipEnd = rule.skip_lines_end ?? 0
  const dateFormat = rule.date_format || '%Y-%m-%d'
  const decimalSep = rule.decimal_separator || '.'
  const negateAmounts = rule.negate_amounts ?? false
  const columns = rule.columns

  const parsed = Papa.parse(content, {
    delimiter: separator,
    skipEmptyLines: true,
  })

  let rows = parsed.data as string[][]

  // Apply skip_lines_start and skip_lines_end
  if (skipStart > 0) {
    rows = rows.slice(skipStart)
  }
  if (skipEnd > 0) {
    rows = rows.slice(0, -skipEnd)
  }

  const transactions: CsvParsedTransaction[] = []

  const hasSplitAmounts = columns.amount_debit != null && columns.amount_credit != null

  for (const row of rows) {
    // Skip rows that don't have enough columns for date
    if (row.length <= columns.date) continue

    const dateStr = (row[columns.date] || '').trim()
    if (!dateStr) continue

    const date = parseDateWithFormat(dateStr, dateFormat)
    if (!date) continue

    let amount: number

    if (hasSplitAmounts) {
      // Separate debit/credit columns
      const drStr = (row[columns.amount_debit!] || '').trim()
      const crStr = (row[columns.amount_credit!] || '').trim()
      if (!drStr && !crStr) continue

      if (crStr) {
        const parsed = parseAmountStr(crStr, decimalSep)
        if (parsed === null) continue
        amount = Math.abs(parsed) // Credit = money in = positive
      } else {
        const parsed = parseAmountStr(drStr, decimalSep)
        if (parsed === null) continue
        amount = -Math.abs(parsed) // Debit = money out = negative
      }
    } else {
      // Single amount column
      if (columns.amount == null || row.length <= columns.amount) continue
      const amountStr = (row[columns.amount] || '').trim()
      if (!amountStr) continue

      const parsed = parseAmountStr(amountStr, decimalSep)
      if (parsed === null) continue
      amount = parsed
    }

    if (negateAmounts) {
      amount = -amount
    }

    const payee = columns.payee != null && row[columns.payee] != null
      ? row[columns.payee].trim()
      : ''
    const narration = columns.narration != null && row[columns.narration] != null
      ? row[columns.narration].trim()
      : ''
    const memo = columns.memo != null && row[columns.memo] != null
      ? row[columns.memo].trim()
      : ''

    transactions.push({ date, payee, narration, amount, memo })
  }

  return transactions
}

function parseAmountStr(amountStr: string, decimalSep: string): number | null {
  let normalized = amountStr
  // Detect accounting-style negative: (100.00) or $(1,234.56)
  const isParensNegative = /^\s*\(.*\)\s*$/.test(normalized)
  if (decimalSep !== '.') {
    normalized = normalized.replace(/\./g, '').replace(decimalSep, '.')
  }
  normalized = normalized.replace(/[^0-9.-]/g, '')
  const value = parseFloat(normalized)
  if (isNaN(value)) return null
  return isParensNegative ? -Math.abs(value) : value
}

function parseDateWithFormat(dateStr: string, format: string): string | null {
  // Extract separator characters from format string (non-alpha, non-% chars)
  const formatSeparators = format.replace(/%[a-zA-Z]/g, '').replace(/[a-zA-Z]/g, '')
  const sep = formatSeparators.length > 0 ? formatSeparators[0] : null

  // Split format into tokens and date into parts
  const formatTokens = format.split(/[^%a-zA-Z]+/).filter(Boolean)
  const dateParts = sep ? dateStr.split(sep) : [dateStr]

  if (formatTokens.length !== dateParts.length) return null

  let year = 0, month = 0, day = 0

  for (let i = 0; i < formatTokens.length; i++) {
    const token = formatTokens[i]
    const value = parseInt(dateParts[i], 10)
    if (isNaN(value)) return null

    switch (token) {
      case '%Y': year = value; break
      case '%y': year = value < 70 ? 2000 + value : 1900 + value; break
      case '%m': month = value; break
      case '%d': day = value; break
      default: return null
    }
  }

  if (year === 0 || month === 0 || day === 0) return null
  if (month < 1 || month > 12 || day < 1 || day > 31) return null

  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}
