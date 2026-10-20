/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Add a price, or replace the one at the same (date, commodity, quote currency).
 */
export type PriceSet = {
    date: string;
    /**
     * Commodity/currency code (e.g., 'USD', 'AAPL', 'BRK.B')
     */
    commodity: string;
    /**
     * Price of one unit (> 0).
     */
    number: (number | string);
    /**
     * Commodity/currency code (e.g., 'USD', 'AAPL', 'BRK.B')
     */
    quote_currency: string;
    /**
     * False: fail with 409 PRICE_EXISTS if this date already has a price, instead of replacing it.
     */
    replace?: boolean;
};

