/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PlannedRow = {
    id: string;
    date: string;
    /**
     * Statement row type as printed
     */
    kind: string;
    description: string;
    amount?: (string | null);
    units?: (string | null);
    nav?: (string | null);
    /**
     * Statement running unit balance
     */
    balance?: (string | null);
    interpretation: PlannedRow.interpretation;
    /**
     * What the importer proposed before any correction
     */
    proposed: PlannedRow.proposed;
    /**
     * Why the importer reads it this way, when not obvious
     */
    reason?: (string | null);
    status: PlannedRow.status;
    corrected?: boolean;
    pair_id?: (string | null);
    /**
     * Name of the paired row's fund
     */
    pair_fund?: (string | null);
    /**
     * For a tax row: the row it belongs to
     */
    parent_id?: (string | null);
    /**
     * Index into transactions
     */
    transaction?: (number | null);
};
export namespace PlannedRow {
    export enum interpretation {
        BUY = 'buy',
        SELL = 'sell',
        REINVEST = 'reinvest',
        DIVIDEND = 'dividend',
        SWITCH_OUT = 'switch_out',
        SWITCH_IN = 'switch_in',
        MERGE_OUT = 'merge_out',
        MERGE_IN = 'merge_in',
        TAX = 'tax',
        SEGREGATION = 'segregation',
        REVERSAL = 'reversal',
        INFO = 'info',
        UNSUPPORTED = 'unsupported',
    }
    /**
     * What the importer proposed before any correction
     */
    export enum proposed {
        BUY = 'buy',
        SELL = 'sell',
        REINVEST = 'reinvest',
        DIVIDEND = 'dividend',
        SWITCH_OUT = 'switch_out',
        SWITCH_IN = 'switch_in',
        MERGE_OUT = 'merge_out',
        MERGE_IN = 'merge_in',
        TAX = 'tax',
        SEGREGATION = 'segregation',
        REVERSAL = 'reversal',
        INFO = 'info',
        UNSUPPORTED = 'unsupported',
    }
    export enum status {
        IMPORT = 'import',
        ALREADY = 'already',
        BEFORE_START = 'before_start',
        BEFORE_HAND = 'before_hand',
        INFO = 'info',
        UNSUPPORTED = 'unsupported',
        EXCLUDED = 'excluded',
        REVERSED = 'reversed',
        FAILED = 'failed',
    }
}

