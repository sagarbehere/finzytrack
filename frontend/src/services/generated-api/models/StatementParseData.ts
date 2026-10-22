/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { StatementDistributor } from './StatementDistributor';
import type { StatementFundSummary } from './StatementFundSummary';
import type { StatementPreselection } from './StatementPreselection';
export type StatementParseData = {
    /**
     * Refers to the parsed statement in later calls (30 minutes)
     */
    token: string;
    /**
     * Statement format, e.g. 'cas'
     */
    source: string;
    /**
     * 'detailed' or 'summary'
     */
    variant: string;
    period_start: string;
    period_end: string;
    /**
     * Investor names shown on the statement (display only)
     */
    investors: Array<string>;
    distributors: Array<StatementDistributor>;
    funds: Array<StatementFundSummary>;
    row_count: number;
    needs_dividend_account: boolean;
    needs_taxes_account: boolean;
    needs_gains_account: boolean;
    needs_cash_account: boolean;
    /**
     * Files the import can write to (root first); one entry = no choice
     */
    ledger_files: Array<string>;
    preselected: StatementPreselection;
    warnings?: Array<string>;
};

