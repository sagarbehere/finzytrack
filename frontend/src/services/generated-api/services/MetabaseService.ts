/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_MetabaseInitializeData_ } from '../models/ApiResponse_MetabaseInitializeData_';
import type { ApiResponse_MetabaseResetData_ } from '../models/ApiResponse_MetabaseResetData_';
import type { ApiResponse_MetabaseStartData_ } from '../models/ApiResponse_MetabaseStartData_';
import type { ApiResponse_MetabaseStatusData_ } from '../models/ApiResponse_MetabaseStatusData_';
import type { ApiResponse_MetabaseStopData_ } from '../models/ApiResponse_MetabaseStopData_';
import type { ApiResponse_MetabaseSyncSchemaData_ } from '../models/ApiResponse_MetabaseSyncSchemaData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MetabaseService {
    /**
     * Get Status
     * Get Metabase status.
     *
     * Returns information about whether Metabase is running, healthy, initialized, etc.
     * @returns ApiResponse_MetabaseStatusData_ Successful Response
     * @throws ApiError
     */
    public static getMetabaseStatus(): CancelablePromise<ApiResponse_MetabaseStatusData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/metabase/status',
        });
    }
    /**
     * Start Metabase
     * Start Metabase subprocess.
     *
     * This endpoint starts the Metabase server. Metabase will run on the configured port
     * and will be accessible via the browser. The first time Metabase starts, it needs
     * to be initialized via the /initialize endpoint.
     * @returns ApiResponse_MetabaseStartData_ Successful Response
     * @throws ApiError
     */
    public static startMetabase(): CancelablePromise<ApiResponse_MetabaseStartData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/metabase/start',
        });
    }
    /**
     * Stop Metabase
     * Stop Metabase gracefully.
     *
     * This endpoint stops the Metabase server. It attempts a graceful shutdown first,
     * and will force-kill the process if it doesn't stop within 10 seconds.
     * @returns ApiResponse_MetabaseStopData_ Successful Response
     * @throws ApiError
     */
    public static stopMetabase(): CancelablePromise<ApiResponse_MetabaseStopData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/metabase/stop',
        });
    }
    /**
     * Initialize Metabase
     * Initialize Metabase (first-run setup).
     *
     * This endpoint performs the initial setup of Metabase:
     * 1. Creates an admin account with a randomly-generated password
     * 2. Connects to the DuckDB database
     * 3. Imports dashboard templates (if available)
     *
     * The admin password is returned once and should be saved by the user.
     * @returns ApiResponse_MetabaseInitializeData_ Successful Response
     * @throws ApiError
     */
    public static initializeMetabase(): CancelablePromise<ApiResponse_MetabaseInitializeData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/metabase/initialize',
        });
    }
    /**
     * Sync Schema
     * Trigger Metabase to refresh DuckDB schema.
     *
     * This endpoint tells Metabase to re-scan the DuckDB database and update
     * its schema cache. This is useful after the ledger has been updated and
     * exported to DuckDB, to ensure Metabase reflects the latest data structure.
     * @returns ApiResponse_MetabaseSyncSchemaData_ Successful Response
     * @throws ApiError
     */
    public static syncMetabaseSchema(): CancelablePromise<ApiResponse_MetabaseSyncSchemaData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/metabase/sync-schema',
        });
    }
    /**
     * Reset Metabase
     * Perform a factory reset of the Metabase instance.
     *
     * This is a destructive operation that will:
     * 1. Stop the Metabase server.
     * 2. Delete the Metabase application database file, wiping out all users and settings.
     * 3. Reset the application's configuration to the un-initialized state.
     *
     * After this, Metabase can be initialized again from scratch.
     * @returns ApiResponse_MetabaseResetData_ Successful Response
     * @throws ApiError
     */
    public static resetMetabase(): CancelablePromise<ApiResponse_MetabaseResetData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/metabase/reset',
        });
    }
}
