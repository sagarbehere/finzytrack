/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Acquire a holding at cost.
 */
export type BuyEvent = {
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
     * Assets account receiving the units
     */
    holding_account: string;
    /**
     * Commodity code (is_currency=0)
     */
    security: string;
    /**
     * Units acquired (> 0)
     */
    units: (number | string);
    /**
     * Cost per unit in `currency`
     */
    unit_price: (number | string);
    /**
     * Assets/Liabilities account funding the buy
     */
    cash_account: string;
    /**
     * Optional fee amount
     */
    fee?: (number | string | null);
    /**
     * Expenses account for the fee
     */
    fee_account?: (string | null);
};

