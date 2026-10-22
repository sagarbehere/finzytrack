/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FundPlan } from './FundPlan';
import type { PlannedBalance } from './PlannedBalance';
import type { PlannedCommodity } from './PlannedCommodity';
import type { PlannedPrice } from './PlannedPrice';
import type { PlannedTransaction } from './PlannedTransaction';
import type { StatementIssue } from './StatementIssue';
import type { StatementPlanSummary } from './StatementPlanSummary';
import type { UnclaimedBankTransaction } from './UnclaimedBankTransaction';
export type StatementPlanData = {
    summary: StatementPlanSummary;
    funds: Array<FundPlan>;
    transactions: Array<PlannedTransaction>;
    commodities: Array<PlannedCommodity>;
    balances: Array<PlannedBalance>;
    prices: Array<PlannedPrice>;
    /**
     * Hand-entered transactions this import replaces
     */
    removed_hand_entries: number;
    unclaimed_bank_transactions?: Array<UnclaimedBankTransaction>;
    /**
     * Must be fixed before Register
     */
    blocking: Array<StatementIssue>;
    warnings: Array<StatementIssue>;
    target_file: string;
    preview_hash: string;
};

