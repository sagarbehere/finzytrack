/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommodityDetails } from './CommodityDetails';
/**
 * Response data for commodity update results.
 */
export type CommodityUpdateData = {
    /**
     * Whether commodity was updated
     */
    commodity_updated: boolean;
    /**
     * Updated commodity details if successful
     */
    commodity_details?: (CommodityDetails | null);
    /**
     * Update result message
     */
    message: string;
};

