/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountsConfig } from './AccountsConfig';
import type { AIConfig } from './AIConfig';
import type { BackupConfig } from './BackupConfig';
import type { EmailImportConfig } from './EmailImportConfig';
import type { LoggingConfig } from './LoggingConfig';
import type { ServerConfig } from './ServerConfig';
/**
 * Main application configuration with nested sections.
 */
export type Config = {
    /**
     * Path to main Beancount ledger
     */
    ledger_file?: string;
    server?: ServerConfig;
    accounts?: AccountsConfig;
    backup?: BackupConfig;
    logging?: LoggingConfig;
    /**
     * AI and machine learning settings
     */
    ai?: AIConfig;
    /**
     * Email import settings (IMAP fetch, rule parsing)
     */
    email_import?: EmailImportConfig;
    /**
     * Path to the config file this configuration was loaded from
     */
    config_file_path?: (string | null);
};

