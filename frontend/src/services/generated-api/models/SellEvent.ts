/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LotSelection } from './LotSelection';
/**
 * Reduce a holding and realize a gain — cost basis resolved from the mirror lots.
 *
 * Provide either an explicit ``lots`` selection (specific-lot) or a ``strategy``
 * (fifo/lifo/hifo) to auto-select which lots to consume. Every reducing leg is
 * written with an explicit ``{cost, date}`` so it books deterministically under any
 * Beancount booking method (dev-docs/investment-view.md §6.3 event 4, §6.5).
 */
export type SellEvent = {
    /**
     * Event date (YYYY-MM-DD)
     */
    date: string;
    /**
     * Cash / quote currency (e.g. USD)
     */
    currency: string;
    /**
     * Overrides the default narration
     */
    narration?: (string | null);
    /**
     * Optional payee
     */
    payee?: (string | null);
    /**
     * Optional memo (stored as memo: metadata)
     */
    memo?: (string | null);
    /**
     * Transaction tags
     */
    tags?: Array<string>;
    /**
     * Transaction links
     */
    links?: Array<string>;
    /**
     * Broker transaction id (dedupe)
     */
    external_id?: (string | null);
    /**
     * Type of external_id
     */
    external_id_type?: (string | null);
    /**
     * Free-text note (stored in meta)
     */
    note?: (string | null);
    type: string;
    /**
     * Assets account the units are sold from
     */
    holding_account: string;
    /**
     * Commodity code (is_currency=0)
     */
    security: string;
    /**
     * Total units to sell (> 0)
     */
    units: (number | string);
    /**
     * Sale price per unit in `currency`
     */
    sale_price: (number | string);
    /**
     * Assets account receiving the proceeds
     */
    cash_account: string;
    /**
     * Income account for the realized gain (income-type: capital-gains)
     */
    gains_account: string;
    /**
     * Optional fee amount
     */
    fee?: (number | string | null);
    /**
     * Expenses account for the fee
     */
    fee_account?: (string | null);
    /**
     * Specific lots to consume; if omitted, `strategy` selects them
     */
    lots?: (Array<LotSelection> | null);
    /**
     * Auto lot-selection when `lots` is omitted: fifo (oldest), lifo (newest), hifo (highest cost)
     */
    strategy?: SellEvent.strategy;
};
export namespace SellEvent {
    /**
     * Auto lot-selection when `lots` is omitted: fifo (oldest), lifo (newest), hifo (highest cost)
     */
    export enum strategy {
        FIFO = 'fifo',
        LIFO = 'lifo',
        HIFO = 'hifo',
    }
}

