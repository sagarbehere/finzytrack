import type { Money } from '@/utils/money'

export interface CsvParsedTransaction {
  date: string
  payee: string
  narration: string
  amount: Money
  memo: string
}

export interface CsvFileDetails {
  filename: string
  ruleName: string
  transactionCount: number
  startDate: string | null
  endDate: string | null
  rawTransactions: CsvParsedTransaction[]
}
