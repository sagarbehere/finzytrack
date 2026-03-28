/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DeleteTransactionResponse_ } from '../models/ApiResponse_DeleteTransactionResponse_';
import type { ApiResponse_ExportData_ } from '../models/ApiResponse_ExportData_';
import type { ApiResponse_ExportStatusData_ } from '../models/ApiResponse_ExportStatusData_';
import type { ApiResponse_LedgerErrorsResponse_ } from '../models/ApiResponse_LedgerErrorsResponse_';
import type { ApiResponse_QueryData_ } from '../models/ApiResponse_QueryData_';
import type { ApiResponse_UpdateTransactionResponse_ } from '../models/ApiResponse_UpdateTransactionResponse_';
import type { Body_exportLedger } from '../models/Body_exportLedger';
import type { DeleteTransactionRequest } from '../models/DeleteTransactionRequest';
import type { QueryRequest } from '../models/QueryRequest';
import type { UpdateTransactionRequest } from '../models/UpdateTransactionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class LedgerService {
    /**
     * Export Ledger
     * Export ledger to SQLite database.
     *
     * Examples:
     * POST /api/ledger/export
     * POST /api/ledger/export {"force": true}
     * @param requestBody
     * @returns ApiResponse_ExportData_ Successful Response
     * @throws ApiError
     */
    public static exportLedger(
        requestBody?: Body_exportLedger,
    ): CancelablePromise<ApiResponse_ExportData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/ledger/export',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Export Status
     * Get SQLite export status.
     *
     * Examples:
     * GET /api/ledger/export/status
     * @returns ApiResponse_ExportStatusData_ Successful Response
     * @throws ApiError
     */
    public static getExportStatus(): CancelablePromise<ApiResponse_ExportStatusData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ledger/export/status',
        });
    }
    /**
     * Update Ledger Transactions
     * Update existing transactions in the ledger.
     *
     * This endpoint:
     * 1. Validates all transactions before making changes (atomic)
     * 2. Locates transactions by ID (UUIDv7) in the ledger
     * 3. Updates them atomically in the ledger file
     * 4. Returns success with count of updated transactions
     *
     * If any transaction fails validation, the entire operation is rolled back.
     * @param requestBody
     * @returns ApiResponse_UpdateTransactionResponse_ Successful Response
     * @throws ApiError
     */
    public static updateLedgerTransactions(
        requestBody: UpdateTransactionRequest,
    ): CancelablePromise<ApiResponse_UpdateTransactionResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/ledger/transactions',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Ledger Transactions
     * Delete transactions from the ledger by ID.
     *
     * This endpoint:
     * 1. Validates that all transaction IDs exist in the ledger
     * 2. Removes them atomically from the ledger file
     * 3. Returns success with count of deleted transactions
     *
     * If any transaction ID is not found, the entire operation is rolled back.
     * @param requestBody
     * @returns ApiResponse_DeleteTransactionResponse_ Successful Response
     * @throws ApiError
     */
    public static deleteLedgerTransactions(
        requestBody: DeleteTransactionRequest,
    ): CancelablePromise<ApiResponse_DeleteTransactionResponse_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/ledger/transactions',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Execute Query
     * Execute a query against the specified database engine.
     *
     * Examples:
     * POST /api/ledger/query {"query": "SELECT * FROM postings LIMIT 10"}
     * POST /api/ledger/query?db_type=sqlite {"query": "SELECT account, SUM(amount) FROM postings GROUP BY account"}
     * POST /api/ledger/query?db_type=beanquery {"query": "SELECT account, sum(position) FROM postings GROUP BY account"}
     * @param requestBody
     * @param dbType Database/engine type: 'sqlite' or 'beanquery'. Defaults to 'sqlite'.
     * @returns ApiResponse_QueryData_ Successful Response
     * @throws ApiError
     */
    public static executeQuery(
        requestBody: QueryRequest,
        dbType?: (string | null),
    ): CancelablePromise<ApiResponse_QueryData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/ledger/query',
            query: {
                'db_type': dbType,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Postings Schema
     * Return the postings table schema as Markdown (used by the frontend SQL assistant).
     * @returns string Successful Response
     * @throws ApiError
     */
    public static getPostingsSchema(): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ledger/schema/postings',
        });
    }
    /**
     * Get Ledger Errors
     * Return current ledger parse errors from the cache.
     *
     * This is a lightweight read — no re-parsing occurs.
     * @returns ApiResponse_LedgerErrorsResponse_ Successful Response
     * @throws ApiError
     */
    public static getLedgerErrors(): CancelablePromise<ApiResponse_LedgerErrorsResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ledger/errors',
        });
    }
}
