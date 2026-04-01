/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for the setup wizard completion.
 */
export type SetupRequest = {
    /**
     * Default currency code (e.g. USD, EUR, INR)
     */
    currency: string;
    /**
     * 'fresh' to create a starter ledger, or 'existing' to import an existing file
     */
    ledger_mode?: string;
    /**
     * Path to existing Beancount file (required when ledger_mode='existing')
     */
    existing_ledger_path?: (string | null);
    /**
     * Optional AI/LLM configuration (provider, api_url, api_key, model)
     */
    ai_config?: (Record<string, any> | null);
};

