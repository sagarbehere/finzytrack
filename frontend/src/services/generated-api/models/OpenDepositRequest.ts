/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DepositFirstDeposit } from './DepositFirstDeposit';
import type { DepositIncomeAccount } from './DepositIncomeAccount';
/**
 * Open a savings, CD or bond account — optionally with its own interest income
 * account and its first deposit — as one ledger write.
 */
export type OpenDepositRequest = {
    /**
     * New Assets account name
     */
    account: string;
    /**
     * Written as account-type
     */
    account_type: OpenDepositRequest.account_type;
    /**
     * The deposit's currency (also constrains its open)
     */
    currency: string;
    open_date: string;
    /**
     * Stated rate as text, e.g. '4.50%'
     */
    interest_rate?: (string | null);
    /**
     * CD and bond only
     */
    maturity_date?: (string | null);
    income_account?: (DepositIncomeAccount | null);
    first_deposit?: (DepositFirstDeposit | null);
    /**
     * Return the preview only; write nothing
     */
    dry_run?: boolean;
    /**
     * preview_hash of the preview the user saw. When given and the write would differ, it fails with 409 PREVIEW_STALE and writes nothing.
     */
    expected_preview_hash?: (string | null);
};
export namespace OpenDepositRequest {
    /**
     * Written as account-type
     */
    export enum account_type {
        SAVINGS = 'savings',
        CD = 'cd',
        BOND = 'bond',
    }
}

