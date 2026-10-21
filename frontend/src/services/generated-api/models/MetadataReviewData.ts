/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MetadataReviewItem } from './MetadataReviewItem';
export type MetadataReviewData = {
    items: Array<MetadataReviewItem>;
    /**
     * Per scope: open, in-use items whose main key (asset-class for commodities, account-type for accounts, income-type for income accounts) is not set. Drives the dashboard banners.
     */
    summary: Record<string, number>;
    /**
     * When false, a commodity's asset-class decides whether it counts as a currency
     */
    has_operating_currencies: boolean;
};

