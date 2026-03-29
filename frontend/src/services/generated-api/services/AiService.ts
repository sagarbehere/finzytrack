/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GenerateQueryRequest } from '../models/GenerateQueryRequest';
import type { ParseNLTransactionRequest } from '../models/ParseNLTransactionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AiService {
    /**
     * Parse Nl Transaction
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static parseNlTransaction(
        requestBody: ParseNLTransactionRequest,
    ): CancelablePromise<any> {
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
     * @returns any Successful Response
     * @throws ApiError
     */
    public static generateQuery(
        requestBody: GenerateQueryRequest,
    ): CancelablePromise<any> {
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
