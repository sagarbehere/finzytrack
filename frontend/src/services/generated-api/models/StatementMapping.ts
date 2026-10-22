/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FundKey } from './FundKey';
/**
 * The user's choices. Every account is one they picked (never proposed).
 */
export type StatementMapping = {
    /**
     * Distributor key → holding account
     */
    holding_accounts?: Record<string, string>;
    /**
     * Cash side when no bank transaction matches
     */
    cash_fallback_account?: (string | null);
    /**
     * Transfer account holding bank transactions to match
     */
    cash_matching_account?: (string | null);
    match_window_days?: number;
    /**
     * Before this date, use cash_before_cutoff_account
     */
    cutoff_date?: (string | null);
    cash_before_cutoff_account?: (string | null);
    /**
     * Import history from this date; earlier lots become opening holdings
     */
    history_start?: (string | null);
    dividend_account?: (string | null);
    taxes_account?: (string | null);
    gains_account?: (string | null);
    /**
     * Fund (security key) → commodity code override
     */
    commodity_codes?: Record<string, string>;
    /**
     * Fund → what to do with hand-entered holdings of it
     */
    hand_entries?: Record<string, 'replace' | 'after_latest'>;
    /**
     * Ledger file to write to (one of ledger_files); default the root
     */
    target_file?: (string | null);
    /**
     * Set income-type: dividend on the dividend account
     */
    tag_dividend_account?: boolean;
    /**
     * Funds not to import (e.g. someone else's): no transactions, balance check or price
     */
    excluded_funds?: Array<FundKey>;
};

