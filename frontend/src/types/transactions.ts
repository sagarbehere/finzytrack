// To be defined in frontend/src/types/transactions.ts
import type { Money } from '@/utils/money'

export interface PostingViewModel {
  account: string;
  amount: Money | null;
  currency: string;

  // Cost fields
  cost?: {
    amount?: Money;
    currency?: string;
    date?: string; // ISO date YYYY-MM-DD
  };

  // Price fields
  price?: {
    amount?: Money;
    currency?: string;
    type?: '@' | '@@'; // @ = per-unit, @@ = total
  };

  // Posting-level metadata (arbitrary key-value pairs)
  meta?: Record<string, string>;
}

/**
 * Core transaction view model representing Beancount transaction data.
 * This type only contains the essential transaction fields and metadata.
 * Context-specific metadata (import, ledger, etc.) is stored separately
 * to keep this type clean and reusable across different UI contexts.
 */
export interface TransactionViewModel {
  // Unique ID (UUIDv7 from backend for committed transactions, temporary for import preview)
  // Backend generates stable UUIDv7 when committing; frontend can use temporary IDs for preview
  id: string;

  // Core Beancount Fields, editable by the user in the table
  date: string;
  flag: string;
  payee: string;
  memo?: string; // Optional memo/reference, stored as memo: metadata in the ledger
  narration: string;
  tags: string[];
  links: string[];
  postings: PostingViewModel[];

  // Beancount metadata (arbitrary key-value pairs)
  // Backend-managed keys: id (UUIDv7), content_hash (SHA256), source_account
  // Import keys: external_id, external_id_type, source_rule, etc.
  meta: Record<string, string>;

  // Frontend-only state (NOT sent to backend/ledger)
  internal: {
    isNew: boolean;         // True for new transactions being imported
    isModified: boolean;    // True if the user has changed any field
    source_currency?: string; // Helper for frontend logic
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
  balance?: Money;
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