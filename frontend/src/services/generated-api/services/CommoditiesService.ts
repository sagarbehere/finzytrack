/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_CommodityListData_ } from '../models/ApiResponse_CommodityListData_';
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
}
