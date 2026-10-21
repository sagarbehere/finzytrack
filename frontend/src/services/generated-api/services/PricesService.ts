/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AmfiSearchData_ } from '../models/ApiResponse_AmfiSearchData_';
import type { ApiResponse_PriceEditData_ } from '../models/ApiResponse_PriceEditData_';
import type { ApiResponse_PriceHistoryData_ } from '../models/ApiResponse_PriceHistoryData_';
import type { ApiResponse_PriceOverviewData_ } from '../models/ApiResponse_PriceOverviewData_';
import type { ApiResponse_PriceUpdateData_ } from '../models/ApiResponse_PriceUpdateData_';
import type { PriceEditRequest } from '../models/PriceEditRequest';
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
    /**
     * Get Price Overview
     * One row per (commodity, quote currency) with prices or held units: how
     * the fetcher prices it, its latest price, and whether that is out of date.
     * @returns ApiResponse_PriceOverviewData_ Successful Response
     * @throws ApiError
     */
    public static getPriceOverview(): CancelablePromise<ApiResponse_PriceOverviewData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prices/overview',
        });
    }
    /**
     * Get Price History
     * One pair's prices, newest first, with where each one is written.
     * @param commodity
     * @param quote
     * @param limit
     * @param offset
     * @returns ApiResponse_PriceHistoryData_ Successful Response
     * @throws ApiError
     */
    public static getPriceHistory(
        commodity: string,
        quote: string,
        limit: number = 50,
        offset?: number,
    ): CancelablePromise<ApiResponse_PriceHistoryData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prices/history',
            query: {
                'commodity': commodity,
                'quote': quote,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Edit Prices
     * Add, change and remove prices in the price file, as one write.
     *
     * ``set`` adds a price or replaces the one at the same (date, commodity, quote
     * currency, unless ``replace`` is false: then a taken date fails with 409
     * ``PRICE_EXISTS`` so the caller can ask first); ``remove`` deletes one. Prices
     * written in the ledger file can't be changed here (422 ``PRICE_IN_LEDGER``),
     * and none may be dated after tomorrow (422). Changing a price's date or
     * currency is a remove plus a set.
     * @param requestBody
     * @returns ApiResponse_PriceEditData_ Successful Response
     * @throws ApiError
     */
    public static editPrices(
        requestBody: PriceEditRequest,
    ): CancelablePromise<ApiResponse_PriceEditData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/prices',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Search Amfi Schemes
     * Find Indian mutual fund schemes by name, with each one's latest NAV, so the
     * user can pick the plan and option they hold and set ``fetch_symbol`` to
     * ``amfi:<scheme code>``. Calls mfapi.in.
     * @param q
     * @returns ApiResponse_AmfiSearchData_ Successful Response
     * @throws ApiError
     */
    public static searchAmfiSchemes(
        q: string,
    ): CancelablePromise<ApiResponse_AmfiSearchData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prices/amfi/search',
            query: {
                'q': q,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
