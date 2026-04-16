/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CategorizationEngine } from './CategorizationEngine';
/**
 * Transaction auto-categorization configuration.
 */
export type CategorizationConfig = {
    /**
     * Enable auto-categorization
     */
    enabled?: boolean;
    /**
     * Categorization engine: 'classifier' (scikit-learn) or 'ai' (requires ai.llm to be configured)
     */
    engine?: CategorizationEngine;
};

