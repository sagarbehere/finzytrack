/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountsConfig } from './AccountsConfig';
import type { AIConfig } from './AIConfig';
import type { AnalyticsConfig } from './AnalyticsConfig';
import type { BackupConfig } from './BackupConfig';
import type { EmailImportConfig } from './EmailImportConfig';
import type { FeaturesConfig } from './FeaturesConfig';
import type { LoggingConfig } from './LoggingConfig';
import type { SecurityConfig } from './SecurityConfig';
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
    features?: FeaturesConfig;
    backup?: BackupConfig;
    logging?: LoggingConfig;
    security?: SecurityConfig;
    /**
     * AI and machine learning settings
     */
    ai?: AIConfig;
    /**
     * Path to OFX account mappings YAML file
     */
    ofx_mappings_file?: (string | null);
    /**
     * Directory containing CSV import rule YAML files
     */
    csv_rules_dir?: (string | null);
    /**
     * Directory containing XLS import rule YAML files
     */
    xls_rules_dir?: (string | null);
    /**
     * Directory containing dashboard recipe JSON files
     */
    recipes_dir?: string;
    /**
     * Analytics and reporting settings
     */
    analytics?: AnalyticsConfig;
    /**
     * Email import settings (IMAP fetch, rule parsing)
     */
    email_import?: EmailImportConfig;
    /**
     * Path to the config file this configuration was loaded from
     */
    config_file_path?: (string | null);
};

