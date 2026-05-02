/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssistantChatRequest } from '../models/AssistantChatRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AssistantService {
    /**
     * Ai Diagnostics
     * Report whether every file the AI assistant depends on is on disk.
     *
     * A standalone health-check the user (or a script) can hit to verify that
     * `scripts/sync_ai_reference.py` was run and the bundle is intact. This
     * prevents 'silent failure' where the assistant believes it has access to
     * reference files / schemas that aren't actually present.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static aiDiagnosticsApiAiDiagnosticsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/ai/diagnostics',
        });
    }
    /**
     * Assistant Chat
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static assistantChatApiAssistantChatPost(
        requestBody: AssistantChatRequest,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/assistant/chat',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
