// To be defined in frontend/src/types/transactions.ts

export interface PostingViewModel {
  account: string;
  amount: number | null;
  currency: string;
}

export interface TransactionViewModel {
  // Client-side unique ID for Vue's key binding
  id: string;

  // Core Beancount Fields, editable by the user in the table
  date: string;
  flag: string;
  payee: string;
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

  // Properties specific to the import process, not displayed directly
  // but used for context (e.g., showing a "Duplicate" badge)
  import_details?: {
    confidence?: number;
    is_duplicate: boolean;
    duplicate_info?: object; // Opaque data from the backend's DuplicateInfo schema
  };
}