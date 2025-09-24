/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for creating new Beancount commodities.
 */
export type CommodityCreateRequest = {
    /**
     * Commodity/currency code (uppercase alphanumeric)
     */
    code: string;
    /**
     * Optional full name
     */
    name?: (string | null);
    /**
     * Commodity type (defaults to 'Unknown')
     */
    type?: (string | null);
    /**
     * Optional commodity metadata
     */
    metadata?: (Record<string, any> | null);
};

