/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Per-currency transaction data for an account.
 */
export type AccountCurrencyData = {
    /**
     * Currency code (e.g., 'USD')
     */
    currency: string;
    /**
     * Number of transactions in this currency
     */
    transaction_count: number;
    /**
     * Date of last transaction (YYYY-MM-DD)
     */
    last_transaction_date?: (string | null);
    /**
     * Current balance in this currency
     */
    balance: string;
};

