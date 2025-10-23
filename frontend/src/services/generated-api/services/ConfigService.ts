/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_Config_ } from '../models/ApiResponse_Config_';
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
}
