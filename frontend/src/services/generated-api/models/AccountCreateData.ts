/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountDetails } from './AccountDetails';
/**
 * Response data for account creation results.
 */
export type AccountCreateData = {
    /**
     * Whether account was created
     */
    account_created: boolean;
    /**
     * Created account details if successful
     */
    account_details?: (AccountDetails | null);
    /**
     * Creation result message
     */
    message: string;
};

