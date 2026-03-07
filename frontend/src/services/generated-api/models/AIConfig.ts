/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CategorizationConfig } from './CategorizationConfig';
import type { LLMConfig } from './LLMConfig';
/**
 * AI and machine learning settings.
 */
export type AIConfig = {
    /**
     * Transaction auto-categorization settings
     */
    categorization?: CategorizationConfig;
    /**
     * LLM API settings for natural language features
     */
    llm?: LLMConfig;
};

