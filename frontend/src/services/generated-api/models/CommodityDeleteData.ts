/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response data for commodity delete results.
 */
export type CommodityDeleteData = {
    /**
     * Whether commodity was deleted
     */
    commodity_deleted: boolean;
    /**
     * Delete result message
     */
    message: string;
    /**
     * Any warnings about the deletion
     */
    warnings?: (Array<string> | null);
};

