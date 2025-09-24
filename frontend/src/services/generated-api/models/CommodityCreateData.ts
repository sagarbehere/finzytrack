/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommodityDetails } from './CommodityDetails';
/**
 * Response data for commodity creation results.
 */
export type CommodityCreateData = {
    /**
     * Whether commodity was created
     */
    commodity_created: boolean;
    /**
     * Created commodity details if successful
     */
    commodity_details?: (CommodityDetails | null);
    /**
     * Creation result message
     */
    message: string;
};

