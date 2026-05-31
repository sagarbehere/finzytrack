/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_RecipeWriteResponse_ } from '../models/ApiResponse_RecipeWriteResponse_';
import type { ApiResponse_RuleContentResponse_ } from '../models/ApiResponse_RuleContentResponse_';
import type { RecipeWriteRequest } from '../models/RecipeWriteRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class RecipesService {
    /**
     * Get Manifest
     * Return the auto-discovered recipe manifest.
     *
     * Any `widgets*.json` and `dashboards*.json` under the recipes
     * directory is included. Paths are sorted alphabetically; the manifest
     * is recomputed on every request, so files added by `cp`, `mv`, or any
     * other out-of-band write are picked up immediately.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getManifestApiRecipesManifestJsonGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/recipes/manifest.json',
        });
    }
    /**
     * Get Recipe File Raw
     * Return raw text content of a recipe JSON file.
     * @param filePath
     * @returns ApiResponse_RuleContentResponse_ Successful Response
     * @throws ApiError
     */
    public static getRecipeRaw(
        filePath: string,
    ): CancelablePromise<ApiResponse_RuleContentResponse_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/recipes/{file_path}/raw',
            path: {
                'file_path': filePath,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Recipe File
     * Return an individual recipe JSON file.
     * @param filePath
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getRecipeFileApiRecipesFilePathGet(
        filePath: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/recipes/{file_path}',
            path: {
                'file_path': filePath,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Write Recipe File
     * Write or update a recipe JSON file. Validates content, then writes
     * via the backup manager's atomic-write path (temp file + fsync + atomic
     * rename). Existing files also get a timestamped backup first; new files
     * skip the backup step automatically (no original to snapshot). Same path
     * for both new and existing files, matching the AI tool in
     * ``ai/tools/write_recipe.py``.
     * @param filePath
     * @param requestBody
     * @returns ApiResponse_RecipeWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static writeRecipeFileApiRecipesFilePathPut(
        filePath: string,
        requestBody: RecipeWriteRequest,
    ): CancelablePromise<ApiResponse_RecipeWriteResponse_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/recipes/{file_path}',
            path: {
                'file_path': filePath,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Recipe File
     * Delete a recipe file. A timestamped backup is taken first (same
     * retention policy as overwrites) so an accidental delete can be
     * recovered from the backup directory. Auto-discovery picks up the
     * removal on the next manifest fetch.
     * @param filePath
     * @returns ApiResponse_RecipeWriteResponse_ Successful Response
     * @throws ApiError
     */
    public static deleteRecipeFileApiRecipesFilePathDelete(
        filePath: string,
    ): CancelablePromise<ApiResponse_RecipeWriteResponse_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/recipes/{file_path}',
            path: {
                'file_path': filePath,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
