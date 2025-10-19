/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DeleteTransactionResponse_ } from '../models/ApiResponse_DeleteTransactionResponse_';
import type { ApiResponse_DuckDBExportData_ } from '../models/ApiResponse_DuckDBExportData_';
import type { ApiResponse_DuckDBStatusData_ } from '../models/ApiResponse_DuckDBStatusData_';
import type { ApiResponse_ExportData_ } from '../models/ApiResponse_ExportData_';
import type { ApiResponse_ExportStatusData_ } from '../models/ApiResponse_ExportStatusData_';
import type { ApiResponse_QueryData_ } from '../models/ApiResponse_QueryData_';
import type { ApiResponse_UpdateTransactionResponse_ } from '../models/ApiResponse_UpdateTransactionResponse_';
import type { Body_exportLedger } from '../models/Body_exportLedger';
import type { DatabaseType } from '../models/DatabaseType';
import type { DeleteTransactionRequest } from '../models/DeleteTransactionRequest';
import type { DuckDBExportRequest } from '../models/DuckDBExportRequest';
import type { QueryRequest } from '../models/QueryRequest';
import type { UpdateTransactionRequest } from '../models/UpdateTransactionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class LedgerService {
    /**
     * Export Ledger
     * Export ledger to specified database or active database.
     *
     * This endpoint allows manual export to either DuckDB or SQLite, regardless of
     * which database is configured as active in analytics.metabase.db_type.
     *
     * Examples:
     * POST /api/ledger/export                           # Export to active database (from config)
     * POST /api/ledger/export {"db_type": "sqlite"}    # Explicitly export to SQLite
     * POST /api/ledger/export {"db_type": "duckdb"}    # Explicitly export to DuckDB
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
     * Get export status for specified database or active database.
     *
     * Examples:
     * GET /api/ledger/export/status                    # Status of active database
     * GET /api/ledger/export/status?db_type=sqlite     # Status of SQLite database
     * GET /api/ledger/export/status?db_type=duckdb     # Status of DuckDB database
     * @param dbType Database type. If not specified, uses active database from config.
     * @returns ApiResponse_ExportStatusData_ Successful Response
     * @throws ApiError
     */
    public static getExportStatus(
        dbType?: (DatabaseType | null),
    ): CancelablePromise<ApiResponse_ExportStatusData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ledger/export/status',
            query: {
                'db_type': dbType,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
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
     * @param dbType Database/engine type: 'duckdb', 'sqlite', or 'beanquery'. If not specified, uses active database from config.
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
}
