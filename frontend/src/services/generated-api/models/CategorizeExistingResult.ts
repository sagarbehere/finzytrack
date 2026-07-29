/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Suggested account for one existing transaction.
 */
export type CategorizeExistingResult = {
    /**
     * Matches the request id
     */
    id: string;
    /**
     * Suggested account
     */
    suggested_category?: (string | null);
    /**
     * Classifier confidence (0.0-1.0); null for AI
     */
    confidence?: (number | null);
};

