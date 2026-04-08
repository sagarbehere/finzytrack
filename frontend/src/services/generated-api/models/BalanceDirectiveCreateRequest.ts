/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to create a balance assertion (optionally with pad).
 */
export type BalanceDirectiveCreateRequest = {
    date: string;
    currency: string;
    amount: number;
    include_pad?: boolean;
    pad_source_account?: (string | null);
};

