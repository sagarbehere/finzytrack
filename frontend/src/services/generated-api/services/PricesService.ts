/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_PriceUpdateData_ } from '../models/ApiResponse_PriceUpdateData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PricesService {
    /**
     * Update Prices
     * Fetch prices for every priced holding and persist them to the sidecar.
     * @returns ApiResponse_PriceUpdateData_ Successful Response
     * @throws ApiError
     */
    public static updatePrices(): CancelablePromise<ApiResponse_PriceUpdateData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/prices/update',
        });
    }
}
