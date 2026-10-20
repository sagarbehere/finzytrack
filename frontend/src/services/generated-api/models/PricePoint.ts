/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PricePoint = {
    date: string;
    number: string;
    quote_currency: string;
    source: PricePoint.source;
};
export namespace PricePoint {
    export enum source {
        SIDECAR = 'sidecar',
        LEDGER = 'ledger',
    }
}

