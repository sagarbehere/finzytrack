/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ComputeData_ } from '../models/ApiResponse_ComputeData_';
import type { ComputeRequest } from '../models/ComputeRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ComputeService {
    /**
     * Execute Compute
     * Execute a registered compute function with validated scalar args.
     * @param requestBody
     * @returns ApiResponse_ComputeData_ Successful Response
     * @throws ApiError
     */
    public static executeCompute(
        requestBody: ComputeRequest,
    ): CancelablePromise<ApiResponse_ComputeData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/compute',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
