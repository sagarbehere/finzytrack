/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountCloseRequest } from '../models/AccountCloseRequest';
import type { AccountCreateRequest } from '../models/AccountCreateRequest';
import type { AccountUpdateRequest } from '../models/AccountUpdateRequest';
import type { ApiResponse_AccountCloseData_ } from '../models/ApiResponse_AccountCloseData_';
import type { ApiResponse_AccountCreateData_ } from '../models/ApiResponse_AccountCreateData_';
import type { ApiResponse_AccountDeleteData_ } from '../models/ApiResponse_AccountDeleteData_';
import type { ApiResponse_AccountListData_ } from '../models/ApiResponse_AccountListData_';
import type { ApiResponse_AccountUpdateData_ } from '../models/ApiResponse_AccountUpdateData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AccountsService {
    /**
     * List Accounts
     * Retrieve all accounts with full details including transaction history and balances.
     *
     * Returns both open and closed accounts. Frontend applications should filter
     * accounts based on the close_date field to show open vs closed accounts.
     * @returns ApiResponse_AccountListData_ Successful Response
     * @throws ApiError
     */
    public static listAccounts(): CancelablePromise<ApiResponse_AccountListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/accounts',
        });
    }
    /**
     * Create Account Endpoint
     * Create and open a new Beancount account with comprehensive configuration.
     * @param requestBody
     * @returns ApiResponse_AccountCreateData_ Successful Response
     * @throws ApiError
     */
    public static createAccount(
        requestBody: AccountCreateRequest,
    ): CancelablePromise<ApiResponse_AccountCreateData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/accounts',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Account
     * Update account details.
     * @param accountName Beancount account name to update
     * @param requestBody
     * @returns ApiResponse_AccountUpdateData_ Successful Response
     * @throws ApiError
     */
    public static updateAccount(
        accountName: string,
        requestBody: AccountUpdateRequest,
    ): CancelablePromise<ApiResponse_AccountUpdateData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/accounts/{account_name}',
            path: {
                'account_name': accountName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Account
     * Remove account from ledger (deletes the opening directive).
     * @param accountName Beancount account name to delete
     * @returns ApiResponse_AccountDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteAccount(
        accountName: string,
    ): CancelablePromise<ApiResponse_AccountDeleteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/accounts/{account_name}',
            path: {
                'account_name': accountName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Close Account
     * Close an account by adding a closing directive to the Beancount ledger.
     * @param accountName Beancount account name to close
     * @param requestBody
     * @returns ApiResponse_AccountCloseData_ Successful Response
     * @throws ApiError
     */
    public static closeAccount(
        accountName: string,
        requestBody: AccountCloseRequest,
    ): CancelablePromise<ApiResponse_AccountCloseData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/accounts/{account_name}/close',
            path: {
                'account_name': accountName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
