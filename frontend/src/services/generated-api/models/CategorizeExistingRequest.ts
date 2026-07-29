/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CategorizeExistingTransaction } from './CategorizeExistingTransaction';
/**
 * Request body for the categorize-existing endpoint.
 */
export type CategorizeExistingRequest = {
    /**
     * Existing transactions to categorize
     */
    transactions: Array<CategorizeExistingTransaction>;
    /**
     * Override engine: 'ai' or 'classifier'. If unset, uses config.
     */
    force_engine?: (string | null);
};

