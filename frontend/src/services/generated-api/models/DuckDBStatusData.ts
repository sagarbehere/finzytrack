/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * DuckDB export status data.
 */
export type DuckDBStatusData = {
    /**
     * Whether DuckDB file exists
     */
    exists: boolean;
    /**
     * Path to DuckDB file
     */
    path: string;
    /**
     * File size in bytes
     */
    size_bytes: number;
    /**
     * Last modified timestamp (ISO format)
     */
    last_modified?: (string | null);
    /**
     * Number of postings in database
     */
    postings_count?: number;
    /**
     * Whether DuckDB is up-to-date with ledger
     */
    is_current?: boolean;
    /**
     * Ledger file modification timestamp
     */
    ledger_modified?: (string | null);
};

