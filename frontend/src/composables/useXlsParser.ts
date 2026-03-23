import { ref } from 'vue'
import * as XLSX from 'xlsx'
import type { XlsRule } from '@/services/generated-api'
import type { CsvParsedTransaction, CsvFileDetails } from '@/types/csv'

export function useXlsParser() {
  const selectedFile = ref<File | null>(null)
  const fileDetails = ref<CsvFileDetails | null>(null)
  const parseError = ref<string | null>(null)
  const isParsing = ref<boolean>(false)

  const processFile = async (file: File, rule: XlsRule) => {
    const lower = file.name.toLowerCase()
    if (!lower.endsWith('.xls') && !lower.endsWith('.xlsx')) {
      parseError.value = 'Invalid file type. Please select an XLS or XLSX file.'
      return
    }

    clearFile()
    selectedFile.value = file
    isParsing.value = true

    try {
      const buffer = await readFileAsArrayBuffer(file)
      const transactions = parseXlsContent(buffer, rule)

      if (transactions.length === 0) {
        parseError.value = 'No transactions found in the XLS file with the selected rule.'
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
      console.error('Error processing XLS file:', err)
      parseError.value = err.message || 'Failed to parse XLS file.'
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

function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target?.result as ArrayBuffer)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsArrayBuffer(file)
  })
}

export function extractXlsSheets(buffer: ArrayBuffer): { name: string; rows: string[][] }[] {
  const workbook = XLSX.read(buffer, { type: 'array', cellDates: false })
  return workbook.SheetNames.map(sheetName => {
    const sheet = workbook.Sheets[sheetName]
    const rows = XLSX.utils.sheet_to_json<string[]>(sheet, {
      header: 1,
      raw: false,
      defval: '',
    }) as string[][]
    return { name: sheetName, rows }
  })
}

export function extractXlsText(buffer: ArrayBuffer, rule: XlsRule): string {
  const workbook = XLSX.read(buffer, { type: 'array', cellDates: false })
  let sheetName: string
  if (rule.sheet_name) {
    sheetName = rule.sheet_name
  } else {
    const idx = rule.sheet_index ?? 0
    sheetName = workbook.SheetNames[idx]
  }
  if (!sheetName || !workbook.Sheets[sheetName]) return ''
  const sheet = workbook.Sheets[sheetName]
  return XLSX.utils.sheet_to_csv(sheet, { FS: '\t' })
}

