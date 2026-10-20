/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MetadataReviewItem } from './MetadataReviewItem';
export type MetadataReviewData = {
    items: Array<MetadataReviewItem>;
    /**
     * Per scope: number of items with at least one unset key
     */
    summary: Record<string, number>;
    /**
     * When false, a commodity's asset-class decides whether it counts as a currency
     */
    has_operating_currencies: boolean;
};

