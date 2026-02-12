// Account management types for the Accounts view

export type AccountType = 'Assets' | 'Liabilities' | 'Equity' | 'Income' | 'Expenses';
export type AccountStatus = 'open' | 'closed';

export interface AggregatedBalance {
  currency: string;
  balance: number;
}

export interface AccountTreeNode {
  id: string;                           // Full path: "Assets:Bank:Checking"
  name: string;                         // Display name: "Checking"
  fullPath: string;
  depth: number;
  isVirtual: boolean;                   // No open directive exists for this node
  children: AccountTreeNode[];
  type: AccountType;
  status: AccountStatus;
  openDate: string | null;
  closeDate: string | null;
  aggregatedBalances: AggregatedBalance[];
  notes: string | null;                 // From metadata.description
  currencyBadges: string[];             // From accountDetails.currencies
}

export interface BalanceDirective {
  date: string
  currency: string
  expectedBalance: number
  hasPad: boolean
  padSourceAccount: string | null
  hasError: boolean
  errorMessage: string | null
}

export interface AccountFilters {
  search: string;
  type: AccountType | 'All';
  status: AccountStatus | 'All';
}

// Type color mapping for badges
export const typeColors: Record<AccountType, string> = {
  Assets: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  Liabilities: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  Equity: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
  Income: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  Expenses: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400',
};

// Status color mapping for badges
export const statusColors: Record<AccountStatus, string> = {
  open: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  closed: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-400',
};
