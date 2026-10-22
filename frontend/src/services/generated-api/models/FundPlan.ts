/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlannedRow } from './PlannedRow';
import type { YearCheck } from './YearCheck';
export type FundPlan = {
    security_key: string;
    folio: string;
    name: string;
    institution: string;
    investor?: (string | null);
    distributor: string;
    holding_account?: (string | null);
    commodity: string;
    commodity_status: FundPlan.commodity_status;
    isin?: (string | null);
    amfi?: (string | null);
    close_units: string;
    /**
     * Units in the ledger after the import
     */
    ledger_units: string;
    reconciled: boolean;
    /**
     * The statement's own rows add up to its closing units
     */
    statement_consistent: boolean;
    held: boolean;
    /**
     * Status → row count
     */
    counts: Record<string, number>;
    matched?: number;
    fallback?: number;
    /**
     * Hand-entered transactions found for this fund
     */
    hand_entries?: (number | null);
    hand_entries_latest?: (string | null);
    hand_entries_choice?: (string | null);
    years: Array<YearCheck>;
    rows: Array<PlannedRow>;
};
export namespace FundPlan {
    export enum commodity_status {
        NEW = 'new',
        EXISTING = 'existing',
    }
}

