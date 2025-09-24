/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for updating commodity details.
 */
export type CommodityUpdateRequest = {
    /**
     * Updated full name
     */
    name?: (string | null);
    /**
     * Updated commodity type
     */
    type?: (string | null);
    /**
     * Updated metadata (merges with existing)
     */
    metadata?: (Record<string, any> | null);
};

