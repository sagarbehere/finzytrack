/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_GenerateQueryData_ } from '../models/ApiResponse_GenerateQueryData_';
import type { ApiResponse_ParseNLTransactionData_ } from '../models/ApiResponse_ParseNLTransactionData_';
import type { GenerateQueryRequest } from '../models/GenerateQueryRequest';
import type { ParseNLTransactionRequest } from '../models/ParseNLTransactionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AiService {
    /**
     * Parse Nl Transaction
     * @param requestBody
     * @returns ApiResponse_ParseNLTransactionData_ Successful Response
     * @throws ApiError
     */
    public static parseNlTransaction(
        requestBody: ParseNLTransactionRequest,
    ): CancelablePromise<ApiResponse_ParseNLTransactionData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/ai/parse-nl-transaction',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Generate Query
     * @param requestBody
     * @returns ApiResponse_GenerateQueryData_ Successful Response
     * @throws ApiError
     */
    public static generateQuery(
        requestBody: GenerateQueryRequest,
    ): CancelablePromise<ApiResponse_GenerateQueryData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/ai/generate-query',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
