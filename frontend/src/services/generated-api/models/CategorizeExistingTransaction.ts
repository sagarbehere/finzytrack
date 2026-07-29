/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One existing transaction to categorize.
 */
export type CategorizeExistingTransaction = {
    /**
     * Stable transaction id (matches request to response)
     */
    id: string;
    /**
     * Transaction payee
     */
    payee: string;
    /**
     * Optional memo
     */
    memo?: (string | null);
    /**
     * Transaction narration
     */
    narration?: string;
    /**
     * The known (non-unknown) posting's account — AI prompt context
     */
    source_account: string;
};

