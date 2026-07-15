/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DashboardTheme_ } from '../models/ApiResponse_DashboardTheme_';
import type { ApiResponse_DashboardThemeListResponse_ } from '../models/ApiResponse_DashboardThemeListResponse_';
import type { ApiResponse_DashboardThemeWriteResponse_ } from '../models/ApiResponse_DashboardThemeWriteResponse_';
import type { DashboardThemeWriteRequest } from '../models/DashboardThemeWriteRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DashboardThemesService {
    /**
     * Get Active Dashboard Theme
     * The active dashboard theme. Never 404s — falls back to the default.
     * @returns ApiResponse_DashboardTheme_ Successful Response
     * @throws ApiError
     */
    public static getActiveDashboardThemeApiDashboardThemeGet(): CancelablePromise<ApiResponse_DashboardTheme_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/dashboard-theme',
        });
    }
    /**
     * List Dashboard Themes
     * Available themes (bundled ∪ user; a user copy overrides the bundled one).
     * @returns ApiResponse_DashboardThemeListResponse_ Successful Response
     * @throws ApiError
     */
    public static listDashboardThemesApiDashboardThemesGet(): CancelablePromise<ApiResponse_DashboardThemeListResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/dashboard-themes',
        });
    }
    /**
     * Get Dashboard Theme
     * A specific theme by id.
     * @param themeId
     * @returns ApiResponse_DashboardTheme_ Successful Response
     * @throws ApiError
     */
    public static getDashboardThemeApiDashboardThemeThemeIdGet(
        themeId: string,
    ): CancelablePromise<ApiResponse_DashboardTheme_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/dashboard-theme/{theme_id}',
            path: {
                'theme_id': themeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Write Dashboard Theme
     * Write/update a theme file (validated, atomic write + backup).
     * @param themeId
     * @param requestBody
     * @returns ApiResponse_DashboardThemeWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static writeDashboardThemeApiDashboardThemeThemeIdPut(
        themeId: string,
        requestBody: DashboardThemeWriteRequest,
    ): CancelablePromise<ApiResponse_DashboardThemeWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/dashboard-theme/{theme_id}',
            path: {
                'theme_id': themeId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
