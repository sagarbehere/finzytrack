/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Accounts the user chose for the same roles in their last import (from the ledger).
 */
export type StatementPreselection = {
    holding_accounts?: Record<string, string>;
    cash_fallback_account?: (string | null);
    cash_matching_account?: (string | null);
    dividend_account?: (string | null);
    taxes_account?: (string | null);
    gains_account?: (string | null);
};

