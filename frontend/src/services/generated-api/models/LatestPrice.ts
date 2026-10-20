/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type LatestPrice = {
    date: string;
    /**
     * Price of one unit, in the row's quote currency.
     */
    number: string;
    /**
     * 'sidecar' (the price file) or 'ledger' (a price written in the ledger file).
     */
    source: LatestPrice.source;
};
export namespace LatestPrice {
    /**
     * 'sidecar' (the price file) or 'ledger' (a price written in the ledger file).
     */
    export enum source {
        SIDECAR = 'sidecar',
        LEDGER = 'ledger',
    }
}

