/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommitTransaction } from './CommitTransaction';
/**
 * Request body for commit endpoint.
 */
export type CommitRequest = {
    /**
     * List of transactions to commit to ledger
     */
    transactions: Array<CommitTransaction>;
    /**
     * Bypass the pre-write validation gate. Set true only on an explicit 'Register anyway' after the user has seen the introduced errors (§6.6).
     */
    acknowledge?: boolean;
};

