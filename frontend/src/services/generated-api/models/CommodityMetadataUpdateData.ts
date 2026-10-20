/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommodityDetails } from './CommodityDetails';
/**
 * Result of a commodity metadata batch.
 */
export type CommodityMetadataUpdateData = {
    /**
     * Codes whose directive was created or changed
     */
    changed: Array<string>;
    /**
     * Read-back of every commodity named in the request
     */
    commodities: Array<CommodityDetails>;
};

