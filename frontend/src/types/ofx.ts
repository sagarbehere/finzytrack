// OFX transaction structure from ofx-js parser
export interface OFXTransaction {
  NAME?: string
  PAYEE?: string
  MEMO?: string
  CHECKNUM?: string
  TRNAMT?: string
  DTPOSTED?: string
  TRNTYPE?: string
  FITID?: string
}

import type { Money } from '@/utils/money'

// OFX file details extracted from parsed OFX file
export interface OfxFileDetails {
  institution: string
  institutionFid: string | null
  accountType: string
  accountId: string
  currency: string
  transactionCount: number
  startDate: string | null
  endDate: string | null
  balance: Money
  rawTransactions: OFXTransaction[]
}