/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Metabase initialization result data.
 */
export type MetabaseInitializeData = {
    /**
     * Admin email
     */
    admin_email: string;
    /**
     * Admin password (one-time display)
     */
    admin_password: string;
    /**
     * Session token
     */
    session_token: string;
    /**
     * DuckDB database ID in Metabase
     */
    database_id: number;
    /**
     * Number of dashboards imported
     */
    dashboards_imported: number;
};