export function parseXlsContent(buffer: ArrayBuffer, rule: XlsRule): CsvParsedTransaction[] {
  const workbook = XLSX.read(buffer, { type: 'array', cellDates: false })

  // Select sheet by name or index
  let sheetName: string
  if (rule.sheet_name) {
    sheetName = rule.sheet_name
  } else {
    const idx = rule.sheet_index ?? 0
    sheetName = workbook.SheetNames[idx]
  }

  if (!sheetName || !workbook.Sheets[sheetName]) {
    throw new Error(`Sheet not found in XLS file`)
  }

  const sheet = workbook.Sheets[sheetName]

  // Convert sheet to 2D array of raw values (strings, numbers, dates)
  // raw: true returns unformatted values; defval: '' fills empty cells with ''
  const rows: any[][] = XLSX.utils.sheet_to_json(sheet, {
    header: 1,
    raw: false,   // format values as strings (handles dates as formatted strings)
    defval: '',
  })

  const skipStart = rule.skip_lines_start ?? 0
  const skipEnd = rule.skip_lines_end ?? 0
  const dateFormat = rule.date_format || '%Y-%m-%d'
  const decimalSep = rule.decimal_separator || '.'
  const negateAmounts = rule.negate_amounts ?? false
  const columns = rule.columns

  let dataRows = rows as string[][]
  if (skipStart > 0) dataRows = dataRows.slice(skipStart)
  if (skipEnd > 0) dataRows = dataRows.slice(0, -skipEnd)

  // Rule files use 1-based column indices (column 1 = leftmost); convert to 0-based for array access
  const col = {
    date:          columns.date - 1,
    amount:        columns.amount        != null ? columns.amount        - 1 : null,
    amount_debit:  columns.amount_debit  != null ? columns.amount_debit  - 1 : null,
    amount_credit: columns.amount_credit != null ? columns.amount_credit - 1 : null,
    payee:         columns.payee         != null ? columns.payee         - 1 : null,
    narration:     columns.narration     != null ? columns.narration     - 1 : null,
    memo:          columns.memo          != null ? columns.memo          - 1 : null,
  }

  const transactions: CsvParsedTransaction[] = []
  const hasSplitAmounts = col.amount_debit != null && col.amount_credit != null

  for (const row of dataRows) {
    if (row.length <= col.date) continue

    const dateStr = String(row[col.date] ?? '').trim()
    if (!dateStr) continue

    const date = parseDateWithFormat(dateStr, dateFormat)
    if (!date) continue

    let amount: number

    if (hasSplitAmounts) {
      const drStr = String(row[col.amount_debit!] ?? '').trim()
      const crStr = String(row[col.amount_credit!] ?? '').trim()
      if (!drStr && !crStr) continue

      if (crStr && parseAmountStr(crStr, decimalSep) !== null && Math.abs(parseAmountStr(crStr, decimalSep)!) > 0) {
        const parsed = parseAmountStr(crStr, decimalSep)!
        amount = Math.abs(parsed) // Credit = money in = positive
      } else if (drStr) {
        const parsed = parseAmountStr(drStr, decimalSep)
        if (parsed === null) continue
        if (Math.abs(parsed) === 0) continue // both zero — skip
        amount = -Math.abs(parsed) // Debit = money out = negative
      } else {
        continue
      }
    } else {
      if (col.amount == null || row.length <= col.amount) continue
      const amountStr = String(row[col.amount] ?? '').trim()
      if (!amountStr) continue
      const parsed = parseAmountStr(amountStr, decimalSep)
      if (parsed === null) continue
      amount = parsed
    }

    if (negateAmounts) amount = -amount

    const payee = col.payee != null && row[col.payee] != null
      ? String(row[col.payee]).trim()
      : ''
    const narration = col.narration != null && row[col.narration] != null
      ? String(row[col.narration]).trim()
      : ''
    const memo = col.memo != null && row[col.memo] != null
      ? String(row[col.memo]).trim()
      : ''

    transactions.push({ date, payee, narration, amount, memo })
  }

  return transactions
}

function parseAmountStr(amountStr: string, decimalSep: string): number | null {
  let normalized = amountStr
  const isParensNegative = /^\s*\(.*\)\s*$/.test(normalized)
  if (decimalSep !== '.') {
    normalized = normalized.replace(/\./g, '').replace(decimalSep, '.')
  }
  normalized = normalized.replace(/[^0-9.-]/g, '')
  const value = parseFloat(normalized)
  if (isNaN(value)) return null
  return isParensNegative ? -Math.abs(value) : value
}

const MONTH_ABBREVS: Record<string, number> = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
}

function parseDateWithFormat(dateStr: string, format: string): string | null {
  const formatSeparators = format.replace(/%[a-zA-Z]/g, '').replace(/[a-zA-Z]/g, '')
  const sep = formatSeparators.length > 0 ? formatSeparators[0] : null

  const formatTokens = format.split(/[^%a-zA-Z]+/).filter(Boolean)
  const dateParts = sep ? dateStr.split(sep) : [dateStr]

  if (formatTokens.length !== dateParts.length) return null

  let year = 0, month = 0, day = 0

  for (let i = 0; i < formatTokens.length; i++) {
    const token = formatTokens[i]
    const part = dateParts[i]

    if (token === '%b') {
      month = MONTH_ABBREVS[part.toLowerCase().slice(0, 3)] ?? 0
      if (month === 0) return null
      continue
    }

    const value = parseInt(part, 10)
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
