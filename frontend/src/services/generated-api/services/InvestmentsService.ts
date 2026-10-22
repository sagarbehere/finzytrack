/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AssembleData_ } from '../models/ApiResponse_AssembleData_';
import type { ApiResponse_HoldingsData_ } from '../models/ApiResponse_HoldingsData_';
import type { ApiResponse_OpenDepositData_ } from '../models/ApiResponse_OpenDepositData_';
import type { ApiResponse_StatementCommitData_ } from '../models/ApiResponse_StatementCommitData_';
import type { ApiResponse_StatementImportListData_ } from '../models/ApiResponse_StatementImportListData_';
import type { ApiResponse_StatementParseData_ } from '../models/ApiResponse_StatementParseData_';
import type { ApiResponse_StatementPlanData_ } from '../models/ApiResponse_StatementPlanData_';
import type { ApiResponse_StatementUndoData_ } from '../models/ApiResponse_StatementUndoData_';
import type { AssembleRequest } from '../models/AssembleRequest';
import type { Body_parseStatement } from '../models/Body_parseStatement';
import type { OpenDepositRequest } from '../models/OpenDepositRequest';
import type { StatementCommitRequest } from '../models/StatementCommitRequest';
import type { StatementPlanRequest } from '../models/StatementPlanRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class InvestmentsService {
    /**
     * Assemble Investment Events
     * Assemble one or more typed investment events into balanced draft transactions.
     *
     * Partial success is allowed: some events become drafts while others return typed
     * warnings/errors. The response is never committed — drafts stage into the review
     * table and Register uses the existing commit path.
     * @param requestBody
     * @returns ApiResponse_AssembleData_ Successful Response
     * @throws ApiError
     */
    public static assembleInvestmentEvents(
        requestBody: AssembleRequest,
    ): CancelablePromise<ApiResponse_AssembleData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/investments/assemble',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Investment Holdings
     * Return every held position and its open lots (with latest price).
     * @returns ApiResponse_HoldingsData_ Successful Response
     * @throws ApiError
     */
    public static listInvestmentHoldings(): CancelablePromise<ApiResponse_HoldingsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/investments/holdings',
        });
    }
    /**
     * Get Investment Holdings For Commodity
     * Return the open lots for a single commodity (feeds the Sale lot picker).
     * @param commodity
     * @returns ApiResponse_HoldingsData_ Successful Response
     * @throws ApiError
     */
    public static getInvestmentHoldingsForCommodity(
        commodity: string,
    ): CancelablePromise<ApiResponse_HoldingsData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/investments/holdings/{commodity}',
            path: {
                'commodity': commodity,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Open Deposit
     * Open a savings, CD or bond account, optionally with its own interest income
     * account (linked by `interest_account`) and its first deposit, as one write.
     *
     * With `dry_run`, nothing is written: the response is the text that would be
     * written (apart from the transfer's IDs), checked against the current accounts,
     * and its `preview_hash`. Send that back as `expected_preview_hash` so the write
     * refuses (409 PREVIEW_STALE) if it would write anything different.
     * @param requestBody
     * @returns ApiResponse_OpenDepositData_ Successful Response
     * @throws ApiError
     */
    public static openDeposit(
        requestBody: OpenDepositRequest,
    ): CancelablePromise<ApiResponse_OpenDepositData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/investments/open-deposit',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Parse Statement
     * Read a CAS PDF locally and keep the parsed statement for 30 minutes.
     * Returns what the mapping screen needs: distributors, funds, which accounts are
     * needed, the ledger's files, and the user's own earlier choices.
     * @param formData
     * @returns ApiResponse_StatementParseData_ Successful Response
     * @throws ApiError
     */
    public static parseStatement(
        formData: Body_parseStatement,
    ): CancelablePromise<ApiResponse_StatementParseData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/investments/statement/parse',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Statement
     * Plan the import for the user's mapping and row corrections. Nothing is written.
     * @param requestBody
     * @returns ApiResponse_StatementPlanData_ Successful Response
     * @throws ApiError
     */
    public static previewStatement(
        requestBody: StatementPlanRequest,
    ): CancelablePromise<ApiResponse_StatementPlanData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/investments/statement/preview',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Commit Statement
     * Write the import the user previewed, in one ledger write (409 PREVIEW_STALE if
     * the ledger changed since the preview).
     * @param requestBody
     * @returns ApiResponse_StatementCommitData_ Successful Response
     * @throws ApiError
     */
    public static commitStatement(
        requestBody: StatementCommitRequest,
    ): CancelablePromise<ApiResponse_StatementCommitData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/investments/statement/commit',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Statement Imports
     * Earlier statement imports recorded in the ledger, newest first.
     * @returns ApiResponse_StatementImportListData_ Successful Response
     * @throws ApiError
     */
    public static listStatementImports(): CancelablePromise<ApiResponse_StatementImportListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/investments/statement/imports',
        });
    }
    /**
     * Undo Statement Import
     * Remove one import's transactions, balance assertions and record in one write.
     * @param importId
     * @returns ApiResponse_StatementUndoData_ Successful Response
     * @throws ApiError
     */
    public static undoStatementImport(
        importId: string,
    ): CancelablePromise<ApiResponse_StatementUndoData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/investments/statement/imports/{import_id}',
            path: {
                'import_id': importId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
