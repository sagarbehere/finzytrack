/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A balance assertion directive with optional associated pad.
 */
export type BalanceDirectiveData = {
    date: string;
    currency: string;
    expected_balance: string;
    has_pad: boolean;
    pad_source_account?: (string | null);
    has_error?: boolean;
    error_message?: (string | null);
};

