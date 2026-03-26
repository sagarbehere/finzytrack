/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_RecipeWriteResponse_ } from '../models/ApiResponse_RecipeWriteResponse_';
import type { RecipeWriteRequest } from '../models/RecipeWriteRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class RecipesService {
    /**
     * Get Manifest
     * Return the recipe manifest.
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
     * Write or update a recipe JSON file.
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
     * Delete a recipe file and remove it from the manifest.
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
