/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AssembleData_ } from '../models/ApiResponse_AssembleData_';
import type { ApiResponse_HoldingsData_ } from '../models/ApiResponse_HoldingsData_';
import type { ApiResponse_OpenDepositData_ } from '../models/ApiResponse_OpenDepositData_';
import type { AssembleRequest } from '../models/AssembleRequest';
import type { OpenDepositRequest } from '../models/OpenDepositRequest';
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
     * With `dry_run`, nothing is written: the response is the exact text that would
     * be written, checked against the current accounts.
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
}
