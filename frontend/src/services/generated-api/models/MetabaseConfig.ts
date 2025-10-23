/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DatabaseType_Output } from './DatabaseType_Output';
/**
 * Metabase analytics configuration.
 */
export type MetabaseConfig = {
    /**
     * Database type for Metabase connection
     */
    db_type?: DatabaseType_Output;
    /**
     * Metabase version
     */
    version?: string;
    /**
     * Metabase server port
     */
    port?: number;
    /**
     * Auto-start Metabase when app launches
     */
    auto_start?: boolean;
    /**
     * Path to Metabase JAR file
     */
    jar_path?: string;
    /**
     * Path to Metabase plugins directory
     */
    plugins_dir?: string;
    /**
     * Path to Metabase data directory
     */
    data_dir?: string;
    /**
     * Path to dashboard templates
     */
    dashboard_templates_dir?: string;
    /**
     * Java heap size for Metabase
     */
    java_heap_size?: string;
    /**
     * Additional Java options
     */
    java_opts?: string;
    /**
     * Whether Metabase has been initialized
     */
    initialized?: boolean;
    /**
     * Admin email for Metabase
     */
    admin_email?: string;
    /**
     * Encrypted admin password
     */
    admin_password?: string;
    /**
     * Metabase session token for auto-login
     */
    session_token?: string;
    /**
     * Database ID in Metabase
     */
    database_id?: (number | null);
    /**
     * Path to field metadata configuration JSON file. If null, field metadata configuration is skipped.
     */
    field_metadata_config?: (string | null);
};

