/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DuplicateInfo } from './DuplicateInfo';
/**
 * Result of categorization for a single transaction.
 * Uses ID-based matching to correlate request and response.
 */
export type CategorizedTransactionResult = {
    /**
     * Frontend-generated temporary ID (matches request)
     */
    id: string;
    /**
     * ML-suggested expense category (e.g., Expenses:Groceries)
     */
    suggested_category?: (string | null);
    /**
     * ML confidence score (0.0 to 1.0)
     */
    confidence?: (number | null);
    /**
     * Whether this is a potential duplicate
     */
    is_duplicate?: boolean;
    /**
     * Details about the duplicate match
     */
    duplicate_info?: (DuplicateInfo | null);
};

