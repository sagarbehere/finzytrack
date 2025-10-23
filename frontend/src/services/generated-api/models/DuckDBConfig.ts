/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * DuckDB export configuration.
 */
export type DuckDBConfig = {
    /**
     * Path to DuckDB export file
     */
    export_path?: string;
    /**
     * Enable automatic sync on ledger changes
     */
    auto_sync_enabled?: boolean;
    /**
     * Debounce delay in seconds before syncing
     */
    sync_debounce_seconds?: number;
};

