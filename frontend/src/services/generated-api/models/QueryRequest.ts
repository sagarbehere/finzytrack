/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to execute a query.
 */
export type QueryRequest = {
    /**
     * SQL or beanquery to execute
     */
    query: string;
    /**
     * Named parameter values bound into the SQL as :name placeholders (sqlite engine only — the database binds them, so values can never be parsed as SQL). Omit for a self-contained query. Ignored by the beanquery engine, which takes a complete query string.
     */
    parameters?: (Record<string, (string | number | null)> | null);
};

