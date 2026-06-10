/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_NoticesResponse_ } from '../models/ApiResponse_NoticesResponse_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class NoticesService {
    /**
     * Get Notices
     * Return the current server-side advisories.
     *
     * Stateless — recomputed on every call from the SQLite mirror and the
     * on-disk ledger tree.
     * @returns ApiResponse_NoticesResponse_ Successful Response
     * @throws ApiError
     */
    public static getNotices(): CancelablePromise<ApiResponse_NoticesResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/notices',
        });
    }
}
