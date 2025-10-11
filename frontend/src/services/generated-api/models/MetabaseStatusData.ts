/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Metabase status data.
 */
export type MetabaseStatusData = {
    /**
     * Whether Metabase is running
     */
    running: boolean;
    /**
     * Whether Metabase is responding
     */
    healthy: boolean;
    /**
     * Whether Metabase has been initialized
     */
    initialized: boolean;
    /**
     * Metabase port
     */
    port: number;
    /**
     * Uptime in seconds
     */
    uptime_seconds: number;
    /**
     * Process ID
     */
    pid?: (number | null);
    /**
     * Start time (ISO format)
     */
    started_at?: (string | null);
};

