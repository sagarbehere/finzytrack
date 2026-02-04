/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response data for account delete results.
 */
export type AccountDeleteData = {
    /**
     * Whether account was deleted
     */
    account_deleted: boolean;
    /**
     * Delete result message
     */
    message: string;
    /**
     * Any warnings about the deletion
     */
    warnings?: (Array<string> | null);
    /**
     * Number of transactions deleted if delete_transactions was true
     */
    transactions_deleted?: (number | null);
};

