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
import type { OFXAccountMapping } from './OFXAccountMapping';
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
     * OFX account mappings
     */
    ofx_account_mappings?: Array<OFXAccountMapping>;
    /**
     * Analytics and reporting settings
     */
    analytics?: AnalyticsConfig;
};

