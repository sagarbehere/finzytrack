/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CategorizationStats } from './CategorizationStats';
import type { CategorizedTransactionResult } from './CategorizedTransactionResult';
/**
 * Response body for categorization endpoint.
 */
export type CategorizeResponse = {
    /**
     * Categorization results (same order as request)
     */
    results: Array<CategorizedTransactionResult>;
    /**
     * Batch statistics
     */
    stats: CategorizationStats;
};

