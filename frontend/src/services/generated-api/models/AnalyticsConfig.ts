/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DuckDBConfig } from './DuckDBConfig';
import type { MetabaseConfig } from './MetabaseConfig';
import type { SQLiteConfig } from './SQLiteConfig';
/**
 * Analytics and reporting configuration.
 */
export type AnalyticsConfig = {
    /**
     * Metabase analytics settings
     */
    metabase?: MetabaseConfig;
    /**
     * DuckDB export settings
     */
    duckdb?: DuckDBConfig;
    /**
     * SQLite export settings
     */
    sqlite?: SQLiteConfig;
};

