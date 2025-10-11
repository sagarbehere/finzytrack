/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * DuckDB export result data.
 */
export type DuckDBExportData = {
    /**
     * Number of postings exported
     */
    postings_count: number;
    /**
     * Number of transactions exported
     */
    transactions_count: number;
    /**
     * Export duration in milliseconds
     */
    duration_ms: number;
    /**
     * Path to DuckDB file
     */
    path: string;
    /**
     * Last sync timestamp (ISO format)
     */
    last_sync: string;
};

