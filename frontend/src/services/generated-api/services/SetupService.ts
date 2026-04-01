/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_SetupResponse_ } from '../models/ApiResponse_SetupResponse_';
import type { SetupRequest } from '../models/SetupRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SetupService {
    /**
     * Complete Setup
     * Finalize first-run setup.
     *
     * Applies the user's wizard choices: sets currency, creates or copies
     * the ledger file, optionally configures AI, and marks setup as complete.
     * @param requestBody
     * @returns ApiResponse_SetupResponse_ Successful Response
     * @throws ApiError
     */
    public static completeSetupApiSetupCompletePost(
        requestBody: SetupRequest,
    ): CancelablePromise<ApiResponse_SetupResponse_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/setup/complete',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
