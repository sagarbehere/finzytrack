/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_BudgetListData_ } from '../models/ApiResponse_BudgetListData_';
import type { ApiResponse_BudgetWriteData_ } from '../models/ApiResponse_BudgetWriteData_';
import type { BudgetWriteRequest } from '../models/BudgetWriteRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class BudgetsService {
    /**
     * Get Budgets
     * Effective budgets as of ``as_of`` (default today), or all raw directives
     * with ``history=true``.
     * @param account Filter to one account
     * @param currency Filter to one currency
     * @param asOf Effective date (YYYY-MM-DD); defaults to today
     * @param history Return all raw directives (history) instead of the effective set
     * @returns ApiResponse_BudgetListData_ Successful Response
     * @throws ApiError
     */
    public static getBudgets(
        account?: (string | null),
        currency?: (string | null),
        asOf?: (string | null),
        history: boolean = false,
    ): CancelablePromise<ApiResponse_BudgetListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/budgets',
            query: {
                'account': account,
                'currency': currency,
                'as_of': asOf,
                'history': history,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Budget
     * @param requestBody
     * @returns ApiResponse_BudgetWriteData_ Successful Response
     * @throws ApiError
     */
    public static createBudget(
        requestBody: BudgetWriteRequest,
    ): CancelablePromise<ApiResponse_BudgetWriteData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/budgets',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Budget
     * @param budgetId
     * @param requestBody
     * @returns ApiResponse_BudgetWriteData_ Successful Response
     * @throws ApiError
     */
    public static updateBudget(
        budgetId: string,
        requestBody: BudgetWriteRequest,
    ): CancelablePromise<ApiResponse_BudgetWriteData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/budgets/{budget_id}',
            path: {
                'budget_id': budgetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Budget
     * @param budgetId
     * @returns ApiResponse_BudgetWriteData_ Successful Response
     * @throws ApiError
     */
    public static deleteBudget(
        budgetId: string,
    ): CancelablePromise<ApiResponse_BudgetWriteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/budgets/{budget_id}',
            path: {
                'budget_id': budgetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
