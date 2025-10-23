/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ConfigUpdateResponse_ } from '../models/ApiResponse_ConfigUpdateResponse_';
import type { ApiResponse_FileContent_ } from '../models/ApiResponse_FileContent_';
import type { ApiResponse_LedgerUpdateResponse_ } from '../models/ApiResponse_LedgerUpdateResponse_';
import type { FileUpdateRequest } from '../models/FileUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class FilesService {
    /**
     * Get Config File
     * Get config file content for editing.
     *
     * Returns raw YAML content for Monaco editor.
     * @returns ApiResponse_FileContent_ Successful Response
     * @throws ApiError
     */
    public static getConfigFileApiFilesConfigGet(): CancelablePromise<ApiResponse_FileContent_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/files/config',
        });
    }
    /**
     * Update Config File
     * Update config file with validation and hot-reload.
     *
     * Steps:
     * 1. Validate YAML syntax
     * 2. Validate against Config schema (Pydantic)
     * 3. Write atomically with BackupManager
     * 4. Hot-reload config in memory (safe fields only)
     * 5. Return metadata + parsed config + restart info
     *
     * Note: Does NOT echo content back (frontend already has it).
     * @param requestBody
     * @returns ApiResponse_ConfigUpdateResponse_ Successful Response
     * @throws ApiError
     */
    public static updateConfigFileApiFilesConfigPut(
        requestBody: FileUpdateRequest,
    ): CancelablePromise<ApiResponse_ConfigUpdateResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/files/config',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Ledger File
     * Get ledger file content for viewing/editing.
     *
     * Returns raw Beancount text for Monaco editor.
     * Includes size warning for large files (> 10 MB).
     * @returns ApiResponse_FileContent_ Successful Response
     * @throws ApiError
     */
    public static getLedgerFileApiFilesLedgerGet(): CancelablePromise<ApiResponse_FileContent_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/files/ledger',
        });
    }
    /**
     * Update Ledger File
     * Update ledger file without validation (validation is optional via separate endpoint).
     *
     * Uses BeancountManager's atomic_ledger_write for:
     * - Atomic write with BackupManager
     * - Automatic cache invalidation
     * - Notification of registered callbacks
     *
     * Note: Does NOT echo content back (frontend already has it).
     * Frontend should issue GET request to reload content after successful save.
     * @param requestBody
     * @returns ApiResponse_LedgerUpdateResponse_ Successful Response
     * @throws ApiError
     */
    public static updateLedgerFileApiFilesLedgerPut(
        requestBody: FileUpdateRequest,
    ): CancelablePromise<ApiResponse_LedgerUpdateResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/files/ledger',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
