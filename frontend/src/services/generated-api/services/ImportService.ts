/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_LearnOFXAccountData_ } from '../models/ApiResponse_LearnOFXAccountData_';
import type { ApiResponse_OFXDetectionData_ } from '../models/ApiResponse_OFXDetectionData_';
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
}
