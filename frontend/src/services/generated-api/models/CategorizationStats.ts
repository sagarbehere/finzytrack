/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Statistics about the categorization batch.
 */
export type CategorizationStats = {
    /**
     * Total number of transactions processed
     */
    total_count: number;
    /**
     * Number of transactions with ML categories
     */
    categorized_count: number;
    /**
     * Number of potential duplicates detected
     */
    duplicate_count: number;
    /**
     * ML training warnings or info messages
     */
    ml_training_info?: (string | null);
};

