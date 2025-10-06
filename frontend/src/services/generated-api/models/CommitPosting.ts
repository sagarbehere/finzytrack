/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single posting in a transaction to be committed.
 */
export type CommitPosting = {
    /**
     * Beancount account name
     */
    account: string;
    /**
     * Posting amount
     */
    amount: (number | string);
    /**
     * Currency code
     */
    currency: string;
    /**
     * Cost amount per unit
     */
    cost_amount?: (number | string | null);
    /**
     * Cost currency
     */
    cost_currency?: (string | null);
    /**
     * Cost date (optional)
     */
    cost_date?: (string | null);
    /**
     * Price amount (per-unit or total)
     */
    price_amount?: (number | string | null);
    /**
     * Price currency
     */
    price_currency?: (string | null);
    /**
     * Price type: '@' (per-unit) or '@@' (total)
     */
    price_type?: (string | null);
    /**
     * Arbitrary posting metadata
     */
    posting_meta?: (Record<string, string> | null);
};

