/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * LLM API configuration for natural language features.
 */
export type LLMConfig = {
    /**
     * OpenAI-compatible API base URL (e.g. http://127.0.0.1:1234 or https://api.openai.com)
     */
    api_url?: string;
    /**
     * API key (required for cloud providers, leave empty for local)
     */
    api_key?: string;
    /**
     * Model name (e.g. gpt-4o, llama-3.1-8b)
     */
    model?: string;
    /**
     * Sampling temperature (0=deterministic, 2=very random)
     */
    temperature?: number;
    /**
     * Maximum tokens in LLM response
     */
    max_tokens?: number;
};

