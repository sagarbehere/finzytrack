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
     * Transaction date
     */
    date: string;
    /**
     * Combined payee from frontend (includes memo)
     */
    payee: string;
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

