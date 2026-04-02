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
import type { ApiResponse_RuleContentResponse_ } from '../models/ApiResponse_RuleContentResponse_';
import type { ApiResponse_RuleDeleteResponse_ } from '../models/ApiResponse_RuleDeleteResponse_';
import type { ApiResponse_RuleWriteResponse_ } from '../models/ApiResponse_RuleWriteResponse_';
import type { ApiResponse_TestConnectionResponse_ } from '../models/ApiResponse_TestConnectionResponse_';
import type { ApiResponse_TrialExtractResult_ } from '../models/ApiResponse_TrialExtractResult_';
import type { ApiResponse_XlsRule_ } from '../models/ApiResponse_XlsRule_';
import type { ApiResponse_XlsRuleListData_ } from '../models/ApiResponse_XlsRuleListData_';
import type { Body_llm_parse_api_import_llm_parse_post } from '../models/Body_llm_parse_api_import_llm_parse_post';
import type { CategorizeRequest } from '../models/CategorizeRequest';
import type { CommitRequest } from '../models/CommitRequest';
import type { FetchRequest } from '../models/FetchRequest';
import type { LearnOFXAccountRequest } from '../models/LearnOFXAccountRequest';
import type { OFXDetectionRequest } from '../models/OFXDetectionRequest';
import type { RuleCreateRequest } from '../models/RuleCreateRequest';
import type { RuleWriteRequest } from '../models/RuleWriteRequest';
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
     * Get Ofx Mappings Raw
     * Read raw YAML content of the OFX mappings file.
     * @returns ApiResponse_RuleContentResponse_ Successful Response
     * @throws ApiError
     */
    public static getOfxMappingsRaw(): CancelablePromise<ApiResponse_RuleContentResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/ofx-mappings/raw',
        });
    }
    /**
     * Update Ofx Mappings
     * Validate and write the OFX mappings file with atomic write + backup.
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static updateOfxMappings(
        requestBody: RuleWriteRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/import/ofx-mappings',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Categorize Transactions
     * Categorize transactions and detect potential duplicates.
     *
     * Engine selection:
     * - force_engine in request overrides config (for one-time AI fallback)
     * - config.ai.categorization.engine determines default engine
     * - If engine=classifier but insufficient training data, returns a warning
     * with ai_fallback_available flag so frontend can offer AI fallback
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
     * Create Csv Rule
     * Create a new CSV rule file.
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static createCsvRule(
        requestBody: RuleCreateRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/csv-rules',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
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
     * Update Csv Rule
     * Update an existing CSV rule file with atomic write + backup.
     * @param filename
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static updateCsvRule(
        filename: string,
        requestBody: RuleWriteRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/import/csv-rules/{filename}',
            path: {
                'filename': filename,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Csv Rule
     * Delete a CSV rule file.
     * @param filename
     * @returns ApiResponse_RuleDeleteResponse_ Successful Response
     * @throws ApiError
     */
    public static deleteCsvRule(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleDeleteResponse_> {
        return __request(OpenAPI, {
            method: 'DELETE',
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
     * Get Csv Rule Raw
     * Read raw YAML content of a CSV rule file.
     * @param filename
     * @returns ApiResponse_RuleContentResponse_ Successful Response
     * @throws ApiError
     */
    public static getCsvRuleRaw(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleContentResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/csv-rules/{filename}/raw',
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
     * Create Xls Rule
     * Create a new XLS rule file.
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static createXlsRule(
        requestBody: RuleCreateRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/xls-rules',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
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
     * Update Xls Rule
     * Update an existing XLS rule file with atomic write + backup.
     * @param filename
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static updateXlsRule(
        filename: string,
        requestBody: RuleWriteRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/import/xls-rules/{filename}',
            path: {
                'filename': filename,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Xls Rule
     * Delete an XLS rule file.
     * @param filename
     * @returns ApiResponse_RuleDeleteResponse_ Successful Response
     * @throws ApiError
     */
    public static deleteXlsRule(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleDeleteResponse_> {
        return __request(OpenAPI, {
            method: 'DELETE',
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
     * Get Xls Rule Raw
     * Read raw YAML content of an XLS rule file.
     * @param filename
     * @returns ApiResponse_RuleContentResponse_ Successful Response
     * @throws ApiError
     */
    public static getXlsRuleRaw(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleContentResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/xls-rules/{filename}/raw',
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
    /**
     * Get Email Rule Raw
     * Read raw YAML content of an email rule file.
     * @param filename
     * @returns ApiResponse_RuleContentResponse_ Successful Response
     * @throws ApiError
     */
    public static getEmailRuleRaw(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleContentResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/import/email/rules/{filename}/raw',
            path: {
                'filename': filename,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Email Rule
     * Create a new email rule file.
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static createEmailRule(
        requestBody: RuleCreateRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/email/rules',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Email Rule
     * Update an existing email rule file with atomic write + backup.
     * @param filename
     * @param requestBody
     * @returns ApiResponse_RuleWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static updateEmailRule(
        filename: string,
        requestBody: RuleWriteRequest,
    ): CancelablePromise<ApiResponse_RuleWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/import/email/rules/{filename}',
            path: {
                'filename': filename,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Email Rule
     * Delete an email rule file.
     * @param filename
     * @returns ApiResponse_RuleDeleteResponse_ Successful Response
     * @throws ApiError
     */
    public static deleteEmailRule(
        filename: string,
    ): CancelablePromise<ApiResponse_RuleDeleteResponse_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/import/email/rules/{filename}',
            path: {
                'filename': filename,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Llm Parse
     * Parse transactions from a file using the configured LLM.
     * @param formData
     * @returns any Successful Response
     * @throws ApiError
     */
    public static llmParseApiImportLlmParsePost(
        formData: Body_llm_parse_api_import_llm_parse_post,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/import/llm-parse',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
