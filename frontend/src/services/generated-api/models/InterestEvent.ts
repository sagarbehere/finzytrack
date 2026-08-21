/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Periodic interest — credited to the instrument (compounding) or a separate account (paid out).
 *
 * The geometry is the same either way; which account receives the interest is the
 * caller's choice, and that choice is what makes it compounding vs paid out
 * (dev-docs/metadata-conventions.md "Interest attribution").
 */
export type InterestEvent = {
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
     * Account receiving the interest — the instrument itself (compounding) or a cash account (paid out)
     */
    deposit_account: string;
    /**
     * Interest amount (> 0)
     */
    interest: (number | string);
    /**
     * Income account (income-type: interest)
     */
    income_account: string;
};

