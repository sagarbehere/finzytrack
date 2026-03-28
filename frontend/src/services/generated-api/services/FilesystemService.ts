/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_BrowseResponse_ } from '../models/ApiResponse_BrowseResponse_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class FilesystemService {
    /**
     * Browse Directory
     * List entries in a directory for the file picker UI.
     *
     * Returns directories first (sorted), then files (sorted).
     * Hides dotfiles/dotdirs. Applies extension filtering when requested.
     * @param path Directory to list. Defaults to current working directory.
     * @param mode Selection mode: 'file' shows files and directories, 'directory' shows directories only.
     * @param extensions Comma-separated file extensions to filter by (e.g. '.beancount,.bean'). Only applies when mode=file. Directories are always shown.
     * @returns ApiResponse_BrowseResponse_ Successful Response
     * @throws ApiError
     */
    public static browseDirectoryApiFilesystemBrowseGet(
        path?: (string | null),
        mode: 'file' | 'directory' = 'file',
        extensions?: (string | null),
    ): CancelablePromise<ApiResponse_BrowseResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/filesystem/browse',
            query: {
                'path': path,
                'mode': mode,
                'extensions': extensions,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
