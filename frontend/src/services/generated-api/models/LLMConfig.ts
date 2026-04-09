/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * LLM API configuration for natural language features.
 */
export type LLMConfig = {
    /**
     * Use FinzyTrack AI managed service. When enabled, provider/api_url/api_key/model are ignored — the proxy controls everything.
     */
    finzytrack_ai?: boolean;
    /**
     * Authentication token for FinzyTrack AI service.
     */
    finzytrack_ai_token?: string;
    /**
     * FinzyTrack AI proxy URL (override for development/testing).
     */
    finzytrack_ai_url?: string;
    /**
     * LLM provider: 'openai' (any OpenAI-compatible endpoint incl. LM Studio, Ollama, OpenAI, Groq) or 'anthropic' (Anthropic API directly)
     */
    provider?: LLMConfig.provider;
    /**
     * OpenAI-compatible API base URL — only used when provider=openai (e.g. http://127.0.0.1:1234 or https://api.openai.com)
     */
    api_url?: string;
    /**
     * API key (required for cloud providers, leave empty for local LLMs)
     */
    api_key?: string;
    /**
     * Model name (e.g. gpt-4o, claude-sonnet-4-6, llama-3.1-8b-instruct)
     */
    model?: string;
    /**
     * Sampling temperature (0=deterministic, 2=very random)
     */
    temperature?: number;
    /**
     * Maximum tokens in LLM response. 0 = use model default (Anthropic requires a value > 0).
     */
    max_tokens?: number;
    /**
     * Maximum tool-call round-trips per user message in the AI assistant.
     */
    max_tool_rounds?: number;
    /**
     * Timeout in seconds for LLM API requests.
     */
    timeout_secs?: number;
};
export namespace LLMConfig {
    /**
     * LLM provider: 'openai' (any OpenAI-compatible endpoint incl. LM Studio, Ollama, OpenAI, Groq) or 'anthropic' (Anthropic API directly)
     */
    export enum provider {
        OPENAI = 'openai',
        ANTHROPIC = 'anthropic',
    }
}

