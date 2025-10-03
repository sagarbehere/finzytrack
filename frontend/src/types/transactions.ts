// To be defined in frontend/src/types/transactions.ts

export interface PostingViewModel {
  account: string;
  amount: number | null;
  currency: string;
}

/**
 * Core transaction view model representing Beancount transaction data.
 * This type only contains the essential transaction fields and metadata.
 * Context-specific metadata (import, ledger, etc.) is stored separately
 * to keep this type clean and reusable across different UI contexts.
 */
export interface TransactionViewModel {
  // Client-side unique ID for Vue's key binding
  id: string;

  // Core Beancount Fields, editable by the user in the table
  date: string;
  flag: string;
  payee: string;
  memo?: string;
  narration: string;
  tags: string[];
  links: string[];
  postings: PostingViewModel[];

  // Internal metadata for state management and API communication
  meta: {
    ofx_id?: string;
    transaction_id?: string; // For existing ledger transactions
    isNew: boolean;         // True for new transactions being imported
    isModified: boolean;      // True if the user has changed any field
    source_account?: string;  // The source account for imported transactions
    source_currency?: string; // The source currency for imported transactions
  };
}

/**
 * Context metadata for transactions being imported from external sources (OFX, CSV, etc.).
 * Stored separately from core transaction data to keep TransactionViewModel clean.
 * Maps transaction.id -> ImportContext
 */
export interface ImportContext {
  /** Whether this transaction is a potential duplicate of an existing ledger transaction */
  is_duplicate: boolean;
  /** Confidence score from autocategorization (0.0 to 1.0), set after autocategorization */
  confidence?: number;
  /** Opaque data from the backend's duplicate detection (e.g., matching transaction IDs) */
  duplicate_info?: object;
}

/**
 * Context metadata for transactions from the ledger.
 * This will be used when displaying existing ledger transactions.
 * Maps transaction.id -> LedgerContext
 */
export interface LedgerContext {
  /** Current running balance at this transaction */
  balance?: number;
  /** Whether this transaction has been reconciled with bank statements */
  reconciled?: boolean;
  /** ISO timestamp of last modification */
  last_modified?: string;
  /** Whether this transaction has pending edits not yet saved */
  pending_edits?: boolean;
}

/**
 * Bundle of transactions and their import context.
 * Returned by conversion functions that process imported data.
 */
export interface TransactionImportBundle {
  transactions: TransactionViewModel[];
  importContext: Map<string, ImportContext>;
}

/**
 * Bundle of transactions and their ledger context.
 * Returned by functions that fetch ledger data.
 */
export interface TransactionLedgerBundle {
  transactions: TransactionViewModel[];
  ledgerContext: Map<string, LedgerContext>;
}