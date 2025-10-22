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
     * OFX transaction ID for duplicate detection
     */
    ofx_id?: (string | null);
};

