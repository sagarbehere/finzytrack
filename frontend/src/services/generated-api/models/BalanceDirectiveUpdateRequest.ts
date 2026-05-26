/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to update an existing balance directive.
 */
export type BalanceDirectiveUpdateRequest = {
    original_date: string;
    original_currency: string;
    original_amount: (number | string);
    new_date?: (string | null);
    new_currency?: (string | null);
    new_amount?: (number | string | null);
    include_pad?: (boolean | null);
    pad_source_account?: (string | null);
};

