/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountsConfig } from './AccountsConfig';
import type { AnalyticsConfig } from './AnalyticsConfig';
import type { BackupConfig } from './BackupConfig';
import type { FeaturesConfig } from './FeaturesConfig';
import type { LoggingConfig } from './LoggingConfig';
import type { MLConfig } from './MLConfig';
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
    ml?: MLConfig;
    features?: FeaturesConfig;
    backup?: BackupConfig;
    logging?: LoggingConfig;
    security?: SecurityConfig;
    /**
     * Path to OFX account mappings YAML file
     */
    ofx_mappings_file?: (string | null);
    /**
     * Directory containing CSV import rule YAML files
     */
    csv_rules_dir?: (string | null);
    /**
     * Analytics and reporting settings
     */
    analytics?: AnalyticsConfig;
};

