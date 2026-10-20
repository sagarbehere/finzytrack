/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Result of a price fetch → sidecar write.
 */
export type PriceUpdateData = {
    /**
     * Number of new (date, base, quote) price points added.
     */
    added: number;
    /**
     * Existing price points whose number changed (the newest stored day is re-fetched).
     */
    updated?: number;
    /**
     * Total price points in the sidecar after the update.
     */
    total: number;
    /**
     * Date of the most recent price, YYYY-MM-DD, or null if none.
     */
    as_of?: (string | null);
    /**
     * Tickers that were fetched.
     */
    symbols?: Array<string>;
    /**
     * Holdings not fetched (e.g. money-market funds).
     */
    skipped?: Array<string>;
    /**
     * Holdings the provider returned nothing for, as 'CODE (SYMBOL): reason'.
     */
    failed?: Array<string>;
};

