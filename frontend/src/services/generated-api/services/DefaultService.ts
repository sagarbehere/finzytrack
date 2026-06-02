/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Root
     * @returns any Successful Response
     * @throws ApiError
     */
    public static rootGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/',
        });
    }
    /**
     * Health
     * Perform health checks on critical application components.
     *
     * The response also carries app/runtime metadata (version, python,
     * platform) that the frontend's About tab surfaces and that users
     * include in bug reports via the "Copy diagnostics" button. The
     * metadata fields don't participate in the healthy/unhealthy
     * determination — they're purely informational.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static healthHealthGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * Debug Config
     * Debug endpoint to inspect current configuration values.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static debugConfigDebugConfigGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/debug/config',
        });
    }
}
