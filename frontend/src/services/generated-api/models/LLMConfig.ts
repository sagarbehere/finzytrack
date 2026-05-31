/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * LLM API configuration for natural language features.
 */
export type LLMConfig = {
    /**
     * Use Finzytrack AI managed service. When enabled, provider/api_url/api_key/model are ignored — the proxy controls everything.
     */
    finzytrack_ai?: boolean;
    /**
     * Authentication token for Finzytrack AI service.
     */
    finzytrack_ai_token?: string;
    /**
     * Finzytrack AI proxy URL (override for development/testing).
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
    /**
     * Advanced: provider-specific request parameters to merge into every chat call. Bring-your-own only — ignored when finzytrack_ai is enabled. Routed to OpenAI SDK's extra_body or merged into Anthropic SDK's call kwargs depending on the active provider. Stored in plaintext in config.yaml.
     */
    extra_request_body?: (Record<string, any> | null);
    /**
     * True if AI is usable — either via Finzytrack AI or a fully-specified bring-your-own model.
     *
     * For bring-your-own, a model name alone isn't enough: provider=openai needs
     * api_url (the endpoint base URL — local LLMs leave api_key empty, so that's
     * not required), and provider=anthropic needs api_key (Anthropic is always cloud).
     */
    readonly is_configured: boolean;
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

