/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LatestPrice } from './LatestPrice';
/**
 * One (commodity, quote currency) pair that has prices or is held.
 */
export type PriceOverviewRow = {
    /**
     * Commodity/currency code (e.g., 'USD', 'AAPL', 'BRK.B')
     */
    commodity: string;
    /**
     * Quote currency; null for a held commodity with no price and no cost currency.
     */
    quote_currency?: (string | null);
    /**
     * The commodity's asset-class metadata, as written.
     */
    asset_class?: (string | null);
    /**
     * How the fetcher prices it: daily, monthly, computed (I-Bond) or never.
     */
    cadence: PriceOverviewRow.cadence;
    /**
     * Net units across all accounts are above zero.
     */
    held: boolean;
    latest?: (LatestPrice | null);
    /**
     * Days from the latest price to today.
     */
    age_days?: (number | null);
    /**
     * Number of prices for this pair.
     */
    count: number;
    /**
     * Held, priced by cadence, and the latest price is older than the cadence allows (or missing).
     */
    out_of_date: boolean;
    /**
     * Why the fetcher won't price it, when it won't.
     */
    not_fetched_reason?: (string | null);
};
export namespace PriceOverviewRow {
    /**
     * How the fetcher prices it: daily, monthly, computed (I-Bond) or never.
     */
    export enum cadence {
        DAILY = 'daily',
        MONTHLY = 'monthly',
        COMPUTED = 'computed',
        NEVER = 'never',
    }
}

