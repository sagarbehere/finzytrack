/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Dividend paid to cash (not reinvested).
 */
export type CashDividendEvent = {
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
     * Assets account receiving the cash
     */
    cash_account: string;
    /**
     * Security the dividend is for (attribution)
     */
    security: string;
    /**
     * Dividend amount (> 0)
     */
    amount: (number | string);
    /**
     * Income account (income-type: dividend)
     */
    income_account: string;
};

