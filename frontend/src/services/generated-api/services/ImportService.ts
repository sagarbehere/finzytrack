/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_CategorizeResponse_ } from '../models/ApiResponse_CategorizeResponse_';
import type { ApiResponse_CommitResponse_ } from '../models/ApiResponse_CommitResponse_';
import type { ApiResponse_LearnOFXAccountData_ } from '../models/ApiResponse_LearnOFXAccountData_';
import type { ApiResponse_OFXDetectionData_ } from '../models/ApiResponse_OFXDetectionData_';
import type { CategorizeRequest } from '../models/CategorizeRequest';
import type { CommitRequest } from '../models/CommitRequest';
import type { LearnOFXAccountRequest } from '../models/LearnOFXAccountRequest';
import type { OFXDetectionRequest } from '../models/OFXDetectionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ImportService {
    /**
     * Detect Ofx Account
     * @param requestBody
     * @returns ApiResponse_OFXDetectionData_ Successful Response
     * @throws ApiError
     */
    public static detectOfxAccount(
        requestBody: OFXDetectionRequest,
    ): CancelablePromise<ApiResponse_OFXDetectionData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/detect-ofx-account',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Learn Ofx Account
     * @param requestBody
     * @returns ApiResponse_LearnOFXAccountData_ Successful Response
     * @throws ApiError
     */
    public static learnOfxAccount(
        requestBody: LearnOFXAccountRequest,
    ): CancelablePromise<ApiResponse_LearnOFXAccountData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/learn-ofx-account',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Categorize Transactions
     * Categorize transactions using ML and detect potential duplicates.
     *
     * This endpoint:
     * 1. Trains ML classifier (or skips if disabled/insufficient data)
     * 2. Categorizes each transaction using ML or fallback to default
     * 3. Checks for duplicates using OFX ID and fuzzy matching
     * 4. Returns results in same order as request with statistics
     *
     * Args:
     * request: CategorizeRequest with transactions to process
     * config_manager: Injected config manager
     * beancount_manager: Injected beancount manager (provides cached data)
     *
     * Returns:
     * CategorizeResponse with results and stats
     * @param requestBody
     * @returns ApiResponse_CategorizeResponse_ Successful Response
     * @throws ApiError
     */
    public static categorizeTransactionsApiImportCategorizePost(
        requestBody: CategorizeRequest,
    ): CancelablePromise<ApiResponse_CategorizeResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/categorize',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Commit Transactions
     * Commit transactions to the Beancount ledger.
     *
     * This endpoint:
     * 1. Validates each transaction with Beancount
     * 2. Generates transaction IDs
     * 3. Formats transactions with proper Beancount syntax
     * 4. Atomically writes to ledger with backup
     *
     * Args:
     * request: CommitRequest with transactions to commit
     * config_manager: Injected config manager
     * backup_manager: Injected backup manager
     * beancount_manager: Injected beancount manager
     *
     * Returns:
     * CommitResponse with success status and count
     *
     * Raises:
     * APIError: If validation or write fails
     * @param requestBody
     * @returns ApiResponse_CommitResponse_ Successful Response
     * @throws ApiError
     */
    public static commitTransactionsApiImportCommitPost(
        requestBody: CommitRequest,
    ): CancelablePromise<ApiResponse_CommitResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/commit',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
