/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_CommodityCreateData_ } from '../models/ApiResponse_CommodityCreateData_';
import type { ApiResponse_CommodityDeleteData_ } from '../models/ApiResponse_CommodityDeleteData_';
import type { ApiResponse_CommodityListData_ } from '../models/ApiResponse_CommodityListData_';
import type { ApiResponse_CommodityUpdateData_ } from '../models/ApiResponse_CommodityUpdateData_';
import type { CommodityCreateRequest } from '../models/CommodityCreateRequest';
import type { CommodityUpdateRequest } from '../models/CommodityUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CommoditiesService {
    /**
     * List Commodities
     * Retrieve all commodities with full details including usage statistics and metadata.
     *
     * Returns commodities discovered from commodity directives, transactions, and price entries.
     * @returns ApiResponse_CommodityListData_ Successful Response
     * @throws ApiError
     */
    public static listCommodities(): CancelablePromise<ApiResponse_CommodityListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/commodities',
        });
    }
    /**
     * Create Commodity Endpoint
     * Create and add a new Beancount commodity with metadata.
     * @param requestBody
     * @returns ApiResponse_CommodityCreateData_ Successful Response
     * @throws ApiError
     */
    public static createCommodity(
        requestBody: CommodityCreateRequest,
    ): CancelablePromise<ApiResponse_CommodityCreateData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/commodities',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Commodity
     * Update commodity details.
     * @param commodityCode Commodity code to update
     * @param requestBody
     * @returns ApiResponse_CommodityUpdateData_ Successful Response
     * @throws ApiError
     */
    public static updateCommodity(
        commodityCode: string,
        requestBody: CommodityUpdateRequest,
    ): CancelablePromise<ApiResponse_CommodityUpdateData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/commodities/{commodity_code}',
            path: {
                'commodity_code': commodityCode,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Commodity
     * Remove commodity from ledger (deletes the commodity directive).
     * @param commodityCode Commodity code to delete
     * @returns ApiResponse_CommodityDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteCommodity(
        commodityCode: string,
    ): CancelablePromise<ApiResponse_CommodityDeleteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/commodities/{commodity_code}',
            path: {
                'commodity_code': commodityCode,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
