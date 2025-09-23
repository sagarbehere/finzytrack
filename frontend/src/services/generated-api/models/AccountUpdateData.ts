/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountDetails } from './AccountDetails';
/**
 * Response data for account update results.
 */
export type AccountUpdateData = {
    /**
     * Whether account was updated
     */
    account_updated: boolean;
    /**
     * Updated account details if successful
     */
    account_details?: (AccountDetails | null);
    /**
     * Update result message
     */
    message: string;
};

