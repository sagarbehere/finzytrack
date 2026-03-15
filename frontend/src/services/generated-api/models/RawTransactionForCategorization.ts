/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Simplified transaction data needed for categorization.
 * Only includes fields required for ML classification and duplicate detection.
 */
export type RawTransactionForCategorization = {
    /**
     * Frontend-generated temporary ID for matching request to response
     */
    id: string;
    /**
     * Transaction date
     */
    date: string;
    /**
     * Transaction payee
     */
    payee: string;
    /**
     * OFX memo field
     */
    memo?: (string | null);
    /**
     * User notes (usually empty at this stage)
     */
    narration?: string;
    /**
     * Transaction amount from source account posting
     */
    amount: (number | string);
    /**
     * External transaction ID for duplicate detection (e.g. OFX FITID, UPI reference)
     */
    external_id?: (string | null);
    /**
     * Type of external_id: OFX, UPI, NEFT, IMPS, EMAIL_MESSAGE_ID, CSV
     */
    external_id_type?: (string | null);
};

