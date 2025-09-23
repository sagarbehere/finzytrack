/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for updating account details.
 */
export type AccountUpdateRequest = {
    /**
     * New account name (must be unique if changed)
     */
    new_name?: (string | null);
    /**
     * Updated list of currencies for this account
     */
    currencies?: (Array<string> | null);
    /**
     * Updated opening date
     */
    open_date?: (string | null);
    /**
     * Updated closing date (null to reopen)
     */
    close_date?: (string | null);
    /**
     * Updated metadata (merges with existing)
     */
    metadata?: (Record<string, any> | null);
};

