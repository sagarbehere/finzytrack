/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DuckDBExportData_ } from '../models/ApiResponse_DuckDBExportData_';
import type { ApiResponse_DuckDBStatusData_ } from '../models/ApiResponse_DuckDBStatusData_';
import type { DuckDBExportRequest } from '../models/DuckDBExportRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class LedgerService {
    /**
     * Export To Duckdb
     * Export current ledger to DuckDB format.
     *
     * This endpoint manually triggers a DuckDB export. Normally, exports happen
     * automatically when the ledger changes (with debouncing), but this endpoint
     * allows immediate manual export.
     * @param requestBody
     * @returns ApiResponse_DuckDBExportData_ Successful Response
     * @throws ApiError
     */
    public static exportToDuckDb(
        requestBody?: DuckDBExportRequest,
    ): CancelablePromise<ApiResponse_DuckDBExportData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/ledger/export/duckdb',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Duckdb Status
     * Get DuckDB export status.
     *
     * Returns information about the DuckDB export file, including whether it exists,
     * its size, last modification time, and whether it's up-to-date with the ledger.
     * @returns ApiResponse_DuckDBStatusData_ Successful Response
     * @throws ApiError
     */
    public static getDuckDbStatus(): CancelablePromise<ApiResponse_DuckDBStatusData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ledger/export/duckdb/status',
        });
    }
}
