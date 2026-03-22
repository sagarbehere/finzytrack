/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Email import configuration (formerly the email_service microservice).
 */
export type EmailImportConfig = {
    /**
     * Enable email import functionality
     */
    enabled?: boolean;
    /**
     * Directory containing email import rule YAML files
     */
    rules_directory?: string;
    /**
     * Default number of days to look back for emails
     */
    default_lookback_days?: number;
    /**
     * Max emails to fetch per request; truncates with warning
     */
    max_emails?: number;
    /**
     * Socket timeout for IMAP operations; 0 = no timeout
     */
    imap_timeout_secs?: number;
    /**
     * Default parsing mode: 'regex' or 'llm'; overridden per account or per rule
     */
    parsing_mode?: string;
};

