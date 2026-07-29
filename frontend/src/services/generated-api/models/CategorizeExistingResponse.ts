/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CategorizationStats } from './CategorizationStats';
import type { CategorizeExistingResult } from './CategorizeExistingResult';
/**
 * Response body for the categorize-existing endpoint.
 */
export type CategorizeExistingResponse = {
    /**
     * Suggestions keyed by id
     */
    results: Array<CategorizeExistingResult>;
    /**
     * Batch statistics (duplicate_count is always 0)
     */
    stats: CategorizationStats;
};

