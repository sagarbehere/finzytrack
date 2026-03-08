/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { QueryColumnInfo } from './QueryColumnInfo';
/**
 * Query result data.
 */
export type QueryData = {
    /**
     * The executed query
     */
    query: string;
    /**
     * Query engine used (sqlite or beanquery)
     */
    engine: string;
    /**
     * Query execution time in milliseconds
     */
    execution_time_ms: number;
    /**
     * Number of rows returned
     */
    row_count: number;
    /**
     * Column information
     */
    columns: Array<QueryColumnInfo>;
    /**
     * Query result rows
     */
    rows: Array<Record<string, any>>;
};

