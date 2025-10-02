/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Information about a potential duplicate transaction in the ledger.
 */
export type DuplicateInfo = {
    /**
     * Ledger transaction ID of potential duplicate
     */
    transaction_id?: (string | null);
    /**
     * Date of the duplicate transaction
     */
    date: string;
    /**
     * Payee of the duplicate transaction
     */
    payee: string;
    /**
     * Narration of the duplicate transaction
     */
    narration: string;
    /**
     * Amount of the duplicate transaction
     */
    amount: string;
    /**
     * Account from the matching posting
     */
    account: string;
};

