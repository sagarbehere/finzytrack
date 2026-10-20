/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_MetadataReviewData_ } from '../models/ApiResponse_MetadataReviewData_';
import type { ApiResponse_MetadataVocabularyData_ } from '../models/ApiResponse_MetadataVocabularyData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MetadataService {
    /**
     * Get Metadata Vocabulary
     * The recognized metadata keys and values. Recognition only — the ledger may
     * carry any key and any value.
     * @returns ApiResponse_MetadataVocabularyData_ Successful Response
     * @throws ApiError
     */
    public static getMetadataVocabulary(): CancelablePromise<ApiResponse_MetadataVocabularyData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/metadata/vocabulary',
        });
    }
    /**
     * Get Metadata Review
     * Commodities and accounts the vocabulary can describe, with their current
     * metadata and the relevant keys that are not set.
     * @returns ApiResponse_MetadataReviewData_ Successful Response
     * @throws ApiError
     */
    public static getMetadataReview(): CancelablePromise<ApiResponse_MetadataReviewData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/metadata/review',
        });
    }
}
