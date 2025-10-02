/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RawTransactionForCategorization } from './RawTransactionForCategorization';
/**
 * Request body for categorization endpoint.
 */
export type CategorizeRequest = {
    /**
     * List of transactions to categorize
     */
    transactions: Array<RawTransactionForCategorization>;
    /**
     * Source account for transactions (e.g., Assets:Bank:Checking)
     */
    source_account: string;
    /**
     * Currency code (e.g., USD)
     */
    currency: string;
};

