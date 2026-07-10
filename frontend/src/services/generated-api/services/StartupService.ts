/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_StartupApplyData_ } from '../models/ApiResponse_StartupApplyData_';
import type { ApiResponse_StartupTasksData_ } from '../models/ApiResponse_StartupTasksData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class StartupService {
    /**
     * Get Startup Tasks
     * Read-only: list pending startup tasks. Nothing is mutated.
     * @returns ApiResponse_StartupTasksData_ Successful Response
     * @throws ApiError
     */
    public static getStartupTasks(): CancelablePromise<ApiResponse_StartupTasksData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/startup/tasks',
        });
    }
    /**
     * Apply Startup Task
     * Apply a startup task after the user consents (e.g. run the recipe migration).
     * @param taskId
     * @returns ApiResponse_StartupApplyData_ Successful Response
     * @throws ApiError
     */
    public static applyStartupTask(
        taskId: string,
    ): CancelablePromise<ApiResponse_StartupApplyData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/startup/tasks/{task_id}/apply',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Dismiss Startup Task
     * Dismiss a non-blocking notice without applying it. For the seed-content
     * notice this snoozes it for the current bundle (it reappears only when a later
     * release ships different content); for a one-shot notice it marks it seen.
     * @param taskId
     * @returns ApiResponse_StartupApplyData_ Successful Response
     * @throws ApiError
     */
    public static dismissStartupTask(
        taskId: string,
    ): CancelablePromise<ApiResponse_StartupApplyData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/startup/tasks/{task_id}/dismiss',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reset Demo Data
     * Settings → "Reset demo data": restore the bundled demo dashboards and demo
     * ledgers to their shipped state, ignoring provenance (backing up whatever's
     * there first). The always-available manual path for a user who tinkered and
     * wants the shipped demo back. See dev-docs/seed-content-refresh.md §9.3.
     * @returns ApiResponse_StartupApplyData_ Successful Response
     * @throws ApiError
     */
    public static resetDemoData(): CancelablePromise<ApiResponse_StartupApplyData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/startup/seed/reset',
        });
    }
}
