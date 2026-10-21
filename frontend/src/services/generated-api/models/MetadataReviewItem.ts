/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A commodity or account that recognized metadata can describe.
 */
export type MetadataReviewItem = {
    /**
     * 'commodity', 'account' or 'income-account'
     */
    scope: string;
    /**
     * Commodity code or account name
     */
    item: string;
    /**
     * Current metadata, values as text
     */
    metadata?: Record<string, string>;
    /**
     * Recognized keys that are relevant here and not set
     */
    unset?: Array<string>;
    /**
     * Commodities only: current currency-vs-holding role
     */
    is_currency?: (boolean | null);
    /**
     * Commodities only: the currency it was most often bought in at cost; null if never held at cost
     */
    cost_currency?: (string | null);
    /**
     * Accounts only: the account is closed
     */
    closed?: boolean;
};

