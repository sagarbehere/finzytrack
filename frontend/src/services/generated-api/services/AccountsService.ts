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
import type { ApiResponse_AccountReopenData_ } from '../models/ApiResponse_AccountReopenData_';
import type { ApiResponse_AccountUpdateData_ } from '../models/ApiResponse_AccountUpdateData_';
import type { ApiResponse_BalanceDirectiveCreateData_ } from '../models/ApiResponse_BalanceDirectiveCreateData_';
import type { ApiResponse_BalanceDirectiveDeleteData_ } from '../models/ApiResponse_BalanceDirectiveDeleteData_';
import type { ApiResponse_BalanceDirectiveListData_ } from '../models/ApiResponse_BalanceDirectiveListData_';
import type { ApiResponse_BalanceDirectiveUpdateData_ } from '../models/ApiResponse_BalanceDirectiveUpdateData_';
import type { BalanceDirectiveCreateRequest } from '../models/BalanceDirectiveCreateRequest';
import type { BalanceDirectiveUpdateRequest } from '../models/BalanceDirectiveUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AccountsService {
    /**
     * List Accounts
     * Retrieve all accounts with full details including transaction history and balances.
     *
     * Supports optional date filtering for balances:
     * - Balance Sheet accounts (Assets, Liabilities, Equity): Balance as of end_date
     * - Income Statement accounts (Income, Expenses): Balance within start_date to end_date
     *
     * Returns both open and closed accounts. Frontend applications should filter
     * accounts based on the close_date field to show open vs closed accounts.
     * @param startDate Start date for balance filtering (YYYY-MM-DD). For Income/Expenses only.
     * @param endDate End date for balance filtering (YYYY-MM-DD). Defaults to today.
     * @returns ApiResponse_AccountListData_ Successful Response
     * @throws ApiError
     */
    public static listAccounts(
        startDate?: (string | null),
        endDate?: (string | null),
    ): CancelablePromise<ApiResponse_AccountListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/accounts',
            query: {
                'start_date': startDate,
                'end_date': endDate,
            },
            errors: {
                422: `Validation Error`,
            },
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
     * Remove account from ledger. Optionally deletes associated transactions.
     * @param accountName Beancount account name to delete
     * @param deleteTransactions Whether to delete transactions associated with this account
     * @returns ApiResponse_AccountDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteAccount(
        accountName: string,
        deleteTransactions: boolean = true,
    ): CancelablePromise<ApiResponse_AccountDeleteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/accounts/{account_name}',
            path: {
                'account_name': accountName,
            },
            query: {
                'delete_transactions': deleteTransactions,
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
    /**
     * Reopen Account
     * Reopen a closed account by removing the close directive from the ledger.
     * @param accountName Beancount account name to reopen
     * @returns ApiResponse_AccountReopenData_ Successful Response
     * @throws ApiError
     */
    public static reopenAccount(
        accountName: string,
    ): CancelablePromise<ApiResponse_AccountReopenData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/accounts/{account_name}/reopen',
            path: {
                'account_name': accountName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Balance Directives
     * List all balance directives for an account, including pad and error info.
     * @param accountName Beancount account name
     * @returns ApiResponse_BalanceDirectiveListData_ Successful Response
     * @throws ApiError
     */
    public static listBalanceDirectives(
        accountName: string,
    ): CancelablePromise<ApiResponse_BalanceDirectiveListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/accounts/{account_name}/balance-directives',
            path: {
                'account_name': accountName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Balance Directive
     * Create a balance assertion (optionally with a pad directive).
     * @param accountName Beancount account name
     * @param requestBody
     * @returns ApiResponse_BalanceDirectiveCreateData_ Successful Response
     * @throws ApiError
     */
    public static createBalanceDirective(
        accountName: string,
        requestBody: BalanceDirectiveCreateRequest,
    ): CancelablePromise<ApiResponse_BalanceDirectiveCreateData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/accounts/{account_name}/balance-directives',
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
     * Update Balance Directive
     * Update an existing balance directive.
     * @param accountName Beancount account name
     * @param requestBody
     * @returns ApiResponse_BalanceDirectiveUpdateData_ Successful Response
     * @throws ApiError
     */
    public static updateBalanceDirective(
        accountName: string,
        requestBody: BalanceDirectiveUpdateRequest,
    ): CancelablePromise<ApiResponse_BalanceDirectiveUpdateData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/accounts/{account_name}/balance-directives',
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
     * Delete Balance Directive
     * Delete a balance directive (and optionally its associated pad).
     * @param accountName Beancount account name
     * @param date Directive date (YYYY-MM-DD)
     * @param currency Currency code
     * @param amount Expected balance amount
     * @param deletePad Also delete associated pad directive
     * @returns ApiResponse_BalanceDirectiveDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteBalanceDirective(
        accountName: string,
        date: string,
        currency: string,
        amount: number,
        deletePad: boolean = true,
    ): CancelablePromise<ApiResponse_BalanceDirectiveDeleteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/accounts/{account_name}/balance-directives',
            path: {
                'account_name': accountName,
            },
            query: {
                'date': date,
                'currency': currency,
                'amount': amount,
                'delete_pad': deletePad,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
