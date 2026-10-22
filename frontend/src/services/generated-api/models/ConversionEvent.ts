/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A tax-neutral conversion of one holding into another — a scheme merger or a
 * face-value change. The ``from_units`` (FIFO lots) become ``to_units`` of
 * ``to_security``: every lot keeps its acquisition date and total cost, its units
 * scaled by the ratio; no gain is realized (dev-docs/cas-import.md §6.4).
 */
export type ConversionEvent = {
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
     * Assets account the units leave
     */
    holding_account: string;
    /**
     * Commodity converted from (is_currency=0)
     */
    security: string;
    /**
     * Units converted (> 0), taken oldest first
     */
    from_units: (number | string);
    /**
     * Account the new units go to (default: holding_account)
     */
    to_account?: (string | null);
    /**
     * Commodity converted into
     */
    to_security: string;
    /**
     * Units received (> 0)
     */
    to_units: (number | string);
    /**
     * Income account for a 0.00 leg that lets the carried costs' rounding balance (no gain is booked)
     */
    gains_account: string;
};

