/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * CD / term-deposit matures: principal (+ any final interest) paid to a destination.
 */
export type CdMaturityEvent = {
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
     * The CD/term-deposit account (Assets, asset-class: cd)
     */
    cd_account: string;
    /**
     * Principal returned (> 0)
     */
    principal: (number | string);
    /**
     * Final interest paid at maturity (>= 0)
     */
    interest?: (number | string);
    /**
     * Account receiving principal + interest (checking/savings)
     */
    destination_account: string;
    /**
     * Income account for the interest (income-type: interest); required if interest > 0
     */
    interest_income_account?: (string | null);
};

