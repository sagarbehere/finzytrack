/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single posting of an assembled draft (mirrors CommitPosting).
 */
export type DraftPosting = {
    account: string;
    amount: string;
    currency: string;
    cost_amount?: (string | null);
    cost_currency?: (string | null);
    cost_date?: (string | null);
    price_amount?: (string | null);
    price_currency?: (string | null);
    price_type?: (string | null);
    posting_meta?: (Record<string, string> | null);
};

