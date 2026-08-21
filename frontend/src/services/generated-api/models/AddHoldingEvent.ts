/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Bootstrap a currently-held position at an aggregate cost (positions-snapshot).
 */
export type AddHoldingEvent = {
    /**
     * Event date (YYYY-MM-DD)
     */
    date: string;
    /**
     * Cash / quote currency (e.g. USD)
     */
    currency: string;
    /**
     * Overrides the default narration
     */
    narration?: (string | null);
    /**
     * Optional payee
     */
    payee?: (string | null);
    /**
     * Optional memo (stored as memo: metadata)
     */
    memo?: (string | null);
    /**
     * Transaction tags
     */
    tags?: Array<string>;
    /**
     * Transaction links
     */
    links?: Array<string>;
    /**
     * Broker transaction id (dedupe)
     */
    external_id?: (string | null);
    /**
     * Type of external_id
     */
    external_id_type?: (string | null);
    /**
     * Free-text note (stored in meta)
     */
    note?: (string | null);
    type: string;
    /**
     * Assets account holding the position
     */
    holding_account: string;
    /**
     * Commodity code (is_currency=0)
     */
    security: string;
    /**
     * Units currently held (> 0)
     */
    units: (number | string);
    /**
     * Aggregate cost per unit in `currency`
     */
    avg_cost: (number | string);
    /**
     * Equity opening-balances account (caller-selected; never hardcoded)
     */
    opening_account: string;
};

