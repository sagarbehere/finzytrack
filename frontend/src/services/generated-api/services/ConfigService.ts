/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_Config_ } from '../models/ApiResponse_Config_';
import type { ApiResponse_ConfigPatchResponse_ } from '../models/ApiResponse_ConfigPatchResponse_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ConfigService {
    /**
     * Get Config Endpoint
     * Get application configuration for frontend use.
     *
     * Returns the complete Config object including all settings.
     * This is safe for single-user local deployment.
     *
     * The Config model is the single source of truth - OpenAPI codegen
     * will automatically generate complete TypeScript types from it.
     * @returns ApiResponse_Config_ Successful Response
     * @throws ApiError
     */
    public static getConfigEndpointApiConfigGet(): CancelablePromise<ApiResponse_Config_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/config',
        });
    }
    /**
     * Patch Config Endpoint
     * Partially update application configuration from the GUI settings editor.
     *
     * Accepts a JSON object containing only the fields to change. Nested fields
     * can be expressed as nested objects (e.g. {"ai": {"llm": {"api_url": "..."}}}).
     *
     * The update is merged into the existing YAML file using a round-trip parser
     * so that comments and formatting are preserved. The result is validated
     * against the full Config schema before writing.
     * @param requestBody
     * @returns ApiResponse_ConfigPatchResponse_ Successful Response
     * @throws ApiError
     */
    public static patchConfigEndpointApiConfigPatch(
        requestBody: Record<string, any>,
    ): CancelablePromise<ApiResponse_ConfigPatchResponse_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/config',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
