/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HoldingLot } from './HoldingLot';
/**
 * All open lots of one commodity, plus its latest price.
 */
export type CommodityHoldings = {
    commodity: string;
    is_currency: boolean;
    asset_class?: (string | null);
    name?: (string | null);
    total_units: string;
    last_price?: (string | null);
    last_price_currency?: (string | null);
    last_price_date?: (string | null);
    lots?: Array<HoldingLot>;
};

