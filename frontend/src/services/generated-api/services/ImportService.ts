/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_CategorizeResponse_ } from '../models/ApiResponse_CategorizeResponse_';
import type { ApiResponse_CommitResponse_ } from '../models/ApiResponse_CommitResponse_';
import type { ApiResponse_CsvRule_ } from '../models/ApiResponse_CsvRule_';
import type { ApiResponse_CsvRuleListData_ } from '../models/ApiResponse_CsvRuleListData_';
import type { ApiResponse_LearnOFXAccountData_ } from '../models/ApiResponse_LearnOFXAccountData_';
import type { ApiResponse_OFXDetectionData_ } from '../models/ApiResponse_OFXDetectionData_';
import type { ApiResponse_ProfilesListResponse_ } from '../models/ApiResponse_ProfilesListResponse_';
import type { ApiResponse_ReloadResponse_ } from '../models/ApiResponse_ReloadResponse_';
import type { ApiResponse_TestConnectionResponse_ } from '../models/ApiResponse_TestConnectionResponse_';
import type { ApiResponse_TrialExtractResult_ } from '../models/ApiResponse_TrialExtractResult_';
import type { ApiResponse_XlsRule_ } from '../models/ApiResponse_XlsRule_';
import type { ApiResponse_XlsRuleListData_ } from '../models/ApiResponse_XlsRuleListData_';
import type { CategorizeRequest } from '../models/CategorizeRequest';
import type { CommitRequest } from '../models/CommitRequest';
import type { FetchRequest } from '../models/FetchRequest';
import type { LearnOFXAccountRequest } from '../models/LearnOFXAccountRequest';
import type { OFXDetectionRequest } from '../models/OFXDetectionRequest';
import type { TestConnectionRequest } from '../models/TestConnectionRequest';
import type { TrialExtractRequest } from '../models/TrialExtractRequest';
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
     * 2. Generates transaction IDs (UUIDv7 + content_hash)
     * 3. Formats transactions with proper Beancount syntax
     * 4. Atomically writes to ledger with backup
     *
     * Args:
     * request: CommitRequest with transactions to commit
     * config_manager: Injected config manager
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
    /**
     * List Csv Rules
     * @returns ApiResponse_CsvRuleListData_ Successful Response
     * @throws ApiError
     */
    public static listCsvRules(): CancelablePromise<ApiResponse_CsvRuleListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/csv-rules',
        });
    }
    /**
     * Get Csv Rule
     * @param filename
     * @returns ApiResponse_CsvRule_ Successful Response
     * @throws ApiError
     */
    public static getCsvRule(
        filename: string,
    ): CancelablePromise<ApiResponse_CsvRule_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/csv-rules/{filename}',
            path: {
                'filename': filename,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Xls Rules
     * @returns ApiResponse_XlsRuleListData_ Successful Response
     * @throws ApiError
     */
    public static listXlsRules(): CancelablePromise<ApiResponse_XlsRuleListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/xls-rules',
        });
    }
    /**
     * Get Xls Rule
     * @param filename
     * @returns ApiResponse_XlsRule_ Successful Response
     * @throws ApiError
     */
    public static getXlsRule(
        filename: string,
    ): CancelablePromise<ApiResponse_XlsRule_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/xls-rules/{filename}',
            path: {
                'filename': filename,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Email Profiles
     * Return list of configured account profiles.
     * Each profile_id is the filename without .yaml extension.
     * Credentials are never included in the response.
     * @returns ApiResponse_ProfilesListResponse_ Successful Response
     * @throws ApiError
     */
    public static listEmailProfilesApiImportEmailProfilesGet(): CancelablePromise<ApiResponse_ProfilesListResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/email/profiles',
        });
    }
    /**
     * Reload Email Profiles
     * Re-scan rules directory and reload all account profiles.
     * @returns ApiResponse_ReloadResponse_ Successful Response
     * @throws ApiError
     */
    public static reloadEmailProfilesApiImportEmailReloadPost(): CancelablePromise<ApiResponse_ReloadResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/email/reload',
        });
    }
    /**
     * Test Email Connection
     * Connectivity test — connect, login, select folder, count matching emails.
     * Loads IMAP credentials from the account profile YAML identified by profile_id.
     * @param requestBody
     * @returns ApiResponse_TestConnectionResponse_ Successful Response
     * @throws ApiError
     */
    public static testEmailConnectionApiImportEmailTestConnectionPost(
        requestBody: TestConnectionRequest,
    ): CancelablePromise<ApiResponse_TestConnectionResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/email/test-connection',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Fetch Email Transactions
     * Stream fetch progress as Server-Sent Events (text/event-stream).
     *
     * This endpoint uses SSE for real-time progress reporting.
     * Errors during streaming are sent as SSE error events.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static fetchEmailTransactionsApiImportEmailFetchPost(
        requestBody: FetchRequest,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/email/fetch',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Trial Extract Email
     * Run a saved email rule against raw .eml content and return extracted fields.
     *
     * Pure validation — no IMAP, no imports. Parses the .eml, finds the first
     * matching transaction type in the rule, runs all extraction patterns, and
     * returns the results so the frontend can display them to the user.
     * @param requestBody
     * @returns ApiResponse_TrialExtractResult_ Successful Response
     * @throws ApiError
     */
    public static trialExtractEmail(
        requestBody: TrialExtractRequest,
    ): CancelablePromise<ApiResponse_TrialExtractResult_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/email/trial-extract',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
