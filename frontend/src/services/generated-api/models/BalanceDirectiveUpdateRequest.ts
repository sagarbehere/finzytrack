/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to update an existing balance directive.
 */
export type BalanceDirectiveUpdateRequest = {
    /**
     * Date in YYYY-MM-DD format
     */
    original_date: string;
    original_currency: string;
    original_amount: number;
    new_date?: (string | null);
    new_currency?: (string | null);
    new_amount?: (number | null);
    include_pad?: (boolean | null);
    pad_source_account?: (string | null);
};

