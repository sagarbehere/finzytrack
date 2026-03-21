/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FetchRequest } from '../models/FetchRequest';
import type { ProfilesListResponse } from '../models/ProfilesListResponse';
import type { ReloadResponse } from '../models/ReloadResponse';
import type { TestConnectionRequest } from '../models/TestConnectionRequest';
import type { TestConnectionResponse } from '../models/TestConnectionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Health
     * @returns any Successful Response
     * @throws ApiError
     */
    public static healthHealthGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * List Profiles
     * Return list of configured account profiles.
     * Each profile_id is the filename without .yaml extension.
     * Credentials are never included in the response.
     * @returns ProfilesListResponse Successful Response
     * @throws ApiError
     */
    public static listProfilesProfilesGet(): CancelablePromise<ProfilesListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles',
        });
    }
    /**
     * Test Connection
     * Connectivity test — connect, login, select folder, count matching emails.
     * Loads IMAP credentials from the account profile YAML identified by profile_id.
     * On success, performs a lightweight IMAP SEARCH to count matching emails.
     * @param requestBody
     * @returns TestConnectionResponse Successful Response
     * @throws ApiError
     */
    public static testConnectionTestConnectionPost(
        requestBody: TestConnectionRequest,
    ): CancelablePromise<TestConnectionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/test-connection',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reload Profiles
     * Re-scan rules directory and reload all account profiles.
     * @returns ReloadResponse Successful Response
     * @throws ApiError
     */
    public static reloadProfilesReloadPost(): CancelablePromise<ReloadResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/reload',
        });
    }
    /**
     * Fetch Transactions
     * Stream fetch progress as Server-Sent Events (text/event-stream).
     *
     * Loads the account profile by profile_id, reads IMAP credentials from the
     * profile's imap_server block, and applies date range/lookback precedence.
     *
     * Each event is a JSON ProgressEvent (see result_schemas.py).
     * Phases emitted: connecting → fetching → parsing → complete.
     * Errors are reported as phase='error' events.
     *
     * Frontend must use fetch() + ReadableStream (not EventSource) because
     * this is a POST endpoint with a JSON body.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static fetchTransactionsFetchPost(
        requestBody: FetchRequest,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/fetch',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
