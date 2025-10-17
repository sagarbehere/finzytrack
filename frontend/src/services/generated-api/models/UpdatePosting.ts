/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A posting in a transaction update request.
 */
export type UpdatePosting = {
    /**
     * Beancount account name
     */
    account: string;
    /**
     * Posting amount
     */
    amount?: (number | string | null);
    /**
     * Currency code
     */
    currency: string;
    /**
     * Cost per unit
     */
    cost_amount?: (number | string | null);
    /**
     * Cost currency
     */
    cost_currency?: (string | null);
    /**
     * Cost date (ISO format)
     */
    cost_date?: (string | null);
    /**
     * Price amount
     */
    price_amount?: (number | string | null);
    /**
     * Price currency
     */
    price_currency?: (string | null);
    /**
     * Price type: '@' or '@@'
     */
    price_type?: (string | null);
    /**
     * Posting metadata
     */
    posting_meta?: (Record<string, string> | null);
};

