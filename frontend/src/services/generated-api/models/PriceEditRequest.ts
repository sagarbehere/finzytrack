/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PriceKey } from './PriceKey';
import type { PriceSet } from './PriceSet';
/**
 * A batch of price edits, saved as one write to the price file.
 */
export type PriceEditRequest = {
    set?: Array<PriceSet>;
    remove?: Array<PriceKey>;
};

