/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_CommodityListData_ } from '../models/ApiResponse_CommodityListData_';
import type { ApiResponse_CommodityMetadataUpdateData_ } from '../models/ApiResponse_CommodityMetadataUpdateData_';
import type { ApiResponse_OperatingCurrenciesData_ } from '../models/ApiResponse_OperatingCurrenciesData_';
import type { CommodityMetadataUpdateRequest } from '../models/CommodityMetadataUpdateRequest';
import type { OperatingCurrenciesUpdateRequest } from '../models/OperatingCurrenciesUpdateRequest';
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
     * Get Operating Currencies
     * Return the ledger's operating currencies — the authoritative currency whitelist.
     *
     * An empty list means no whitelist is declared; commodity currency-roles then
     * fall back to asset-class classification. See
     * dev-docs/commodities-and-currencies.md.
     * @returns ApiResponse_OperatingCurrenciesData_ Successful Response
     * @throws ApiError
     */
    public static getOperatingCurrencies(): CancelablePromise<ApiResponse_OperatingCurrenciesData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/commodities/operating-currencies',
        });
    }
    /**
     * Set Operating Currencies
     * Replace the ledger's operating currencies (writes `option "operating_currency"`).
     *
     * Full-replacement semantics: the given list becomes the complete whitelist;
     * an empty list clears it. Writes to the root ledger file via the single
     * authorised write path.
     * @param requestBody
     * @returns ApiResponse_OperatingCurrenciesData_ Successful Response
     * @throws ApiError
     */
    public static setOperatingCurrencies(
        requestBody: OperatingCurrenciesUpdateRequest,
    ): CancelablePromise<ApiResponse_OperatingCurrenciesData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/commodities/operating-currencies',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Commodity Metadata
     * Set and remove metadata keys on commodities, as one ledger write.
     *
     * Upsert per commodity: the `commodity` directive is updated in place, or
     * created if the commodity has none. Keys not named are kept. Only key syntax
     * is checked — any key and any value may be written (recognized, never
     * required; dev-docs/investment-view.md §10.3).
     * @param requestBody
     * @returns ApiResponse_CommodityMetadataUpdateData_ Successful Response
     * @throws ApiError
     */
    public static updateCommodityMetadata(
        requestBody: CommodityMetadataUpdateRequest,
    ): CancelablePromise<ApiResponse_CommodityMetadataUpdateData_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/commodities/metadata',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
