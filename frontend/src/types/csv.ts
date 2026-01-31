export interface CsvParsedTransaction {
  date: string
  payee: string
  narration: string
  amount: number
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
