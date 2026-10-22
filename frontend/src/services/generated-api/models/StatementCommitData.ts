/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatementCommitData = {
    import_id: string;
    transactions: number;
    balances: number;
    commodities_created: number;
    commodities_updated: number;
    prices: number;
    removed_hand_entries: number;
    /**
     * Balance assertions that don't hold after the write
     */
    failed_balance_checks?: Array<string>;
};

