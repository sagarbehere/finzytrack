/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single posting in a transaction to be committed.
 */
export type CommitPosting = {
    /**
     * Beancount account name
     */
    account: string;
    /**
     * Posting amount
     */
    amount: (number | string);
    /**
     * Currency code
     */
    currency: string;
};

