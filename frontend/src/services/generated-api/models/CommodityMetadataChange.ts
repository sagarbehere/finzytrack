/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Set and/or remove metadata keys on one commodity's `commodity` directive.
 *
 * Keys not named are kept. The directive is created if the commodity has none
 * and something is being set.
 */
export type CommodityMetadataChange = {
    /**
     * Commodity/currency code (e.g., 'USD', 'AAPL', 'BRK.B')
     */
    code: string;
    /**
     * Keys to add or overwrite
     */
    set?: Record<string, string>;
    /**
     * Keys to delete
     */
    remove?: Array<string>;
};

